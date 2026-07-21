"""Known-speaker diarization using enrolled WAV reference clips."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import aiohttp

from .speaker_id import SpeakerProfiles, pcm16_wav


TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"
STT_USAGE_PATH = Path("/data/stt_usage.json")


@dataclass(frozen=True)
class DiarizationResult:
    speaker: str
    transcript: str
    seconds: float


class CloudTranscriber:
    def __init__(
        self,
        api_key: str,
        session: aiohttp.ClientSession,
        model: str = "gpt-4o-mini-transcribe",
        prompt: str = "",
        daily_audio_seconds_limit: float = 600.0,
        timeout_seconds: float = 60.0,
        input_usd_per_million: float = 1.25,
        output_usd_per_million: float = 5.0,
        usage_path: Path = STT_USAGE_PATH,
    ) -> None:
        self.api_key = api_key
        self.session = session
        self.model = model
        self.prompt = prompt
        self.daily_audio_seconds_limit = daily_audio_seconds_limit
        self.timeout_seconds = timeout_seconds
        self.input_usd_per_million = input_usd_per_million
        self.output_usd_per_million = output_usd_per_million
        self.usage_path = usage_path
        self.usage = self._load_usage()

    def _load_usage(self) -> dict[str, Any]:
        try:
            value = json.loads(self.usage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "days": {}}
        return value if isinstance(value, dict) else {"version": 1, "days": {}}

    @staticmethod
    def _day() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def _day_item(self) -> dict[str, Any]:
        days = self.usage.setdefault("days", {})
        item = days.setdefault(self._day(), {})
        for key, default in {
            "requests": 0,
            "audio_sent_seconds": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }.items():
            item.setdefault(key, default)
        return item

    def _save_usage(self) -> None:
        self.usage_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.usage_path.with_suffix(self.usage_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.usage, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.usage_path)

    def today_summary(self) -> dict[str, Any]:
        item = self._day_item()
        input_tokens = int(item["input_tokens"])
        output_tokens = int(item["output_tokens"])
        estimated_cost = (
            input_tokens * self.input_usd_per_million
            + output_tokens * self.output_usd_per_million
        ) / 1_000_000
        return {
            "requests": int(item["requests"]),
            "audio_sent_seconds": float(item["audio_sent_seconds"]),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": int(item["total_tokens"]),
            "estimated_cost_usd": round(estimated_cost, 9),
            "daily_audio_seconds_limit": self.daily_audio_seconds_limit,
        }

    def _record_usage(self, audio_seconds: float, usage: dict[str, Any]) -> None:
        item = self._day_item()
        item["requests"] = int(item["requests"]) + 1
        item["audio_sent_seconds"] = round(
            float(item["audio_sent_seconds"]) + audio_seconds, 3
        )
        for field in ("input_tokens", "output_tokens", "total_tokens"):
            value = usage.get(field)
            if isinstance(value, int):
                item[field] = int(item[field]) + value
        item["model"] = self.model
        item["last_usage"] = usage
        item["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._save_usage()

    async def transcribe(self, pcm16_16k: bytes) -> str:
        audio_seconds = len(pcm16_16k) / 2 / 16_000
        already_sent = float(self._day_item()["audio_sent_seconds"])
        if already_sent + audio_seconds > self.daily_audio_seconds_limit:
            raise RuntimeError(
                "Дневной лимит STT исчерпан: "
                f"{already_sent:.1f} из {self.daily_audio_seconds_limit:.0f} с"
            )
        form = aiohttp.FormData()
        form.add_field(
            "file",
            pcm16_wav(pcm16_16k),
            filename="turn.wav",
            content_type="audio/wav",
        )
        form.add_field("model", self.model)
        form.add_field("response_format", "text")
        form.add_field("language", "ru")
        form.add_field("stream", "true")
        if self.prompt:
            form.add_field("prompt", self.prompt)
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds, connect=10)
        async with self.session.post(
            TRANSCRIPTIONS_URL,
            data=form,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "OpenAI-Safety-Identifier": "sokol9-home",
            },
            timeout=timeout,
        ) as response:
            body_text = await response.text(encoding="utf-8")
            if response.status >= 300:
                raise RuntimeError(
                    f"cloud STT HTTP {response.status}: {body_text[:1000]}"
                )
        chunks: list[str] = []
        final_text = ""
        usage: dict[str, Any] = {}
        for raw_line in body_text.splitlines():
            if not raw_line.startswith("data:"):
                continue
            payload = raw_line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "transcript.text.delta":
                chunks.append(str(event.get("delta", "")))
            elif event.get("type") == "transcript.text.done":
                final_text = str(event.get("text", "")).strip()
                raw_usage = event.get("usage")
                if isinstance(raw_usage, dict):
                    usage = raw_usage
        if not final_text:
            final_text = "".join(chunks).strip()
        if not final_text and body_text.strip() and not body_text.lstrip().startswith("data:"):
            final_text = body_text.strip()
        self._record_usage(audio_seconds, usage)
        return final_text


class SpeakerDiarizer:
    def __init__(
        self,
        api_key: str,
        session: aiohttp.ClientSession,
        profiles: SpeakerProfiles,
        model: str = "gpt-4o-transcribe-diarize",
    ) -> None:
        self.api_key = api_key
        self.session = session
        self.profiles = profiles
        self.model = model

    @property
    def ready(self) -> bool:
        return bool(self.profiles.reference_clips())

    async def identify(self, pcm16_16k: bytes) -> DiarizationResult | None:
        references = self.profiles.reference_clips()
        if not references:
            return None
        form = aiohttp.FormData()
        form.add_field(
            "file",
            pcm16_wav(pcm16_16k),
            filename="turn.wav",
            content_type="audio/wav",
        )
        form.add_field("model", self.model)
        form.add_field("response_format", "diarized_json")
        form.add_field("chunking_strategy", "auto")
        form.add_field("language", "ru")
        for name, wav_bytes in references:
            form.add_field("known_speaker_names[]", name)
            reference = base64.b64encode(wav_bytes).decode("ascii")
            form.add_field(
                "known_speaker_references[]", f"data:audio/wav;base64,{reference}"
            )
        timeout = aiohttp.ClientTimeout(total=35, connect=10)
        async with self.session.post(
            TRANSCRIPTIONS_URL,
            data=form,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "OpenAI-Safety-Identifier": "sokol9-home",
            },
            timeout=timeout,
        ) as response:
            body: Any = await response.json(content_type=None)
            if response.status >= 300:
                detail = body.get("error", body) if isinstance(body, dict) else body
                raise RuntimeError(f"speaker diarization HTTP {response.status}: {detail}")
        segments = body.get("segments") or []
        durations: dict[str, float] = {}
        texts: list[str] = []
        for segment in segments:
            speaker = str(segment.get("speaker", "")).strip()
            text = str(segment.get("text", "")).strip()
            if text:
                texts.append(text)
            if speaker:
                duration = max(
                    0.0,
                    float(segment.get("end", 0)) - float(segment.get("start", 0)),
                )
                durations[speaker] = durations.get(speaker, 0.0) + duration
        if not durations:
            return None
        speaker, seconds = max(durations.items(), key=lambda item: item[1])
        known_names = {name for name, _ in references}
        if speaker not in known_names:
            return None
        return DiarizationResult(speaker, " ".join(texts), seconds)
