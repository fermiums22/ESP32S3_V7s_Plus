# -*- coding: utf-8 -*-
"""Voice-only STT gateway: local filtering, paid transcription, no AI replies."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
import base64
from datetime import datetime, timezone
import io
import hashlib
import json
from pathlib import Path
import queue
import re
import sys
import threading
import time
import wave

import numpy as np
import requests
from rich.console import Console, Group
from rich.live import Live
from rich.rule import Rule
from rich.text import Text
import sounddevice as sd
from speaker_profiles import SpeakerMatch, SpeakerProfiles
from silero_vad_onnx import FRAME_MS, FRAME_SAMPLES, SileroSegmenter


ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "stt_config.json"
AUDIO_KEY_FILE = ROOT / "audio_secrets.txt"
FALLBACK_KEY_FILE = ROOT / "secrets.txt"
OUTPUT_DIR = ROOT / "transcripts"
PREVIEW_DIR = ROOT / "preview_audio"
USAGE_FILE = ROOT / "audio_usage.json"
ACTIVE_SPEAKER_FILE = ROOT / "active_speaker.json"
QUICK_REPLIES_FILE = ROOT / "quick_replies.json"
QUICK_REPLY_CACHE = ROOT / "quick_reply_cache"
SILERO_MODEL_FILE = ROOT / "vad_models" / "silero_vad_16k_op15.onnx"
TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"
TARGET_RATE = 16_000
WAKE_WORD = "сокол"


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def load_json(path: Path, default: dict) -> dict:
    if not path.is_file():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Не удалось прочитать {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"В {path.name} должен быть JSON-объект")
    return value


def save_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def load_key() -> str:
    key_file = AUDIO_KEY_FILE if AUDIO_KEY_FILE.is_file() else FALLBACK_KEY_FILE
    if not key_file.is_file():
        raise RuntimeError(
            f"Нет {AUDIO_KEY_FILE.name}. Положи туда ключ платного Audio API."
        )
    key = key_file.read_text(encoding="utf-8-sig").strip()
    if not key.startswith(("sk-", "sk-proj-")):
        raise RuntimeError(f"В {key_file.name} нет ключа OpenAI")
    return key


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resample_mono(block: np.ndarray, source_rate: int) -> np.ndarray:
    mono = block[:, 0].astype(np.float64, copy=False)
    if source_rate == TARGET_RATE:
        return mono.astype("<i2")
    output_size = max(1, round(len(mono) * TARGET_RATE / source_rate))
    source_positions = np.arange(len(mono), dtype=np.float64)
    target_positions = np.linspace(0, len(mono) - 1, output_size)
    return np.interp(target_positions, source_positions, mono).astype("<i2")


def high_pass(pcm: np.ndarray, cutoff_hz: float = 90.0) -> np.ndarray:
    """Cheap first-order rumble/DC filter suitable for the HA CPU path."""
    if len(pcm) < 2:
        return pcm
    source = pcm.astype(np.float64)
    result = np.empty_like(source)
    rc = 1.0 / (2.0 * np.pi * cutoff_hz)
    dt = 1.0 / TARGET_RATE
    alpha = rc / (rc + dt)
    result[0] = 0.0
    for index in range(1, len(source)):
        result[index] = alpha * (result[index - 1] + source[index] - source[index - 1])
    return np.clip(result, -32768, 32767).astype("<i2")


def wav_bytes(pcm: np.ndarray) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(TARGET_RATE)
        wav_file.writeframes(pcm.astype("<i2", copy=False).tobytes())
    return output.getvalue()


def contains_wake_word(text: str) -> bool:
    return WAKE_WORD in re.findall(r"[а-яё]+", text.casefold())


def emergency_local_command(text: str, dialog_open: bool) -> str | None:
    """Return only a safe offline command; never pass arbitrary text onward."""
    normalized = re.sub(
        r"[^а-яa-z0-9]+", " ", text.casefold().replace("ё", "е")
    ).strip()
    addressed = bool(re.search(r"\bсокол\w*\b", normalized))
    if addressed:
        normalized = re.sub(r"\bсокол\w*\b", " ", normalized, count=1).strip()
    elif not dialog_open:
        return None
    if normalized in {"стоп", "стой", "остановись", "аварийный стоп"}:
        return "stop"
    if normalized in {
        "домой",
        "на базу",
        "едь домой",
        "езжай домой",
        "возвращайся на базу",
    }:
        return "home"
    if normalized in {"следуй за мной", "едь за мной", "езжай за мной"}:
        return "follow"
    return None


def cached_whisper_model(model_name: str) -> str:
    repository = f"models--Systran--faster-whisper-{model_name}"
    roots = (
        Path.home() / ".cache" / "huggingface" / "hub" / repository,
        Path("D:/cltkfq_tts/cache/huggingface/hub") / repository,
    )
    for root in roots:
        snapshots = root / "snapshots"
        if not snapshots.is_dir():
            continue
        for snapshot in snapshots.iterdir():
            model_file = snapshot / "model.bin"
            if snapshot.is_dir() and model_file.is_file() and model_file.stat().st_size > 0:
                return str(snapshot)
    return model_name


def speech_meter(rms: float, threshold: float, width: int = 20) -> str:
    filled = min(width, max(0, round(rms / max(threshold, 1.0) * 5)))
    return "█" * filled + "░" * (width - filled)


class TerminalUI:
    def __init__(self, config: dict, microphone: str, local_only: bool) -> None:
        self.console = Console()
        self.interactive = self.console.is_terminal
        self.lock = threading.RLock()
        self.local_model = str(config["local_model"])
        self.api_model = str(config["api_model"])
        self.microphone = microphone
        self.local_only = local_only
        self.summary = (
            "ИТОГО UTC: API отключён"
            if local_only
            else "ИТОГО UTC: загрузка статистики..."
        )
        self.cost = "РАСХОД UTC: $0.000000"
        self.api_status = "API: готов |░░░░░░░░░░░░░░░░░░░░|"
        self.calibration = (
            "Silero VAD: "
            f"старт {float(config.get('silero_threshold', 0.50)):.2f}, "
            f"конец {float(config.get('silero_negative_threshold', 0.35)):.2f}"
        )
        self.status = "○ ТИШИНА |░░░░░░░░░░░░░░░░░░░░|"
        self.log_lines: deque[str] = deque(
            maxlen=max(10, self.console.height - 12)
        )
        self.live = Live(
            self._render(),
            console=self.console,
            refresh_per_second=12,
            vertical_overflow="crop",
        )
        self.started = False

    def _render(self) -> Group:
        mode = (
            "Режим предпросмотра: сеть и API отключены."
            if self.local_only
            else f"Локальный фильтр → {self.api_model}."
        )
        header = "\n".join(
            (
                "Детектор речи: Silero VAD ONNX (CPU, 1 поток)",
                f"Аварийные команды: faster-whisper/{self.local_model} (ленивая загрузка)",
                self.summary,
                self.cost,
                self.api_status,
                f"Микрофон: {self.microphone}",
                mode,
                "Предварительный ответ: локально; облако только уточняет твою фразу.",
                "— перед фразой означает платную обработку API.",
                "Можно говорить сразу. Enter — остановить.",
                self.calibration,
                self.status,
            )
        )
        log_text = "\n".join(self.log_lines)
        return Group(Text(header), Rule("РАЗГОВОР"), Text(log_text))

    def _refresh(self) -> None:
        if self.started and self.interactive:
            self.live.update(self._render(), refresh=True)

    def start(self) -> None:
        with self.lock:
            self.started = True
            if self.interactive:
                self.live.start(refresh=True)
            else:
                self.console.print(self._render())

    def stop(self) -> None:
        with self.lock:
            if self.started:
                if self.interactive:
                    self.live.update(self._render(), refresh=True)
                    self.live.stop()
                self.started = False

    def set_summary(self, summary: dict) -> None:
        with self.lock:
            self.summary = (
                "ИТОГО UTC: "
                f"{summary['requests']} запросов; "
                f"{summary['total_tokens']} токенов "
                f"(вход {summary['input_tokens']}, выход {summary['output_tokens']}); "
                f"аудио {summary['audio_sent_seconds']:.1f} с"
            )
            self.cost = f"РАСХОД UTC: ${summary['estimated_cost_usd']:.6f}"
            self._refresh()
            if not self.interactive:
                self.console.print(self.summary)
                self.console.print(self.cost)

    def set_calibration(self, noise: float, threshold: float) -> None:
        with self.lock:
            self.calibration = (
                f"Калибровка: шум={noise:.0f}, порог речи={threshold:.0f}"
            )
            self._refresh()
            if not self.interactive:
                self.console.print(self.calibration)

    def set_api_status(
        self,
        stage: str,
        elapsed: float = 0.0,
        *,
        complete: bool = False,
    ) -> None:
        with self.lock:
            if complete:
                meter = "█" * 20
            else:
                position = int(elapsed * 10) % 20
                cells = ["░"] * 20
                cells[position] = "█"
                meter = "".join(cells)
            self.api_status = f"API: {stage} |{meter}| {elapsed:.1f} с"
            self._refresh()

    def set_status(self, speaking: bool, rms: float = 0.0, threshold: float = 1.0) -> None:
        with self.lock:
            marker = "● РЕЧЬ" if speaking else "○ ТИШИНА"
            meter = speech_meter(rms, threshold) if speaking else "░" * 20
            self.status = f"{marker} |{meter}|"
            self._refresh()

    def log(self, message: str, *, new_phrase: bool = False) -> None:
        with self.lock:
            if new_phrase and self.log_lines:
                self.log_lines.append("")
            self.log_lines.extend(message.splitlines() or [""])
            self._refresh()
            if not self.interactive:
                if new_phrase:
                    self.console.print()
                self.console.print(message)


class StatsLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()

    def write(self, event_type: str, **fields: object) -> None:
        record = {"time": utc_now(), "type": event_type, **fields}
        with self.lock, self.path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")


class QuickReplySelector:
    def __init__(self) -> None:
        data = load_json(QUICK_REPLIES_FILE, {})
        if not data:
            raise RuntimeError(f"Некорректный {QUICK_REPLIES_FILE.name}")
        self.categories = data
        self.indexes: dict[str, int] = {}

    def choose(self, draft: str) -> str:
        normalized = draft.casefold().replace("ё", "е")
        words = set(re.findall(r"[a-zа-я0-9]+", normalized))

        def matches(keyword: object) -> bool:
            pattern = str(keyword).casefold().replace("ё", "е")
            if " " in pattern or len(pattern) > 3:
                return pattern in normalized
            return pattern in words

        category = ""
        for name, item in self.categories.items():
            keywords = item.get("keywords", [])
            if any(matches(keyword) for keyword in keywords):
                category = name
                break
        if not category:
            return ""
        replies = self.categories[category].get("replies", [])
        if not replies:
            return ""
        index = self.indexes.get(category, 0)
        self.indexes[category] = index + 1
        return str(replies[index % len(replies)])


class QuickReplySpeaker:
    def __init__(self, config: dict, playback_active: threading.Event, stats: StatsLog) -> None:
        self.voice = str(config.get("quick_reply_voice", "M2"))
        self.speed = float(config.get("quick_reply_speed", 1.18))
        self.steps = int(config.get("quick_reply_steps", 6))
        self.playback_active = playback_active
        self.interrupt_requested = threading.Event()
        self.stats = stats
        self.queue: queue.Queue[
            tuple[str, Callable[[], None] | None] | None
        ] = queue.Queue(maxsize=4)
        QUICK_REPLY_CACHE.mkdir(exist_ok=True)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def speak(
        self, text: str, on_complete: Callable[[], None] | None = None
    ) -> None:
        try:
            self.queue.put_nowait((text, on_complete))
        except queue.Full:
            self.stats.write("quick_reply_dropped", text=text)

    def close(self) -> None:
        if not self.thread.is_alive():
            return
        try:
            self.queue.put(None, timeout=1.0)
        except queue.Full:
            self.stats.write("quick_reply_close_timeout")
            return
        self.thread.join(timeout=30.0)

    def interrupt(self) -> None:
        if self.playback_active.is_set():
            self.interrupt_requested.set()

    def _cache_path(self, text: str) -> Path:
        key = f"{self.voice}|{self.speed}|{self.steps}|{text}".encode("utf-8")
        return QUICK_REPLY_CACHE / f"{hashlib.sha1(key).hexdigest()}.wav"

    def _run(self) -> None:
        try:
            from sokol9_supertonic_panel import ENGINE
        except Exception as exc:
            self.stats.write("quick_reply_tts_unavailable", error=str(exc))
            return
        while True:
            item = self.queue.get()
            if item is None:
                return
            text, on_complete = item
            try:
                path = self._cache_path(text)
                if not path.is_file():
                    path.write_bytes(
                        ENGINE.synthesize_one(
                            text,
                            voice=self.voice,
                            speed=self.speed,
                            steps=self.steps,
                        )
                    )
                with wave.open(str(path), "rb") as wav_file:
                    if wav_file.getsampwidth() != 2:
                        raise RuntimeError("Supertonic вернул WAV не PCM16")
                    rate = wav_file.getframerate()
                    channels = wav_file.getnchannels()
                    pcm = np.frombuffer(
                        wav_file.readframes(wav_file.getnframes()), dtype="<i2"
                    ).reshape(-1, channels)
                self.playback_active.set()
                self.interrupt_requested.clear()
                try:
                    with sd.OutputStream(
                        samplerate=rate,
                        channels=channels,
                        dtype="int16",
                    ) as stream:
                        chunk_frames = max(1, round(rate * 0.05))
                        interrupted = False
                        for start in range(0, len(pcm), chunk_frames):
                            if self.interrupt_requested.is_set():
                                interrupted = True
                                break
                            stream.write(pcm[start : start + chunk_frames])
                    if interrupted:
                        self.stats.write("quick_reply_interrupted", text=text)
                    else:
                        time.sleep(0.1)
                finally:
                    self.playback_active.clear()
                    self.interrupt_requested.clear()
            except Exception as exc:
                self.playback_active.clear()
                self.stats.write("quick_reply_tts_error", text=text, error=str(exc))
            finally:
                if on_complete is not None:
                    try:
                        on_complete()
                    except Exception as exc:
                        self.stats.write(
                            "quick_reply_callback_error", error=str(exc)
                        )


class UsageLog:
    def __init__(self, input_price: float, output_price: float) -> None:
        self.data = load_json(USAGE_FILE, {"version": 1, "days": {}})
        self.lock = threading.Lock()
        self.input_price = input_price
        self.output_price = output_price

    @staticmethod
    def _ensure_item(item: dict) -> None:
        defaults = {
            "requests": 0,
            "audio_sent_seconds": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "usage_seconds": 0,
            "models": {},
        }
        for name, value in defaults.items():
            item.setdefault(name, value)

    def _summary(self, item: dict) -> dict:
        self._ensure_item(item)
        input_tokens = int(item.get("input_tokens", 0))
        output_tokens = int(item.get("output_tokens", 0))
        audio_seconds = float(item.get("audio_sent_seconds", 0.0))
        estimated_cost = (
            input_tokens * self.input_price + output_tokens * self.output_price
        ) / 1_000_000
        estimated_cost += float(item.get("billing_adjustment_usd", 0.0))
        estimated_hour = (
            estimated_cost * 3600 / audio_seconds if audio_seconds > 0 else 0.0
        )
        item["estimated_cost_usd"] = round(estimated_cost, 9)
        return {
            "requests": int(item.get("requests", 0)),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": int(item.get("total_tokens", 0)),
            "audio_sent_seconds": audio_seconds,
            "estimated_cost_usd": estimated_cost,
            "estimated_cost_per_audio_hour_usd": estimated_hour,
        }

    def today_summary(self) -> dict:
        day = datetime.now(timezone.utc).date().isoformat()
        with self.lock:
            item = self.data.setdefault("days", {}).setdefault(day, {})
            summary = self._summary(item)
            save_json(USAGE_FILE, self.data)
        return summary

    def check_audio_budget(self, audio_seconds: float, limit_seconds: float) -> None:
        day = datetime.now(timezone.utc).date().isoformat()
        with self.lock:
            item = self.data.get("days", {}).get(day, {})
            self._ensure_item(item)
            sent = float(item.get("audio_sent_seconds", 0.0))
        if sent + audio_seconds > limit_seconds:
            raise RuntimeError(
                f"Дневной стоп Audio API: отправлено {sent:.1f} из "
                f"{limit_seconds:.0f} с"
            )

    def add(
        self,
        model: str,
        audio_seconds: float,
        usage: dict,
        input_price: float | None = None,
        output_price: float | None = None,
    ) -> dict:
        day = datetime.now(timezone.utc).date().isoformat()
        with self.lock:
            item = self.data.setdefault("days", {}).setdefault(
                day,
                {
                    "requests": 0,
                    "audio_sent_seconds": 0.0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "usage_seconds": 0,
                    "models": {},
                },
            )
            self._ensure_item(item)
            item["requests"] += 1
            item["audio_sent_seconds"] = round(
                float(item["audio_sent_seconds"]) + audio_seconds, 3
            )
            for field in ("input_tokens", "output_tokens", "total_tokens"):
                value = usage.get(field)
                if isinstance(value, int):
                    item[field] += value
            if input_price is not None and output_price is not None:
                price_delta = (
                    int(usage.get("input_tokens", 0))
                    * (input_price - self.input_price)
                    + int(usage.get("output_tokens", 0))
                    * (output_price - self.output_price)
                ) / 1_000_000
                item["billing_adjustment_usd"] = round(
                    float(item.get("billing_adjustment_usd", 0.0)) + price_delta,
                    9,
                )
            seconds = usage.get("seconds")
            if isinstance(seconds, (int, float)):
                item["usage_seconds"] += seconds
            models = item.setdefault("models", {})
            models[model] = int(models.get(model, 0)) + 1
            item["last_usage"] = usage
            item["updated_at"] = utc_now()
            summary = self._summary(item)
            save_json(USAGE_FILE, self.data)
        return summary


class LocalGate:
    def __init__(self, model_name: str) -> None:
        from faster_whisper import WhisperModel

        self.model = WhisperModel(
            cached_whisper_model(model_name), device="cpu", compute_type="int8"
        )

    def transcribe(
        self, pcm: np.ndarray, language: str
    ) -> tuple[str, float, str, float]:
        audio = pcm.astype(np.float32) / 32768.0
        segments, info = self.model.transcribe(
            audio,
            language=None,
            beam_size=1,
            best_of=1,
            condition_on_previous_text=False,
            vad_filter=False,
            temperature=0.0,
        )
        texts: list[str] = []
        no_speech: list[float] = []
        for segment in segments:
            if segment.text.strip():
                texts.append(segment.text.strip())
            no_speech.append(float(segment.no_speech_prob))
        probability = min(no_speech, default=1.0)
        detected_language = str(getattr(info, "language", language) or language)
        language_probability = float(getattr(info, "language_probability", 0.0))
        return (
            " ".join(texts).strip(),
            probability,
            detected_language,
            language_probability,
        )


class CloudTranscriber:
    def __init__(
        self,
        key: str,
        config: dict,
        usage_log: UsageLog,
        ui: TerminalUI,
    ) -> None:
        self.key = key
        self.model = str(config["api_model"])
        self.language = str(config["language"])
        self.prompt = str(config.get("transcription_prompt", "")).strip()
        self.timeout = float(config.get("api_timeout_seconds", 60))
        self.daily_audio_limit = float(config["daily_audio_seconds_limit"])
        self.usage_log = usage_log
        self.ui = ui

    def transcribe(self, pcm: np.ndarray) -> tuple[str, dict, dict]:
        duration = len(pcm) / TARGET_RATE
        self.usage_log.check_audio_budget(duration, self.daily_audio_limit)
        started_at = time.monotonic()
        accepted_at = [0.0]
        accepted = threading.Event()
        finished = threading.Event()

        def show_progress() -> None:
            while not finished.wait(0.1):
                now = time.monotonic()
                if accepted.is_set():
                    self.ui.set_api_status(
                        "✓ ФАЙЛ ПРИНЯТ; ЖДУ ОТВЕТ",
                        now - accepted_at[0],
                        complete=True,
                    )
                else:
                    self.ui.set_api_status("↑ ОТПРАВКА АУДИО", now - started_at)

        self.ui.set_api_status("↑ ОТПРАВКА АУДИО", 0.0)
        progress_thread = threading.Thread(target=show_progress, daemon=True)
        progress_thread.start()
        data = {
            "model": self.model,
            "language": self.language,
            "response_format": "text",
            "stream": "true",
        }
        if self.prompt:
            data["prompt"] = self.prompt
        try:
            response = requests.post(
                TRANSCRIPTIONS_URL,
                headers={"Authorization": f"Bearer {self.key}"},
                data=data,
                files={"file": ("phrase.wav", wav_bytes(pcm), "audio/wav")},
                stream=True,
                timeout=self.timeout,
            )
            accepted_at[0] = time.monotonic()
            accepted.set()
            self.ui.set_api_status("✓ ФАЙЛ ПРИНЯТ; ЖДУ ОТВЕТ", 0.0, complete=True)
            if not response.ok:
                raise RuntimeError(
                    f"Audio API HTTP {response.status_code}: {response.text}"
                )
            response.encoding = "utf-8"
            chunks: list[str] = []
            final_text = ""
            usage: dict = {}
            for raw_line in response.iter_lines(decode_unicode=True):
                if not raw_line or not raw_line.startswith("data:"):
                    continue
                payload = raw_line[5:].strip()
                if payload == "[DONE]":
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                event_type = event.get("type")
                if event_type == "transcript.text.delta":
                    delta = str(event.get("delta", ""))
                    chunks.append(delta)
                elif event_type == "transcript.text.done":
                    final_text = str(event.get("text", "")).strip()
                    usage = event.get("usage") or {}
        except Exception:
            self.ui.set_api_status("× ОШИБКА", time.monotonic() - started_at)
            raise
        finally:
            finished.set()
            progress_thread.join(timeout=1.0)
        wait_seconds = max(0.0, time.monotonic() - accepted_at[0])
        self.ui.set_api_status("✓ ОТВЕТ; ОЖИДАНИЕ", wait_seconds, complete=True)
        if not final_text:
            final_text = "".join(chunks).strip()
        summary = self.usage_log.add(self.model, duration, usage)
        return final_text, usage, summary


class CloudDiarizer:
    def __init__(self, key: str, config: dict, ui: TerminalUI) -> None:
        self.key = key
        self.model = str(config.get("speaker_diarize_model", "gpt-4o-transcribe-diarize"))
        self.language = str(config["language"])
        self.timeout = float(config.get("api_timeout_seconds", 60))
        self.ui = ui

    def transcribe(
        self,
        pcm: np.ndarray,
        references: list[tuple[str, Path]],
    ) -> tuple[list[dict], dict]:
        started_at = time.monotonic()
        self.ui.set_api_status("↑ ПРОВЕРЯЮ ГОВОРЯЩИХ", 0.0)
        data: list[tuple[str, str]] = [
            ("model", self.model),
            ("language", self.language),
            ("response_format", "diarized_json"),
            ("chunking_strategy", "auto"),
        ]
        for name, path in references[:4]:
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            data.append(("known_speaker_names[]", name))
            data.append(
                ("known_speaker_references[]", f"data:audio/wav;base64,{encoded}")
            )
        response = requests.post(
            TRANSCRIPTIONS_URL,
            headers={"Authorization": f"Bearer {self.key}"},
            data=data,
            files={"file": ("speakers.wav", wav_bytes(pcm), "audio/wav")},
            timeout=self.timeout,
        )
        elapsed = time.monotonic() - started_at
        if not response.ok:
            self.ui.set_api_status("× ОШИБКА ГОЛОСОВ", elapsed)
            raise RuntimeError(
                f"Diarize API HTTP {response.status_code}: {response.text}"
            )
        payload = response.json()
        self.ui.set_api_status("✓ ГОВОРЯЩИЕ ОПРЕДЕЛЕНЫ", elapsed, complete=True)
        segments = payload.get("segments") or []
        usage = payload.get("usage") or {}
        return [item for item in segments if isinstance(item, dict)], usage


class PhraseWorker:
    def __init__(
        self,
        key: str | None,
        config: dict,
        transcript_path: Path,
        stats: StatsLog,
        local_only: bool,
        ui: TerminalUI,
        speaker: QuickReplySpeaker,
    ) -> None:
        self.config = config
        self.transcript_path = transcript_path
        self.stats = stats
        self.local_only = local_only
        self.ui = ui
        self.speaker = speaker
        self.replies = QuickReplySelector()
        acknowledgements = config.get(
            "quick_acknowledgements", ["Хм.", "Угу.", "М-м."]
        )
        self.acknowledgements = [str(item) for item in acknowledgements]
        self.acknowledgement_index = 0
        self.dialog_open_seconds = float(config.get("dialog_open_seconds", 45.0))
        self.dialog_open_until = time.monotonic() + float(
            config.get("dialog_start_open_seconds", 15.0)
        )
        self.last_runtime_profile_id: str | None = None
        self.voice_profiles = (
            SpeakerProfiles(
                threshold=float(config.get("speaker_match_threshold", 0.38)),
                owner_name=str(config.get("speaker_owner_name", "Виктор")),
                cloud_names=list(
                    config.get("speaker_cloud_profiles", ["Виктор", "Жена", "Дочка"])
                ),
                device_names=list(
                    config.get("speaker_device_profiles", ["Мика", "Алиса", "Бони"])
                ),
                guest_name=str(config.get("speaker_guest_name", "Кожаный мешок")),
                auto_update=bool(config.get("speaker_profile_auto_update", False)),
                bootstrap_owner_samples=int(
                    config.get("speaker_bootstrap_owner_samples", 5)
                ),
            )
            if bool(config.get("speaker_profiles_enabled", True))
            else None
        )
        self.owner_enrollment_phrase = str(
            config.get(
                "speaker_enrollment_phrase",
                "Сегодня робот запоминает мой голос.",
            )
        )
        self.owner_enrollment_timeout = float(
            config.get("speaker_enrollment_timeout_seconds", 20.0)
        )
        self.enrollment_max_attempts = int(
            config.get("speaker_enrollment_max_attempts", 2)
        )
        self.awaiting_owner_enrollment = False
        self.enrollment_name: str | None = None
        self.enrollment_attempts = 0
        self.owner_enrollment_deadline = 0.0
        if (
            self.voice_profiles is not None
            and not self.voice_profiles.has_owner_profile()
        ):
            self.awaiting_owner_enrollment = True
            self.enrollment_name = str(config.get("speaker_owner_name", "Виктор"))
            prompt = (
                "Нет голосового профиля Виктора. Произнеси: «"
                f"{self.owner_enrollment_phrase}»"
            )
            self.owner_enrollment_deadline = float("inf")
            self.ui.log(f"Сокол: {prompt}", new_phrase=True)
            self._write_phrase("Сокол", prompt)
            self.speaker.speak(prompt, self._arm_enrollment_timeout)
        self.queue: queue.Queue[tuple[np.ndarray, bool, bool] | None] = queue.Queue(
            maxsize=4
        )
        self.usage_log = UsageLog(
            float(config["price_per_million_input_tokens_usd"]),
            float(config["price_per_million_output_tokens_usd"]),
        )
        self.cloud = (
            None
            if local_only
            else CloudTranscriber(key or "", config, self.usage_log, ui)
        )
        self.diarizer = (
            CloudDiarizer(key or "", config, ui)
            if not local_only and bool(config.get("speaker_diarize_enabled", False))
            else None
        )
        if self.cloud is not None:
            self.ui.set_summary(self.cloud.usage_log.today_summary())
        self.local: LocalGate | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def submit(
        self,
        pcm: np.ndarray,
        playback_overlap: bool = False,
        barge_in: bool = False,
    ) -> None:
        try:
            self.queue.put_nowait((pcm, playback_overlap, barge_in))
        except queue.Full:
            self.stats.write("queue_full", duration_seconds=len(pcm) / TARGET_RATE)

    def close(self) -> None:
        self.queue.put(None)
        self.thread.join()

    def _write_phrase(self, speaker: str, text: str) -> None:
        with self.transcript_path.open("a", encoding="utf-8") as output:
            output.write(f"{speaker}: {text}\n\n")

    def _next_acknowledgement(self) -> str:
        if not self.acknowledgements:
            return "Хм."
        value = self.acknowledgements[
            self.acknowledgement_index % len(self.acknowledgements)
        ]
        self.acknowledgement_index += 1
        return value

    def _arm_enrollment_timeout(self) -> None:
        if not self.awaiting_owner_enrollment:
            return
        self.owner_enrollment_deadline = (
            time.monotonic() + self.owner_enrollment_timeout
        )
        self.stats.write(
            "owner_enrollment_window_open",
            timeout_seconds=self.owner_enrollment_timeout,
        )

    def request_voice_enrollment(self, name: str) -> bool:
        """Structured command entry point for the brain; never called from STT text."""
        if self.voice_profiles is None:
            return False
        enrollment_name = name.strip()
        if not enrollment_name:
            return False
        self.awaiting_owner_enrollment = True
        self.enrollment_name = enrollment_name
        self.enrollment_attempts = 0
        self.owner_enrollment_deadline = float("inf")
        prompt = (
            f"{enrollment_name}, произнеси: "
            f"«{self.owner_enrollment_phrase}»"
        )
        self.ui.log(f"Сокол: {prompt}", new_phrase=True)
        self._write_phrase("Сокол", prompt)
        self.speaker.speak(prompt, self._arm_enrollment_timeout)
        self.stats.write(
            "owner_enrollment_started",
            source="brain",
            speaker=enrollment_name,
        )
        return True

    def _transcribe_emergency(self, pcm: np.ndarray) -> tuple[str, float]:
        if self.local is None:
            self.stats.write("emergency_whisper_loading")
            self.local = LocalGate(str(self.config["local_model"]))
        text, no_speech, _language, _probability = self.local.transcribe(
            pcm, str(self.config["language"])
        )
        return text, no_speech

    def _run(self) -> None:
        while True:
            item = self.queue.get()
            if item is None:
                return
            pcm, playback_overlap, barge_in = item
            duration = len(pcm) / TARGET_RATE
            try:
                if playback_overlap and not barge_in and not self.awaiting_owner_enrollment:
                    self.stats.write(
                        "ignored_playback_echo",
                        duration_seconds=round(duration, 3),
                    )
                    continue
                if (
                    self.voice_profiles is not None
                    and self.awaiting_owner_enrollment
                ):
                    if time.monotonic() > self.owner_enrollment_deadline:
                        self.awaiting_owner_enrollment = False
                        self.enrollment_name = None
                        self.enrollment_attempts = 0
                        retry = "Время записи истекло. Повтори команду запоминания голоса."
                        self.ui.log(f"Сокол: {retry}", new_phrase=True)
                        self._write_phrase("Сокол", retry)
                        self.speaker.speak(retry)
                        self.stats.write("owner_enrollment_timeout")
                        continue
                    enrollment_name = self.enrollment_name or str(
                        self.config.get("speaker_owner_name", "Виктор")
                    )
                    self.enrollment_attempts += 1
                    enrolled = self.voice_profiles.enroll_named(
                        pcm, enrollment_name
                    )
                    if enrolled is None:
                        if self.enrollment_attempts >= self.enrollment_max_attempts:
                            self.awaiting_owner_enrollment = False
                            self.enrollment_name = None
                            self.enrollment_attempts = 0
                            failed = (
                                "Не получилось записать голос. "
                                "Повтори команду запоминания."
                            )
                            self.ui.log(f"Сокол: {failed}", new_phrase=True)
                            self._write_phrase("Сокол", failed)
                            self.speaker.speak(failed)
                            self.stats.write("owner_enrollment_failed")
                            continue
                        retry = "Фраза слишком короткая. Произнеси её полностью ещё раз."
                        self.owner_enrollment_deadline = float("inf")
                        self.ui.log(f"Сокол: {retry}", new_phrase=True)
                        self._write_phrase("Сокол", retry)
                        self.speaker.speak(
                            retry, self._arm_enrollment_timeout
                        )
                        self.stats.write(
                            "owner_enrollment_too_short",
                            duration_seconds=round(duration, 3),
                        )
                        continue
                    self.awaiting_owner_enrollment = False
                    self.owner_enrollment_deadline = 0.0
                    self.enrollment_name = None
                    self.enrollment_attempts = 0
                    reply = f"Голос {enrollment_name} сохранён локально. Принято."
                    self.ui.log(f"Сокол: {reply}", new_phrase=True)
                    self._write_phrase("Сокол", reply)
                    self.speaker.speak(reply)
                    self.stats.write(
                        "owner_enrollment_completed",
                        duration_seconds=round(duration, 3),
                    )
                    continue
                if self.local_only:
                    PREVIEW_DIR.mkdir(exist_ok=True)
                    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    preview_path = PREVIEW_DIR / f"phrase_{stamp}.wav"
                    preview_path.write_bytes(wav_bytes(pcm))
                    self.stats.write(
                        "local_preview",
                        duration_seconds=round(duration, 3),
                        audio_file=str(preview_path),
                    )
                    continue
                assert self.cloud is not None
                try:
                    final_text, usage, summary = self.cloud.transcribe(pcm)
                except Exception as cloud_error:
                    self.stats.write(
                        "cloud_stt_unavailable",
                        duration_seconds=round(duration, 3),
                        error=str(cloud_error),
                    )
                    draft, no_speech = self._transcribe_emergency(pcm)
                    command = emergency_local_command(
                        draft, time.monotonic() < self.dialog_open_until
                    )
                    if (
                        not draft
                        or no_speech > float(self.config["local_no_speech_max"])
                        or command is None
                    ):
                        self.ui.log(
                            "Сокол: облако недоступно; аварийная команда не распознана.",
                            new_phrase=True,
                        )
                        continue
                    self.ui.log(f"АВАРИЙНАЯ КОМАНДА [{command}]: {draft}", new_phrase=True)
                    self.stats.write(
                        "emergency_command",
                        command=command,
                        local_text=draft,
                        duration_seconds=round(duration, 3),
                    )
                    continue
                if final_text and not final_text.endswith((".", "!", "?")):
                    final_text += "."
                self.ui.set_summary(summary)
                if not final_text:
                    self.stats.write("cloud_stt_empty", duration_seconds=round(duration, 3))
                    continue
                dialog_open = time.monotonic() < self.dialog_open_until
                addressed = contains_wake_word(final_text)
                if addressed:
                    self.dialog_open_until = time.monotonic() + self.dialog_open_seconds
                elif not dialog_open:
                    self.stats.write(
                        "ignored_outside_dialog",
                        text=final_text,
                        duration_seconds=round(duration, 3),
                    )
                    continue
                else:
                    self.dialog_open_until = time.monotonic() + self.dialog_open_seconds

                speaker_match = SpeakerMatch("disabled", "Говорящий", 0.0, False)
                if self.voice_profiles is not None:
                    speaker_match = self.voice_profiles.identify(
                        pcm, allow_owner_bootstrap=addressed
                    )
                reply = self._next_acknowledgement()
                self.ui.log(f"Сокол → {speaker_match.name}: {reply}", new_phrase=True)
                self.speaker.speak(reply)
                self.ui.log(f"— {final_text}")
                self._write_phrase(speaker_match.name, final_text)
                self.stats.write(
                    "speech_transcript",
                    duration_seconds=round(duration, 3),
                    text=final_text,
                    speaker=speaker_match.name,
                    profile_id=speaker_match.profile_id,
                    usage=usage,
                )
            except Exception as exc:
                self.stats.write("processing_error", error=str(exc))


def main() -> int:
    configure_console()
    ui: TerminalUI | None = None
    worker: PhraseWorker | None = None
    speaker: QuickReplySpeaker | None = None
    try:
        local_only = "--local-only" in sys.argv[1:]
        config = load_json(CONFIG_FILE, {})
        required = (
            "api_model",
            "local_model",
            "language",
            "calibration_seconds",
            "pre_roll_ms",
            "end_silence_ms",
            "min_speech_ms",
            "max_phrase_seconds",
            "local_no_speech_max",
            "daily_audio_seconds_limit",
            "price_per_million_input_tokens_usd",
            "price_per_million_output_tokens_usd",
        )
        missing = [name for name in required if name not in config]
        if missing:
            raise RuntimeError(f"В {CONFIG_FILE.name} отсутствуют: {', '.join(missing)}")
        key = None if local_only else load_key()
        device = config.get("input_device")
        if device in (None, ""):
            device = sd.default.device[0]
        info = sd.query_devices(device, "input")
        source_rate = int(round(float(info["default_samplerate"])))
        source_frames = max(1, round(source_rate * FRAME_MS / 1000))
        audio_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=500)
        stop = threading.Event()

        OUTPUT_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = OUTPUT_DIR / f"voice_stt_{stamp}.txt"
        stats_path = OUTPUT_DIR / f"voice_stats_{stamp}.jsonl"
        stats = StatsLog(stats_path)
        playback_active = threading.Event()
        ui = TerminalUI(config, str(info["name"]), local_only)
        ui.start()
        speaker = QuickReplySpeaker(config, playback_active, stats)
        worker = PhraseWorker(
            key, config, output_path, stats, local_only, ui, speaker
        )

        def callback(indata: np.ndarray, _frames: int, _time: object, status: object) -> None:
            if status:
                stats.write("audio_status", status=str(status))
            try:
                audio_queue.put_nowait(indata.copy())
            except queue.Full:
                stats.write("audio_queue_full")

        def wait_for_enter() -> None:
            input()
            stop.set()

        threading.Thread(target=wait_for_enter, daemon=True).start()
        vad = SileroSegmenter(SILERO_MODEL_FILE, config)
        barge_in_rms_min = float(config.get("barge_in_rms_min", 500.0))
        barge_in_frames = max(1, int(config.get("barge_in_frames", 3)))
        playback_barge_in_enabled = bool(
            config.get("playback_barge_in_enabled", False)
        )
        barge_in_votes = 0
        pending_barge_in = False
        meter_tick = 0

        with sd.InputStream(
            samplerate=source_rate,
            blocksize=source_frames,
            device=device,
            channels=1,
            dtype="int16",
            callback=callback,
        ):
            while not stop.is_set():
                try:
                    source_block = audio_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                playback_overlap = playback_active.is_set()
                block = resample_mono(source_block, source_rate)
                if block.size < FRAME_SAMPLES:
                    block = np.pad(block, (0, FRAME_SAMPLES - block.size))
                elif block.size > FRAME_SAMPLES:
                    block = block[:FRAME_SAMPLES]
                block = block.astype("<i2", copy=False)
                rms = float(np.sqrt(np.mean(block.astype(np.float64) ** 2)))

                if playback_overlap and playback_barge_in_enabled:
                    if rms >= barge_in_rms_min:
                        barge_in_votes += 1
                        if barge_in_votes >= barge_in_frames:
                            speaker.interrupt()
                            pending_barge_in = True
                            barge_in_votes = 0
                    else:
                        barge_in_votes = 0
                else:
                    barge_in_votes = 0
                    if not vad.speaking:
                        pending_barge_in = False

                result, speech_probability = vad.feed(
                    block,
                    playback_overlap,
                    pending_barge_in,
                )
                if vad.speaking:
                    pending_barge_in = False
                meter_tick += 1
                if meter_tick % 3 == 0:
                    ui.set_status(
                        vad.speaking,
                        speech_probability,
                        float(config.get("silero_threshold", 0.50)),
                    )
                if result is not None:
                    pcm, phrase_has_playback, phrase_barged_in = result
                    worker.submit(
                        high_pass(pcm),
                        phrase_has_playback,
                        phrase_barged_in,
                    )
                    ui.set_status(False)

        result = vad.flush()
        if result is not None:
            pcm, phrase_has_playback, phrase_barged_in = result
            worker.submit(
                high_pass(pcm),
                phrase_has_playback,
                phrase_barged_in,
            )
        ui.set_status(False)
        worker.close()
        worker = None
        speaker.close()
        speaker = None
        return 0
    except KeyboardInterrupt:
        if ui is not None:
            ui.log("Остановлено.", new_phrase=True)
        else:
            print("\nОстановлено.")
        return 130
    except Exception as exc:
        if ui is not None:
            ui.log(f"× ОШИБКА | {exc}", new_phrase=True)
        else:
            print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    finally:
        if worker is not None:
            worker.close()
        if speaker is not None:
            speaker.close()
        if ui is not None:
            ui.stop()


if __name__ == "__main__":
    raise SystemExit(main())
