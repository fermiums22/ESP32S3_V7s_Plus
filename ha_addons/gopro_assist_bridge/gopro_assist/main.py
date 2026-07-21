"""GoPro RTSP microphone to Home Assistant Assist bridge.

By default commands are submitted to the configured Home Assistant Conversation
pipeline.  The optional direct-agent mode uses the OpenAI Responses API and may
read entities, call Home Assistant services, and inspect one robot camera frame.
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
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
import websockets

from .agent import (
    AgentConfig,
    BudgetExceeded,
    HomeAssistantTools,
    OpenAIAgent,
    SemanticEventJournal,
    WORLD_DB_PATH,
)
from .speaker_id import SpeakerProfiles
from .speaker_diarization import CloudTranscriber
from .visual_places import VisualPlaceStore
from .memory import MEMORY_DB_PATH, MEMORY_ROOT, WorldMemory


LOGGER = logging.getLogger("gopro_assist")
OPTIONS_PATH = Path("/data/options.json")
HA_HTTP = "http://supervisor/core/api"
HA_WS = "ws://supervisor/core/websocket"
SAMPLE_RATE = 16_000
SAMPLE_WIDTH = 2
CHUNK_MS = 20
CHUNK_BYTES = SAMPLE_RATE * SAMPLE_WIDTH * CHUNK_MS // 1000
SPEAKER_PROFILES_PATH = Path("/data/speaker_profiles.json")
VISUAL_PLACES_PATH = MEMORY_ROOT / "vision" / "places"
DIALOG_HISTORY_PATH = Path("/data/dialog_history.json")
BUILTIN_PROMPT_PATH = Path(__file__).with_name("SOKOL9_SYSTEM_PROMPT.md")
SOL_ADDENDUM_PATH = Path(__file__).with_name("prompts") / "SOL_ROUTING_ADDENDUM.md"
LUNA_PROMPT_PATH = Path(__file__).with_name("prompts") / "LUNA_VISION_PROMPT.md"


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
    agent_enabled: bool
    agent: AgentConfig
    conversation_timeout_seconds: int
    cloud_stt_enabled: bool
    cloud_stt_model: str
    cloud_stt_prompt: str
    cloud_stt_daily_audio_seconds_limit: float
    cloud_stt_timeout_seconds: float
    cloud_stt_input_usd_per_million: float
    cloud_stt_output_usd_per_million: float
    dialog_history_turns: int
    dialog_message_max_chars: int
    robot_event_entities: tuple[str, ...]
    follow_distance_entities: tuple[str, str, str]
    follow_left_wheel_entity: str
    follow_right_wheel_entity: str
    follow_run_entity: str
    follow_safety_entities: tuple[str, ...]
    place_odometry_entities: tuple[str, ...]
    vad_start_rms: int
    vad_end_rms: int
    vad_noise_multiplier: float
    vad_start_rms_max: int
    vad_silence_ms: int
    vad_pre_roll_ms: int
    vad_min_speech_ms: int
    vad_max_segment_ms: int
    debug: bool

    @classmethod
    def load(cls) -> "Config":
        raw = json.loads(OPTIONS_PATH.read_text(encoding="utf-8"))
        system_prompt = str(raw.get("agent_system_prompt", "@builtin")).strip()
        if system_prompt in {"", "@builtin"}:
            system_prompt = BUILTIN_PROMPT_PATH.read_text(encoding="utf-8").strip()
            system_prompt += "\n\n" + SOL_ADDENDUM_PATH.read_text(encoding="utf-8").strip()
        phrases = tuple(
            phrase.strip().casefold()
            for phrase in str(raw.get("wake_phrases", "сокол девять,сокол")).split(",")
            if phrase.strip()
        )
        if not phrases:
            raise ValueError("wake_phrases must contain at least one phrase")
        distance_entities = tuple(
            item.strip()
            for item in str(raw.get(
                "follow_distance_entities",
                "sensor.v7s_plus_front_distance_left,"
                "sensor.v7s_plus_front_distance_center,"
                "sensor.v7s_plus_front_distance_right",
            )).split(",")
            if item.strip()
        )
        if len(distance_entities) != 3:
            raise ValueError("follow_distance_entities must contain left,center,right")
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
            agent_enabled=bool(raw.get("agent_enabled", False)),
            agent=AgentConfig(
                api_key=str(raw.get("openai_api_key", "")).strip(),
                model=str(raw.get("agent_model", "gpt-5.6-sol")).strip(),
                system_prompt=system_prompt,
                max_output_tokens=max(64, int(raw.get("agent_max_output_tokens", 2048))),
                history_turns=max(0, int(raw.get("agent_history_turns", 20))),
                max_tool_rounds=max(0, int(raw.get("agent_max_tool_rounds", 4))),
                reasoning_effort=str(raw.get("agent_reasoning_effort", "none")).strip(),
                daily_limit_usd=max(0.0, float(raw.get("agent_daily_limit_usd", 1.10))),
                monthly_limit_usd=max(0.0, float(raw.get("agent_monthly_limit_usd", 34.0))),
                request_reserve_usd=max(0.0, float(raw.get("agent_request_reserve_usd", 0.05))),
                input_usd_per_million=max(0.0, float(raw.get("agent_input_usd_per_million", 5.0))),
                cached_input_usd_per_million=max(0.0, float(raw.get("agent_cached_input_usd_per_million", 0.5))),
                output_usd_per_million=max(0.0, float(raw.get("agent_output_usd_per_million", 30.0))),
                vision_model=str(raw.get("vision_model", "gpt-5.6-luna")).strip(),
                vision_prompt=LUNA_PROMPT_PATH.read_text(encoding="utf-8").strip(),
                vision_max_output_tokens=max(64, int(raw.get("vision_max_output_tokens", 300))),
                vision_input_usd_per_million=max(0.0, float(raw.get("vision_input_usd_per_million", 1.0))),
                vision_cached_input_usd_per_million=max(0.0, float(raw.get("vision_cached_input_usd_per_million", 0.1))),
                vision_output_usd_per_million=max(0.0, float(raw.get("vision_output_usd_per_million", 6.0))),
                camera_entity=str(raw.get("camera_entity", "camera.robot_eyes")).strip(),
                home_map_entity=str(raw.get("home_map_entity", "")).strip(),
                telemetry_entities=tuple(
                    item.strip()
                    for item in str(raw.get("camera_telemetry_entities", "")).split(",")
                    if item.strip()
                ),
            ),
            conversation_timeout_seconds=max(
                15, min(300, int(raw.get("conversation_timeout_seconds", 45)))
            ),
            cloud_stt_enabled=bool(raw.get("cloud_stt_enabled", True)),
            cloud_stt_model=str(
                raw.get("cloud_stt_model", "gpt-4o-mini-transcribe")
            ).strip(),
            cloud_stt_prompt=str(raw.get("cloud_stt_prompt", "")).strip(),
            cloud_stt_daily_audio_seconds_limit=max(
                0.0, float(raw.get("cloud_stt_daily_audio_seconds_limit", 600))
            ),
            cloud_stt_timeout_seconds=max(
                5.0, float(raw.get("cloud_stt_timeout_seconds", 60))
            ),
            cloud_stt_input_usd_per_million=max(
                0.0, float(raw.get("cloud_stt_input_usd_per_million", 1.25))
            ),
            cloud_stt_output_usd_per_million=max(
                0.0, float(raw.get("cloud_stt_output_usd_per_million", 5.0))
            ),
            dialog_history_turns=max(
                1, min(100, int(raw.get("dialog_history_turns", 20)))
            ),
            dialog_message_max_chars=max(
                255, min(20000, int(raw.get("dialog_message_max_chars", 6000)))
            ),
            robot_event_entities=tuple(
                item.strip()
                for item in str(raw.get("robot_event_entities", "")).split(",")
                if item.strip()
            ),
            follow_distance_entities=distance_entities,  # type: ignore[arg-type]
            follow_left_wheel_entity=str(raw.get(
                "follow_left_wheel_entity", "number.v7s_plus_left_wheel_target"
            )).strip(),
            follow_right_wheel_entity=str(raw.get(
                "follow_right_wheel_entity", "number.v7s_plus_right_wheel_target"
            )).strip(),
            follow_run_entity=str(raw.get(
                "follow_run_entity", "switch.v7s_plus_robot_run"
            )).strip(),
            follow_safety_entities=tuple(
                item.strip()
                for item in str(raw.get(
                    "follow_safety_entities",
                    "binary_sensor.v7s_plus_bumper_hit,"
                    "binary_sensor.v7s_plus_left_motor_fault,"
                    "binary_sensor.v7s_plus_right_motor_fault,"
                    "binary_sensor.v7s_plus_stm32_emergency_stop,"
                    "binary_sensor.v7s_plus_robot_docked",
                )).split(",")
                if item.strip()
            ),
            place_odometry_entities=tuple(
                item.strip()
                for item in str(raw.get(
                    "place_odometry_entities",
                    "sensor.v7s_plus_left_wheel_position,"
                    "sensor.v7s_plus_right_wheel_position,"
                    "sensor.v7s_plus_caster_odometry",
                )).split(",")
                if item.strip()
            ),
            vad_start_rms=start,
            vad_end_rms=end,
            vad_noise_multiplier=float(raw.get("vad_noise_multiplier", 1.35)),
            vad_start_rms_max=max(start, int(raw.get("vad_start_rms_max", 480))),
            vad_silence_ms=max(CHUNK_MS, int(raw.get("vad_silence_ms", 650))),
            vad_pre_roll_ms=max(0, int(raw.get("vad_pre_roll_ms", 450))),
            vad_min_speech_ms=max(CHUNK_MS, int(raw.get("vad_min_speech_ms", 360))),
            vad_max_segment_ms=max(1000, int(raw.get("vad_max_segment_ms", 25000))),
            debug=bool(raw.get("debug", False)),
        )


def pcm_rms(chunk: bytes) -> int:
    samples = array("h")
    samples.frombytes(chunk)
    if not samples:
        return 0
    return math.isqrt(sum(sample * sample for sample in samples) // len(samples))


def high_pass_pcm16(pcm: bytes, cutoff_hz: float = 90.0) -> bytes:
    """Remove GoPro rumble/DC with the same cheap filter as the desktop STT."""
    source = array("h")
    source.frombytes(pcm)
    if len(source) < 2:
        return pcm
    rc = 1.0 / (2.0 * math.pi * cutoff_hz)
    dt = 1.0 / SAMPLE_RATE
    alpha = rc / (rc + dt)
    output = array("h", [0])
    previous_input = float(source[0])
    previous_output = 0.0
    for sample in source[1:]:
        value = alpha * (previous_output + float(sample) - previous_input)
        output.append(max(-32768, min(32767, round(value))))
        previous_input = float(sample)
        previous_output = value
    return output.tobytes()


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
        return max(
            self.config.vad_start_rms,
            min(adaptive, self.config.vad_start_rms_max),
        )

    @property
    def end_threshold(self) -> int:
        adaptive = int(self.noise_floor * 1.15)
        return min(self.start_threshold, max(self.config.vad_end_rms, adaptive))

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

        trim_chunks = max(0, self.silence_ms // CHUNK_MS - 7)
        captured = self.speech[:-trim_chunks] if trim_chunks else self.speech
        audio = high_pass_pcm16(b"".join(captured))
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


class DialogHistory:
    """Persistent UI history; one item represents one user/assistant turn."""

    def __init__(self, path: Path, max_turns: int, max_chars: int) -> None:
        self.path = path
        self.max_turns = max_turns
        self.max_chars = max_chars
        self.turns = self._load()

    def _load(self) -> list[dict[str, Any]]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)][-self.max_turns :]

    def _clip(self, text: str) -> str:
        text = str(text).strip()
        if len(text) <= self.max_chars:
            return text
        return text[: self.max_chars - 1].rstrip() + "…"

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.turns, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def start_turn(self, user: str, speaker: str = "") -> None:
        self.turns.append(
            {
                "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
                "user": self._clip(user),
                "assistant": "",
                "speaker": self._clip(speaker),
            }
        )
        self.turns = self.turns[-self.max_turns :]
        self._save()

    def finish_turn(self, assistant: str) -> None:
        if not self.turns:
            return
        self.turns[-1]["assistant"] = self._clip(assistant)
        self._save()

    def attributes(self) -> dict[str, Any]:
        return {
            "turns": list(self.turns),
            "turn_count": len(self.turns),
            "max_turns": self.max_turns,
            "updated_at": int(time.time()),
        }


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

    async def call_service(
        self, domain: str, service: str, entity_id: str | list[str]
    ) -> None:
        assert self.session is not None
        payload: dict[str, Any] = {"entity_id": entity_id}
        async with self.session.post(
            f"{HA_HTTP}/services/{domain}/{service}", json=payload
        ) as response:
            if response.status >= 300:
                body = await response.text()
                raise RuntimeError(
                    f"{domain}.{service} failed status={response.status}: {body[:500]}"
                )

    async def set_number(self, entity_id: str, value: int) -> None:
        assert self.session is not None
        async with self.session.post(
            f"{HA_HTTP}/services/number/set_value",
            json={"entity_id": entity_id, "value": value},
        ) as response:
            if response.status >= 300:
                body = await response.text()
                raise RuntimeError(
                    f"number.set_value failed status={response.status}: {body[:500]}"
                )

    async def camera_image(self, entity_id: str) -> bytes:
        assert self.session is not None
        async with self.session.get(f"{HA_HTTP}/camera_proxy/{entity_id}") as response:
            response.raise_for_status()
            image = await response.read()
        if not image or len(image) > 8 * 1024 * 1024:
            raise RuntimeError("invalid camera image size")
        return image

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


def is_finish_conversation(text: str) -> bool:
    normalized = re.sub(
        r"[^а-яa-z0-9]+", " ", text.casefold().replace("ё", "е")
    ).strip()
    return normalized in {
        "все",
        "закончили",
        "хватит",
        "спасибо все",
        "до связи",
        "конец разговора",
    }


def local_robot_command(text: str) -> str | None:
    normalized = re.sub(r"[^а-яa-z0-9]+", " ", text.casefold().replace("ё", "е")).strip()
    if re.search(
        r"\b(езжай|поезжай|едь|иди|вернись|возвращайся)\s+(домой|на базу)\b",
        normalized,
    ) or normalized in {"домой", "на базу"}:
        return "home"
    if re.search(r"\b(езжай|поезжай|следуй|иди)\s+за\s+мной\b", normalized):
        return "follow"
    if normalized in {"стой", "стоп", "остановись", "хватит", "не езди за мной"}:
        return "stop"
    return None


def local_personality_response(text: str) -> str | None:
    """Answer identity questions even when the cloud agent is disabled."""
    normalized = re.sub(r"[^а-яa-z0-9]+", " ", text.casefold().replace("ё", "е")).strip()
    if normalized in {
        "как тебя зовут",
        "кто ты",
        "ты кто",
        "как твое имя",
        "назови себя",
    }:
        return (
            "Я Сокол-девять. Слышу тебя, Виктор, и готов общаться."
        )
    if normalized in {"ты меня слышишь", "слышишь меня", "ты слышишь"}:
        return "Да, Виктор, слышу тебя. Я Сокол-девять."
    return None


def local_context_response(text: str) -> str | None:
    normalized = re.sub(
        r"[^а-яa-z0-9]+", " ", text.casefold().replace("ё", "е")
    ).strip()
    now = datetime.now(ZoneInfo("Europe/Minsk"))
    if normalized in {
        "который час", "сколько времени", "сколько сейчас времени", "какое время",
    }:
        return f"Сейчас {now:%H:%M}."
    if normalized in {"какой сегодня день недели", "какой день недели", "а день недели"}:
        days = (
            "понедельник", "вторник", "среда", "четверг",
            "пятница", "суббота", "воскресенье",
        )
        return f"Сегодня {days[now.weekday()]}."
    return None


def needs_agent_tools(text: str) -> bool:
    normalized = re.sub(
        r"[^а-яa-z0-9]+", " ", text.casefold().replace("ё", "е")
    ).strip()
    return bool(re.search(
        r"\b(видишь|посмотри|камера|на карте|найди|где лежит|"
        r"включи|выключи|открой|закрой|состояние|датчик|температур)\b",
        normalized,
    ))


def brief_voice_response(text: str, max_words: int = 18) -> str:
    """Keep spoken turns conversational instead of reading long paragraphs."""
    cleaned = re.sub(r"[*_`#>]", "", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    words = cleaned.split()
    if len(words) <= max_words:
        return cleaned
    return " ".join(words[:max_words]).rstrip(" ,;:-.…") + "…"


def speaker_enrollment_name(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", text.casefold().replace("ё", "е")).strip(" ,.:;!?")
    match = re.fullmatch(
        r"запомни мой голос(?: как| я| это)? ([а-яa-z0-9 -]{2,24})", normalized
    )
    return match.group(1).strip().title() if match else None


def place_enrollment_label(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", text.casefold().replace("ё", "е")).strip(" ,.:;!?")
    match = re.fullmatch(r"запомни (?:это )?место ([а-яa-z0-9 -]{2,32})", normalized)
    return match.group(1).strip().title() if match else None


def is_location_query(text: str) -> bool:
    normalized = re.sub(r"[^а-яa-z]+", " ", text.casefold().replace("ё", "е")).strip()
    return normalized in {
        "где мы", "где ты", "в какой мы комнате", "определи место",
        "определи где мы", "определи свое местоположение",
    }


def follow_wheel_targets(
    left_cm: float | None, center_cm: float | None, right_cm: float | None
) -> tuple[int, int]:
    distances = [left_cm, center_cm, right_cm]
    valid = [(zone, value) for zone, value in enumerate(distances)
             if value is not None and math.isfinite(value) and 5.0 <= value <= 150.0]
    if not valid:
        return 0, 0
    zone, distance = min(valid, key=lambda item: item[1])
    if center_cm is not None and math.isfinite(center_cm) and center_cm <= distance + 10.0:
        zone, distance = 1, center_cm
    if distance <= 55.0:
        return 0, 0
    speed = min(22, max(4, round((distance - 55.0) * 0.45)))
    if zone == 0:
        return round(speed * 0.45), speed
    if zone == 2:
        return speed, round(speed * 0.45)
    return speed, speed


class Bridge:
    def __init__(self, config: Config, ha: HomeAssistantClient) -> None:
        self.config = config
        self.ha = ha
        self.vad = AdaptiveVad(config)
        self.processing_task: asyncio.Task[None] | None = None
        self.suppress_until = 0.0
        self.speaker_active = False
        self.follow_enabled = False
        self.follow_last_targets: tuple[int, int] | None = None
        self.stop_event = asyncio.Event()
        self.last_metrics = 0.0
        self.acknowledgement_index = 0
        self.events = SemanticEventJournal(path=WORLD_DB_PATH)
        self.memory = WorldMemory(database=MEMORY_DB_PATH)
        self.speaker_profiles = SpeakerProfiles(SPEAKER_PROFILES_PATH)
        self.visual_places = VisualPlaceStore(VISUAL_PLACES_PATH)
        self.conversation_until = 0.0
        self.dialog_history = DialogHistory(
            DIALOG_HISTORY_PATH,
            config.dialog_history_turns,
            config.dialog_message_max_chars,
        )
        self.agent = (
            OpenAIAgent(
                config.agent,
                ha.session,
                HomeAssistantTools(ha.session, HA_HTTP),
                ha.set_sensor,
                self.events,
                self.memory,
            )
            if config.agent_enabled and ha.session is not None
            else None
        )
        self.cloud_transcriber = (
            CloudTranscriber(
                api_key=config.agent.api_key,
                session=ha.session,
                model=config.cloud_stt_model,
                prompt=config.cloud_stt_prompt,
                daily_audio_seconds_limit=config.cloud_stt_daily_audio_seconds_limit,
                timeout_seconds=config.cloud_stt_timeout_seconds,
                input_usd_per_million=config.cloud_stt_input_usd_per_million,
                output_usd_per_million=config.cloud_stt_output_usd_per_million,
            )
            if config.cloud_stt_enabled and config.agent.api_key and ha.session is not None
            else None
        )

    @property
    def conversation_active(self) -> bool:
        return time.monotonic() < self.conversation_until

    def extend_conversation(self) -> None:
        self.conversation_until = (
            time.monotonic() + self.config.conversation_timeout_seconds
        )

    async def finish_conversation(self) -> None:
        self.conversation_until = 0.0

    async def deliver_response(self, response: str) -> None:
        if not response:
            response = "Команда выполнена без текстового ответа."
        response = str(response).strip()
        self.dialog_history.finish_turn(response)
        await self.ha.set_sensor(
            "sensor.gopro_assist_response",
            response,
            {
                "full_text": response,
                "updated_at": int(time.time()),
                "dialog_active": self.conversation_active,
            },
        )
        await self.publish_dialog_history()
        await self.ha.set_sensor("sensor.gopro_assist_status", "speaking")
        await self.ha.speak(response)
        self.suppress_until = time.monotonic() + max(
            2.0, min(120.0, len(response) / 11.0 + 2.0)
        )
        LOGGER.info("response sent to %s: %s", self.config.media_player, response)

    async def publish_dialog_history(self) -> None:
        count = len(self.dialog_history.turns)
        await self.ha.set_sensor(
            "sensor.gopro_assist_dialog",
            f"{count} из {self.dialog_history.max_turns}",
            self.dialog_history.attributes(),
        )

    async def publish_stt_usage(self) -> None:
        if self.cloud_transcriber is None:
            return
        summary = self.cloud_transcriber.today_summary()
        await asyncio.gather(
            self.ha.set_sensor(
                "sensor.gopro_stt_requests_today",
                str(summary["requests"]),
                {"model": self.config.cloud_stt_model},
            ),
            self.ha.set_sensor(
                "sensor.gopro_stt_audio_seconds_today",
                f'{summary["audio_sent_seconds"]:.1f}',
                {"limit_seconds": summary["daily_audio_seconds_limit"]},
            ),
            self.ha.set_sensor(
                "sensor.gopro_stt_cost_today",
                f'{summary["estimated_cost_usd"]:.6f}',
                {
                    "unit_of_measurement": "USD",
                    "input_tokens": summary["input_tokens"],
                    "output_tokens": summary["output_tokens"],
                },
            ),
        )

    async def transcribe_precisely(self, audio: bytes, local_draft: str) -> str:
        if self.cloud_transcriber is None:
            return local_draft
        try:
            transcript = await self.cloud_transcriber.transcribe(audio)
            await self.publish_stt_usage()
            return transcript or local_draft
        except Exception as err:
            LOGGER.warning("cloud STT failed; using local draft: %s", err)
            return local_draft

    async def acknowledge_long_request(self) -> None:
        phrases = ("Слышу.", "Понял, думаю.", "Секунду.")
        phrase = phrases[self.acknowledgement_index % len(phrases)]
        self.acknowledgement_index += 1
        await self.ha.set_sensor(
            "sensor.gopro_assist_response",
            phrase,
            {"interim": True, "updated_at": int(time.time())},
        )
        await self.ha.speak(phrase)
        self.suppress_until = time.monotonic() + 1.5

    async def set_wheels(self, left: int, right: int) -> None:
        targets = (left, right)
        if targets == self.follow_last_targets:
            return
        await asyncio.gather(
            self.ha.set_number(self.config.follow_left_wheel_entity, left),
            self.ha.set_number(self.config.follow_right_wheel_entity, right),
        )
        self.follow_last_targets = targets

    async def stop_following(self, disable_drive: bool = True) -> None:
        self.follow_enabled = False
        try:
            await self.set_wheels(0, 0)
        finally:
            if disable_drive:
                await self.ha.call_service(
                    "switch", "turn_off", self.config.follow_run_entity
                )
        await self.ha.set_sensor("sensor.sokol_9_follow_state", "off")

    async def monitor_following(self) -> None:
        lost_cycles = 0
        while not self.stop_event.is_set():
            if not self.follow_enabled:
                await asyncio.sleep(0.2)
                continue
            safety_states = await asyncio.gather(*(
                self.ha.get_state(entity) for entity in self.config.follow_safety_entities
            ))
            blocked = next((entity for entity, state in zip(
                self.config.follow_safety_entities, safety_states
            ) if state != "off"), None)
            if blocked:
                await self.stop_following()
                await self.ha.set_sensor(
                    "sensor.sokol_9_follow_state", "blocked", {"entity_id": blocked}
                )
                continue
            raw_distances = await asyncio.gather(*(
                self.ha.get_state(entity) for entity in self.config.follow_distance_entities
            ))
            distances: list[float | None] = []
            for value in raw_distances:
                try:
                    distances.append(float(value) if value is not None else None)
                except ValueError:
                    distances.append(None)
            targets = follow_wheel_targets(*distances)
            has_target = any(
                value is not None and math.isfinite(value) and 5.0 <= value <= 150.0
                for value in distances
            )
            if targets == (0, 0) and not has_target:
                lost_cycles += 1
            else:
                lost_cycles = 0
            await self.set_wheels(*targets)
            await self.ha.set_sensor(
                "sensor.sokol_9_follow_state",
                "target_lost" if lost_cycles else "following",
                {"distance_cm": distances, "wheel_rpm": list(targets)},
            )
            if lost_cycles >= 6:
                await self.stop_following()
                continue
            await asyncio.sleep(0.5)

    async def handle_local_robot_command(self, command: str) -> str | None:
        action = local_robot_command(command)
        if action == "home":
            self.follow_enabled = False
            try:
                await self.set_wheels(0, 0)
            except Exception as err:
                LOGGER.warning("wheel zero before docking failed: %s", err)
            await self.ha.call_service("script", "turn_on", "script.sokol_9_go_home")
            return "Возвращаюсь на базу."
        if action == "follow":
            self.follow_enabled = False
            await self.set_wheels(0, 0)
            await self.ha.call_service("switch", "turn_on", self.config.follow_run_entity)
            self.follow_enabled = True
            await self.ha.set_sensor("sensor.sokol_9_follow_state", "following")
            return "Еду за тобой. Оставайся передо мной."
        if action == "stop":
            await self.stop_following()
            return "Остановился."
        return None

    async def enroll_visual_place(self, label: str) -> str:
        image = await self.ha.camera_image(self.config.agent.camera_entity)
        values = await asyncio.gather(*(
            self.ha.get_state(entity) for entity in self.config.place_odometry_entities
        ))
        odometry = dict(zip(self.config.place_odometry_entities, values))
        await self.visual_places.enroll(label, image, odometry=odometry)
        count = self.visual_places.count(label)
        await self.ha.set_sensor(
            "sensor.sokol_9_location", label,
            {"learning": True, "reference_frames": count, "odometry": odometry},
        )
        return f"Запомнил место {label}. Кадр {count}."

    async def identify_visual_place(self) -> str:
        if not self.visual_places.entries:
            return "Каталог мест пока пуст. Сначала скажи: запомни место и название."
        image = await self.ha.camera_image(self.config.agent.camera_entity)
        matches = await self.visual_places.match(image)
        best = matches[0]
        score = float(best["similarity"])
        margin = score - float(matches[1]["similarity"]) if len(matches) > 1 else score
        confident = score >= 0.80 and margin >= 0.025
        label = str(best["label"]) if confident else "unknown"
        await self.ha.set_sensor(
            "sensor.sokol_9_location", label,
            {"confidence": round(score, 3), "margin": round(margin, 3),
             "candidates": matches},
        )
        if confident:
            return f"Похоже, мы в месте {best['label']}."
        candidates = ", ".join(str(item["label"]) for item in matches[:2])
        return f"Не уверен. Ближайшие варианты: {candidates}."

    async def monitor_robot_events(self) -> None:
        watched = set(self.config.robot_event_entities)
        if not watched:
            return
        retry_seconds = 2
        while not self.stop_event.is_set():
            try:
                ws = await self.ha.ws_connect()
                message_id = self.ha.next_id()
                await ws.send(json.dumps({
                    "id": message_id,
                    "type": "subscribe_events",
                    "event_type": "state_changed",
                }))
                reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if not reply.get("success"):
                    raise RuntimeError(f"cannot subscribe state events: {reply.get('error')}")
                retry_seconds = 2
                try:
                    while not self.stop_event.is_set():
                        message = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
                        event = message.get("event") or {}
                        data = event.get("data") or {}
                        entity_id = str(data.get("entity_id", ""))
                        if entity_id not in watched:
                            continue
                        old = data.get("old_state") or {}
                        new = data.get("new_state") or {}
                        self.events.record(
                            entity_id,
                            str(old.get("state", "unknown")),
                            str(new.get("state", "unknown")),
                            str(new.get("last_changed") or event.get("time_fired") or ""),
                        )
                finally:
                    await ws.close()
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception as err:
                LOGGER.warning("robot event monitor failed: %s", err)
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=retry_seconds)
                except asyncio.TimeoutError:
                    pass
                retry_seconds = min(30, retry_seconds * 2)

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

    async def _handle_segment_legacy(self, audio: bytes) -> None:
        try:
            await self.ha.set_sensor(
                "sensor.gopro_assist_status", "recognizing", {"audio_bytes": len(audio)}
            )
            if self.conversation_active:
                assert self.realtime is not None
                transcript = ""
                response = ""
                if time.monotonic() >= self.realtime_retry_after:
                    try:
                        transcript, response = await self.realtime.ask_audio(audio)
                    except Exception as err:
                        LOGGER.warning(
                            "Realtime continuation failed; using local fallback: %s", err
                        )
                        self.realtime_retry_after = time.monotonic() + 30
                        await self.realtime.close()
                if not response:
                    transcript = transcript or await self.transcribe_wake_turn(audio)
                    if not transcript:
                        LOGGER.info("ignored Realtime turn without a transcript")
                        return
                    if self.agent is not None:
                        response = await self.agent.ask(transcript)
                    else:
                        response = await self.ha.run_intent(transcript)
                if not transcript:
                    LOGGER.info("ignored Realtime turn without a transcript")
                    return
                shown_transcript = transcript or "голосовая реплика"
                await self.ha.set_sensor(
                    "sensor.gopro_assist_transcript",
                    shown_transcript,
                    {
                        "accepted": True,
                        "continued": True,
                        "updated_at": int(time.time()),
                    },
                )
                self.identify_speaker_in_background(audio)
                LOGGER.info("Realtime continuation transcript=%s", shown_transcript)
                if transcript and is_finish_phrase(transcript):
                    await self.finish_conversation()
                    response = "Хорошо, закончили. Позови меня, когда понадоблюсь."
                else:
                    local_response = local_context_response(transcript)
                    if (
                        local_response is None
                        and needs_agent_tools(transcript)
                        and self.agent is not None
                    ):
                        local_response = await self.agent.ask(transcript)
                    if local_response is None:
                        local_response = await self.handle_local_robot_command(transcript)
                    if local_response is not None:
                        response = local_response
                    self.extend_conversation()
                await self.deliver_response(response)
                return

            transcript = await self.transcribe_wake_turn(audio)
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
            if self.realtime is not None:
                self.extend_conversation()
            if command:
                await self.ha.set_sensor("sensor.gopro_assist_status", "thinking")
                enrollment_name = speaker_enrollment_name(command)
                place_label = place_enrollment_label(command)
                location_query = is_location_query(command)
                speaker_match = (
                    None
                    if enrollment_name or (self.diarizer is not None and self.diarizer.ready)
                    else self.speaker_profiles.identify(audio)
                )
                if enrollment_name:
                    try:
                        samples = self.speaker_profiles.enroll(enrollment_name, audio)
                        await self.ha.set_sensor(
                            "sensor.sokol_9_speaker", enrollment_name,
                            {"enrollment_samples": samples},
                        )
                        response = (
                            f"Голос {enrollment_name} записан. "
                            f"Образец {samples} из трёх."
                        )
                    except ValueError:
                        response = "Не хватило чистого голоса. Повтори фразу ближе ко мне."
                else:
                    self.identify_speaker_in_background(audio)
                    speaker_name = speaker_match[0] if speaker_match else "unknown"
                    speaker_distance = speaker_match[1] if speaker_match else None
                    await self.ha.set_sensor(
                        "sensor.sokol_9_speaker", speaker_name,
                        {"distance": speaker_distance},
                    )
                    if place_label:
                        response = await self.enroll_visual_place(place_label)
                    elif location_query:
                        response = await self.identify_visual_place()
                    else:
                        response = local_personality_response(command)
                        if response is None:
                            response = local_context_response(command)
                        if response is None:
                            response = await self.handle_local_robot_command(command)
                if enrollment_name:
                    LOGGER.info("local speaker enrollment handled without cloud agent")
                elif response is not None:
                    LOGGER.info("local robot command handled without cloud agent")
                elif self.agent is not None and needs_agent_tools(command):
                    await self.acknowledge_long_request()
                    response = await self.agent.ask(command)
                elif self.realtime is not None:
                    try:
                        await self.acknowledge_long_request()
                        response = (await self.realtime.ask_text(command))[1]
                    except Exception as err:
                        LOGGER.warning(
                            "Realtime first turn failed; using Responses fallback: %s", err
                        )
                        self.realtime_retry_after = time.monotonic() + 30
                        await self.realtime.close()
                        if self.agent is not None:
                            response = await self.agent.ask(command)
                        else:
                            response = await self.ha.run_intent(command)
                elif self.agent is not None:
                    try:
                        await self.acknowledge_long_request()
                        prompt = (
                            f"[Говорит: {speaker_match[0]}] {command}"
                            if speaker_match else command
                        )
                        response = await self.agent.ask(prompt)
                    except BudgetExceeded:
                        response = "Лимит расходов достигнут. Облачный агент отключён."
                else:
                    await self.acknowledge_long_request()
                    response = await self.ha.run_intent(command)
            else:
                response = "Слушаю. Можешь говорить дальше без слова Сокол."
            await self.deliver_response(response)
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

    async def handle_segment(self, audio: bytes) -> None:
        """Run the proven local-gate/cloud-STT flow on one GoPro phrase."""
        history_started = False
        try:
            await self.ha.set_sensor(
                "sensor.gopro_assist_status",
                "local_stt",
                {"audio_bytes": len(audio), "source": "gopro_rtsp"},
            )
            local_draft = (await self.ha.run_stt(audio)).strip()
            if not local_draft:
                LOGGER.info("local STT gate rejected an empty phrase")
                return

            draft_addressed, _ = strip_wake_phrase(
                local_draft, self.config.wake_phrases
            )
            continued = self.conversation_active
            if not draft_addressed and not continued:
                LOGGER.info("local STT gate ignored outside-dialog text: %s", local_draft)
                return

            # Match the desktop prototype: acknowledge immediately after the
            # cheap local gate, then spend Audio API time only on accepted speech.
            await self.acknowledge_long_request()
            await self.ha.set_sensor(
                "sensor.gopro_assist_status",
                "cloud_stt" if self.cloud_transcriber is not None else "recognized",
            )
            transcript = (
                await self.transcribe_precisely(audio, local_draft)
            ).strip()
            if not transcript:
                LOGGER.info("precise STT returned no text")
                return

            final_addressed, command = strip_wake_phrase(
                transcript, self.config.wake_phrases
            )
            if continued:
                # A wake word is optional while the dialogue window is open.
                command = command if final_addressed else transcript
            elif not final_addressed:
                # The local gate heard the wake word, but the precise pass may
                # normalize or omit it. Keep the precise text instead of losing
                # an otherwise valid command.
                command = transcript

            if is_finish_conversation(command):
                self.dialog_history.start_turn(transcript)
                history_started = True
                await self.publish_dialog_history()
                await self.finish_conversation()
                await self.deliver_response(
                    "Хорошо, закончили. Позови меня, когда понадоблюсь."
                )
                return

            self.extend_conversation()
            await self.ha.set_sensor(
                "sensor.gopro_assist_transcript",
                transcript,
                {
                    "full_text": transcript,
                    "local_draft": local_draft,
                    "accepted": True,
                    "continued": continued and not final_addressed,
                    "command": command,
                    "updated_at": int(time.time()),
                },
            )
            LOGGER.info(
                "STT accepted transcript=%s local_draft=%s", transcript, local_draft
            )

            response: str | None
            speaker_match = None
            enrollment_name = speaker_enrollment_name(command)
            if enrollment_name:
                speaker_name = enrollment_name
            else:
                speaker_match = self.speaker_profiles.identify(audio)
                speaker_name = speaker_match[0] if speaker_match else "Говорящий"
            self.dialog_history.start_turn(transcript, speaker_name)
            history_started = True
            await self.publish_dialog_history()
            await self.ha.set_sensor(
                "sensor.sokol_9_speaker",
                speaker_name,
                {
                    "distance": speaker_match[1] if speaker_match else None,
                    "updated_at": int(time.time()),
                },
            )

            if not command:
                response = "Слушаю. Можешь говорить дальше без слова Сокол."
            else:
                await self.ha.set_sensor("sensor.gopro_assist_status", "thinking")
                place_label = place_enrollment_label(command)
                location_query = is_location_query(command)
                if enrollment_name:
                    try:
                        samples = self.speaker_profiles.enroll(enrollment_name, audio)
                        response = (
                            f"Голос {enrollment_name} записан. "
                            f"Образец {samples} из трёх."
                        )
                    except ValueError:
                        response = (
                            "Не хватило чистого голоса. Повтори фразу ближе ко мне."
                        )
                elif place_label:
                    response = await self.enroll_visual_place(place_label)
                elif location_query:
                    response = await self.identify_visual_place()
                else:
                    response = local_personality_response(command)
                    if response is None:
                        response = local_context_response(command)
                    if response is None:
                        response = await self.handle_local_robot_command(command)
                    if response is None and self.agent is not None:
                        prompt = (
                            f"[Говорит: {speaker_name}] {command}"
                            if speaker_match else command
                        )
                        try:
                            response = await self.agent.ask(prompt)
                        except BudgetExceeded:
                            response = (
                                "Лимит расходов достигнут. Облачный агент отключён."
                            )
                    if response is None:
                        response = await self.ha.run_intent(command)

            await self.deliver_response(response or "")
            self.extend_conversation()
        except Exception as err:
            LOGGER.exception("voice request failed")
            if history_started:
                self.dialog_history.finish_turn(f"Ошибка: {str(err)[:500]}")
                with contextlib.suppress(Exception):
                    await self.publish_dialog_history()
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
        event_task = asyncio.create_task(self.monitor_robot_events())
        follow_task = asyncio.create_task(self.monitor_following())
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
            with contextlib.suppress(Exception):
                await self.stop_following()
            with contextlib.suppress(Exception):
                await self.finish_conversation()
            media_task.cancel()
            event_task.cancel()
            follow_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await media_task
            with contextlib.suppress(asyncio.CancelledError):
                await event_task
            with contextlib.suppress(asyncio.CancelledError):
                await follow_task


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
        await bridge.publish_dialog_history()
        await bridge.publish_stt_usage()
        if bridge.agent is not None:
            await bridge.agent._publish_usage()
        await bridge.run()


def main() -> None:
    asyncio.run(async_main())
