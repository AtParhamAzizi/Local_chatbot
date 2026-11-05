# Dependency-profile acceptance record

Recorded November 5, 2026. Versions/hashes are in `locks/`; these results certify
the listed environments and checks, not every operating system, GPU or model.

## Fresh environment matrix

| Native environment | Profile | Locked distributions | Installation / pip check | Dependency verifier |
| --- | --- | --- | --- | --- |
| Windows 11 x64, CPython 3.13.14, pip 26.1.2 | CPU | 55 | Passed | CPU build, binary adapters, model classes and tensor/NumPy bridge passed |
| Windows 11 x64, CPython 3.13.14, pip 26.1.2 | CUDA 11.8 | 59 | Passed | Actual GTX 1050 FP16/NF4 execution passed |
| Ubuntu 26.04.1 x86-64 under WSL2, CPython 3.12.16, pip 26.2.2 | CPU | 54 | Passed | CPU build, binary adapters, model classes and tensor/NumPy bridge passed |
| Ubuntu 26.04.1 x86-64 under WSL2, CPython 3.12.16, pip 26.2.2 | CUDA 11.8 | 70 | Passed | `--software-only` imports/tensor checks passed; Linux GPU hardware not certified |

Each environment was newly created, not layered onto the existing developer
environment. The native pip resolution reports supplied all direct/transitive
wheel versions and hashes. Matching local wheelhouses were used for installation
with indexes disabled; CPU PyTorch wheels came from the official PyTorch CPU
artifact host. The large wheels/bundles and raw local-path reports are not tracked
or required as repository files. Public installation uses the same hash-pinned
artifacts from PyPI and the PyTorch index.

Core compatibility versions include Transformers 5.22.1, NumPy 2.5.4,
faster-whisper 1.2.1, CTranslate2 4.8.5, PyAV 18.1.0 and PyMuPDF 1.28.2.
CPU uses PyTorch 2.7.1+cpu. CUDA uses PyTorch/TorchAudio 2.7.1+cu118,
Accelerate 1.15.0 and bitsandbytes 0.47.0. Linux's CUDA/Triton dependencies
are independently recorded rather than omitted by a Windows-only freeze.

The GPU host reported **GeForce GTX 1050**, compute capability **6.1**, NVIDIA
driver **462.59**, and PyTorch CUDA runtime **11.8**. This driver is a recorded
working observation, not a recommended/minimum driver version or a claim about
WSL/container support. Verify your own host's maintained, compatible driver.
The test moved an NF4 layer and FP16 input onto CUDA, synchronized the GPU and
checked finite CUDA output; merely detecting a GPU was not counted as acceptance.

## Commands and regressions

For each native target, a fresh virtual environment's interpreter ran:

```text
python -B scripts/dependencies.py install --profile <cpu-or-cu118> --wheelhouse <matching-native-wheelhouse>
python -B scripts/dependencies.py verify --profile <cpu-or-cu118>
python -B backend/tests/run_tests.py --repeat 2
```

CPU additionally supplied its matching PyTorch CPU wheelhouse. Linux CUDA used
`verify --profile cu118 --software-only`, explicitly not a hardware success.
The offline suite passed twice in each of the four profiles. Fast checks cover
active requirement ranges, exact shared versions/hashes, native target and
CPU/GPU graph separation, and fail-closed installation/recording/verification.
A deliberately changed Flask wheel hash was rejected by pip with exit 1 during
a no-index dry run; no application environment was modified. Python/JavaScript
source/changed-whitespace checks and both workflows' actionlint checks passed.

## Prerequisites and limits

- The Windows host already had the Microsoft C++ runtime/driver prerequisites.
  Python wheels do not install the system driver or provision a clean OS.
  Linux needs compatible glibc/C++/OpenMP libraries; the Dockerfile retains its
  `libstdc++6`/`libgomp1` installation. The recorded Linux wheel tags include
  manylinux 2.28; do not assume an older distribution will run them.
- The first Linux attempt used a 3.4-GiB memory-backed `/tmp` and failed for space.
  Fresh installation on a normal persistent filesystem, with persistent pip
  temporary storage, passed. Allow ample disk space for CUDA wheels plus their
  expanded environment; do not use a small tmpfs for these installations.
- The Linux CUDA software check warned that an 8-bit optimizer was unavailable
  without CUDA hardware. The application's 4-bit NF4 inference path was tested
  on the Windows GPU, not certified by that Linux import-only run.
- ARM64/macOS, additional Python minors, modern CUDA stacks, container GPU
  passthrough and Linux GPU kernels were not hardware-qualified here. The Docker
  image and hosted manual workflow were not rebuilt/executed during this local
  acceptance; native environment recreation and workflow syntax were checked.
- No real embedding/generation/Whisper weights were loaded, no recognition or
  quality/performance evaluation ran, and no configured application database was
  contacted. Full-model VRAM requirements and production proxy behavior remain
  separate acceptance tasks. Package hashes and passing smoke checks do not
  replace security-advisory review or justify trusting arbitrary local model code.

Repeat this matrix and update this record when upgrading; see `README.md` in
this directory for the review, native lock regeneration and rollback process.
