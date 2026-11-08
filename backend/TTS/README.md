# Persian + English TTS

A small local web app for mixed Persian and English text-to-speech. The browser only sends text to a Python server; inference stays on the machine.

## How it works

- Persian script is normalized, converted to phonemes with `Homo-GE2PE-Persian-HF`, and synthesized with `Persian_model`.
- English words and `http://` / `https://` URLs are synthesized with the local Kokoro ONNX model in `English_model`.
- Both speech models and the Persian voice prompt are loaded once when the Flask process starts.
- Everything runs on CPU. No text is stored by the app.

## Setup

Use Python 3.10–3.12 for the smoothest compatibility with PyTorch and Pocket TTS.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`.

The first startup downloads the Persian G2P model and the default Persian voice prompt from Hugging Face. The TTS weights themselves are read from the two local model folders.

## Optional environment variables

```powershell
$env:ENGLISH_MODEL_FILE = "model_quantized.onnx"
$env:ENGLISH_VOICE = "af_heart"
$env:PERSIAN_VOICE_PROMPT = "C:\path\to\your\voice.wav"
$env:TTS_CPU_THREADS = "1"
```

The Persian voice prompt should be a short WAV/MP3/FLAC recording (about five seconds). If omitted, the example prompt shipped by the Persian model is used.

## Project layout

```text
app.py                 Flask routes
tts_engine.py          one-time model loading and audio synthesis
text_router.py         Persian/English/URL segmentation
templates/index.html   UI markup
static/                CSS and browser JavaScript
English_model/         local Kokoro ONNX model and voices
Persian_model/         local Pocket TTS Farsi model
```
