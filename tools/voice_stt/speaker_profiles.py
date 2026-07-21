# -*- coding: utf-8 -*-
"""Lightweight local speaker fingerprints using WeSpeaker ECAPA ONNX."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import wave

import numpy as np
import onnxruntime as ort


ROOT = Path(__file__).resolve().parent
MODEL_FILE = ROOT / "speaker_models" / "voxceleb_ECAPA512_LM.onnx"
PROFILE_DIR = ROOT / "speaker_profiles"
PROFILE_FILE = PROFILE_DIR / "profiles.json"
SAMPLE_RATE = 16_000
REFERENCE_SECONDS = 2


@dataclass
class SpeakerMatch:
    profile_id: str
    name: str
    similarity: float
    is_new: bool
    reference_pcm: np.ndarray | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float32).reshape(-1)
    return vector / max(float(np.linalg.norm(vector)), 1e-8)


def _hz_to_mel(value: np.ndarray | float) -> np.ndarray:
    return 2595.0 * np.log10(1.0 + np.asarray(value) / 700.0)


def _mel_to_hz(value: np.ndarray) -> np.ndarray:
    return 700.0 * (10.0 ** (value / 2595.0) - 1.0)


def _fbank80(pcm: np.ndarray) -> np.ndarray:
    signal = pcm.astype(np.float32, copy=False)
    frame_length = 400
    frame_shift = 160
    if len(signal) < frame_length:
        signal = np.pad(signal, (0, frame_length - len(signal)))
    frame_count = 1 + (len(signal) - frame_length) // frame_shift
    indexes = (
        np.arange(frame_length)[None, :]
        + np.arange(frame_count)[:, None] * frame_shift
    )
    frames = signal[indexes].copy()
    frames -= frames.mean(axis=1, keepdims=True)
    previous = frames.copy()
    frames[:, 1:] -= 0.97 * previous[:, :-1]
    frames[:, 0] -= 0.97 * frames[:, 0]
    frames *= (np.hanning(frame_length) ** 0.85).astype(np.float32)
    power = np.abs(np.fft.rfft(frames, n=512, axis=1)) ** 2

    mel_points = np.linspace(_hz_to_mel(20.0), _hz_to_mel(8000.0), 82)
    bins = np.floor((512 + 1) * _mel_to_hz(mel_points) / SAMPLE_RATE).astype(int)
    filters = np.zeros((80, 257), dtype=np.float32)
    for index in range(80):
        left, center, right = bins[index : index + 3]
        if center > left:
            filters[index, left:center] = np.arange(center - left) / (center - left)
        if right > center:
            filters[index, center:right] = np.arange(right - center, 0, -1) / (
                right - center
            )
    features = np.log(np.maximum(power @ filters.T, 1e-10)).astype(np.float32)
    features -= features.mean(axis=0, keepdims=True)
    return features


def _best_reference(pcm: np.ndarray) -> np.ndarray | None:
    size = SAMPLE_RATE * REFERENCE_SECONDS
    if len(pcm) < size:
        return None
    hop = SAMPLE_RATE // 4
    best_start = 0
    best_energy = -1.0
    for start in range(0, len(pcm) - size + 1, hop):
        candidate = pcm[start : start + size].astype(np.float32)
        energy = float(np.mean(candidate * candidate))
        if energy > best_energy:
            best_energy = energy
            best_start = start
    return pcm[best_start : best_start + size].astype("<i2", copy=True)


def _write_wav(path: Path, pcm: np.ndarray) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(pcm.astype("<i2", copy=False).tobytes())


class SpeakerProfiles:
    def __init__(
        self,
        threshold: float = 0.38,
        owner_name: str = "Виктор",
        cloud_names: list[str] | None = None,
        device_names: list[str] | None = None,
        guest_name: str = "Кожаный мешок",
        auto_update: bool = False,
        bootstrap_owner_samples: int = 5,
    ) -> None:
        if not MODEL_FILE.is_file():
            raise RuntimeError(
                f"Нет модели голосов {MODEL_FILE}. Запусти установщик голосовых профилей."
            )
        PROFILE_DIR.mkdir(exist_ok=True)
        self.threshold = threshold
        self.owner_name = owner_name
        self.cloud_names = cloud_names or [owner_name, "Жена", "Дочка"]
        self.device_names = device_names or ["Мика", "Алиса", "Бони"]
        self.guest_name = guest_name
        self.auto_update = auto_update
        self.bootstrap_owner_samples = max(1, bootstrap_owner_samples)
        self.session = ort.InferenceSession(
            str(MODEL_FILE), providers=["CPUExecutionProvider"]
        )
        self.data = self._load()

    def _load(self) -> dict:
        if not PROFILE_FILE.is_file():
            return {"version": 1, "profiles": []}
        value = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("profiles"), list):
            raise RuntimeError(f"Повреждён {PROFILE_FILE.name}")
        return value

    def _save(self) -> None:
        temporary = PROFILE_FILE.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(PROFILE_FILE)

    def embedding(self, pcm: np.ndarray) -> np.ndarray:
        features = _fbank80(pcm)[None, :, :]
        result = self.session.run(None, {"feats": features})[0][0]
        return _normalize(result)

    def has_owner_profile(self) -> bool:
        return any(
            profile.get("role") == "owner" for profile in self.data["profiles"]
        )

    def enroll_owner(self, pcm: np.ndarray) -> SpeakerMatch | None:
        return self.enroll_named(pcm, self.owner_name)

    def enroll_named(
        self, pcm: np.ndarray, name: str
    ) -> SpeakerMatch | None:
        size = SAMPLE_RATE * REFERENCE_SECONDS
        if len(pcm) < size:
            return None
        hop = size // 2
        candidates: list[tuple[float, np.ndarray]] = []
        for start in range(0, len(pcm) - size + 1, hop):
            reference = pcm[start : start + size].astype("<i2", copy=True)
            signal = reference.astype(np.float32)
            candidates.append((float(np.mean(signal * signal)), reference))
        candidates.sort(key=lambda item: item[0], reverse=True)
        selected = [reference for _, reference in candidates[:3]]
        embeddings = [self.embedding(reference) for reference in selected]
        owner_embedding = _normalize(np.mean(embeddings, axis=0))
        is_owner = name.casefold() == self.owner_name.casefold()
        profile_id = (
            "owner_viktor"
            if is_owner
            else "human_" + hashlib.sha1(name.casefold().encode("utf-8")).hexdigest()[:10]
        )
        reference_file = f"{profile_id}.wav"
        _write_wav(PROFILE_DIR / reference_file, selected[0])
        profiles = self.data["profiles"]
        profiles[:] = [
            profile
            for profile in profiles
            if profile.get("id") != profile_id
            and not (is_owner and profile.get("role") == "owner")
            and profile.get("id") != "runtime_guest"
        ]
        profiles.append(
            {
                "id": profile_id,
                "name": name,
                "kind": "human",
                "role": "owner" if is_owner else "family",
                "priority": 100 if is_owner else 70,
                "embedding": owner_embedding.tolist(),
                "samples": len(selected),
                "reference_file": reference_file,
                "created_at": _utc_now(),
                "updated_at": _utc_now(),
            }
        )
        self._save()
        return SpeakerMatch(
            profile_id,
            name,
            1.0,
            False,
            selected[0],
        )

    def identify(
        self, pcm: np.ndarray, allow_owner_bootstrap: bool = False
    ) -> SpeakerMatch:
        profiles = self.data["profiles"]
        reference = _best_reference(pcm)
        if reference is None:
            if len(pcm) < SAMPLE_RATE or not profiles:
                return SpeakerMatch("short", "Говорящий", 0.0, False)
            size = SAMPLE_RATE * REFERENCE_SECONDS
            reference = np.resize(pcm, size).astype("<i2")
        embedding = self.embedding(reference)
        persistent_profiles = [
            profile for profile in profiles if profile.get("id") != "runtime_guest"
        ]
        if not persistent_profiles:
            if not allow_owner_bootstrap:
                return SpeakerMatch("unenrolled", "Неизвестный", -1.0, False)
            profile_id = "owner_viktor"
            reference_file = f"{profile_id}.wav"
            _write_wav(PROFILE_DIR / reference_file, reference)
            profiles[:] = [
                {
                    "id": profile_id,
                    "name": self.owner_name,
                    "kind": "human",
                    "role": "owner",
                    "priority": 100,
                    "embedding": embedding.tolist(),
                    "samples": 1,
                    "reference_file": reference_file,
                    "created_at": _utc_now(),
                    "updated_at": _utc_now(),
                }
            ]
            self._save()
            return SpeakerMatch(
                profile_id, self.owner_name, 1.0, False, reference
            )
        best: dict | None = None
        best_score = -1.0
        for profile in profiles:
            score = float(np.dot(embedding, _normalize(profile["embedding"])))
            if score > best_score:
                best = profile
                best_score = score
        if best is not None and best_score >= self.threshold:
            owner_enrollment = (
                best.get("role") == "owner"
                and int(best.get("samples", 1)) < self.bootstrap_owner_samples
            )
            if self.auto_update or owner_enrollment:
                count = int(best.get("samples", 1))
                alpha = 1.0 / min(count + 1, 8)
                updated = _normalize(
                    (1.0 - alpha) * np.asarray(best["embedding"], dtype=np.float32)
                    + alpha * embedding
                )
                best["embedding"] = updated.tolist()
                best["samples"] = count + 1
                best["updated_at"] = _utc_now()
                self._save()
            return SpeakerMatch(
                str(best["id"]), str(best["name"]), best_score, False, reference
            )

        if len(pcm) < SAMPLE_RATE * REFERENCE_SECONDS:
            return SpeakerMatch("short", "Говорящий", best_score, False)

        profile_id = "runtime_guest"
        name = self.guest_name
        reference_file = "runtime_guest.wav"
        _write_wav(PROFILE_DIR / reference_file, reference)
        runtime_profile = next(
            (item for item in profiles if item["id"] == profile_id), None
        )
        value = {
            "id": profile_id,
            "name": name,
            "kind": "guest",
            "role": "guest",
            "priority": 10,
            "embedding": embedding.tolist(),
            "samples": 1,
            "reference_file": reference_file,
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        }
        if runtime_profile is None:
            profiles.append(value)
        else:
            runtime_profile.clear()
            runtime_profile.update(value)
        self._save()
        return SpeakerMatch(profile_id, name, best_score, True, reference)

    def detect_change(self, pcm: np.ndarray) -> tuple[bool, float]:
        size = SAMPLE_RATE * REFERENCE_SECONDS
        if len(pcm) < size * 2:
            return False, 1.0
        first = self.embedding(pcm[:size])
        last = self.embedding(pcm[-size:])
        similarity = float(np.dot(first, last))
        return similarity < self.threshold, similarity

    def rename_from_text(self, profile_id: str, text: str) -> str | None:
        patterns = (
            r"(?:сокол[,. ]+)?это\s+([А-ЯЁ][а-яё-]{1,30})(?:[.!?]|$)",
            r"меня зовут\s+([А-ЯЁ][а-яё-]{1,30})(?:[.!?]|$)",
            r"говорит\s+([А-ЯЁ][а-яё-]{1,30})(?:[.!?]|$)",
        )
        name = None
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                name = match.group(1).capitalize()
                break
        if not name:
            return None
        return self.assign_name(profile_id, name)

    def assign_name(self, profile_id: str, name: str) -> str | None:
        aliases = {
            "виктор": self.owner_name,
            "жена": "Жена",
            "супруга": "Жена",
            "дочка": "Дочка",
            "дочь": "Дочка",
            "мика": "Мика",
            "алиса": "Алиса",
            "бони": "Бони",
            "бонни": "Бони",
        }
        name = aliases.get(name.casefold(), "")
        if not name:
            return None
        for profile in self.data["profiles"]:
            if profile["id"] == profile_id:
                profile["name"] = name
                if name.casefold() == self.owner_name.casefold():
                    profile["kind"] = "human"
                    profile["role"] = "owner"
                    profile["priority"] = 100
                elif name in self.cloud_names:
                    profile["kind"] = "human"
                    profile["role"] = "family"
                    profile["priority"] = 70
                elif name in self.device_names:
                    profile["kind"] = "device"
                    profile["role"] = "device"
                    profile["priority"] = 20
                else:
                    return None
                if profile_id == "runtime_guest":
                    fixed_ids = {
                        "Жена": "family_wife",
                        "Дочка": "family_daughter",
                        "Мика": "device_mika",
                        "Алиса": "device_alice",
                        "Бони": "device_boni",
                    }
                    new_id = fixed_ids.get(name, "owner_viktor")
                    old_path = PROFILE_DIR / str(profile["reference_file"])
                    new_file = f"{new_id}.wav"
                    new_path = PROFILE_DIR / new_file
                    if old_path.is_file():
                        old_path.replace(new_path)
                    profile["id"] = new_id
                    profile["reference_file"] = new_file
                profile["updated_at"] = _utc_now()
                self._save()
                return name
        return None

    def known_references(self, limit: int = 3) -> list[tuple[str, Path]]:
        result: list[tuple[str, Path]] = []
        profiles = sorted(
            self.data["profiles"],
            key=lambda item: int(item.get("priority", 0)),
            reverse=True,
        )
        for profile in profiles:
            name = str(profile["name"])
            if name not in self.cloud_names:
                continue
            path = PROFILE_DIR / str(profile["reference_file"])
            if path.is_file():
                result.append((name, path))
            if len(result) == limit:
                break
        return result

    def metadata(self, profile_id: str) -> dict:
        for profile in self.data["profiles"]:
            if profile["id"] == profile_id:
                return {
                    "profile_id": profile_id,
                    "name": str(profile["name"]),
                    "role": str(profile.get("role", "unknown")),
                    "priority": int(profile.get("priority", 0)),
                }
        return {
            "profile_id": profile_id,
            "name": "Говорящий",
            "role": "unknown",
            "priority": 0,
        }

    def metadata_by_name(self, name: str) -> dict:
        for profile in self.data["profiles"]:
            if str(profile["name"]).casefold() == name.casefold():
                return self.metadata(str(profile["id"]))
        if name.casefold() == self.owner_name.casefold():
            return {
                "profile_id": "owner",
                "name": self.owner_name,
                "role": "owner",
                "priority": 100,
            }
        return {
            "profile_id": "unknown",
            "name": name,
            "role": "unknown",
            "priority": 0,
        }
