"""Persistent text-output OpenAI Realtime conversation for Sokol-9.

The robot already owns VAD, echo suppression and TTS. Realtime therefore
receives complete microphone turns and returns text, while Home Assistant's
local Piper voice remains responsible for playback.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import socket
import sys
from array import array
from typing import Any

import websockets


LOGGER = logging.getLogger("gopro_assist.realtime")
REALTIME_URL = "wss://api.openai.com/v1/realtime"


def pcm16_16k_to_24k(audio: bytes) -> bytes:
    """Resample mono signed PCM16 from 16 kHz to Realtime's 24 kHz."""
    source = array("h")
    source.frombytes(audio[: len(audio) - len(audio) % 2])
    if sys.byteorder != "little":
        source.byteswap()
    if len(source) < 2:
        return audio

    output_length = (len(source) * 3) // 2
    output = array("h")
    for output_index in range(output_length):
        # 24 kHz sample n lies at source position n * 2/3.
        numerator = output_index * 2
        source_index, fraction_thirds = divmod(numerator, 3)
        if source_index >= len(source) - 1:
            value = source[-1]
        elif fraction_thirds == 0:
            value = source[source_index]
        else:
            left = source[source_index]
            right = source[source_index + 1]
            value = round((left * (3 - fraction_thirds) + right * fraction_thirds) / 3)
        output.append(max(-32768, min(32767, value)))
    if sys.byteorder != "little":
        output.byteswap()
    return output.tobytes()


def is_finish_phrase(text: str) -> bool:
    normalized = re.sub(
        r"[^а-яa-z0-9]+", " ", text.casefold().replace("ё", "е")
    ).strip()
    return normalized in {
        "закончили",
        "все закончили",
        "давай закончим",
        "закончим разговор",
        "стоп разговор",
        "хватит разговаривать",
        "можешь отдыхать",
    }


class RealtimeConversation:
    """One serialized Realtime WebSocket conversation."""

    def __init__(
        self,
        api_key: str,
        model: str,
        transcription_model: str,
        instructions: str,
        max_output_tokens: int,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.transcription_model = transcription_model
        self.instructions = instructions
        self.max_output_tokens = max(32, min(4096, max_output_tokens))
        self.ws: Any | None = None
        self.lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self.ws is not None and self.ws.state.name == "OPEN"

    async def connect(self) -> None:
        if self.connected:
            return
        await self.close()
        self.ws = await websockets.connect(
            f"{REALTIME_URL}?model={self.model}",
            additional_headers={
                "Authorization": f"Bearer {self.api_key}",
                "OpenAI-Safety-Identifier": "sokol9-home",
            },
            open_timeout=12,
            proxy=None,
            family=socket.AF_INET,
            close_timeout=3,
            ping_interval=20,
            ping_timeout=20,
            max_size=4 * 1024 * 1024,
        )
        created = await self._receive_event(timeout=12)
        if created.get("type") != "session.created":
            raise RuntimeError(f"Realtime did not create session: {created.get('type')}")
        await self._send({
            "type": "session.update",
            "session": {
                "type": "realtime",
                "model": self.model,
                "output_modalities": ["text"],
                "instructions": self.instructions,
                "max_output_tokens": self.max_output_tokens,
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "transcription": {
                            "model": self.transcription_model,
                            "language": "ru",
                        },
                        "turn_detection": None,
                    }
                },
            },
        })
        while True:
            event = await self._receive_event(timeout=12)
            if event.get("type") == "session.updated":
                break
        LOGGER.info("Realtime conversation connected model=%s", self.model)

    async def close(self) -> None:
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                LOGGER.debug("Realtime close failed", exc_info=True)
            self.ws = None

    async def ask_text(self, text: str) -> tuple[str, str]:
        async with self.lock:
            await self.connect()
            await self._send({
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            })
            await self._send({"type": "response.create"})
            response = await self._collect_response(require_transcript=False)
            return text, response

    async def ask_audio(self, pcm16_16k: bytes) -> tuple[str, str]:
        audio = pcm16_16k_to_24k(pcm16_16k)
        async with self.lock:
            await self.connect()
            for offset in range(0, len(audio), 24_000):
                await self._send({
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(audio[offset : offset + 24_000]).decode("ascii"),
                })
            await self._send({"type": "input_audio_buffer.commit"})
            await self._send({"type": "response.create"})
            result = await self._collect_response(require_transcript=True)
            assert isinstance(result, tuple)
            return result

    async def _send(self, event: dict[str, Any]) -> None:
        if self.ws is None:
            raise RuntimeError("Realtime socket is not connected")
        await self.ws.send(json.dumps(event, ensure_ascii=False))

    async def _receive_event(self, timeout: float) -> dict[str, Any]:
        if self.ws is None:
            raise RuntimeError("Realtime socket is not connected")
        raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
        event = json.loads(raw)
        if event.get("type") == "error":
            detail = event.get("error") or {}
            raise RuntimeError(
                f"Realtime {detail.get('code', 'error')}: {detail.get('message', detail)}"
            )
        return event

    async def _collect_response(
        self, require_transcript: bool
    ) -> tuple[str, str] | str:
        transcript = ""
        response_parts: list[str] = []
        response_done = False
        transcript_done = not require_transcript
        deadline = asyncio.get_running_loop().time() + 40
        while not (response_done and transcript_done):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("Realtime response timed out")
            event = await self._receive_event(timeout=remaining)
            event_type = event.get("type")
            if event_type == "response.output_text.delta":
                response_parts.append(str(event.get("delta", "")))
            elif event_type == "response.output_text.done" and not response_parts:
                response_parts.append(str(event.get("text", "")))
            elif event_type == "conversation.item.input_audio_transcription.completed":
                transcript = str(event.get("transcript", "")).strip()
                transcript_done = True
            elif event_type == "conversation.item.input_audio_transcription.failed":
                transcript_done = True
            elif event_type == "response.done":
                response_done = True
                response = event.get("response") or {}
                status = response.get("status")
                if status not in {None, "completed"}:
                    detail = response.get("status_details") or status
                    if not (status == "incomplete" and response_parts):
                        raise RuntimeError(f"Realtime response status={detail}")
                    LOGGER.warning("using partial Realtime response status=%s", detail)
                if not response_parts:
                    for item in response.get("output") or []:
                        for content in item.get("content") or []:
                            if content.get("type") in {"text", "output_text"}:
                                response_parts.append(str(content.get("text", "")))
        response_text = "".join(response_parts).strip()
        if require_transcript:
            return transcript, response_text
        return response_text
