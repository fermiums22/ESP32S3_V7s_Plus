# -*- coding: utf-8 -*-
"""Download the official WeSpeaker ECAPA ONNX model used by speaker_profiles.py."""

from hashlib import sha256
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "speaker_models" / "voxceleb_ECAPA512_LM.onnx"
URL = (
    "https://huggingface.co/Wespeaker/wespeaker-ecapa-tdnn512-LM/"
    "resolve/main/voxceleb_ECAPA512_LM.onnx?download=true"
)
EXPECTED_SHA256 = "d71b85d9b48058ef68004f04f1b78acebefb9dfcf542e19b976a12a5ad1f10b0"


def checksum(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    if TARGET.is_file() and checksum(TARGET) == EXPECTED_SHA256:
        print(f"Модель уже установлена: {TARGET}")
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
        raise RuntimeError("Контрольная сумма модели голосов не совпала")
    temporary.replace(TARGET)
    print(f"Установлено: {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
