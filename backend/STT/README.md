# Real-time Persian speech-to-text

Local Whisper transcription with live, revisable captions. Audio is sent while
you speak, not uploaded only when recording ends. No API key is needed.

## Run on Windows (PowerShell)

Use Python 3.10 or newer:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe STT.py
```

Open **http://localhost:5000**, allow microphone access, and click the microphone.
Click again to stop; the last audio packet is flushed before the final text is
displayed. The copy button copies all displayed transcripts.

The multilingual `base` model downloads to `.models/` on first launch. Internet
access is needed for this download; subsequent launches use the cached model.
The page reports loading/download errors through `/health` instead of pretending
the model is ready. Restart the server after fixing a model-loading error.

## Appearance

The responsive, right-to-left interface uses a Liquid Glass-inspired design with
frosted panels, soft ambient color, and light/dark themes. Use the sun, moon, or
display buttons in the header to choose Light, Dark, or System. Your selection
is saved in this browser; System follows your device's appearance automatically.
The interface also respects reduced-motion and reduced-transparency preferences.
No external fonts, images, or frontend dependencies are required.

## Configuration

Set environment variables before starting the server:

```powershell
$env:STT_MODEL = "small"
$env:STT_LANGUAGE = "fa"
.\.venv\Scripts\python.exe STT.py
```

| Variable | Default | Purpose |
| --- | --- | --- |
| `STT_MODEL` | `base` | Multilingual `tiny`, `base`, `small`, or a local CTranslate2 model directory. Avoid `.en` models for Persian. |
| `STT_LANGUAGE` | `fa` | Whisper language code, or `auto` for detection. |
| `STT_DEVICE` | `cpu` | Set to `cuda` for a configured NVIDIA GPU. |
| `STT_COMPUTE_TYPE` | `int8` on CPU, `float16` on CUDA | Inference precision. |
| `STT_CPU_THREADS` | Up to 4 | CPU inference threads. |
| `STT_VOICE_THRESHOLD` | `0.008` | RMS speech gate; lower for quiet speech, raise for background noise. |
| `STT_LOCAL_FILES_ONLY` | Unset | Set to `1` to disallow model downloads. |
| `STT_HOST` / `STT_PORT` | `127.0.0.1` / `5000` | Listening address and port. |

The original `model/` is an incomplete Transformers/Flax checkpoint (no complete
processor/tokenizer or PyTorch weights). It is left untouched, but is not loaded
by this implementation. The backend uses faster-whisper/CTranslate2 instead of
PyTorch for lower-latency inference. A local model supplied via `STT_MODEL` must
be in CTranslate2 format, with its tokenizer files, not the existing Flax format.

## Streaming behavior

- An AudioWorklet mixes microphone channels to mono, resamples the actual device
  rate to 16 kHz, and sends 100 ms little-endian PCM16 packets over `/stream`.
  This avoids fragmented WebM containers and external FFmpeg processes.
- The server proposes a partial transcript after each additional second of
  audio. Partials replace the same caption; they are not appended as duplicates.
- A 700 ms pause finalizes an utterance. Continuous speech is split at 12 seconds.
  A 300 ms pre-roll preserves quiet onsets; a speech detector also filters audio
  before inference. Very quiet speech below the RMS gate needs a lower threshold.
- Inference runs separately from audio ingestion. Only the newest partial is
  scheduled, final segments have priority, and at most three finalized segments
  can wait. Overload is reported explicitly rather than dropping queued speech.
- Only one transcription session/upload runs at a time. Disconnecting releases
  the microphone, and the server holds its slot until any running inference ends.
- Whisper is a windowed model, not a native token-streaming model. Actual latency
  depends on hardware and utterance length; use `tiny` if `base` cannot keep up.

`POST /voice` still accepts multipart `audio` uploads (up to 10 MB / 60 seconds),
including WebM, for non-streaming clients. `GET /health` returns `ready`, `loading`,
or `error`; non-ready responses use HTTP 503. The frontend and backend are served
from the same origin. Open the served page rather than `index.html` directly.

Microphone access requires localhost or HTTPS. This is a local development app,
not an authenticated public service; do not expose the Flask server to the
internet. CUDA execution requires the compatible CUDA/cuDNN libraries described
in the faster-whisper documentation.
