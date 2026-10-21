# Local RAG Chatbot

A local, Persian-language retrieval-augmented generation (RAG) chatbot with a user chat interface and an administrator dashboard. It stores question-and-answer records, PDF knowledge-base chunks, chat history, feedback, and application logs in MySQL.

## Latest UI Update

The project now includes a refreshed **Liquid Glass** visual experience:

- Translucent glass panels with blur, gradients, soft borders, and luminous accents across the user chat and administrator pages.
- A clickable dark/light theme toggle on the frontend, dashboard, chat inspector, PDF/JSON tools, and system logs.
- Theme selection persists across reloads and stays synchronized between admin pages.
- The admin dashboard includes a Model Selection page for switching between Qwen3.5-2B (the default), Qwen3-4B, and Gemma 4 E2B IT when their local model files are available.
- Responsive layouts have been refined for desktop and mobile screens.

## Features

- Semantic retrieval using the local `Qwen/Qwen3-Embedding-0.6B` embedding model.
- Answer generation using the local `Qwen/Qwen3.5-2B` model by default.
- JSON question-and-answer ingestion through the administrator dashboard.
- PDF upload, text extraction, chunking, embedding, search, and deletion.
- Source metadata, recommended questions, chat-history recording, and feedback.
- Separate user interface (`http://localhost:5000`) and administrator dashboard (`http://localhost:8000`).

## Requirements

- Python 3.10 or newer.
- A running MySQL server (8.0+ recommended).
- Enough disk space and RAM/VRAM for both local Qwen models. Generation uses CUDA automatically when PyTorch detects a compatible GPU, with an explicit warning before falling back to CPU. The embedding model stays on CPU to reserve VRAM for generation.

## Setup

1. Create and activate a virtual environment.

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

2. Install Python dependencies.

   ```powershell
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```

   For NVIDIA GPU support on older GPUs such as the 4 GB GTX 1050, install the pinned CUDA 11.8 build instead of a CPU-only or CUDA 13 build:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements-cuda.txt
   .\.venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.version.cuda); print(torch.cuda.is_available())"
   ```

   This profile pins PyTorch and TorchAudio to 2.7.1+cu118. The CUDA availability check must print `True`. The wheel includes its CUDA runtime; a separate CUDA Toolkit installation is not required. An NVIDIA driver compatible with CUDA 11.8 is required. If CUDA initialization fails, update the NVIDIA driver. Do not install the GPU profile into a shared environment with incompatible `torchvision`/`torchaudio` versions; use the project virtual environment. A mismatched TorchAudio installation can cause `WinError 127` even during text-model loading because Transformers imports its optional audio utilities. Reinstall the CUDA profile to restore matching builds; keep packages with conflicting Transformers requirements, such as `qwen-tts`, in a separate virtual environment.

   The generator selects FP16 compute on GPUs without native BF16 support, including the GTX 1050. Qwen3.5 loads only its text-generation weights, not its unused vision encoder. On GPUs with 6 GiB VRAM or less, it automatically uses 4-bit NF4 weights through bitsandbytes to leave room for CUDA and generation; even text-only FP16 weights need about 3.51 GiB before runtime memory. The CUDA profile pins bitsandbytes 0.47.0 for CUDA 11.8/Pascal support. `GEN_QUANTIZATION` accepts `auto` (default), `4bit`, or `none`. Quantization can slightly change model outputs. Long prompts can still exhaust a small GPU; close other GPU applications or shorten the context if needed.

3. Create the MySQL database and configure the connection. The application reads these environment variables:

   | Variable | Default |
   | --- | --- |
   | `DB_HOST` | `localhost or your ip address to mysql` |
   | `DB_PORT` | `use your mysql port (default : 3306)` |
   | `DB_USER` | `use you mySQl user` |
   | `DB_PASS` | `password to login to your database` |
   | `DB_NAME` | `chatbot database name` |

   Example for PowerShell:

   ```powershell
   $env:DB_HOST = 'localhost or your ip address to mysql'
   $env:DB_PORT = 'use your mysql port (default : 3306)'
   $env:DB_USER = 'use you mySQl user'
   $env:DB_PASS = 'password to login to your database'
   $env:DB_NAME = 'chatbot database name'

   mysql -u root -p -e "CREATE DATABASE chatbot_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
   ```

   The application creates its tables automatically at startup.

4. Download the model files into the expected local directories:

   ```text
   backend/model/Qwen3-Embedding-0.6B/    Qwen/Qwen3-Embedding-0.6B
   backend/modelgen/qwen3.5-2B/          Qwen/Qwen3.5-2B
   ```

   Both directories must contain the corresponding Hugging Face model files, including `config.json`. Paths are resolved relative to `backend/`, regardless of the working directory. Override the embedding location with `QWEN_EMBEDDING_PATH` and the default generator location with `QWEN35_GENERATOR_PATH` or `GEN_MODEL`.

   On the first run, the application selects Qwen3-Embedding-0.6B with Qwen3.5-2B. To change models later, open the admin **Model Selection** page (`http://localhost:8000/adminmodel.html`). The selection is saved in `backend/model_selection.json` and reused on restart, taking precedence over `GEN_MODEL_ID`. Qwen3-4B remains available when its generator files are placed directly in `backend/modelgen/` or configured with `QWEN_GENERATOR_PATH`; it shares the Qwen embedding model. Gemma paths can be configured with `GEMMA_EMBEDDING_PATH` and `GEMMA_GENERATOR_PATH`.

5. The Flask user app serves its assets directly from `frontend/`. No copying is required; changes to the chat interface and feedback controls are served from that directory.

   Voice chat is served by the same app. The microphone button uses
   faster-whisper with the shared model cache in `backend/STT/.models` and sends
   live PCM audio over WebSocket; no API key or second server is required.
   Model loading starts when the chat page checks `/voice/health`, without
   blocking text chat. The first voice startup may download the `base` model.
   Set `STT_MODEL`, `STT_LANGUAGE`,
   `STT_DEVICE`, or `STT_COMPUTE_TYPE` to tune it (the defaults are `base`,
   `fa`, `cpu`, and `int8`).

## Run

From the repository root:

```powershell
python backend\main.py
```

To require GPU generation and use the project CUDA environment, run:

```powershell
.\run_gpu.cmd
```

This launcher fails with a diagnostic instead of silently using CPU when CUDA is unavailable. Alternatively, set `$env:GEN_DEVICE = 'cuda'` and run `.\.venv\Scripts\python.exe backend\main.py`. `GEN_DEVICE` also accepts `auto` (default) and `cpu`. Confirm startup reports `[gen] ready on cuda`; CPU activity from embeddings, tokenization, and retrieval is still expected.

Open:

- User chat: `http://localhost:5000`
- Admin dashboard: `http://localhost:8000`

Allow microphone access in the browser on localhost or HTTPS. Click the
microphone button to start recording; click it again to stop and send. Live
transcripts appear in the input, and the final transcript (plus any existing
draft) goes through the normal `/chat` endpoint, including history and feedback.
Silence and interrupted recordings are not sent automatically. The voice status
shows model loading, missing dependencies, microphone errors, and completion.

Spoken replies use browser speech synthesis only for messages sent by voice.
Uncheck the audio-reply option to mute or stop playback. A Persian voice must be
available on the device/browser; otherwise the reply stays text-only. Depending
on the selected browser/OS voice, speech playback may use a network service;
speech-to-text stays on your own server. `STT_LANGUAGE` changes transcription,
not the Persian chat UI or playback voice. Use `STT_LOCAL_FILES_ONLY=1` to
disallow Whisper model downloads after caching the model. Do not launch
`backend/STT/STT.py` alongside the chatbot on its default port: the standalone
demo also defaults to port 5000. The integrated voice endpoints are
`/voice/health` and `/voice-stream`; one transcription runs at a time.

Use the dashboard to import JSON records shaped like:

```json
[
  {
    "question": "What is this service?",
    "answer": "This is the answer stored in the knowledge base."
  }
]
```

PDF uploads are limited to 50 MB. JSON knowledge-base uploads are limited to 20 MB.

## API

The user service exposes:

- `GET /health` — service health check.
- `POST /chat` — retrieves knowledge and generates a response. Send JSON with `message` and an optional `user_id`. Every successful response includes a saved chat-history `message_id`, including fallback replies.
- `POST /feedback` — send `{"message_id": 123, "feedback": "like"}` (or `"dislike"`) using the ID returned by `/chat`. Saves the rating and its timestamp in `chat_history`; submitting another rating updates the same message. Invalid requests return 400, missing messages return 404, and database failures return 503 so the UI can offer a retry.

Administrator endpoints are available under `/admin/api/` on port 8000 for knowledge-base management, PDFs, logs, and chat history.

## Project layout

```text
backend/
  main.py             Starts the user and admin Flask applications
  user.py             User-facing chat API
  admin.py            Administrator APIs and dashboard
  model_registry.py   Persisted model selection and local model paths
  database.py         MySQL schema and connection helpers
  interface.py        Embedding model loading and configuration
  generator.py        Response-generation model loading
  model/              Local embedding model files
  modelgen/           Local generation model files
frontend/             Source user-interface assets (including Liquid Glass theme assets)
```

## Logging

Application logs are written to the MySQL `adminlogger` table by `logger.logDrop` in `backend/database.py`. The logger automatically records the calling file (including `.py`) in `_level` and the calling function in `_from`.

```python
from database import logger

logger.logDrop('Operation completed', logger.INFORMATION)
logger.logDrop('Using a fallback response', logger.WARNING)
logger.logDrop('Operation failed', logger.FAILED)
```

Severity codes are **1 = information**, **2 = warning**, and **3 = failed**. The existing four-argument `logger.logDrop(level, log_from, message, warninglevel)` calls remain supported, but file and function names are inferred automatically. `@logger.operation` records completed operations, exceptions, and HTTP outcomes (4xx as warnings and 5xx as failures) without logging request bodies or model inputs. Coverage includes startup, database initialization, chat and feedback, retrieval, embeddings, generation, model changes, JSON/PDF tools, and administrator APIs.

At startup, `init_logger_table()` upgrades existing `CHAR`/`VARCHAR` message columns to `TEXT` without removing existing logs. Database logging failures fall back to the console and do not interrupt application requests. View and filter logs on the admin **System Logs** page.

## Notes

- Models are loaded locally; the generator is configured not to download model files at runtime.
- Default database credentials are intended only for local development. Set `DB_PASS` to a secure value before any non-local deployment.
- Flask debug mode is enabled in the current application configuration and should be disabled before production use.
