#!/usr/bin/env python3
"""Capture one diagnostic stereo PCM block while only BM2 drives PDM DATA."""

from __future__ import annotations

import argparse
import csv
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import serial

from mic_level_console import find_port


BEGIN_RE = re.compile(r"MIC_PCM_BEGIN\s+FRAMES=(?P<count>\d+)")
SAMPLE_RE = re.compile(r"(?P<index>\d+)=(?P<left>-?\d+),(?P<right>-?\d+)")
END_RE = re.compile(r"MIC_PCM_END")


def describe(name: str, values: list[int]) -> str:
    return (
        f"{name}: min={min(values)}, max={max(values)}, "
        f"mean={statistics.fmean(values):.2f}, unique={len(set(values))}"
    )


def capture(
    port: str, baud: int, timeout: float, block_count: int
) -> tuple[int, list[tuple[int, int, int, int]]]:
    deadline = time.monotonic() + timeout
    expected = 0
    samples: dict[int, tuple[int, int]] = {}
    captured: list[tuple[int, int, int, int]] = []
    receiving = False

    with serial.Serial(port, baudrate=baud, timeout=0.25) as connection:
        connection.reset_input_buffer()
        print(f"Жду сырой PCM-блок от BM2 на {port}...")
        while time.monotonic() < deadline:
            raw = connection.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace")
            begin = BEGIN_RE.search(line)
            if begin:
                expected = int(begin.group("count"))
                samples.clear()
                receiving = True
                continue
            if not receiving:
                continue
            for match in SAMPLE_RE.finditer(line):
                samples[int(match.group("index"))] = (
                    int(match.group("left")),
                    int(match.group("right")),
                )
            if END_RE.search(line):
                ordered = [(index, *samples[index]) for index in sorted(samples)]
                if len(ordered) == expected:
                    block = len(captured) // expected
                    captured.extend((block, *row) for row in ordered)
                    print(f"Получен блок {block + 1}/{block_count}")
                    if block + 1 == block_count:
                        return expected * block_count, captured
                receiving = False

    raise RuntimeError(f"За {timeout:g} с получено недостаточно блоков MIC_PCM")


def main() -> int:
    parser = argparse.ArgumentParser(description="Сохранить сырой PCM-захват BM2 в CSV")
    parser.add_argument("port", nargs="?", help="COM-порт; без аргумента определяется автоматически")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--blocks", type=int, default=1, help="число блоков по 256 кадров")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    try:
        port = find_port(args.port)
        if args.blocks < 1:
            raise RuntimeError("--blocks должен быть не меньше 1")
        expected, rows = capture(port, args.baud, args.timeout, args.blocks)
        if len(rows) != expected:
            raise RuntimeError(f"Блок повреждён: получено {len(rows)} из {expected} кадров")

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = Path(__file__).resolve().parents[1] / f"bm2_pcm_capture_{stamp}.csv"
        with output.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(("block", "frame", "pdm_left_bm2", "pdm_right_bm1"))
            writer.writerows(rows)

        left = [row[2] for row in rows]
        right = [row[3] for row in rows]
        equal = sum(a == b for a, b in zip(left, right))
        print(describe("PDM L/BM2", left))
        print(describe("PDM R/BM1", right))
        print(f"Одинаковых L/R кадров: {equal}/{len(rows)}")
        print(f"CSV: {output}")
        return 0
    except (OSError, RuntimeError, serial.SerialException) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
