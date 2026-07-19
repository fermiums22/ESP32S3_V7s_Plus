"""GoPro RTSP microphone to Home Assistant Assist bridge.

This process deliberately has no robot motor, OTA, firmware, Modbus, or raw
device endpoints.  Commands are submitted only to the configured Home
Assistant Conversation pipeline, so Home Assistant's exposed-entity policy is
the safety boundary.  The only service called directly is ``tts.speak``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import re
import signal
import time
from array import array
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
import websockets


LOGGER = logging.getLogger("gopro_assist")
OPTIONS_PATH = Path("/data/options.json")
HA_HTTP = "http://supervisor/core/api"
HA_WS = "ws://supervisor/core/websocket"
SAMPLE_RATE = 16_000
SAMPLE_WIDTH = 2
CHUNK_MS = 20
CHUNK_BYTES = SAMPLE_RATE * SAMPLE_WIDTH * CHUNK_MS // 1000


@dataclass(frozen=True)
class Config:
    rtsp_url: str
    pipeline_id: str
    pipeline_name: str
    stt_engine: str
    stt_language: str
    wake_phrases: tuple[str, ...]
    tts_entity: str
    media_player: str
    language: str
    vad_start_rms: int
    vad_end_rms: int
    vad_noise_multiplier: float
    vad_silence_ms: int
    vad_pre_roll_ms: int
    vad_min_speech_ms: int
    vad_max_segment_ms: int
    debug: bool

    @classmethod
    def load(cls) -> "Config":
        raw = json.loads(OPTIONS_PATH.read_text(encoding="utf-8"))
        phrases = tuple(
            phrase.strip().casefold()
            for phrase in str(raw.get("wake_phrases", "робот,фермиум")).split(",")
            if phrase.strip()
        )
        if not phrases:
            raise ValueError("wake_phrases must contain at least one phrase")
        start = int(raw.get("vad_start_rms", 700))
        end = int(raw.get("vad_end_rms", 400))
        if start <= 0 or end <= 0 or end >= start:
            raise ValueError("VAD thresholds must satisfy 0 < end < start")
        return cls(
            rtsp_url=str(raw["rtsp_url"]),
            pipeline_id=str(raw.get("pipeline_id", "")).strip(),
            pipeline_name=str(raw.get("pipeline_name", "GPT")).strip(),
            stt_engine=str(raw.get("stt_engine", "stt.faster_whisper")).strip(),
            stt_language=str(raw.get("stt_language", "ru")).strip(),
            wake_phrases=phrases,
            tts_entity=str(raw.get("tts_entity", "tts.piper")),
            media_player=str(raw["media_player"]),
            language=str(raw.get("language", "ru_RU")),
            vad_start_rms=start,
            vad_end_rms=end,
            vad_noise_multiplier=float(raw.get("vad_noise_multiplier", 3.0)),
            vad_silence_ms=max(CHUNK_MS, int(raw.get("vad_silence_ms", 700))),
            vad_pre_roll_ms=max(0, int(raw.get("vad_pre_roll_ms", 400))),
            vad_min_speech_ms=max(CHUNK_MS, int(raw.get("vad_min_speech_ms", 300))),
            vad_max_segment_ms=max(1000, int(raw.get("vad_max_segment_ms", 12000))),
            debug=bool(raw.get("debug", False)),
        )


def pcm_rms(chunk: bytes) -> int:
    samples = array("h")
    samples.frombytes(chunk)
    if not samples:
        return 0
    return math.isqrt(sum(sample * sample for sample in samples) // len(samples))


class AdaptiveVad:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.noise_floor = max(30.0, config.vad_end_rms / 3.0)
        self.pre_roll: deque[bytes] = deque(
            maxlen=max(1, config.vad_pre_roll_ms // CHUNK_MS)
        )
        self.speech: list[bytes] = []
        self.speech_ms = 0
        self.silence_ms = 0
        self.start_hits = 0

    @property
    def start_threshold(self) -> int:
        adaptive = int(self.noise_floor * self.config.vad_noise_multiplier)
        return max(self.config.vad_start_rms, adaptive)

    @property
    def end_threshold(self) -> int:
        adaptive = int(self.noise_floor * 1.6)
        return max(self.config.vad_end_rms, adaptive)

    def feed(self, chunk: bytes) -> tuple[bytes | None, int]:
        rms = pcm_rms(chunk)
        if not self.speech:
            if rms < self.start_threshold:
                self.noise_floor = 0.985 * self.noise_floor + 0.015 * rms
                self.start_hits = 0
                self.pre_roll.append(chunk)
                return None, rms

            self.start_hits += 1
            self.pre_roll.append(chunk)
            if self.start_hits < 3:
                return None, rms

            self.speech = list(self.pre_roll)
            self.speech_ms = len(self.speech) * CHUNK_MS
            self.silence_ms = 0
            self.pre_roll.clear()
            LOGGER.info(
                "speech started rms=%d start_threshold=%d noise=%.0f",
                rms,
                self.start_threshold,
                self.noise_floor,
            )
            return None, rms

        self.speech.append(chunk)
        self.speech_ms += CHUNK_MS
        if rms < self.end_threshold:
            self.silence_ms += CHUNK_MS
        else:
            self.silence_ms = 0

        complete = (
            self.speech_ms >= self.config.vad_max_segment_ms
            or (
                self.speech_ms >= self.config.vad_min_speech_ms
                and self.silence_ms >= self.config.vad_silence_ms
            )
        )
        if not complete:
            return None, rms

        audio = b"".join(self.speech)
        duration = self.speech_ms
        trailing = self.speech[-max(1, self.config.vad_pre_roll_ms // CHUNK_MS) :]
        self.pre_roll.extend(trailing)
        self.speech = []
        self.speech_ms = 0
        self.silence_ms = 0
        self.start_hits = 0
        LOGGER.info("speech ended duration_ms=%d bytes=%d", duration, len(audio))
        return audio, rms

    def reset(self) -> None:
        self.pre_roll.clear()
        self.speech.clear()
        self.speech_ms = 0
        self.silence_ms = 0
        self.start_hits = 0


class HomeAssistantClient:
    def __init__(self, config: Config, token: str) -> None:
        self.config = config
        self.token = token
        self.session: aiohttp.ClientSession | None = None
        self.pipeline_id = config.pipeline_id
        self._message_id = 0

    async def __aenter__(self) -> "HomeAssistantClient":
        timeout = aiohttp.ClientTimeout(total=30)
        self.session = aiohttp.ClientSession(
            timeout=timeout,
            headers={"Authorization": f"Bearer {self.token}"},
        )
        await self.resolve_pipeline()
        return self

    async def __aexit__(self, *_: object) -> None:
        if self.session is not None:
            await self.session.close()

    def next_id(self) -> int:
        self._message_id += 1
        return self._message_id

    async def ws_connect(self) -> Any:
        ws = await websockets.connect(
            HA_WS,
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
            max_size=4 * 1024 * 1024,
        )
        hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if hello.get("type") != "auth_required":
            await ws.close()
            raise RuntimeError(f"unexpected WebSocket greeting: {hello.get('type')}")
        await ws.send(json.dumps({"type": "auth", "access_token": self.token}))
        auth = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if auth.get("type") != "auth_ok":
            await ws.close()
            raise RuntimeError("Home Assistant WebSocket authentication failed")
        return ws

    async def resolve_pipeline(self) -> None:
        ws = await self.ws_connect()
        try:
            message_id = self.next_id()
            await ws.send(
                json.dumps(
                    {"id": message_id, "type": "assist_pipeline/pipeline/list"}
                )
            )
            response = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if not response.get("success"):
                raise RuntimeError(f"cannot list Assist pipelines: {response.get('error')}")
            result = response.get("result") or {}
            pipelines = result.get("pipelines") or []
            ids = {str(item.get("id")): item for item in pipelines}
            if self.pipeline_id and self.pipeline_id in ids:
                selected = ids[self.pipeline_id]
            else:
                selected = next(
                    (
                        item
                        for item in pipelines
                        if str(item.get("name", "")).casefold()
                        == self.config.pipeline_name.casefold()
                    ),
                    None,
                )
                if selected is None:
                    available = ", ".join(str(item.get("name")) for item in pipelines)
                    raise RuntimeError(
                        f"Assist pipeline '{self.config.pipeline_name}' not found; "
                        f"available: {available}"
                    )
                self.pipeline_id = str(selected["id"])
            LOGGER.info(
                "Assist pipeline selected name=%s id=%s language=%s",
                selected.get("name"),
                self.pipeline_id,
                selected.get("language"),
            )
            if (
                self.config.stt_engine
                and (
                    selected.get("stt_engine") != self.config.stt_engine
                    or selected.get("stt_language") != self.config.stt_language
                )
            ):
                update_id = self.next_id()
                update = {
                    key: selected.get(key)
                    for key in (
                        "conversation_engine",
                        "conversation_language",
                        "language",
                        "name",
                        "stt_engine",
                        "stt_language",
                        "tts_engine",
                        "tts_language",
                        "tts_voice",
                        "wake_word_entity",
                        "wake_word_id",
                        "prefer_local_intents",
                    )
                }
                update["stt_engine"] = self.config.stt_engine
                update["stt_language"] = self.config.stt_language
                await ws.send(
                    json.dumps(
                        {
                            "id": update_id,
                            "type": "assist_pipeline/pipeline/update",
                            "pipeline_id": self.pipeline_id,
                            **update,
                        },
                        ensure_ascii=False,
                    )
                )
                response = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                if not response.get("success"):
                    raise RuntimeError(
                        f"cannot update Assist STT engine: {response.get('error')}"
                    )
                LOGGER.info(
                    "Assist pipeline STT updated engine=%s language=%s",
                    self.config.stt_engine,
                    self.config.stt_language,
                )
        finally:
            await ws.close()

    async def set_sensor(
        self, entity_id: str, state: str, attributes: dict[str, Any] | None = None
    ) -> None:
        assert self.session is not None
        payload = {
            "state": str(state)[:255],
            "attributes": {
                "friendly_name": entity_id.removeprefix("sensor.")
                .replace("_", " ")
                .title(),
                **(attributes or {}),
            },
        }
        try:
            async with self.session.post(
                f"{HA_HTTP}/states/{entity_id}", json=payload
            ) as response:
                if response.status not in (200, 201):
                    body = await response.text()
                    LOGGER.warning(
                        "state update failed entity=%s status=%d body=%s",
                        entity_id,
                        response.status,
                        body[:300],
                    )
        except Exception as err:
            LOGGER.warning("state update failed entity=%s: %s", entity_id, err)

    async def get_state(self, entity_id: str) -> str | None:
        assert self.session is not None
        try:
            async with self.session.get(f"{HA_HTTP}/states/{entity_id}") as response:
                if response.status == 404:
                    return None
                response.raise_for_status()
                data = await response.json()
                return str(data.get("state", "")).casefold()
        except Exception as err:
            LOGGER.debug("state read failed entity=%s: %s", entity_id, err)
            return None

    async def run_stt(self, audio: bytes) -> str:
        result = await self._run_pipeline("stt", "stt", audio=audio)
        return str(result.get("transcript", "")).strip()

    async def run_intent(self, text: str) -> str:
        result = await self._run_pipeline("intent", "intent", text=text)
        return str(result.get("response", "")).strip()

    async def _run_pipeline(
        self,
        start_stage: str,
        end_stage: str,
        *,
        audio: bytes | None = None,
        text: str | None = None,
    ) -> dict[str, str]:
        ws = await self.ws_connect()
        message_id = self.next_id()
        command: dict[str, Any] = {
            "id": message_id,
            "type": "assist_pipeline/run",
            "pipeline": self.pipeline_id,
            "start_stage": start_stage,
            "end_stage": end_stage,
            "timeout": 90,
            "input": {"sample_rate": SAMPLE_RATE}
            if start_stage == "stt"
            else {"text": text or ""},
        }
        transcript = ""
        response_text = ""
        binary_handler: int | None = None
        stt_started = False
        audio_sent = False
        try:
            await ws.send(json.dumps(command, ensure_ascii=False))
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=100)
                if isinstance(raw, bytes):
                    continue
                message = json.loads(raw)
                if message.get("id") != message_id:
                    continue
                if message.get("type") == "result":
                    if not message.get("success"):
                        raise RuntimeError(
                            f"Assist pipeline rejected: {message.get('error')}"
                        )
                    continue
                if message.get("type") != "event":
                    continue
                event = message.get("event") or {}
                event_type = event.get("type")
                data = event.get("data") or {}
                if event_type == "run-start":
                    runner = data.get("runner_data") or {}
                    handler = runner.get("stt_binary_handler_id")
                    if handler is not None:
                        binary_handler = int(handler)
                elif event_type == "stt-start":
                    stt_started = True
                elif event_type == "stt-end":
                    transcript = str((data.get("stt_output") or {}).get("text", ""))
                elif event_type == "intent-end":
                    response_text = extract_speech(data.get("intent_output") or {})
                elif event_type == "error":
                    raise RuntimeError(
                        f"Assist {data.get('code', 'error')}: {data.get('message', '')}"
                    )
                elif event_type == "run-end":
                    return {"transcript": transcript, "response": response_text}

                if (
                    audio is not None
                    and binary_handler is not None
                    and stt_started
                    and not audio_sent
                ):
                    prefix = bytes((binary_handler,))
                    for offset in range(0, len(audio), 6400):
                        await ws.send(prefix + audio[offset : offset + 6400])
                    await ws.send(prefix)
                    audio_sent = True
        finally:
            await ws.close()

    async def speak(self, message: str) -> None:
        assert self.session is not None
        payload = {
            "entity_id": self.config.tts_entity,
            "media_player_entity_id": self.config.media_player,
            "message": message,
            "language": self.config.language,
            "cache": True,
        }
        async with self.session.post(
            f"{HA_HTTP}/services/tts/speak", json=payload
        ) as response:
            if response.status >= 300:
                body = await response.text()
                raise RuntimeError(
                    f"tts.speak failed status={response.status}: {body[:500]}"
                )


def extract_speech(intent_output: Any) -> str:
    if not isinstance(intent_output, dict):
        return ""
    response = intent_output.get("response")
    if isinstance(response, dict):
        speech = response.get("speech")
        if isinstance(speech, dict):
            plain = speech.get("plain")
            if isinstance(plain, dict) and plain.get("speech"):
                return str(plain["speech"])
    for key in ("speech", "text", "response"):
        value = intent_output.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def strip_wake_phrase(text: str, phrases: tuple[str, ...]) -> tuple[bool, str]:
    for phrase in sorted(phrases, key=len, reverse=True):
        pattern = re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)", re.IGNORECASE)
        if pattern.search(text):
            stripped = pattern.sub(" ", text, count=1)
            stripped = re.sub(r"^[\s,.:;!?—–-]+|[\s]+$", "", stripped)
            return True, stripped
    return False, text


class Bridge:
    def __init__(self, config: Config, ha: HomeAssistantClient) -> None:
        self.config = config
        self.ha = ha
        self.vad = AdaptiveVad(config)
        self.processing_task: asyncio.Task[None] | None = None
        self.suppress_until = 0.0
        self.speaker_active = False
        self.stop_event = asyncio.Event()
        self.last_metrics = 0.0

    async def monitor_media_player(self) -> None:
        """Block microphone VAD while any audio is emitted by the robot."""
        previous: str | None = None
        active_states = {"playing", "buffering", "paused", "announcing"}
        while not self.stop_event.is_set():
            state = await self.ha.get_state(self.config.media_player)
            active = state in active_states
            if active:
                self.suppress_until = max(self.suppress_until, time.monotonic() + 2.0)
            elif self.speaker_active:
                # Let room echo decay after the player reports idle.
                self.suppress_until = max(self.suppress_until, time.monotonic() + 2.0)
            self.speaker_active = active
            if state != previous:
                LOGGER.info(
                    "media player state=%s microphone_suppressed=%s",
                    state,
                    active or time.monotonic() < self.suppress_until,
                )
                previous = state
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass

    async def handle_segment(self, audio: bytes) -> None:
        try:
            await self.ha.set_sensor(
                "sensor.gopro_assist_status", "recognizing", {"audio_bytes": len(audio)}
            )
            transcript = await self.ha.run_stt(audio)
            await self.ha.set_sensor(
                "sensor.gopro_assist_transcript",
                transcript or "no speech",
                {"accepted": False, "updated_at": int(time.time())},
            )
            if not transcript:
                LOGGER.info("STT returned no text")
                return

            accepted, command = strip_wake_phrase(transcript, self.config.wake_phrases)
            if not accepted:
                LOGGER.info("ignored transcript without wake phrase: %s", transcript)
                return

            await self.ha.set_sensor(
                "sensor.gopro_assist_transcript",
                transcript,
                {
                    "accepted": True,
                    "command": command,
                    "updated_at": int(time.time()),
                },
            )
            LOGGER.info("wake phrase accepted command=%s", command or "<empty>")
            if command:
                await self.ha.set_sensor("sensor.gopro_assist_status", "thinking")
                response = await self.ha.run_intent(command)
            else:
                response = "Слушаю."
            if not response:
                response = "Команда выполнена без текстового ответа."
            await self.ha.set_sensor(
                "sensor.gopro_assist_response",
                response,
                {"updated_at": int(time.time())},
            )
            await self.ha.set_sensor("sensor.gopro_assist_status", "speaking")
            await self.ha.speak(response)
            self.suppress_until = time.monotonic() + max(
                4.0, min(30.0, len(response) / 11.0 + 2.0)
            )
            LOGGER.info("response sent to %s: %s", self.config.media_player, response)
        except Exception as err:
            LOGGER.exception("voice request failed")
            await self.ha.set_sensor(
                "sensor.gopro_assist_status",
                "error",
                {"error": str(err)[:500], "updated_at": int(time.time())},
            )
        finally:
            if time.monotonic() >= self.suppress_until:
                await self.ha.set_sensor("sensor.gopro_assist_status", "listening")

    async def read_ffmpeg_stderr(self, stream: asyncio.StreamReader) -> None:
        while line := await stream.readline():
            message = line.decode(errors="replace").strip()
            if message:
                LOGGER.warning("ffmpeg: %s", message)

    async def capture_once(self) -> None:
        await self.ha.set_sensor(
            "sensor.gopro_assist_status",
            "connecting",
            {"rtsp_url": self.config.rtsp_url, "pipeline": self.config.pipeline_name},
        )
        command = [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-rtsp_transport",
            "tcp",
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-i",
            self.config.rtsp_url,
            "-map",
            "0:a:0",
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "s16le",
            "pipe:1",
        ]
        LOGGER.info("starting ffmpeg RTSP audio capture")
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        stderr_task = asyncio.create_task(self.read_ffmpeg_stderr(process.stderr))
        await self.ha.set_sensor("sensor.gopro_assist_status", "listening")
        self.vad.reset()
        try:
            while not self.stop_event.is_set():
                chunk = await process.stdout.readexactly(CHUNK_BYTES)
                if self.speaker_active or time.monotonic() < self.suppress_until:
                    self.vad.reset()
                    continue
                segment, rms = self.vad.feed(chunk)
                now = time.monotonic()
                if now - self.last_metrics >= 5:
                    self.last_metrics = now
                    await self.ha.set_sensor(
                        "sensor.gopro_assist_status",
                        "processing"
                        if self.processing_task and not self.processing_task.done()
                        else "listening",
                        {
                            "rms": rms,
                            "noise_floor": round(self.vad.noise_floor),
                            "start_threshold": self.vad.start_threshold,
                            "end_threshold": self.vad.end_threshold,
                            "pipeline": self.config.pipeline_name,
                            "media_player": self.config.media_player,
                        },
                    )
                if segment is None:
                    continue
                if self.processing_task and not self.processing_task.done():
                    LOGGER.warning("dropping speech segment while previous request is active")
                    continue
                self.processing_task = asyncio.create_task(self.handle_segment(segment))
        except asyncio.IncompleteReadError as err:
            raise RuntimeError(
                f"ffmpeg audio stream ended after {len(err.partial)} trailing bytes"
            ) from err
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            await stderr_task

    async def run(self) -> None:
        media_task = asyncio.create_task(self.monitor_media_player())
        try:
            retry_seconds = 2
            while not self.stop_event.is_set():
                try:
                    await self.capture_once()
                    retry_seconds = 2
                except asyncio.CancelledError:
                    raise
                except Exception as err:
                    LOGGER.error("audio capture failed: %s", err)
                    await self.ha.set_sensor(
                        "sensor.gopro_assist_status",
                        "reconnecting",
                        {"error": str(err)[:500], "retry_seconds": retry_seconds},
                    )
                    try:
                        await asyncio.wait_for(
                            self.stop_event.wait(), timeout=retry_seconds
                        )
                    except asyncio.TimeoutError:
                        pass
                    retry_seconds = min(30, retry_seconds * 2)
        finally:
            media_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await media_task


async def async_main() -> None:
    config = Config.load()
    logging.basicConfig(
        level=logging.DEBUG if config.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RuntimeError("SUPERVISOR_TOKEN is not available")
    LOGGER.info(
        "starting GoPro Assist bridge pipeline=%s wake_phrases=%s output=%s",
        config.pipeline_name,
        ",".join(config.wake_phrases),
        config.media_player,
    )
    async with HomeAssistantClient(config, token) as ha:
        bridge = Bridge(config, ha)
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, bridge.stop_event.set)
        await ha.set_sensor("sensor.gopro_assist_transcript", "waiting")
        await ha.set_sensor("sensor.gopro_assist_response", "waiting")
        await bridge.run()


def main() -> None:
    asyncio.run(async_main())
