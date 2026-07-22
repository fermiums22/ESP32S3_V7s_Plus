# ONNX Runtime for old x86-64 CPUs

`linux_x86_64_glibc/onnxruntime-1.20.1-1noavx-cp312-cp312-linux_x86_64.whl`
is the reduced CPU wheel for the bundled Silero VAD model.

- baseline: Intel Nehalem (`-march=nehalem`, SSE4.2, no required AVX);
- Python: CPython 3.12;
- platform: Linux x86-64 with glibc;
- SHA-256: `6e3b6f90195d9a3a947d9b4018c7ff4eff1dc282850edfc654cd7293eb593f8c`;
- smoke-tested with `qemu-x86_64 -cpu Nehalem`.

MLAS keeps newer kernels in the binary but selects them only after CPU feature
detection. The Nehalem smoke test confirms that the Silero inference path does
not execute AVX instructions.

This wheel is not compatible with the Alpine/musl Home Assistant add-on image.
Use it in a Debian/Ubuntu service or rebuild it inside the exact add-on image.
