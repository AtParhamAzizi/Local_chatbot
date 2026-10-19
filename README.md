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
- Enough disk space and RAM/VRAM for both local Qwen models. CUDA is used automatically when PyTorch detects a compatible GPU; otherwise the models run on CPU.

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

   For NVIDIA GPU support, install the PyTorch build appropriate for your CUDA version from the official PyTorch installation instructions before starting the application.

3. Create the MySQL database and configure the connection. The application reads these environment variables:

   | Variable | Default |
   | --- | --- |
   | `DB_HOST` | `localhost` |
   | `DB_PORT` | `3306` |
   | `DB_USER` | `root` |
   | `DB_PASS` | `159632@qw` |
   | `DB_NAME` | `chatbot_db` |

   Example for PowerShell:

   ```powershell
   $env:DB_HOST = 'localhost'
   $env:DB_PORT = '3306'
   $env:DB_USER = 'chatbot_user'
   $env:DB_PASS = 'change-me'
   $env:DB_NAME = 'chatbot_db'

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

5. Copy the user-interface assets into `backend/`. The Flask user app serves `index.html`, `style.css`, and `script.js` from that directory.

   ```powershell
   Copy-Item frontend\index.html, frontend\style.css, frontend\script.js, frontend\theme.js backend\
   ```

## Run

From the repository root:

```powershell
python backend\main.py
```

Open:

- User chat: `http://localhost:5000`
- Admin dashboard: `http://localhost:8000`

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
- `POST /chat` — retrieves knowledge and generates a response. Send JSON with `message` and an optional `user_id`.
- `POST /feedback` — records `like` or `dislike` feedback for a chat-history message.

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

## Notes

- Models are loaded locally; the generator is configured not to download model files at runtime.
- Default database credentials are intended only for local development. Set `DB_PASS` to a secure value before any non-local deployment.
- Flask debug mode is enabled in the current application configuration and should be disabled before production use.
