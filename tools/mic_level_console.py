#!/usr/bin/env python3
"""Live two-channel microphone meter for the V7s Plus ESP32-S3."""

from __future__ import annotations

import argparse
import math
import re
import sys
import time

import serial
from serial.tools import list_ports


LEVEL_RE = re.compile(
    r"MIC_LEVEL\s+L=(?P<left>\d+)\s+R=(?P<right>\d+)\s+"
    r"LP=(?P<left_peak>\d+)\s+RP=(?P<right_peak>\d+)\s+N=(?P<count>\d+)"
)
ESPRESSIF_VID = 0x303A
BAR_WIDTH = 50


def find_port(requested: str | None) -> str:
    ports = list(list_ports.comports())
    if requested:
        requested = requested.upper()
        for port in ports:
            if port.device.upper() == requested:
                return port.device
        raise RuntimeError(f"Порт {requested} не найден")

    esp_ports = [port for port in ports if port.vid == ESPRESSIF_VID]
    if len(esp_ports) == 1:
        return esp_ports[0].device
    if not esp_ports:
        visible = ", ".join(port.device for port in ports) or "нет COM-портов"
        raise RuntimeError(f"ESP32-S3 не найден ({visible})")

    names = ", ".join(port.device for port in esp_ports)
    raise RuntimeError(f"Найдено несколько ESP32: {names}. Укажите порт аргументом")


def dbfs(value: int) -> float:
    if value <= 0:
        return -96.0
    return max(-96.0, 20.0 * math.log10(value / 32768.0))


def bar(value: int) -> str:
    level_db = dbfs(value)
    ratio = min(1.0, max(0.0, (level_db + 60.0) / 60.0))
    filled = round(ratio * BAR_WIDTH)
    return "█" * filled + "░" * (BAR_WIDTH - filled)


def channel_line(label: str, rms: int, peak: int) -> str:
    suffix = "  ЛИНИЯ ПОСТОЯННАЯ" if rms <= 1 and peak >= 1000 else ""
    return (
        f"{label:<7} [{bar(rms)}] {dbfs(rms):6.1f} dBFS  "
        f"raw peak {peak:5d}{suffix}"
    )


def draw(port: str, values: tuple[int, int, int, int, int] | None, status: str) -> None:
    lines = [
        f"V7s Plus — два PDM-микрофона — {port}",
        "Говорите или легко постучите рядом с каждым микрофоном. Выход: Ctrl+C",
        "",
    ]
    if values is None:
        lines.extend(["LEFT/BM1 [ожидание данных]", "RIGHT/BM2 [ожидание данных]"])
    else:
        left, right, left_peak, right_peak, count = values
        lines.extend(
            [
                channel_line("LEFT/BM1", right, right_peak),
                channel_line("RIGHT/BM2", left, left_peak),
                f"Отсчётов за окно: {count}",
            ]
        )
    lines.extend(["", status])
    sys.stdout.write("\x1b[H" + "\n".join(lines) + "\x1b[J")
    sys.stdout.flush()


def run(port: str, baud: int, duration: float | None) -> int:
    started = time.monotonic()
    last_data = started
    values: tuple[int, int, int, int, int] | None = None
    raw_values: tuple[int, int, int, int, int] | None = None
    smoothed_rms = [0.0, 0.0]

    with serial.Serial(port, baudrate=baud, timeout=0.25) as connection:
        connection.reset_input_buffer()
        sys.stdout.write("\x1b[2J\x1b[?25l")
        draw(port, None, "USB подключён, ждём метки MIC_LEVEL...")

        while duration is None or time.monotonic() - started < duration:
            raw = connection.readline()
            if raw:
                line = raw.decode("utf-8", errors="replace")
                match = LEVEL_RE.search(line)
                if match:
                    raw_values = tuple(int(match.group(name)) for name in (
                        "left", "right", "left_peak", "right_peak", "count"
                    ))
                    for channel, current in enumerate(raw_values[:2]):
                        previous = smoothed_rms[channel]
                        if previous == 0.0:
                            smoothed_rms[channel] = float(current)
                        else:
                            # Fast attack and slow release make speech readable
                            # without the 100 ms windows flickering wildly.
                            alpha = 0.65 if current > previous else 0.20
                            smoothed_rms[channel] += alpha * (current - previous)
                    values = (
                        round(smoothed_rms[0]),
                        round(smoothed_rms[1]),
                        raw_values[2],
                        raw_values[3],
                        raw_values[4],
                    )
                    last_data = time.monotonic()

            age = time.monotonic() - last_data
            if raw_values is not None and raw_values[4] == 0:
                status = "I2S запускается; первые отсчёты ещё не получены..."
            elif raw_values is not None and (
                raw_values[0] <= 16
                and raw_values[1] <= 16
                and abs(raw_values[0] - raw_values[1]) <= 1
                and raw_values[2] == raw_values[3]
            ):
                status = (
                    "ВНИМАНИЕ: оба PDM-слота дают одинаковый слабый фон. "
                    "При снятом R1 это базовый поток BM2 без акустической реакции."
                )
            elif raw_values is not None and (
                (raw_values[0] <= 1 and raw_values[2] >= 1000)
                or (raw_values[1] <= 1 and raw_values[3] >= 1000)
            ):
                status = (
                    "ВНИМАНИЕ: DATA застыла. Проверьте 5V, EN=GPIO12, "
                    "SCK=GPIO10 и DATA=GPIO11."
                )
            elif raw_values is not None and max(raw_values[:2]) >= 80 and (
                max(raw_values[:2]) >= 12 * max(1, min(raw_values[:2]))
            ):
                # Removing BM1's DATA link R1 experimentally removed the
                # signal from the ESP's right slot. The fitted microphones'
                # slot polarity is therefore opposite to the stale T3902
                # library metadata used by the schematic symbol.
                weak_channel = "RIGHT / BM2" if raw_values[0] < raw_values[1] else "LEFT / BM1"
                status = (
                    f"ВНИМАНИЕ: {weak_channel} почти без сигнала; "
                    "проверьте питание, землю и DATA этой ветви."
                )
            elif values is None and age > 3.0:
                status = "Нет данных. Нажмите RESET на ESP; EN микрофонов должен быть включён."
            elif values is not None and age > 2.0:
                status = "Поток уровней остановился; ожидаю восстановление..."
            else:
                status = "Уровни обновляются локально по USB — HA и Wi-Fi не используются."
            draw(port, values, status)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Два живых индикатора PDM-микрофонов ESP32-S3")
    parser.add_argument("port", nargs="?", help="COM-порт; без аргумента определяется автоматически")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--duration", type=float, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    try:
        return run(find_port(args.port), args.baud, args.duration)
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError, serial.SerialException) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1
    finally:
        sys.stdout.write("\x1b[?25h\n")
        sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
