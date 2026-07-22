"""Download the pinned official Silero VAD ONNX model."""

from hashlib import sha256
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "vad_models" / "silero_vad_16k_op15.onnx"
URL = (
    "https://raw.githubusercontent.com/snakers4/silero-vad/v6.2.1/"
    "src/silero_vad/data/silero_vad_16k_op15.onnx"
)
EXPECTED_SHA256 = "7ed98ddbad84ccac4cd0aeb3099049280713df825c610a8ed34543318f1b2c49"


def checksum(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    if TARGET.is_file() and checksum(TARGET) == EXPECTED_SHA256:
        print(f"Silero VAD уже установлен: {TARGET}")
        return 0
    TARGET.parent.mkdir(exist_ok=True)
    temporary = TARGET.with_suffix(".onnx.download")
    with requests.get(URL, stream=True, timeout=120) as response:
        response.raise_for_status()
        with temporary.open("wb") as output:
            for chunk in response.iter_content(1024 * 1024):
                if chunk:
                    output.write(chunk)
    if checksum(temporary) != EXPECTED_SHA256:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("Контрольная сумма Silero VAD не совпала")
    temporary.replace(TARGET)
    print(f"Silero VAD установлен: {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
