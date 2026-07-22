#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/sokol-silero-noavx"
WHEEL="${SCRIPT_DIR}/wheels/linux_x86_64_glibc/onnxruntime-1.20.1-1noavx-cp312-cp312-linux_x86_64.whl"
MODEL_DIR="${SCRIPT_DIR}/vad_models"
MODEL="${MODEL_DIR}/silero_vad_16k_op15.onnx"
MODEL_SHA256=7ed98ddbad84ccac4cd0aeb3099049280713df825c610a8ed34543318f1b2c49
MODEL_URL="https://raw.githubusercontent.com/snakers4/silero-vad/v6.2.1/src/silero_vad/data/silero_vad_16k_op15.onnx"

if [[ "$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" != "3.12" ]]; then
  echo "Нужен Python 3.12 внутри WSL Ubuntu."
  exit 1
fi

if [[ ! -f "${WHEEL}" ]]; then
  echo "Не найден wheel: ${WHEEL}"
  exit 1
fi

python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install --disable-pip-version-check "${WHEEL}"

mkdir -p "${MODEL_DIR}"
if [[ ! -f "${MODEL}" ]] || [[ "$(sha256sum "${MODEL}" | cut -d' ' -f1)" != "${MODEL_SHA256}" ]]; then
  echo "Загружаю модель Silero VAD..."
  curl -L --fail --retry 3 "${MODEL_URL}" -o "${MODEL}.download"
  echo "${MODEL_SHA256}  ${MODEL}.download" | sha256sum -c -
  mv "${MODEL}.download" "${MODEL}"
fi

echo "Проверяю Silero на профиле Intel Nehalem без AVX..."
if command -v qemu-x86_64 >/dev/null 2>&1; then
  qemu-x86_64 -cpu Nehalem "${VENV_DIR}/bin/python" "${SCRIPT_DIR}/smoke_test_silero.py"
else
  echo "qemu-x86_64 не установлен; выполняю нативную проверку wheel."
  "${VENV_DIR}/bin/python" "${SCRIPT_DIR}/smoke_test_silero.py"
fi
