# Persian + English TTS

A small local web app for mixed Persian and English text-to-speech. The browser only sends text to a Python server; inference stays on the machine.

## How it works

- Persian script is normalized, converted to phonemes with `Homo-GE2PE-Persian-HF`, and synthesized with `Persian_model`.
- English words and `http://` / `https://` URLs are synthesized with the local Kokoro ONNX model in `English_model`.
- Both speech models and the Persian voice prompt are loaded once on the first status or synthesis request.
- Persian sentences are phonemized separately and split into short phoneme chunks for generation. Question marks are removed before G2P, and ezafe links are kept together at chunk boundaries.
- The speed control applies to English speech.
- Everything runs on CPU. No text is stored by the app.

## Setup

Run these commands from the repository root, using a separate speech environment:

```powershell
python -m venv .venv-tts
.\.venv-tts\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install torch==2.7.1 --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements-tts.txt
python backend/TTS/app.py
```

Open `http://127.0.0.1:5000`.

The first synthesis/status request downloads the Persian G2P model from Hugging Face unless it is already cached. The TTS weights are read from the two local model folders, and the default Persian voice prompt is bundled in `voices/prompt_hello.wav`. Keep the model folders when cleaning the app directory. Install the Pocket TTS fork listed in `requirements.txt`; the stock package may reject the Persian configuration.

The chatbot uses `chat_worker.py` through `backend/speech.py`; it does not require this standalone Flask server. Verify actual Persian/English speech from the repository root with:

```powershell
python -B scripts/check_speech.py --offline
```

The offline check requires the G2P model to have been cached previously.

## Optional environment variables

```powershell
$env:ENGLISH_MODEL_FILE = "model_quantized.onnx"
$env:ENGLISH_VOICE = "af_heart"
$env:PERSIAN_VOICE_PROMPT = "C:\path\to\your\voice.wav"
$env:TTS_CPU_THREADS = "1"
```

The Persian voice prompt should be a short WAV/MP3/FLAC recording (at most five seconds). If omitted, the bundled example prompt is used.

All settings are defined and validated in `backend/settings.py`, shared with the
chatbot and standalone STT demo. Relative prompt paths resolve against this TTS
directory. `TTS_CPU_THREADS` must be an integer from 1 to 256; `ENGLISH_MODEL_FILE`
must be an ONNX filename without directories. `PERSIAN_G2P_REPO` selects the G2P
model identifier or path. Standalone `HOST`/`PORT` default to `127.0.0.1`/`5000`.
Validate without loading models with `python -B backend/settings.py --scope tts`;
add `--diagnostics` for effective non-secret values (model/prompt paths omitted).

## Project layout

```text
app.py                 Flask routes
tts_engine.py          one-time model loading and audio synthesis
chat_worker.py         private JSON worker for chatbot audio replies
text_router.py         Persian/English/URL segmentation
templates/index.html   UI markup
static/                CSS and browser JavaScript
English_model/         local Kokoro ONNX model and voices
Persian_model/         local Pocket TTS Farsi model
voices/                bundled Persian voice prompt
.runtime/              generated local config and voice archive (ignored by Git)
```

The generated `.runtime` directory is recreated as needed. The old nested `tts/` demo and `_inspect/` wheel extracts are not used by either entry point.
