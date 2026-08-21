#!/usr/bin/env python3
"""Reset the microphone test firmware and save its 10-second stereo capture."""

from __future__ import annotations

import argparse
import re
import sys
import time
import wave
from datetime import datetime
from pathlib import Path

import serial

from mic_level_console import find_port


BEGIN_RE = re.compile(
    r"MIC_WAV_BEGIN\s+BYTES=(?P<bytes>\d+)\s+RATE=(?P<rate>\d+)\s+"
    r"CHANNELS=(?P<channels>\d+)\s+BITS=(?P<bits>\d+)"
)
DATA_RE = re.compile(r"MIC_WAV\s+(?P<offset>[0-9A-Fa-f]+):(?P<data>[0-9A-Fa-f]+)")
END_RE = re.compile(r"MIC_WAV_END")
LEVEL_RE = re.compile(
    r"MIC_LEVEL\s+L=(?P<left>\d+)\s+R=(?P<right>\d+)\s+"
    r"LP=(?P<left_peak>\d+)\s+RP=(?P<right_peak>\d+)\s+N=(?P<count>\d+)"
)


def meter(value: int, width: int = 24) -> str:
    filled = min(width, max(0, round(value / 250.0 * width)))
    return "█" * filled + "░" * (width - filled)


def reset_target(connection: serial.Serial) -> None:
    connection.dtr = False
    connection.rts = True
    time.sleep(0.1)
    connection.rts = False


def main() -> int:
    parser = argparse.ArgumentParser(description="Записать 10 секунд с двух PDM-микрофонов в WAV")
    parser.add_argument("port", nargs="?", help="COM-порт; без аргумента определяется автоматически")
    parser.add_argument("--timeout", type=float, default=90.0, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    try:
        port = find_port(args.port)
        deadline = time.monotonic() + args.timeout
        expected = rate = channels = bits = 0
        chunks: dict[int, bytes] = {}
        receiving = False
        recording_started: float | None = None
        last_percent = -1

        with serial.Serial(port, baudrate=115200, timeout=0.25) as connection:
            connection.reset_input_buffer()
            print(f"Готовлю запись с BM1 (левый) и BM2 (правый) на {port}...", flush=True)
            reset_target(connection)

            while time.monotonic() < deadline:
                line = connection.readline().decode("utf-8", errors="replace")
                if "MIC_WAV_RECORDING" in line:
                    recording_started = time.monotonic()
                    continue
                level = LEVEL_RE.search(line)
                if level and recording_started is not None and not receiving:
                    elapsed = min(10.0, time.monotonic() - recording_started)
                    # Physical BM1 is PDM slot R; physical BM2 is PDM slot L.
                    bm1 = int(level.group("right"))
                    bm2 = int(level.group("left"))
                    print(
                        f"\rЗапись {elapsed:4.1f}/10.0 с  "
                        f"BM1 [{meter(bm1)}]  BM2 [{meter(bm2)}]",
                        end="",
                        flush=True,
                    )
                    continue
                begin = BEGIN_RE.search(line)
                if begin:
                    expected = int(begin.group("bytes"))
                    rate = int(begin.group("rate"))
                    channels = int(begin.group("channels"))
                    bits = int(begin.group("bits"))
                    chunks.clear()
                    receiving = True
                    print("\nЗапись остановлена. Сохранение WAV:   0%", end="", flush=True)
                    continue
                if not receiving:
                    continue
                data = DATA_RE.search(line)
                if data:
                    chunks[int(data.group("offset"), 16)] = bytes.fromhex(data.group("data"))
                    received = sum(len(chunk) for chunk in chunks.values())
                    percent = min(100, received * 100 // expected)
                    if percent >= last_percent + 2:
                        print(f"\rЗапись остановлена. Сохранение WAV: {percent:3d}%", end="", flush=True)
                        last_percent = percent
                    continue
                if END_RE.search(line):
                    pcm = b"".join(chunks[offset] for offset in sorted(chunks))
                    if len(pcm) != expected:
                        raise RuntimeError(f"Получено {len(pcm)} из {expected} байт")
                    if bits != 16 or channels not in (1, 2):
                        raise RuntimeError(f"Неподдерживаемый формат: {channels} каналов, {bits} бит")

                    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    output = Path(__file__).resolve().parents[1] / f"mic_record_{stamp}.wav"
                    if channels == 2:
                        # The board's PDM slots are opposite to the physical
                        # microphone positions: slot L is BM2, slot R is BM1.
                        pcm = b"".join(pcm[index + 2 : index + 4] + pcm[index : index + 2] for index in range(0, len(pcm), 4))
                    with wave.open(str(output), "wb") as stream:
                        stream.setnchannels(channels)
                        stream.setsampwidth(bits // 8)
                        stream.setframerate(rate)
                        stream.writeframes(pcm)
                    print(f"\rЗапись остановлена. Сохранение WAV: 100%")
                    print(f"Готово: {output}", flush=True)
                    return 0

        raise RuntimeError("Запись не получена; проверьте, что прошита mic-level-test.yaml")
    except (OSError, RuntimeError, ValueError, serial.SerialException) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
