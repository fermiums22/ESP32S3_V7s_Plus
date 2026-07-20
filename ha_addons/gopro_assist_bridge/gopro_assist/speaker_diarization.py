"""Known-speaker diarization using enrolled WAV reference clips."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import aiohttp

from .speaker_id import SpeakerProfiles, pcm16_wav


TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"


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
    ) -> None:
        self.api_key = api_key
        self.session = session
        self.model = model

    async def transcribe(self, pcm16_16k: bytes) -> str:
        form = aiohttp.FormData()
        form.add_field(
            "file",
            pcm16_wav(pcm16_16k),
            filename="turn.wav",
            content_type="audio/wav",
        )
        form.add_field("model", self.model)
        form.add_field("response_format", "json")
        form.add_field("language", "ru")
        timeout = aiohttp.ClientTimeout(total=25, connect=10)
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
                raise RuntimeError(f"cloud STT HTTP {response.status}: {detail}")
        return str(body.get("text", "")).strip()


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
