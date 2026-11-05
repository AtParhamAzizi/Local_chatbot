# Reproducible application environments (F017)

Use a **new environment** for each CPU/GPU profile. The installer refuses to
modify an existing application/developer environment or system Python. It selects
the native lock, installs only exact hash-checked wheels, and runs `pip check`.
Verification rejects missing, changed and extra application packages. Keep tools
such as `qwen-tts`, notebooks and unrelated audio/vision libraries elsewhere;
Transformers can discover incompatible optional packages simply because they are
installed. The existing `.venv` and ignored `backend/req` bundles are not modified.

## Recorded targets

| Profile | Windows x64, CPython 3.13 | Linux x86-64, CPython 3.12 |
| --- | --- | --- |
| `cpu` | `locks/cpu-windows-py313.txt` | `locks/cpu-linux-py312.txt` |
| `cu118` | `locks/cu118-windows-py313.txt` | `locks/cu118-linux-py312.txt` |

These are native **distribution/wheel locks**, not cross-platform freezes. Every
direct/transitive package has one exact version and a SHA-256 for the target's
wheel. Windows and Linux CUDA graphs differ: Linux also needs the recorded NVIDIA
CUDA 11 libraries and Triton; Windows wheels carry their runtime differently.
Trying another Python minor, architecture, implementation or free-threaded build
fails instead of silently resolving a different stack. Patch-version compatibility
does not mean the whole OS/driver image is bit-for-bit reproducible; tested Python,
installer and hardware versions are recorded in `VALIDATION.md`.

The CPU wheel is **PyTorch 2.7.1+cpu**, not a default PyPI Linux CUDA build forced
onto CPU. It installs no NVIDIA libraries, TorchAudio, bitsandbytes or Accelerate.
Both profiles include Flask, PDF extraction, speech dependencies, MySQL's RSA
authentication extra, and the pure-Python Gunicorn distribution. Gunicorn serving
remains Linux-only; installing its wheel does not make native Windows production
supported. Speech uses CPU/int8 in both profiles.

The compatibility GPU profile intentionally retains **PyTorch/TorchAudio
2.7.1+cu118**, **bitsandbytes 0.47.0** and CUDA 11.8. It preserves the project's
Pascal/GTX 1050 use case; it is not a universal NVIDIA profile. A compatible NVIDIA
driver is required, but a separate CUDA Toolkit is not needed for these wheels.
Newer GPU architectures may need a separately validated stack. Do not replace
cu118 with an unqualified latest PyTorch/CUDA build or assume BF16 support on
Pascal. The application continues to select FP16 and small-GPU NF4 loading.
Current CTranslate2/faster-whisper GPU inference requires a different CUDA/cuDNN
setup; this text-generation profile does **not** enable GPU speech.

## Recreate and verify

Windows, using CPython 3.13 x64 installed explicitly:

```powershell
python -m venv .venv
.venv\Scripts\python -B scripts/dependencies.py install --profile cpu
.venv\Scripts\python -B scripts/dependencies.py verify --profile cpu
```

For a separate GPU environment, substitute `cu118` for `cpu`. Verify with actual
GPU hardware before launching `run_gpu.cmd`; that launcher still uses `.venv`,
requires CUDA and sets `GEN_DEVICE=cuda`. Do not install a second profile over the
first: create another environment and invoke its interpreter explicitly instead.
Keep the previous working environment until the replacement passes acceptance.
If installation is interrupted, start another new environment rather than treating
a partially installed one as fresh.

Linux, using CPython 3.12 x86-64:

```sh
python3.12 -m venv .venv
.venv/bin/python -B scripts/dependencies.py install --profile cpu
.venv/bin/python -B scripts/dependencies.py verify --profile cpu
```

Package installation needs PyPI and the official PyTorch index (or an approved
mirror serving the same hashed bytes). No model is downloaded. Verification
blocks socket/DNS access and enables model-hub offline flags. It does not import
application startup, use `.env`, connect to MySQL, read model weights or start
retention. It checks exact inventory, `pip check`, web/speech/PDF binary imports,
NumPy/CPU tensor operations and the application's Qwen3/Qwen3.5/Gemma4 model-class
availability. CUDA verification additionally executes a small FP16/NF4 layer on
the GPU. This catches missing kernels/runtime support without an LLM download.
It is not a model-quality, throughput, full-model VRAM or STT-accuracy evaluation.

`verify --profile cu118 --software-only` checks imports but explicitly does **not**
certify hardware/driver/quantization. Use it for container builds and hosted CPU
CI only; do not treat it as GPU acceptance or fall back to CPU after a GPU failure.

### Offline wheelhouses

Download the exact wheels on the **same target OS/Python**, preserving the locks:

```text
python -m pip download --require-hashes --only-binary=:all: --index-url https://pypi.org/simple --extra-index-url https://download.pytorch.org/whl/cu118 -r requirements/locks/cu118-windows-py313.txt --dest wheelhouse
python -B scripts/dependencies.py install --profile cu118 --wheelhouse wheelhouse
```

Choose the CPU index/lock for CPU. `--wheelhouse` may be repeated and always turns
off indexes. Missing wheels, mismatched hashes and unsatisfied dependencies fail;
there is no online or source-build fallback. Keep wheelhouses outside Git. Existing
ignored `backend/req/windows` or `backend/req/ubuntu` bundles can supply matching
wheels, but locks do not require those local bundles or their absolute paths.

## Inputs, containers and other platforms

`requirements.txt`, `requirements-production.txt`, `requirements-docker.txt` and
`backend/STT/requirements.txt` express the existing bounded application inputs;
they are **not release locks**. `requirements-cpu.txt` selects the CPU build and
`requirements-cuda.txt` selects the older-GPU build. `constraints.txt` records the
shared dependency baseline used when resolving either profile. Constraints alone
do not install dependencies or authenticate artifact bytes; use the locks for
supported-target recreation. Direct `pip install -r requirements.txt` remains an
unlocked development path, not a reproducibility guarantee.

The x86-64 Docker image installs the appropriate lock and verifies it at build
time. CPU is the default; `compose.gpu.yaml` keeps its explicit CUDA profile and
GPU reservation. The base image/OS packages are outside the Python wheel lock.
The existing Apple Silicon/Linux ARM64 CPU container route is retained with
version constraints and CPU-index PyTorch 2.7.1, **not** an x86-64 wheel lock.
ARM64, native macOS, other Python minors and newer CUDA stacks are not in this
tested artifact matrix. Do not relabel an x86 lock as an ARM/macOS lock: resolve
and verify natively before adding such a target.

## Upgrade procedure

1. Preserve the working environment and previous lock set. Use a review branch,
   isolated resolver environments on **each native target**, and disposable data.
   Do not derive a release by freezing the developer's polluted environment.
2. Review upstream release notes, Python/wheel support and security advisories.
   Edit the bounded inputs and the selected entries in `constraints.txt` together.
   Review PyTorch, TorchAudio, Transformers, Accelerate, bitsandbytes,
   CTranslate2, NumPy and PyAV as a compatibility set. Keep cu118/Pascal pins unless
   intentionally qualifying a replacement on the older GPU. Add a distinct modern
   GPU profile rather than breaking the compatibility profile for everyone.
3. Resolve a complete graph without considering installed packages. CPU example:

   ```text
   python -m pip --isolated install --dry-run --ignore-installed --only-binary=:all: --report candidate.json -r requirements-cpu.txt
   python -B scripts/dependencies.py record --profile cpu --target windows-py313 --report candidate.json --output candidate.lock.txt
   ```

   For GPU resolve `-r requirements-docker.txt -r requirements-cuda.txt` and record
   `--profile cu118`. Repeat natively on Linux with `--target linux-py312` and the
   supported interpreter. `--no-index --find-links <wheelhouse>` also works for
   an approved offline artifact set. Recording rejects incomplete/wrong-platform,
   yanked/editable/source artifacts, absent hashes and incompatible profile builds.
   Deliberate PyTorch baseline changes also require updating the explicit
   compatibility guards in `scripts/dependencies.py` and their regression tests.
4. Review the complete lock diff, including transitive changes and Linux NVIDIA
   runtime dependencies. Do not hand-edit hashes or only regenerate one platform.
   Replace all affected locks with the reviewed candidates; keep indexes, tokens,
   local paths and raw resolver reports out of committed locks.
5. Recreate **new** CPU and GPU environments from the candidate locks. Run the
   verifier, then the offline suite twice; use the separately isolated MySQL and
   browser suites for behavioral regressions. Check actual FP16/NF4 execution on
   the GTX 1050-class compatibility target before certifying GPU support. Verify
   real models/voice separately with approved local models/data when qualifying
   runtime behavior; import/kernel checks cannot guarantee full-model capacity.
6. Record the date, Python patch, pip, OS/architecture, GPU/driver/capability,
   commands, results and limitations in `VALIDATION.md`. Review/merge the inputs,
   constraints, locks, tests and evidence as one change. Archive matching wheels
   if offline recreation is required. A lock is not a security-support guarantee;
   periodically review the old stack and do not load untrusted local model code.
7. Roll back by recreating the **previous** profile in a fresh environment, never
   by layering old packages over an upgraded environment. Dependency rollback does
   not roll back databases, local models or runtime state; manage those separately.

## Automation boundaries

The default fast suite checks all lock syntax, active input requirements, exact/shared versions, platform
separation, older-GPU pins and fail-closed installer/recorder behavior without
downloading/importing models or provisioned dependencies. The manual GitHub
`Dependency profiles` workflow recreates the selected profile on hosted Windows
and Linux, running software-only checks for CUDA. It is separate from `Merge
checks`; large wheels, real models and GPU hardware are not merge prerequisites.
Run hardware acceptance manually on an approved host, never by letting unreviewed
PR code execute on a credentialed/self-hosted GPU machine.
