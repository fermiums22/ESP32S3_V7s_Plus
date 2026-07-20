"""Persistent lightweight visual-place catalogue.

Reference JPEGs are kept for later embedding/geometry upgrades. A tiny structural
signature provides an immediate offline shortlist without a model or API tokens.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


GRID_WIDTH = 32
GRID_HEIGHT = 18


def _normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]


def signature_from_gray(gray: bytes) -> list[float]:
    if len(gray) != GRID_WIDTH * GRID_HEIGHT:
        raise ValueError("unexpected grayscale frame size")
    pixels = [value / 255.0 for value in gray]
    features: list[float] = []

    # Coarse layout: 8x6 cells. It mostly captures walls, doors and large edges,
    # so a moved chair or walking person affects only a few values.
    for cell_y in range(6):
        for cell_x in range(8):
            values = []
            for y in range(cell_y * 3, cell_y * 3 + 3):
                start = y * GRID_WIDTH + cell_x * 4
                values.extend(pixels[start: start + 4])
            features.append(sum(values) / len(values))

    # Global brightness-independent histogram and horizontal/vertical structure.
    histogram = [0.0] * 16
    for value in gray:
        histogram[min(15, value // 16)] += 1.0 / len(gray)
    features.extend(histogram)
    for band in range(6):
        horizontal = 0.0
        vertical = 0.0
        count = 0
        for y in range(band * 3, band * 3 + 3):
            for x in range(GRID_WIDTH):
                index = y * GRID_WIDTH + x
                if x:
                    horizontal += abs(pixels[index] - pixels[index - 1])
                if y:
                    vertical += abs(pixels[index] - pixels[index - GRID_WIDTH])
                count += 1
        features.extend((horizontal / count, vertical / count))
    mean = sum(features[:48]) / 48.0
    features[:48] = [value - mean for value in features[:48]]
    return _normalize(features)


def similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return -1.0
    return sum(a * b for a, b in zip(left, right))


async def image_signature(jpeg: bytes) -> list[float]:
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", "pipe:0", "-vf", f"scale={GRID_WIDTH}:{GRID_HEIGHT}",
        "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    output, error = await process.communicate(jpeg)
    if process.returncode != 0:
        raise RuntimeError(f"cannot decode camera image: {error.decode(errors='replace')[:300]}")
    return signature_from_gray(output)


class VisualPlaceStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.manifest_path = root / "manifest.json"
        self.entries: list[dict[str, Any]] = self._load()

    def _load(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            return list(data) if isinstance(data, list) else []
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.manifest_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.entries, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(self.manifest_path)

    @staticmethod
    def _slug(label: str) -> str:
        slug = re.sub(r"[^a-zа-я0-9]+", "_", label.casefold()).strip("_")
        return slug[:48] or "place"

    async def enroll(
        self,
        label: str,
        jpeg: bytes,
        odometry: dict[str, str | None] | None = None,
        map_pose: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        signature = await image_signature(jpeg)
        now = datetime.now(UTC)
        sequence = 1 + max((int(entry.get("sequence", 0)) for entry in self.entries), default=0)
        relative = Path(self._slug(label)) / f"{now:%Y%m%dT%H%M%S}_{sequence}.jpg"
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(jpeg)
        entry: dict[str, Any] = {
            "sequence": sequence,
            "label": label,
            "captured_at": now.isoformat(),
            "image": relative.as_posix(),
            "signature": signature,
            "odometry": odometry or {},
            "map_pose": map_pose,
        }
        self.entries.append(entry)
        self._save()
        return entry

    async def match(self, jpeg: bytes, limit: int = 3) -> list[dict[str, Any]]:
        current = await image_signature(jpeg)
        matches = [{
            "label": str(entry.get("label", "unknown")),
            "similarity": round(similarity(current, list(entry.get("signature", []))), 4),
            "image": entry.get("image"),
            "map_pose": entry.get("map_pose"),
            "odometry": entry.get("odometry", {}),
        } for entry in self.entries]
        matches.sort(key=lambda item: float(item["similarity"]), reverse=True)
        return matches[:max(1, min(limit, 5))]

    def count(self, label: str) -> int:
        needle = label.casefold()
        return sum(str(entry.get("label", "")).casefold() == needle for entry in self.entries)
