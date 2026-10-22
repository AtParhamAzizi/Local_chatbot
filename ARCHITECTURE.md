# ARCHITECTURE.md - Local RAG Chatbot (agent briefing)

Read this first. It should be enough to work on the project without reading every file.
Search by function name, since line numbers drift. Items marked (unverified) were not read in detail.

## 1. What this is

A local, Persian-language (RTL) RAG chatbot. Python/Flask, MySQL, local HuggingFace models.
- **User app** (Flask `ui_app`, port 5000): chat UI, `/chat`, `/feedback`, voice speech-to-text.
- **Admin app** (Flask `admin_app`, port 8000): JSON Q&A import, PDF ingestion, chat history, system logs, model selection.
- Both run in ONE process (`backend/main.py`): user app in a daemon thread, admin app on the main thread.
- Knowledge = `qa_documents` (JSON Q&A) + `pdf_chunks` (PDF text). Embeddings are float32 BLOBs in MySQL. Search is brute-force cosine in numpy (no vector DB).
- Models load from local folders (no runtime download). Exception: the Whisper STT model may download on first use unless `STT_LOCAL_FILES_ONLY=1`.

## 2. Stack

Flask, Flask-Cors, flask-sock, PyMySQL, numpy, torch, transformers (>=5.2), PyMuPDF (`fitz`), faster-whisper, av.
GPU profile: `requirements-cuda.txt` (torch 2.7.1+cu118, bitsandbytes 0.47.0, accelerate). Primary platform is Windows (`run_gpu.cmd`, `.venv\Scripts\python.exe`).

## 3. Repository map

```
backend/
  main.py             Entry point: init DB + models, start both servers
  user.py             User Flask app: /, /health, /chat, /feedback; retrieval orchestration
  admin.py            Admin Flask app: all /admin/api/* (very large, ~1600 lines)
  database.py         DB_CONFIG, get_conn, table creators, `logger` class, hashing helpers
  interface.py        Embedding model (load/embed) + shared constants (ports, limits, MIN_SCORE)
  generator.py        Generation LLM (load/generate), system prompt, sampling params
  searchForAnswer.py  Vector search over qa_documents / pdf_chunks + build_context
  model_registry.py   Model catalog + persisted active-model selection
  voice.py            Whisper STT integrated into the user app (/voice/health, /voice-stream)
  STT/                Standalone STT demo (STT.py), streaming.py (used by voice.py), audio-worklet.js
  admin/              Admin pages: admin (JSON), admin_pdf, adminchat, adminlogger, adminmodel (.html/.css/.js each)
  dashboard.html/.css/.js, theme.js   Extra admin/dashboard assets in backend/ (check references before editing)
  test.py             Ad-hoc retrieval smoke script, not a test suite
  logger.py           Effectively empty; the real logger is in database.py
  model/              (gitignored) embedding models, e.g. Qwen3-Embedding-0.6B/
  modelgen/           (gitignored) generator models, e.g. qwen3.5-2B/, gemma-4-E2B-it/
  datasets/           (gitignored) sample JSON
  model_selection.json (gitignored, runtime) persisted active model id
frontend/             User chat UI: index.html, script.js, style.css, theme.js
requirements.txt, requirements-cuda.txt, run_gpu.cmd, README.md
```

## 4. Startup pipeline (`backend/main.py: main()`, `@logger.operation`)

1. `init_logger_table()` -> `adminlogger`
2. `init_db()` -> `qa_documents`
3. `init_documents_table()` -> `pdf_documents`
4. `init_documents_chunk_table()` -> `pdf_chunks`
5. `init_history_table()` -> `chat_history`
6. `get_embedder()` (interface.py) loads the embedding model on CPU
7. `get_generator()` (generator.py) loads the LLM (CUDA if available)
8. Thread: `user_run()` (port 5000), then `admin_run()` (port 8000, host 0.0.0.0, `debug=True`, `use_reloader=False`)

`user.py` calls `configure_voice(ui_app)` at import time, which registers the voice routes.

## 5. Database (MySQL, utf8mb4, created by `database.py`)

| Table | Key columns | Notes |
| --- | --- | --- |
| `qa_documents` | id, question, answer, `question_hash` UNIQUE, `embedding` LONGBLOB, `embedding_dim`, created_at, updated_at | Dedup via `hash_question()` (sha256 of stripped question) |
| `pdf_documents` | id, filename, `document_hash` UNIQUE, file_size, page_count, chunk_count, embedded_chunk_count, embedding_dim, `status` ENUM(processing, completed, failed), error_message | Dedup via `sha256_bytes()` of the file |
| `pdf_chunks` | id, document_id FK (ON DELETE CASCADE), chunk_index, page_start, page_end, content LONGTEXT, embedding, embedding_dim; UNIQUE(document_id, chunk_index) | Only chunks of `completed` docs are searched |
| `chat_history` | id, user_id, ip_address, user_message, bot_response, `feedback` ENUM(like, dislike) NULL, feedback_at, created_at | One row per `/chat`; `id` is returned as `message_id` |
| `adminlogger` | id, `_level` (source file), `_from` (function), message TEXT, `warninglevel` (1 info, 2 warning, 3 failed), created_at | Written by `logger` |

Env: `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASS`, `DB_NAME`. The active config is `database.DB_CONFIG` (defaults: localhost, 3306, root, a hard-coded dev password, `chatbot_db`). Cursors return dicts (rows are accessed by column name). `interface.py` also defines a `DB_CONFIG` with different defaults. Use `database.get_conn()`, not that one.

## 6. Pipelines

### 6.1 Chat / RAG (`user.py: chat()`, POST `/chat`)

1. Read JSON `{message, user_id='anonymous'}` and `get_client_ip()` (honors `X-Forwarded-For`). Empty message returns 400.
2. `_retrieve_top(query, k=6)`:
   - `embed_texts([query])` gives qvec (float32).
   - `_search_qa(qvec, k=5)` and `_search_pdf(qvec, k=8, query=query)` from searchForAnswer.py.
   - Keep `score >= MIN_SCORE` (0.35), sort descending, take top k.
3. No hits: fixed Persian fallback reply (phone 1649), logged as WARNING.
   Hits: `generate_reply(message, hits[:6])`. If generation raises, the reply falls back to `hits[0]['answer']` or `['content']`, then the fallback text.
4. `recommended_questions` = `question` of `hits[1:4]` (QA hits only).
5. INSERT into `chat_history`. On failure returns 503.
6. Response: `{message_id, reply, recommended_questions, score, sources:[{type, filename, pages, score}]}`.

Scoring:
- QA: `_cosine_score(qvec, blob, embedding_dim)`; rows with a dimension mismatch are skipped.
- PDF: `0.82 * semantic + 0.18 * _lexical_overlap(query, content)` (uses `_terms`).

### 6.2 Generation (`generator.py`)

- `generate_reply(question, context_answers)` calls `build_context(results)` (searchForAnswer.py, labels each source as QA or PDF). It builds a Persian prompt that asks for a rewritten answer, not a copy. It prepends `SYSTEM_PROMPT` (Payam Iran assistant, answer only from context, refer support questions to 1649), applies the chat template, then calls `model.generate`.
- Params: `MAX_NEW_TOKENS=512, TEMPERATURE=0.3, TOP_P=0.8, TOP_K=15, REPETITION_PENALTY=1.05`.
- Lazy singletons `_gen_tokenizer/_gen_model/_gen_device` guarded by `_gen_lock`. `get_generator()` returns `(tokenizer, model, device)`.
- `_generator_device()` reads `GEN_DEVICE` (auto|cuda|cpu) and warns before a CPU fallback.
- `_generator_quantization(device, dtype)` reads `GEN_QUANTIZATION` (auto|4bit|none). Auto uses 4-bit NF4 (bitsandbytes) on GPUs with 6 GiB VRAM or less. FP16 is used on GPUs without BF16.
- `set_generator_model(model_id)` swaps the model at runtime (called by the admin model API). `_sync_model_selection()` re-reads the registry.
- `generate_reply` has `@logger.operation` applied twice (harmless).

### 6.3 Embeddings (`interface.py`)

- Functions: `get_embedder()`, `_load_model()`, `embedding_dim()`, `_model_hidden_size()`, `embed_texts(texts)` (`@torch.no_grad`), `set_embedding_model(model_id)`, `_sync_model_selection()`.
- Default model: `Qwen/Qwen3-Embedding-0.6B` in `backend/model/Qwen3-Embedding-0.6B/`.
- Constants: `ADMIN_PORT=8000`, `UI_PORT=5000`, `MAX_UPLOAD_BYTES=20MB`, `ALLOWED_EXT={'.json'}`, `MIN_SCORE=0.35`, `MAX_SEQ_LEN=512`, `ADMIN_DIR=backend/admin`.
- Stored embeddings with a different dimension are skipped by search. Re-ingest data after changing the embedding model.

### 6.4 Model selection (`model_registry.py`)

- `DEFAULT_MODEL_ID='qwen3.5-2b'`. `MODEL_DEFINITIONS` maps id to `{embedding_path, generator_path}` for Qwen3.5-2B (default), Qwen3-4B and Gemma 4 E2B IT. Paths can be overridden by env (`GEN_MODEL`, `QWEN_GENERATOR_PATH`, `GEN_MODEL_ID`).
- Selection is persisted in `backend/model_selection.json` (`_read_saved_id`, `_write_saved_id`).
- Public functions: `get_active_model_id()`, `get_model_definition(id)`, `get_model_catalog()`, `model_available(id)`, `set_active_model(id)`.
- Admin flow: POST `/admin/api/models` -> `admin_set_model()` -> `set_active_model` + `set_embedding_model` + `set_generator_model`.

### 6.5 JSON Q&A ingestion (`admin.py: admin_upload()`, POST `/admin/api/upload`)

Upload a `.json` list of `{question, answer}` (max 20 MB), validate, dedup by `hash_question()`, `embed_texts`, then store the embedding BLOB and `embedding_dim` in `qa_documents`. Related: `admin_documents()` (list/search/paginate), `admin_delete(doc_id)`, `admin_stats()`.

### 6.6 PDF ingestion (`admin.py: admin_pdf_upload()`, POST `/admin/api/pdf/upload`)

Upload a `.pdf` (max 50 MB), dedup by `sha256_bytes`, insert `pdf_documents` (`processing`), extract text per page with PyMuPDF, `split_text(text, chunk_size, overlap)` (`PDF_CHUNK_SIZE=900`, `PDF_CHUNK_OVERLAP=160`), embed in batches (`PDF_EMBED_BATCH_SIZE=16`), insert `pdf_chunks` with page ranges, then mark `completed` (or `failed` with `error_message`). Related: `admin_pdf_documents()`, `admin_pdf_delete(doc_id)` (chunks cascade), `admin_pdf_stats()`.

### 6.7 Feedback (`user.py: feedback()`, POST `/feedback`)

`{message_id: int > 0, feedback: 'like'|'dislike'}` updates `chat_history` (a new rating overwrites the old one). Returns 400 for invalid input, 404 for an unknown id, 503 on a DB error.

### 6.8 Voice / STT

- `voice.py: configure_voice(app)` registers on the user app: GET `/voice/audio-worklet.js`, GET `/voice/health` (also starts background model load), WebSocket `/voice-stream` (flask-sock).
- Helpers: `_load_model()` (faster-whisper `WhisperModel`), `_start_model_loading()`, `_model_status()`, `_transcribe_audio(audio)`, `_release_slot(future)`. One-thread executor plus `BoundedSemaphore(1)`, so one transcription runs at a time.
- `STT/streaming.py`: `AudioStream` (`feed`, `_feed_frame`, `_finalize`, `next_job`, `finish`, `has_pending`), `TranscriptionJob`, `SAMPLE_RATE=16000`, `MAX_PACKET_BYTES`. Energy-based VAD via `voice_threshold`.
- Env: `STT_MODEL` (base), `STT_DEVICE` (cpu), `STT_COMPUTE_TYPE` (int8), `STT_LANGUAGE` (fa), `STT_VOICE_THRESHOLD` (0.008), `STT_CPU_THREADS`, `STT_LOCAL_FILES_ONLY`.
- `STT/STT.py` is a standalone demo server that also defaults to port 5000. Never run it together with `main.py`.
- Frontend flow: mic -> AudioWorklet PCM -> WebSocket -> live transcript in the input box. On stop, the final text goes through the normal `/chat`. Spoken replies use browser `speechSynthesis` and only apply to voice-sent messages.

### 6.9 Logging (`database.py: class logger`)

- `logger.logDrop(message, level)`, or the legacy `logDrop(level, log_from, message, warninglevel)`. `_write()` inserts into `adminlogger` and falls back to the console on DB error. Caller file and function are inferred with `inspect`.
- Levels: `logger.INFORMATION=1`, `logger.WARNING=2`, `logger.FAILED=3`.
- `@logger.operation` wraps nearly every function. It logs completion, exceptions and Flask HTTP outcomes (4xx warning, 5xx failed). It never logs request bodies or model inputs.
- Decorator order for routes: `@app.route(...)` on top, `@logger.operation` below it.

## 7. HTTP API

### User app (port 5000, CORS `*`)
| Method | Path | Handler |
| --- | --- | --- |
| GET | `/` | `ui_index` (serves `frontend/index.html`) |
| GET | `/health` | `health` |
| POST | `/chat` | `chat` |
| POST | `/feedback` | `feedback` |
| GET | `/voice/health`, `/voice/audio-worklet.js` | `voice_health`, `voice_worklet` |
| WS | `/voice-stream` | `voice_stream` |

### Admin app (port 8000, CORS origin `null` with credentials, no authentication)
| Method | Path | Handler |
| --- | --- | --- |
| GET | `/` | `admin_index` (serves `admin/admin.html`) |
| GET, POST | `/admin/api/models` | `admin_models`, `admin_set_model` |
| GET | `/admin/api/stats` | `admin_stats` |
| POST | `/admin/api/upload` | `admin_upload` |
| GET | `/admin/api/documents` | `admin_documents` |
| DELETE | `/admin/api/documents/<int:doc_id>` | `admin_delete` |
| POST | `/admin/api/pdf/upload` | `admin_pdf_upload` |
| GET | `/admin/api/pdf/documents` | `admin_pdf_documents` |
| DELETE | PDF document by id (multi-line route in admin.py) | `admin_pdf_delete` |
| GET | `/admin/api/pdf/stats` | `admin_pdf_stats` |
| GET | `/admin/api/logs` | `get_admin_logs` |
| GET | `/admin/api/chat/sessions` | `admin_chat_sessions` |
| GET | `/admin/api/chat/messages` | `admin_chat_messages` |
| GET | `/admin/api/chat/stats` | `admin_chat_stats` |
| DELETE | chat session (multi-line route) | `admin_chat_delete_session` |
| DELETE | chat message (multi-line route) | `admin_chat_delete_message` |

The exact URL patterns of the multi-line DELETE routes are in `admin.py` next to their handlers.

## 8. Frontend (vanilla JS, no build step)

- **User UI** `frontend/`: `index.html`, `style.css` (Liquid Glass theme), `theme.js` (persisted dark/light toggle), `script.js`.
  - `script.js`: `sendMessage(messageOverride, speakReply)` -> POST `/chat`; `showTyping`/`hideTyping`; `addFeedbackButtons` and `sendFeedback` -> `/feedback`; `showRecommendedQuestions`, `sendQuickQuestion`; voice: `startVoiceRecording`, `stopVoiceRecording`, `connectVoiceStream`, `voiceTranscript`, `voiceMessage`, `speakVoiceReply`, `setVoiceStatus`, `updateVoiceControls`, `failVoiceSession`, `releaseVoiceMicrophone`. Uses `BACKEND_URL`.
- **Admin UI** `backend/admin/` (each page = html + css + js):
  - `admin.*` JSON Q&A: `api`, `loadStats`, `handleFile`, upload button, `loadDocuments`, `deleteDoc`, pagination and search.
  - `admin_pdf.*` PDF: `handleFile`, upload flow, `loadStats`, `loadDocuments`, `deletePdf`, `statusText`, `formatBytes`.
  - `adminchat.*` chat inspector: `loadSessions`, `renderSessions`, `selectSession`, `loadMessages`, `loadStats`, `renderThread`, `bubble`, `setupAutoRefresh`.
  - `adminlogger.*` system logs: `loadLogs`, `getFilteredLogs`, `renderLogs`, `createLogRow`, `updateStats`, `getStatus`.
  - `adminmodel.*` model selection: `loadModels`, `renderModels`, `selectModel`.
  - `backend/theme.js` and `backend/dashboard.*` also exist. Check which files the admin HTML actually references before editing (unverified).

## 9. Configuration and run

| Var | Purpose | Default |
| --- | --- | --- |
| `DB_HOST/PORT/USER/PASS/NAME` | MySQL | localhost / 3306 / root / dev pw / chatbot_db |
| `GEN_DEVICE` | auto, cuda, cpu | auto |
| `GEN_QUANTIZATION` | auto, 4bit, none | auto |
| `GEN_MODEL`, `GEN_MODEL_ID`, `QWEN_GENERATOR_PATH` | generator path / model id override | registry defaults |
| `STT_*` | Whisper settings | see 6.8 |

Run: `python backend\main.py`, or `.\run_gpu.cmd` (asserts CUDA, sets `GEN_DEVICE=cuda`). User UI at `http://localhost:5000`, admin at `http://localhost:8000`.

## 10. Conventions and gotchas

- Decorate new functions and routes with `@logger.operation` and log through `logger.logDrop`.
- DB pattern: `conn = get_conn()`, `with conn.cursor()`, `conn.close()`. Commit explicitly on writes.
- `database.py` is imported by almost every module (the logger lives there). Avoid circular imports by keeping it dependency-light.
- `generator.py` imports `build_context` from `searchForAnswer.py`; `user.py` imports from `interface`, `generator`, `searchForAnswer`, `voice`; `admin.py` imports from `generator`, `interface`, `model_registry`, `database`.
- Security and production caveats: the admin API has no authentication, Flask `debug=True`, a dev DB password is hard-coded as a default, and user CORS is `*`.
- There is no automated test suite. `backend/test.py` is a manual retrieval check.
- Persian strings in source can show as mojibake in some Windows consoles. Files are UTF-8, so keep that encoding when editing.
- `.gitignore` excludes model folders, `model_selection.json`, `datasets/` and STT model/assets. `backend/__pycache__/*.pyc` files are tracked in git despite the ignore rule.

## Admin Controller (runtime control)

- Page: ackend/admin/controller.html|css|js served at GET /controller (Liquid Glass style, uses ackend/theme.js). Each feature/model is a toggle switch.
- ackend/controller.py: state helpers (_state, _set_state, _save_state, _load_state), _free_memory(), _gpu_info(). Persists oice_enabled and chat_enabled in ackend/controller_state.json.
- ackend/controller_api.py: egister() runs at import (imported once in ackend/main.py). Adds:
  - controller_gate (before_request on ui_app): /voice* returns 403 when voice is off, /chat returns 503 when chat is off.
  - controller_inject (after_request on /): injects window.__CTL_FEATURES__ and /controller/features.js, which hides the voice UI and shows a chat-off banner (polls every 5s).
  - GET /admin/api/controller/status returns features, models (generator, embedder, voice), busy, errors, gpu.
  - POST /admin/api/controller/action accepts oice_enable|voice_disable|chat_enable|chat_disable|generator_load|generator_unload|embedder_load|embedder_unload|voice_load|voice_unload. Model actions run in a background thread and return 202.
- Notes: unloaded generator/embedder reload on the next chat message; admin API has no auth; no link from other admin pages.