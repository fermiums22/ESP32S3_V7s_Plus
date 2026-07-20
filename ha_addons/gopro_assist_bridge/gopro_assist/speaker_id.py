"""Small dependency-free speaker profiles for a family voice UI.

This is convenience identification, not authentication. It combines median
pitch with a normalized coarse spectral envelope and keeps several enrollment
templates per person.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from array import array
from pathlib import Path
from typing import Any


SAMPLE_RATE = 16_000
FREQUENCIES = (120, 180, 260, 380, 550, 780, 1100, 1550, 2200, 3100, 4000)


def _goertzel(frame: list[float], frequency: int) -> float:
    coefficient = 2.0 * math.cos(2.0 * math.pi * frequency / SAMPLE_RATE)
    previous = 0.0
    previous2 = 0.0
    for sample in frame:
        current = sample + coefficient * previous - previous2
        previous2, previous = previous, current
    return previous2 * previous2 + previous * previous - coefficient * previous * previous2


def _pitch(frame: list[float]) -> tuple[float, float]:
    best_lag = 0
    best_score = 0.0
    for lag in range(40, 321, 2):
        numerator = 0.0
        left_energy = 0.0
        right_energy = 0.0
        for index in range(lag, len(frame), 2):
            left = frame[index]
            right = frame[index - lag]
            numerator += left * right
            left_energy += left * left
            right_energy += right * right
        denominator = math.sqrt(left_energy * right_energy)
        score = numerator / denominator if denominator else 0.0
        if score > best_score:
            best_lag, best_score = lag, score
    return (SAMPLE_RATE / best_lag if best_lag else 0.0), best_score


def voice_features(pcm: bytes) -> list[float] | None:
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if sys.byteorder != "little":
        samples.byteswap()
    frame_size = 640
    hop = 480
    candidates: list[tuple[float, list[float]]] = []
    for offset in range(0, max(0, len(samples) - frame_size + 1), hop):
        frame = [float(value) for value in samples[offset: offset + frame_size]]
        mean = sum(frame) / frame_size
        frame = [value - mean for value in frame]
        rms = math.sqrt(sum(value * value for value in frame) / frame_size)
        if rms >= 350.0:
            candidates.append((rms, frame))
    if len(candidates) < 3:
        return None

    # Use the strongest distributed frames; this rejects silence and keeps CPU bounded.
    selected = sorted(candidates, key=lambda item: item[0], reverse=True)[:12]
    pitches: list[float] = []
    spectra = [0.0] * len(FREQUENCIES)
    for rms, frame in selected:
        normalized = [value / rms for value in frame]
        pitch, correlation = _pitch(normalized)
        if correlation >= 0.28 and 55.0 <= pitch <= 400.0:
            pitches.append(pitch)
        for index, frequency in enumerate(FREQUENCIES):
            spectra[index] += math.log1p(_goertzel(normalized, frequency))
    if not pitches:
        return None
    spectra = [value / len(selected) for value in spectra]
    mean_spectrum = sum(spectra) / len(spectra)
    spectra = [value - mean_spectrum for value in spectra]
    norm = math.sqrt(sum(value * value for value in spectra)) or 1.0
    spectra = [value / norm for value in spectra]
    pitch_feature = math.log2(statistics.median(pitches) / 100.0)
    return [pitch_feature, *spectra]


def feature_distance(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        return float("inf")
    pitch_distance = abs(left[0] - right[0])
    spectrum_distance = math.sqrt(sum(
        (a - b) * (a - b) for a, b in zip(left[1:], right[1:])
    )) / 2.0
    return 0.65 * pitch_distance + 0.35 * spectrum_distance


class SpeakerProfiles:
    def __init__(self, path: Path, threshold: float = 0.48) -> None:
        self.path = path
        self.threshold = threshold
        self.profiles: dict[str, list[list[float]]] = self._load()

    def _load(self) -> dict[str, list[list[float]]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return {
                str(name): [list(map(float, feature)) for feature in templates]
                for name, templates in data.items()
                if isinstance(templates, list)
            }
        except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError):
            return {}

    def enroll(self, name: str, pcm: bytes) -> int:
        feature = voice_features(pcm)
        if feature is None:
            raise ValueError("not enough voiced audio for enrollment")
        templates = self.profiles.setdefault(name, [])
        templates.append(feature)
        del templates[:-5]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.profiles, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        return len(templates)

    def identify(self, pcm: bytes) -> tuple[str, float] | None:
        feature = voice_features(pcm)
        if feature is None or not self.profiles:
            return None
        matches = [
            (min(feature_distance(feature, template) for template in templates), name)
            for name, templates in self.profiles.items() if templates
        ]
        if not matches:
            return None
        distance, name = min(matches)
        return (name, distance) if distance <= self.threshold else None
