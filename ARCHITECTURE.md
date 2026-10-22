# ARCHITECTURE.md - Local RAG Chatbot (agent briefing)

Read this first. It should be enough to work on the project without reading every file.
Search by function name, since line numbers drift.

## 1. What this is

A local, Persian-language (RTL) RAG chatbot. Python/Flask, MySQL, local HuggingFace models.
- **User app** (Flask `ui_app`, port 5000): chat UI, `/chat`, `/feedback`, voice speech-to-text, runtime feature gate.
- **Admin app** (Flask `admin_app`, port 8000): dashboard shell plus pages for JSON Q&A import, PDF ingestion, model selection, chat history, system logs and a runtime controller.
- Both run in ONE process (`backend/main.py`): user app in a daemon thread, admin app on the main thread.
- Knowledge = `qa_documents` (JSON Q&A) + `pdf_chunks` (PDF text). Embeddings are float32 BLOBs in MySQL. Search is brute-force cosine in numpy (no vector DB).
- Models load from local folders (`local_files_only=True`). Exception: the Whisper STT model may download on first use unless `STT_LOCAL_FILES_ONLY=1`.

## 2. Stack

Flask, Flask-Cors, flask-sock, simple-websocket, PyMySQL, numpy, torch, transformers (>=5.2,<6), PyMuPDF (`fitz`), faster-whisper, av.
GPU profile: `requirements-cuda.txt` (cu118 index, torch/torchaudio 2.7.1+cu118, accelerate, bitsandbytes 0.47.0). Primary platform is Windows (`run_gpu.cmd`, `.venv\Scripts\python.exe`).
`backend/STT/requirements.txt` is only for the standalone STT demo.

## 3. Repository map

```
backend/
  main.py             Entry point: init DB tables + models, import controller_api, start both servers
  user.py             User Flask app: /, /health, /chat, /feedback; _retrieve_top orchestration
  admin.py            Admin Flask app: dashboard/static routes + all /admin/api/* (~1700 lines)
  controller.py       Runtime controller state (controller_state.json), memory/GPU helpers
  controller_api.py   Controller routes + hooks on both apps; registers itself on import
  database.py         DB_CONFIG, get_conn, table creators, `logger` class, get_client_ip, hashing helpers
  interface.py        Embedding model (load/embed) + shared constants (ports, limits, MIN_SCORE, ADMIN_DIR)
  generator.py        Generation LLM (load/generate), SYSTEM_PROMPT, sampling params
  searchForAnswer.py  Vector search over qa_documents / pdf_chunks + build_context
  model_registry.py   Model catalog + persisted active-model selection
  voice.py            Whisper STT on the user app (/voice/health, /voice/audio-worklet.js, WS /voice-stream)
  STT/                streaming.py (AudioStream, used by voice.py), audio-worklet.js, standalone demo STT.py + README
  dashboard.html/.css/.js  Admin shell served at admin `/`: sidebar + iframe that loads admin/*.html
  theme.js            Shared dark/light theme for the dashboard and every admin page (loaded as ../theme.js)
  admin/              Admin pages (.html/.css/.js each): admin (JSON), admin_pdf, adminmodel, adminchat, adminlogger, controller
  test.py             Ad-hoc retrieval smoke script, not a test suite
  logger.py           Fully commented out; the real logger is in database.py
  model/              (gitignored) embedding models: Qwen3-Embedding-0.6B/, gemma-4-E2B-it/
  modelgen/           (gitignored) generator models: qwen3.5-2B/, gemma-4-E2B-it/ (Qwen3-4B files go in modelgen/ itself)
  datasets/           (gitignored) sample JSON
  model_selection.json   (gitignored, runtime) persisted active model id
  controller_state.json  (gitignored, runtime) persisted voice/chat feature toggles
frontend/             User chat UI: index.html, script.js, style.css, theme.js
requirements.txt, requirements-cuda.txt, run_gpu.cmd, README.md, LICENSE
```

## 4. Startup pipeline (`backend/main.py: main()`, `@logger.operation`)

Import side effects: importing `user` calls `configure_voice(ui_app)`; importing `controller` loads `controller_state.json`; `import controller_api` runs `register()`, which adds hooks and routes to both apps.

1. `init_logger_table()` -> `adminlogger` (also migrates an old CHAR/VARCHAR `message` column to TEXT)
2. `init_db()` -> `qa_documents`
3. `init_documents_table()` -> `pdf_documents`
4. `init_documents_chunk_table()` -> `pdf_chunks`
5. `init_history_table()` -> `chat_history`
6. `get_embedder()` (interface.py) loads the embedding model on CPU
7. `get_generator()` (generator.py) loads the LLM (CUDA if available)
8. Daemon thread `user_run()`, then `admin_run()` on the main thread. Both use host `0.0.0.0`, `debug=True`, `use_reloader=False` (user app also `threaded=True`).

The Whisper model is not loaded at startup. It loads lazily on the first `/voice/health` call or the controller `voice_load` action.

## 5. Database (MySQL, utf8mb4, created by `database.py`)

| Table | Key columns | Notes |
| --- | --- | --- |
| `qa_documents` | id, question, answer, `question_hash` UNIQUE, `embedding` LONGBLOB, `embedding_dim`, created_at, updated_at | Dedup via `hash_question()` (sha256 of stripped question) |
| `pdf_documents` | id, filename, `document_hash` UNIQUE, file_size, page_count, chunk_count, embedded_chunk_count, embedding_dim, `status` ENUM(processing, completed, failed), error_message | Dedup via `sha256_bytes()` of the file |
| `pdf_chunks` | id, document_id FK (ON DELETE CASCADE), chunk_index, page_start, page_end, content LONGTEXT, embedding, embedding_dim; UNIQUE(document_id, chunk_index) | Only chunks of `completed` docs are searched |
| `chat_history` | id, user_id, ip_address, user_message, bot_response, `feedback` ENUM(like, dislike) NULL, feedback_at, created_at | One row per `/chat`; `id` is returned as `message_id` |
| `adminlogger` | id, `_level` (source file), `_from` (function), message TEXT, `warninglevel` (1 info, 2 warning, 3 failed), created_at | Written by `logger` |

Env: `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASS`, `DB_NAME`. The active config is `database.DB_CONFIG` (defaults: localhost, 3306, root, a hard-coded dev password, `chatbot_db`; `autocommit=True`). Cursors are `DictCursor`, so rows are accessed by column name. `interface.py` also defines an unused `DB_CONFIG` with different defaults. Use `database.get_conn()`, not that one.
## 6. Pipelines

### 6.1 Chat / RAG (`user.py: chat()`, POST `/chat`)

0. `controller_gate` (before_request) returns 503 when chat is disabled in the controller.
1. Read JSON `{message, user_id='anonymous'}` and `get_client_ip()` (honors `X-Forwarded-For`). An empty message returns 400.
2. `_retrieve_top(query, k=6)` (an exception here returns 500):
   - `embed_texts([query])` gives qvec (float32).
   - `_search_qa(qvec, k=5)` and `_search_pdf(qvec, k=8, query=query)` from searchForAnswer.py.
   - Keep `score >= MIN_SCORE` (0.35), sort descending, take the top k.
3. No hits: fixed Persian fallback reply (phone 1649), logged as WARNING.
   Hits: `generate_reply(message, hits[:6])`. If generation raises, the reply falls back to `hits[0]['answer']` or `['content']`, then to the fallback text.
4. `recommended_questions` = `question` of `hits[1:4]` (QA hits only).
5. INSERT into `chat_history`. On failure returns 503.
6. Response: `{message_id, reply, recommended_questions, score, sources:[{type, filename, pages, score}]}`.

Scoring:
- QA: `_cosine_score(qvec, blob, embedding_dim)`. Rows with a dimension mismatch or zero norm are skipped.
- PDF: `0.82 * semantic + 0.18 * _lexical_overlap(query, content)`. `_terms` maps Arabic yeh/kaf to Persian forms and casefolds. Overlap = shared terms / query terms.

### 6.2 Generation (`generator.py`)

- `generate_reply(question, context_answers)` calls `build_context(results)` (searchForAnswer.py), which labels each source as QA or PDF with its similarity. It builds a Persian prompt that asks for a rewritten answer, not a copy. It prepends `SYSTEM_PROMPT` (Payam Iran assistant, answer only from context, refer support questions to 1649), applies the chat template with `enable_thinking=False`, then calls `model.generate` with sampling. Any `<think>...</think>` block is stripped from the output.
- Params: `MAX_NEW_TOKENS=512, TEMPERATURE=0.3, TOP_P=0.8, TOP_K=15, REPETITION_PENALTY=1.05`.
- Lazy singletons `_gen_tokenizer/_gen_model/_gen_device`, guarded by `_gen_lock`. `get_generator()` returns `(tokenizer, model, device)`. `_load_generator()` requires the model folder and its `config.json`. For `model_type == 'qwen3_5'` it loads with `config.text_config`.
- `_generator_device()` reads `GEN_DEVICE` (auto|cuda|cpu). It uses BF16 on compute capability >= 8 and FP16 otherwise. `auto` warns and falls back to CPU (float32); `cuda` raises if CUDA is unavailable.
- `_generator_quantization(device, dtype)` reads `GEN_QUANTIZATION` (auto|4bit|none). `auto` uses 4-bit NF4 (bitsandbytes + accelerate, `device_map` on cuda:0) on GPUs with 6 GiB VRAM or less. `4bit` raises on CPU.
- `set_generator_model(model_id)` swaps the model at runtime (called by the admin model API). `_sync_model_selection()` re-reads the registry on every load.

### 6.3 Embeddings (`interface.py`)

- Functions: `get_embedder()`, `_load_model()`, `embedding_dim()`, `_model_hidden_size()` (also checks `config.text_config`), `embed_texts(texts)` (`@torch.no_grad`), `set_embedding_model(model_id)`, `_sync_model_selection()`.
- Loading: `AutoModel`, float32, CPU, tokenizer `padding_side='left'`. Pooling takes the last token, then L2-normalizes. Inputs are truncated at `MAX_SEQ_LEN=512`.
- Default model: `Qwen3-Embedding-0.6B` in `backend/model/Qwen3-Embedding-0.6B/`.
- Constants: `ADMIN_PORT=8000`, `UI_PORT=5000`, `MAX_UPLOAD_BYTES=20MB`, `ALLOWED_EXT={'.json'}`, `MIN_SCORE=0.35`, `MAX_SEQ_LEN=512`, `ADMIN_DIR=backend/admin`.
- Search skips stored embeddings with a different dimension. Re-ingest data after changing the embedding model. The Gemma entry uses a different embedding folder.

### 6.4 Model selection (`model_registry.py`)

- `DEFAULT_MODEL_ID='qwen3.5-2b'`. `MODEL_DEFINITIONS` maps each id to `{label, embedding_path, generator_path}`:
  - `qwen3.5-2b`: embedding `QWEN_EMBEDDING_PATH` (default `model/Qwen3-Embedding-0.6B`); generator `QWEN35_GENERATOR_PATH`, else `GEN_MODEL`, else `modelgen/qwen3.5-2B`.
  - `qwen3-4b`: same embedding; generator `QWEN_GENERATOR_PATH`, else `GEN_MODEL`, else `modelgen/`.
  - `gemma-4-e2b-it`: embedding `GEMMA_EMBEDDING_PATH` (default `model/gemma-4-E2B-it`); generator `GEMMA_GENERATOR_PATH` (default `modelgen/gemma-4-E2B-it`).
- A model is available when both of its paths contain `config.json`. `get_active_model_id()` tries the saved id or `GEN_MODEL_ID` if available, then the first available model, then the requested or default id.
- The selection is persisted atomically in `backend/model_selection.json` (`_read_saved_id`, `_write_saved_id`).
- Public functions: `get_active_model_id()`, `get_model_definition(id)`, `get_model_catalog()`, `model_available(id)`, `set_active_model(id)` (raises `FileNotFoundError` if unavailable).
- Admin flow: POST `/admin/api/models` -> `admin_set_model()` -> `set_active_model` + `set_embedding_model` + `set_generator_model`. Returns 409 when model files are missing and 400 for an unknown id. Models reload lazily on the next request.

### 6.5 JSON Q&A ingestion (`admin.py: admin_upload()`, POST `/admin/api/upload`)

The upload is a multipart `file` (`.json`, UTF-8, max 20 MB) containing a list of `{question, answer}`. Invalid rows are collected as errors (the first 50 are returned), and duplicates within the file are dropped. All questions are embedded in one `embed_texts` call; only the question is embedded, not the answer. Rows whose `question_hash` already exists are skipped, or updated (answer + embedding) when the form field `replace` is true. Response: `{received, inserted, updated, skipped, embedded, duration_sec, errors}`.
Related: `admin_documents()` (`page`, `per_page` <= 100, `q` search), `admin_delete(doc_id)`, `admin_stats()` (`total_documents`, `embedded_documents`, `dim`).

### 6.6 PDF ingestion (`admin.py: admin_pdf_upload()`, POST `/admin/api/pdf/upload`)

1. The upload is a multipart `file` (`.pdf`, max 50 MB, non-empty, at least one page). Dedup uses `sha256_bytes`: a duplicate returns 409 unless the form field `replace` is true, in which case the old document row (and its chunks) is deleted.
2. Text is extracted per page with PyMuPDF (`get_text("text", sort=True)`). A PDF with no extractable text (e.g. a scan) returns 400.
3. `split_text(text, chunk_size, overlap)` runs per page with `PDF_CHUNK_SIZE=900` and `PDF_CHUNK_OVERLAP=160`. It removes soft hyphens and hyphenated line breaks, collapses whitespace, and prefers paragraph, line and sentence breaks. Chunks never span pages (`page_start == page_end`).
4. All chunks are embedded in one `embed_texts` call. `PDF_EMBED_BATCH_SIZE=16` is defined but unused.
5. The code inserts `pdf_documents` (`processing`) and `pdf_chunks`, then marks the document `completed` (or `failed` with `error_message`).

Related: `admin_pdf_documents()` (paginated, `q` search), `admin_pdf_delete(doc_id)` (chunks cascade), `admin_pdf_stats()`.

### 6.7 Feedback (`user.py: feedback()`, POST `/feedback`)

`{message_id: int > 0, feedback: 'like'|'dislike'}` updates `chat_history`; a new rating overwrites the old one. Returns 400 for invalid input, 404 for an unknown id, and 503 on a DB error.
### 6.8 Voice / STT

- `voice.py: configure_voice(app)` registers on the user app: GET `/voice/audio-worklet.js`, GET `/voice/health`, and WebSocket `/voice-stream` (flask-sock; registered only if the voice dependencies imported). `/voice/health` starts the background model load and returns 200 when ready, or 503 with status `loading|error|unavailable`.
- `/voice-stream` protocol: the server rejects cross-origin connections, connections made before the model is ready, and connections while another transcription is active. It sends `ready`, then `transcript {segment_id, text, is_final}` messages, then `done`. The client sends binary PCM16 packets and then `{"type":"stop"}`. 30 s without audio ends the session.
- Helpers: `_load_model()` (faster-whisper `WhisperModel`, cached in `STT/.models`), `_start_model_loading()`, `_model_status()`, `_transcribe_audio(audio)` (beam 1, Whisper VAD filter), `_release_slot(future)`. A one-thread executor plus `BoundedSemaphore(1)` allows one transcription session at a time.
- `STT/streaming.py`: `AudioStream` (`feed`, `_feed_frame`, `_finalize`, `next_job`, `finish`, `has_pending`), `TranscriptionJob`, `SAMPLE_RATE=16000`, `MAX_PACKET_BYTES` (16000 bytes, 0.5 s). Voice detection is RMS energy per 100 ms frame (`voice_threshold`). A segment closes after 0.7 s of silence or 12 s of audio, and a partial transcript is produced every 1 s. More than 3 queued segments raises "cannot keep up".
- Env: `STT_MODEL` (base), `STT_DEVICE` (cpu), `STT_COMPUTE_TYPE` (int8, or float16 on cuda), `STT_LANGUAGE` (fa; `auto` = detect), `STT_VOICE_THRESHOLD` (0.008), `STT_CPU_THREADS` (min(4, cpus)), `STT_LOCAL_FILES_ONLY`.
- `STT/STT.py` is a standalone demo server (`/`, `/health`, WS `/stream`, POST `/voice`) that also defaults to port 5000 (`STT_PORT`). Never run it together with `main.py`. Its UI files are gitignored.
- Frontend flow: mic -> AudioWorklet (downsamples to 16 kHz PCM16) -> WebSocket -> live transcript in the input box. On stop, the final text goes through the normal `/chat`. Spoken replies use the browser's `speechSynthesis` (a Persian voice if available) and only apply to messages sent by voice when the "voice replies" checkbox is on.

### 6.9 Runtime controller (`controller.py`, `controller_api.py`)

- State: `_state = {voice_enabled, chat_enabled}` (both default true), persisted atomically in `backend/controller_state.json` (`_load_state`, `_save_state`, `_set_state`). Helpers: `_free_memory()` (gc + `torch.cuda.empty_cache`) and `_gpu_info()`.
- `controller_api.register()` runs at import and is idempotent (`ui_app.extensions['controller']`). It only reads and resets singletons in `generator`, `interface` and `voice`; those modules are not modified.
- Hooks on the user app:
  - `controller_gate` (before_request): `/voice*` returns 403 when voice is off, and `/chat` returns 503 when chat is off.
  - `controller_inject` (after_request on `/` HTML): injects `window.__CTL_FEATURES__` and `<script src="/controller/features.js">`, and disables caching.
  - GET `/controller/features` (JSON) and GET `/controller/features.js`. The script hides the voice UI when voice is off, shows a "chat disabled" banner when chat is off, polls every 5 s, and reloads the page when voice is turned back on.
- Admin routes: GET `/controller` (serves `admin/controller.html`), GET `/admin/api/controller/status` (`features`, `models` {generator, embedder, voice}, `busy`, `errors`, `gpu`), POST `/admin/api/controller/action`.
- Actions: `voice_enable|voice_disable|chat_enable|chat_disable` apply immediately. `generator_load|generator_unload|embedder_load|embedder_unload|voice_load|voice_unload` run in a daemon thread and return 202. They return 409 when the same model is busy and 400 for an unknown action.
- An unloaded generator or embedder reloads automatically on the next chat message.

### 6.10 Logging (`database.py: class logger`)

- `logger.logDrop(message, level)`, or the legacy `logDrop(level, log_from, message, warninglevel)`. `_write()` inserts into `adminlogger` and falls back to the `chatbot.audit` Python logger on DB error. A thread-local flag prevents recursive logging. Caller file and function are inferred with `inspect`.
- Levels: `logger.INFORMATION=1`, `logger.WARNING=2`, `logger.FAILED=3`.
- `@logger.operation` wraps nearly every function. It logs completion, exceptions, and Flask HTTP outcomes (4xx = warning, 5xx = failed, including the response `error`/`message`). It never logs request bodies or model inputs. Note that this writes one DB row per decorated call, including every search and embedding call.
- Decorator order for routes: `@app.route(...)` on top, `@logger.operation` below it.

## 7. HTTP API

### User app (port 5000, CORS `*`)
| Method | Path | Handler |
| --- | --- | --- |
| GET | `/` | `ui_index` (serves `frontend/index.html`, controller script injected) |
| GET | static files | Flask static folder = `frontend/` (`static_url_path=''`) |
| GET | `/health` | `health` |
| POST | `/chat` | `chat` |
| POST | `/feedback` | `feedback` |
| GET | `/voice/health`, `/voice/audio-worklet.js` | `voice_health`, `voice_worklet` |
| WS | `/voice-stream` | `voice_stream` |
| GET | `/controller/features`, `/controller/features.js` | `controller_features`, `controller_features_js` |

### Admin app (port 8000, CORS origin `null` with credentials, no authentication)
| Method | Path | Handler |
| --- | --- | --- |
| GET | `/` | `admin_index` (serves `backend/dashboard.html`) |
| GET | `/dashboard.css`, `/dashboard.js`, `/theme.js` | `dashboard_asset` |
| GET | `/admin/<path:filename>` | `admin_page_asset` (files from `backend/admin/`) |
| GET | `/controller` | `controller_page` |
| GET, POST | `/admin/api/models` | `admin_models`, `admin_set_model` |
| GET | `/admin/api/stats` | `admin_stats` |
| POST | `/admin/api/upload` | `admin_upload` |
| GET | `/admin/api/documents` | `admin_documents` |
| DELETE | `/admin/api/documents/<int:doc_id>` | `admin_delete` |
| POST | `/admin/api/pdf/upload` | `admin_pdf_upload` |
| GET | `/admin/api/pdf/documents` | `admin_pdf_documents` |
| DELETE | `/admin/api/pdf/documents/<int:doc_id>` | `admin_pdf_delete` |
| GET | `/admin/api/pdf/stats` | `admin_pdf_stats` |
| GET | `/admin/api/logs` | `get_admin_logs` (all rows, newest first; the page filters and paginates client-side) |
| GET | `/admin/api/chat/sessions` | `admin_chat_sessions` (`limit` 1-1000, default 300; `q` search) |
| GET | `/admin/api/chat/messages` | `admin_chat_messages` (`user_id` required, `ip` optional) |
| GET | `/admin/api/chat/stats` | `admin_chat_stats` |
| DELETE | `/admin/api/chat/sessions?user_id=&ip=` | `admin_chat_delete_session` |
| DELETE | `/admin/api/chat/logs?id=` | `admin_chat_delete_message` |
| GET | `/admin/api/controller/status` | `controller_status` |
| POST | `/admin/api/controller/action` | `controller_action` |

A chat "session" is a `(user_id, ip_address)` group in `chat_history`.
## 8. Frontend (vanilla JS, no build step)

- **User UI** `frontend/`: `index.html`, `style.css` (Liquid Glass theme), `theme.js` (persisted dark/light toggle, `localStorage` key `liquid-theme`), `script.js`.
  - `script.js`: `BACKEND_URL` = page origin, or `http://localhost:5000` when opened as `file:`. `USER_ID` is a random `user_...` id generated per page load, so every reload starts a new admin "session".
  - Chat: `sendMessage(messageOverride, speakReply)` -> POST `/chat`; `addMessage` (typewriter effect); `showTyping`/`hideTyping`; `addFeedbackButtons` and `sendFeedback` -> `/feedback`; `showRecommendedQuestions`, `sendQuickQuestion`.
  - Health: `checkBackendConnection` (`/health`); `checkVoiceBackend` (`/voice/health`, polled every 3 s).
  - Voice: `startVoiceRecording`, `stopVoiceRecording`, `connectVoiceStream`, `voiceTranscript`, `voiceMessage`, `speakVoiceReply`, `setVoiceStatus`, `updateVoiceControls`, `failVoiceSession`, `releaseVoiceMicrophone`. The worklet is loaded from `/voice/audio-worklet.js` (`backend/STT/audio-worklet.js`).
- **Admin UI**: `backend/dashboard.html` is the shell. Its sidebar items (`data-page`) set the `src` of iframe `#pageFrame` (default `admin/admin.html`). Every page loads `../theme.js` (`backend/theme.js`). Pages call the API on the same origin, or on `http://localhost:8000` when opened as `file:`.
  - `admin.*` JSON Q&A: `api`, `loadStats`, `handleFile`, `showResult`, `loadDocuments`, `deleteDoc`, pagination and search.
  - `admin_pdf.*` PDF: `api`, `handleFile` (iframe preview), upload, `loadStats`, `loadDocuments`, `deletePdf`, `statusText`, `formatBytes`.
  - `adminmodel.*` model selection: `loadModels`, `renderModels`, `selectModel`.
  - `adminchat.*` chat inspector: `loadSessions`, `renderSessions`, `selectSession`, `loadMessages`, `loadStats`, `renderThread`, `bubble`, `setupAutoRefresh`. Its `BACKEND_URL` is hard-coded to `http://localhost:8000`.
  - `adminlogger.*` system logs: `loadLogs`, `getFilteredLogs`, `renderLogs`, `createLogRow`, `updateStats`, `getStatus` (25 rows per page, client-side).
  - `controller.*` runtime controller: `refresh` (polls status every 2 s), `render`, `sendAction`; one toggle switch per feature and per model.
  - The sidebar also links `admin/settings.html`, which does not exist (404).

## 9. Configuration and run

| Var | Purpose | Default |
| --- | --- | --- |
| `DB_HOST/PORT/USER/PASS/NAME` | MySQL | localhost / 3306 / root / dev pw / chatbot_db |
| `GEN_DEVICE` | auto, cuda, cpu | auto |
| `GEN_QUANTIZATION` | auto, 4bit, none | auto |
| `GEN_MODEL_ID` | initial model id when nothing is saved | none |
| `GEN_MODEL`, `QWEN35_GENERATOR_PATH`, `QWEN_GENERATOR_PATH`, `GEMMA_GENERATOR_PATH` | generator folders | see 6.4 |
| `QWEN_EMBEDDING_PATH`, `GEMMA_EMBEDDING_PATH` | embedding folders | see 6.4 |
| `STT_*` | Whisper settings | see 6.8 |

Run: `python backend\main.py` (run from anywhere; imports are flat, relative to `backend/`), or `.\run_gpu.cmd` (requires `.venv`, asserts CUDA, sets `GEN_DEVICE=cuda`). User UI at `http://localhost:5000`, admin dashboard at `http://localhost:8000`.
`admin.py` and `user.py` each have a `__main__` block for running one app alone. `admin.py`'s block calls `admin_run()` (defined earlier in the file).

## 10. Conventions and gotchas

- Decorate new functions and routes with `@logger.operation`, and log through `logger.logDrop`.
- DB pattern: `conn = get_conn()`, `with conn.cursor()`, `conn.close()` in `finally`. Autocommit is on; existing code still calls `conn.commit()` on writes.
- Modules import each other by flat names (`from database import ...`), so `backend/` must be the script directory or on `sys.path`.
- `database.py` is imported by almost every module (the logger lives there), so keep it dependency-light to avoid circular imports.
- Import graph: `generator` -> `searchForAnswer`, `model_registry`; `interface` -> `model_registry`; `user` -> `interface`, `generator`, `searchForAnswer`, `voice`; `admin` -> `generator`, `interface`, `model_registry`, `database`; `controller_api` -> `admin`, `user`, `controller`, `generator`, `interface`, `voice`.
- Feature changes that touch the user page or model lifecycle should go through `controller_api.py` hooks, which follows the existing "don't modify the core modules" pattern.
- Security and production caveats: the admin API and controller have no authentication, both apps bind `0.0.0.0` with Flask `debug=True`, a dev DB password is hard-coded as a default, and user CORS is `*`.
- There is no automated test suite. `backend/test.py` is a manual retrieval check.
- `user.py`, `generator.py` and `searchForAnswer.py` contain commented-out older versions and duplicate imports. The live code is the uncommented one.
- Persian strings in source can show as mojibake in some Windows consoles. Files are UTF-8; keep that encoding when editing.
- `.gitignore` excludes model folders, `model_selection.json`, `controller_state.json`, `datasets/`, `__pycache__/` and STT model/UI assets. `backend/__pycache__/*.pyc` files are still tracked in git despite the ignore rule.