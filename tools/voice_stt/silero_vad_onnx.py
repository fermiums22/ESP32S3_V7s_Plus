"""Small streaming Silero VAD wrapper using NumPy and ONNX Runtime only."""

from __future__ import annotations

from collections import deque
from pathlib import Path

import numpy as np
import onnxruntime as ort


SAMPLE_RATE = 16_000
FRAME_SAMPLES = 512
FRAME_MS = 32
CONTEXT_SAMPLES = 64


class SileroVadModel:
    def __init__(self, model_path: Path) -> None:
        if not model_path.is_file():
            raise RuntimeError(
                f"Нет модели Silero VAD: {model_path}. "
                "Запусти install_silero_vad.bat."
            )
        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
        self.session = ort.InferenceSession(
            str(model_path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self.reset()

    def reset(self) -> None:
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, CONTEXT_SAMPLES), dtype=np.float32)

    def probability(self, pcm16: np.ndarray) -> float:
        if pcm16.size != FRAME_SAMPLES:
            raise ValueError(
                f"Silero ожидает {FRAME_SAMPLES} отсчётов, получено {pcm16.size}"
            )
        frame = pcm16.astype(np.float32, copy=False).reshape(1, -1) / 32768.0
        model_input = np.concatenate((self.context, frame), axis=1)
        output, self.state = self.session.run(
            None,
            {
                "input": model_input,
                "state": self.state,
                "sr": np.asarray(SAMPLE_RATE, dtype=np.int64),
            },
        )
        self.context = model_input[:, -CONTEXT_SAMPLES:]
        return float(output[0, 0])


class SileroSegmenter:
    def __init__(self, model_path: Path, config: dict) -> None:
        self.model = SileroVadModel(model_path)
        self.threshold = float(config.get("silero_threshold", 0.50))
        self.negative_threshold = float(
            config.get("silero_negative_threshold", max(0.01, self.threshold - 0.15))
        )
        self.start_frames = max(1, int(config.get("silero_start_frames", 2)))
        self.min_speech_ms = max(FRAME_MS, int(config.get("min_speech_ms", 360)))
        self.silence_ms_limit = max(
            FRAME_MS, int(config.get("end_silence_ms", 650))
        )
        self.max_phrase_ms = max(
            1000, int(float(config.get("max_phrase_seconds", 25)) * 1000)
        )
        pre_roll_frames = max(
            1, round(float(config.get("pre_roll_ms", 450)) / FRAME_MS)
        )
        self.pre_roll: deque[tuple[np.ndarray, bool]] = deque(
            maxlen=pre_roll_frames
        )
        self.reset()

    def reset(self) -> None:
        self.model.reset()
        self.pre_roll.clear()
        self.frames: list[np.ndarray] = []
        self.playback_overlap = False
        self.barge_in = False
        self.speech_ms = 0
        self.silence_ms = 0
        self.start_hits = 0
        self.speaking = False

    def feed(
        self,
        frame: np.ndarray,
        playback: bool,
        barge_in: bool = False,
    ) -> tuple[tuple[np.ndarray, bool, bool] | None, float]:
        probability = self.model.probability(frame)
        if not self.speaking:
            self.pre_roll.append((frame.copy(), playback))
            if probability >= self.threshold:
                self.start_hits += 1
            else:
                self.start_hits = 0
            if self.start_hits < self.start_frames:
                return None, probability
            self.speaking = True
            self.frames = [item[0] for item in self.pre_roll]
            self.playback_overlap = any(item[1] for item in self.pre_roll)
            self.barge_in = barge_in
            self.speech_ms = len(self.frames) * FRAME_MS
            self.silence_ms = 0
            self.pre_roll.clear()
            return None, probability

        self.frames.append(frame.copy())
        self.playback_overlap = self.playback_overlap or playback
        self.barge_in = self.barge_in or barge_in
        self.speech_ms += FRAME_MS
        if probability < self.negative_threshold:
            self.silence_ms += FRAME_MS
        elif probability >= self.threshold:
            self.silence_ms = 0

        complete = self.speech_ms >= self.max_phrase_ms or (
            self.speech_ms >= self.min_speech_ms
            and self.silence_ms >= self.silence_ms_limit
        )
        if not complete:
            return None, probability

        keep_tail_frames = max(1, round(100 / FRAME_MS))
        trim_frames = max(0, self.silence_ms // FRAME_MS - keep_tail_frames)
        captured = self.frames[:-trim_frames] if trim_frames else self.frames
        audio = np.concatenate(captured).astype("<i2", copy=False)
        result = (audio, self.playback_overlap, self.barge_in)

        trailing = self.frames[-self.pre_roll.maxlen :]
        self.model.reset()
        self.pre_roll.clear()
        self.pre_roll.extend((item.copy(), False) for item in trailing)
        self.frames = []
        self.playback_overlap = False
        self.barge_in = False
        self.speech_ms = 0
        self.silence_ms = 0
        self.start_hits = 0
        self.speaking = False
        return result, probability

    def flush(self) -> tuple[np.ndarray, bool, bool] | None:
        if not self.speaking or self.speech_ms < self.min_speech_ms or not self.frames:
            self.reset()
            return None
        audio = np.concatenate(self.frames).astype("<i2", copy=False)
        result = (audio, self.playback_overlap, self.barge_in)
        self.reset()
        return result
