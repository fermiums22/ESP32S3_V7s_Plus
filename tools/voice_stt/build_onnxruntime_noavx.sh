#!/usr/bin/env bash
set -euo pipefail

# Requires: Python 3.12 + venv, CMake, Ninja, GCC/G++, curl and tar.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_ROOT="${ORT_NOAVX_BUILD_ROOT:-${SCRIPT_DIR}/.build-onnxruntime-noavx}"
ORT_VERSION=1.20.1
EIGEN_COMMIT=e7248b26a1ed53fa030c5c459f7ea095dfd276ac
SRC_DIR="${BUILD_ROOT}/onnxruntime-${ORT_VERSION}"
EIGEN_DIR="${BUILD_ROOT}/eigen"
VENV_DIR="${BUILD_ROOT}/venv"
OUT_DIR="${SCRIPT_DIR}/wheels/linux_x86_64_glibc"

mkdir -p "${BUILD_ROOT}" "${OUT_DIR}"

if [[ ! -f "${SRC_DIR}/tools/ci_build/build.py" ]]; then
  curl -L --fail --retry 3 \
    "https://github.com/microsoft/onnxruntime/archive/refs/tags/v${ORT_VERSION}.tar.gz" \
    -o "${BUILD_ROOT}/onnxruntime.tar.gz"
  tar -xzf "${BUILD_ROOT}/onnxruntime.tar.gz" -C "${BUILD_ROOT}"
fi

if [[ ! -f "${EIGEN_DIR}/Eigen/Core" ]]; then
  curl -L --fail --retry 3 \
    "https://gitlab.com/libeigen/eigen/-/archive/${EIGEN_COMMIT}/eigen-${EIGEN_COMMIT}.zip" \
    -o "${BUILD_ROOT}/eigen.zip"
  python3 -m zipfile -e "${BUILD_ROOT}/eigen.zip" "${BUILD_ROOT}/eigen-unpacked"
  mkdir -p "${EIGEN_DIR}"
  cp -a "${BUILD_ROOT}/eigen-unpacked/eigen-${EIGEN_COMMIT}/." "${EIGEN_DIR}/"
fi

python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install -q \
  -r "${SRC_DIR}/tools/ci_build/github/linux/python/requirements.txt"

export CFLAGS="-O2 -march=nehalem -mtune=nehalem -mno-avx -mno-avx2 -mno-avx512f"
export CXXFLAGS="${CFLAGS}"

"${VENV_DIR}/bin/python" "${SRC_DIR}/tools/ci_build/build.py" \
  --config MinSizeRel \
  --build_dir "${BUILD_ROOT}/build" \
  --parallel "$(nproc)" \
  --build_wheel \
  --skip_tests \
  --skip_submodule_sync \
  --include_ops_by_config "${SCRIPT_DIR}/silero-required-operators.config" \
  --cmake_generator Ninja \
  --use_preinstalled_eigen \
  --eigen_path "${EIGEN_DIR}"

cp "${BUILD_ROOT}/build/MinSizeRel/dist/onnxruntime-${ORT_VERSION}-cp312-cp312-linux_x86_64.whl" \
  "${OUT_DIR}/onnxruntime-${ORT_VERSION}-1noavx-cp312-cp312-linux_x86_64.whl"
sha256sum "${OUT_DIR}/onnxruntime-${ORT_VERSION}-1noavx-cp312-cp312-linux_x86_64.whl"
