"""Load the bundled Silero model and run one silent 32 ms frame."""

from pathlib import Path

import numpy as np

from silero_vad_onnx import SileroVadModel


model = SileroVadModel(Path(__file__).parent / "vad_models" / "silero_vad_16k_op15.onnx")
probability = model.probability(np.zeros(512, dtype=np.int16))
print(f"Silero OK, silence probability={probability:.6f}")
