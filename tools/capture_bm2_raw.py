#!/usr/bin/env python3
"""Save one raw 1-bit PDM diagnostic block from the ESP32-S3."""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import serial

from mic_level_console import find_port


BEGIN_RE = re.compile(
    r"PDM_RAW_BEGIN\s+BYTES=(?P<bytes>\d+)\s+ONES=(?P<ones>\d+)\s+TRANSITIONS=(?P<transitions>\d+)"
)
DATA_RE = re.compile(r"PDM_RAW\s+(?P<offset>[0-9A-Fa-f]+):(?P<data>[0-9A-Fa-f]+)")
END_RE = re.compile(r"PDM_RAW_END")


def main() -> int:
    parser = argparse.ArgumentParser(description="Сохранить сырой однобитный PDM BM2")
    parser.add_argument("port", nargs="?", help="COM-порт; без аргумента определяется автоматически")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    try:
        port = find_port(args.port)
        deadline = time.monotonic() + args.timeout
        expected = ones = transitions = 0
        chunks: dict[int, bytes] = {}
        receiving = False

        with serial.Serial(port, baudrate=args.baud, timeout=0.25) as connection:
            connection.reset_input_buffer()
            print(f"Жду сырой 1-битный PDM BM2 на {port}...")
            while time.monotonic() < deadline:
                line = connection.readline().decode("utf-8", errors="replace")
                begin = BEGIN_RE.search(line)
                if begin:
                    expected = int(begin.group("bytes"))
                    ones = int(begin.group("ones"))
                    transitions = int(begin.group("transitions"))
                    chunks.clear()
                    receiving = True
                    continue
                if not receiving:
                    continue
                data = DATA_RE.search(line)
                if data:
                    chunks[int(data.group("offset"), 16)] = bytes.fromhex(data.group("data"))
                    continue
                if END_RE.search(line):
                    raw = b"".join(chunks[offset] for offset in sorted(chunks))
                    if len(raw) != expected:
                        receiving = False
                        continue
                    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    output = Path(__file__).resolve().parents[1] / f"bm2_raw_pdm_{stamp}.bin"
                    output.write_bytes(raw)
                    bit_count = len(raw) * 8
                    print(f"Получено: {len(raw)} байт / {bit_count} бит")
                    print(f"Единиц: {ones} ({100.0 * ones / bit_count:.2f}%)")
                    print(f"Переходов 0/1: {transitions} ({100.0 * transitions / (bit_count - 1):.2f}%)")
                    print(f"BIN: {output}")
                    return 0

        raise RuntimeError(f"За {args.timeout:g} с не получен полный блок PDM_RAW")
    except (OSError, RuntimeError, ValueError, serial.SerialException) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
