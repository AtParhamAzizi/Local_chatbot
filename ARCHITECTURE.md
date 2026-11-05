# ARCHITECTURE.md - Local RAG Chatbot (agent briefing)

Read this first. It should be enough to work on the project without reading every file.
Search by function name, since line numbers drift.

## 1. What this is

A local, Persian-language (RTL) RAG chatbot. Python/Flask, MySQL, local HuggingFace models.
- **User app** (Flask `ui_app`, port 5000): chat UI, `/chat`, `/feedback`, voice speech-to-text, runtime feature gate.
- **Admin app** (Flask `admin_app`, port 8000): dashboard shell plus pages for JSON Q&A import, PDF ingestion, model selection, chat history, system logs and a runtime controller.
- Both share ONE serving process (`backend/main.py`): development uses two Flask servers; production uses one threaded Gunicorn worker listening on both ports, plus its supervising master.
- Knowledge = `qa_documents` (JSON Q&A) + `pdf_chunks` (PDF text). Embeddings are float32 BLOBs in MySQL. Search is brute-force cosine in numpy (no vector DB).
- Models load from local folders (`local_files_only=True`). Exception: the Whisper STT model may download on first use unless `STT_LOCAL_FILES_ONLY=1`.

## 2. Stack

Flask, Flask-Cors, flask-sock, simple-websocket, PyMySQL, numpy, torch, transformers (>=5.2,<6), PyMuPDF (`fitz`), faster-whisper, av.
GPU profile: `requirements-cuda.txt` (cu118 index, torch/torchaudio 2.7.1+cu118, accelerate, bitsandbytes 0.47.0). Primary platform is Windows (`run_gpu.cmd`, `.venv\Scripts\python.exe`).
`backend/STT/requirements.txt` is only for the standalone STT demo.
`requirements-production.txt` adds Gunicorn for Linux/WSL production serving; native Windows is local-development only.

## 3. Repository map

```
backend/
  main.py             Entry point: init DB tables + models, import controller_api, start both servers
  serving.py          Development server guard and production Gunicorn socket-port dispatcher
  settings.py         APP_ENV, validated APP_HOST (loopback default), credentials, shared ports
  security.py         Explicit browser-origin guards/CORS, peer-validated forwarding, safe errors
  public_errors.py    Allowlisted public failure codes/messages shared by HTTP and voice
  retention.py        Bounded privacy cleanup, shared scheduled worker, and dry-run/apply CLI
  request_validation.py Strict request schemas, bounded JSON/form parsing and upload spooling
  abuse_controls.py   Bounded per-client sliding windows and concurrent-request admission
  chat_sessions.py    Server-issued anonymous chat cookies and message ownership hashes
  admin_sessions.py   MySQL-backed opaque sessions, fixed/idle expiry, logout revocation
  admin_csrf.py       Session-bound CSRF token generation/validation, including pre-login forms
  admin_policy.py     Exact endpoint/method/asset/action operation policies; unknown operations denied
  admin-csrf.js       Shared same-origin adminFetch helper for authenticated mutations
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
  tests/              Offline unittest regressions; mysql/ tests real DB effects; browser/ tests UI journeys
  logger.py           Fully commented out; the real logger is in database.py
  model/              (gitignored) embedding models: Qwen3-Embedding-0.6B/, gemma-4-E2B-it/
  modelgen/           (gitignored) generator models: qwen3.5-2B/, gemma-4-E2B-it/ (Qwen3-4B files go in modelgen/ itself)
  datasets/           (gitignored) sample JSON
  model_selection.json   (gitignored, runtime) persisted active model id
  controller_state.json  (gitignored, runtime) persisted voice/chat feature toggles
frontend/             User chat UI: index.html, script.js, style.css, theme.js
requirements.txt, requirements-cuda.txt, run_gpu.cmd, README.md, LICENSE
```

## 4. Startup pipeline (`backend/main.py: main()` / `create_apps()`)

`main()` validates settings before importing the database/model/application modules.
Explicit `APP_ENV=development` (also `dev`, `local`, `test`) selects development;
all other values select production. Production launches Gunicorn with one `gthread`
worker, eight threads, no preload/reload, and a 300-second timeout. `create_apps()`
runs inside the worker after fork, so the master never loads models/CUDA. Its
`PortDispatcher` routes by the listening `SERVER_PORT`, retaining the original
WSGI environment, including the raw socket needed by Flask-Sock. Both apps share
model/controller state. Native Windows production exits without a dev-server fallback.

Import side effects: importing `user` calls `configure_voice(ui_app)`; importing `controller` loads `controller_state.json`; `import controller_api` runs `register()`, which adds hooks and routes to both apps.

1. `init_logger_table()` -> `adminlogger` (also migrates an old CHAR/VARCHAR `message` column to TEXT)
2. `init_db()` -> `qa_documents`
   `init_admin_users_table()` -> `admin-users` (migrates `session_version`) + `admin_sessions`; no automatic account creation
3. `init_documents_table()` -> `pdf_documents`
4. `init_documents_chunk_table()` -> `pdf_chunks`
5. `init_history_table()` -> `chat_history`
6. `get_embedder()` (interface.py) loads the embedding model on CPU
7. `get_generator()` (generator.py) loads the LLM (CUDA if available)
8. `start_retention_worker(ui_app, admin_app)` starts one shared privacy-cleanup daemon after table/model initialization; its first sweep runs immediately, then hourly by default. No cleanup starts on import.
9. Return both apps to the selected runtime. Development starts user serving in a daemon thread and admin serving on the main thread. Production serves both through the worker's shared thread pool. Both profiles default to `APP_HOST=127.0.0.1`; LAN exposure requires explicitly setting an interface address or wildcard. Debugger and reloader are disabled.

The Whisper model is not loaded at startup. It loads lazily on the first `/voice/health` call or the controller `voice_load` action.

## 5. Database (MySQL, utf8mb4, created by `database.py`)

| Table | Key columns | Notes |
| --- | --- | --- |
| `qa_documents` | id, question, answer, `question_hash` UNIQUE, `embedding` LONGBLOB, `embedding_dim`, created_at, updated_at | Dedup via `hash_question()` (sha256 of stripped question) |
| `pdf_documents` | id, filename, `document_hash` UNIQUE, file_size, page_count, chunk_count, embedded_chunk_count, embedding_dim, `status` ENUM(processing, completed, failed), error_message | Dedup via `sha256_bytes()` of the file |
| `pdf_chunks` | id, document_id FK (ON DELETE CASCADE), chunk_index, page_start, page_end, content LONGTEXT, embedding, embedding_dim; UNIQUE(document_id, chunk_index) | Only chunks of `completed` docs are searched |
| `chat_history` | id, user_id, owner_hash NULL, ip_address, user_message, bot_response, `feedback` ENUM(like, dislike) NULL, feedback_at, created_at | One row per `/chat`; `id` is returned as `message_id`; legacy rows retain NULL ownership |
| `adminlogger` | id, `_level` (source file), `_from` (function), message TEXT, `warninglevel` (1 info, 2 warning, 3 failed), created_at | Written by `logger` |
| `admin-users` | id, username UNIQUE, password_hash, is_active, is_admin, permissions JSON, session_version, created_at, updated_at | Security changes increment session_version atomically |
| `admin_sessions` | token_hash PRIMARY KEY, user_id FK, session_version, expires_at, last_seen_at | Opaque admin logins; Unix-second deadlines; user deletion cascades |

Env: `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASS`, `DB_NAME`. The active config is `database.DB_CONFIG` (defaults: localhost, 3306, root, a hard-coded dev password, `chatbot_db`; `autocommit=True`). Cursors are `DictCursor`, so rows are accessed by column name. `interface.py` also defines an unused `DB_CONFIG` with different defaults. Use `database.get_conn()`, not that one.
## 6. Pipelines

### 6.1 Chat / RAG (`user.py: chat()`, POST `/chat`)

0. `controller_gate` (before_request) returns 503 when chat is disabled in the controller. The chat admission decorator rejects excess per-IP traffic with 429 and retry guidance before parsing the message or running retrieval/generation.
1. Read JSON `{message}` and `get_client_ip()` (uses only middleware-validated `request.remote_addr`). An empty message returns 400. `chat_sessions.chat_owner_hash(create=True)` establishes or reuses a signed anonymous chat session; the server derives the public `user_id` from its ownership hash. Any supplied `user_id` is ignored.
2. `_retrieve_top(query, k=6)` (an exception here returns 500):
   - `embed_texts([query])` gives qvec (float32).
   - `_search_qa(qvec, k=5)` and `_search_pdf(qvec, k=8, query=query)` from searchForAnswer.py.
   - Keep `score >= MIN_SCORE` (0.35), sort descending, take the top k.
3. No hits: fixed Persian fallback reply (phone 1649), logged as WARNING.
   Hits: `generate_reply(message, hits[:6])`. If generation raises, the reply falls back to `hits[0]['answer']` or `['content']`, then to the fallback text.
4. `recommended_questions` = `question` of `hits[1:4]` (QA hits only).
5. INSERT into `chat_history`, including the server-derived `user_id` and `owner_hash`. IP is `''` unless `RETENTION_IP_DAYS > 0`. On failure returns 503. With `RETENTION_CHAT_DAYS=0`, skip history/cookie creation and return `message_id: null`; answering still works. Response `feedback_enabled` is false if chat or feedback retention is zero, and the frontend hides rating controls.
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

The upload is a multipart `file` (`.json`, UTF-8, max 20 MiB) containing 1-256 objects with exactly `{question, answer}`: nonblank strings of at most 4096 and 16000 characters respectively. Every row is validated before any embedding or persistence; one invalid row rejects the entire import with 400 rather than partially accepting it. Duplicate questions within valid files are dropped. All questions are embedded in one `embed_texts` call; only the question is embedded, not the answer. Rows whose `question_hash` already exists are skipped, or updated (answer + embedding) when the form field `replace` is true. Response: `{received, inserted, updated, skipped, embedded, duration_sec, errors}` (`errors` is empty on success).
Related: `admin_documents()` (`page`, `per_page` <= 100, `q` search), `admin_delete(doc_id)`, `admin_stats()` (`total_documents`, `embedded_documents`, `dim`).

### 6.6 PDF ingestion (`admin.py: admin_pdf_upload()`, POST `/admin/api/pdf/upload`)

1. The upload is a multipart `file` (`.pdf`, max 50 MiB, non-empty, 1-200 pages). Page counts above the limit return 413 before extraction.
2. Text is extracted per page with PyMuPDF (`get_text("text", sort=True)`), capped at 500000 total characters before trimming. Unreadable PDFs or PDFs with no extractable text (e.g. scans) return 400. Documents close even when extraction fails or hits a limit.
3. `split_text(text, chunk_size, overlap)` runs per page with `PDF_CHUNK_SIZE=900` and `PDF_CHUNK_OVERLAP=160`. It removes soft hyphens and hyphenated line breaks, collapses whitespace, and prefers paragraph, line and sentence breaks. Chunks never span pages (`page_start == page_end`).
4. At most 256 chunks are accepted. Text/chunk overages return 413 before model work or document changes. After validation, dedup uses `sha256_bytes`: a duplicate returns 409 unless `replace` is true. All chunks are embedded in one `embed_texts` call. `PDF_EMBED_BATCH_SIZE=16` is defined but unused.
5. The code inserts `pdf_documents` (`processing`) and `pdf_chunks`, then marks the document `completed` (or `failed` with `error_message`).

Related: `admin_pdf_documents()` (paginated, `q` search), `admin_pdf_delete(doc_id)` (chunks cascade), `admin_pdf_stats()`.

### 6.7 Feedback (`user.py: feedback()`, POST `/feedback`)

`{message_id: int > 0, feedback: 'like'|'dislike'}` updates `chat_history`; a new rating overwrites the old one. Zero chat/feedback retention disables feedback with 403 before DB access. The signed chat cookie is required; missing, tampered, or expired proof returns 401 without a history lookup. Both the UPDATE and its unchanged-row existence check constrain `id` AND the current `owner_hash`. Unknown, foreign, and legacy-unowned rows all return the same 404. Invalid input returns 400; a DB error returns 503. Client-supplied user IDs, ownership fields, and IP headers cannot grant ownership. Chat/feedback responses are no-store.
### 6.8 Voice / STT

- `voice.py: configure_voice(app)` registers on the user app: GET `/voice/audio-worklet.js`, GET `/voice/health`, and WebSocket `/voice-stream` (flask-sock; registered only if the voice dependencies imported). `/voice/health` starts the background model load and returns 200 when ready, or 503 with status `loading|error|unavailable`.
- `/voice-stream` protocol: the shared security guard rejects missing or unapproved browser origins before upgrade, using `UI_ALLOWED_ORIGINS`. Per-client admission also runs before upgrade. The server rejects connections made before the model is ready and connections while another transcription is active (with retry guidance). It sends `ready`, then `transcript {segment_id, text, is_final}` messages, then `done`. The client sends binary PCM16 packets and then `{"type":"stop"}`. 30 s without audio ends the session; wall time and total PCM audio are each capped at 120 s, including fast senders.
- Helpers: `_load_model()` (faster-whisper `WhisperModel`, cached in `STT/.models`), `_start_model_loading()`, `_model_status()`, `_transcribe_audio(audio)` (beam 1, Whisper VAD filter), `_release_slot(future)`. A one-thread executor plus `BoundedSemaphore(1)` allows one transcription session at a time.
- `STT/streaming.py`: `AudioStream` (`feed`, `_feed_frame`, `_finalize`, `next_job`, `finish`, `has_pending`), `TranscriptionJob`, `SAMPLE_RATE=16000`, `MAX_PACKET_BYTES` (16000 bytes, 0.5 s). Voice detection is RMS energy per 100 ms frame (`voice_threshold`). A segment closes after 0.7 s of silence or 12 s of audio, and a partial transcript is produced every 1 s. More than 3 queued segments raises "cannot keep up".
- Env: `STT_MODEL` (base), `STT_DEVICE` (cpu), `STT_COMPUTE_TYPE` (int8, or float16 on cuda), `STT_LANGUAGE` (fa; `auto` = detect), `STT_VOICE_THRESHOLD` (0.008), `STT_CPU_THREADS` (min(4, cpus)), `STT_LOCAL_FILES_ONLY`.
- `STT/STT.py` is a standalone demo server (`/`, `/health`, WS `/stream`, POST `/voice`) that also defaults to port 5000 (`STT_PORT`). Never run it together with `main.py`. Its UI files are gitignored.
- Frontend flow: mic -> AudioWorklet (downsamples to 16 kHz PCM16) -> WebSocket -> live transcript in the input box. On stop, the final text goes through the normal `/chat`. Spoken replies use the browser's `speechSynthesis` (a Persian voice if available) and only apply to messages sent by voice when the "voice replies" checkbox is on. Playback defaults off; the visible notice distinguishes local server-side STT/no audio files from browser/OS playback that may send reply text to a network provider.

### 6.9 Runtime controller (`controller.py`, `controller_api.py`)

- State: `_state = {voice_enabled, chat_enabled}` (both default true), persisted atomically in `backend/controller_state.json` (`_load_state`, `_save_state`, `_set_state`). Helpers: `_free_memory()` (gc + `torch.cuda.empty_cache`) and `_gpu_info()`.
- `controller_api.register()` runs at import and is idempotent (`ui_app.extensions['controller']`). It only reads and resets singletons in `generator`, `interface` and `voice`; those modules are not modified.
- Hooks on the user app:
  - `controller_gate` (before_request): `/voice*` returns 403 when voice is off, and `/chat` returns 503 when chat is off.
  - `controller_inject` (after_request on `/` HTML): injects `window.__CTL_FEATURES__` and `<script src="/controller/features.js">`, and disables caching.
  - GET `/controller/features` (JSON) and GET `/controller/features.js`. The script hides the voice UI when voice is off, shows a "chat disabled" banner when chat is off, polls every 5 s, and reloads the page when voice is turned back on.
- Admin routes: GET `/controller` (serves `admin/controller.html`), GET `/admin/api/controller/status` (`features`, `models` {generator, embedder, voice}, `busy`, `errors`, `gpu`), POST `/admin/api/controller/action`.
- Actions: `voice_enable|voice_disable|chat_enable|chat_disable` apply immediately. `generator_load|generator_unload|embedder_load|embedder_unload|voice_load|voice_unload` run in a daemon thread and return 202. They return 409 when the same model is busy; unknown/unregistered actions receive 403. Each action requires its own registered `controller.<action>` policy before state mutation or background dispatch, in addition to the route's `controller.dispatch` policy.
- An unloaded generator or embedder reloads automatically on the next chat message.

### 6.10 Logging (`database.py: class logger`)

- `logger.logDrop(message, level)`, or the legacy `logDrop(level, log_from, message, warninglevel)`. `_write()` inserts into `adminlogger` and falls back to the `chatbot.audit` Python logger on DB error. A thread-local flag prevents recursive logging. Caller file and function are inferred with `inspect`.
- Levels: `logger.INFORMATION=1`, `logger.WARNING=2`, `logger.FAILED=3`.
- `@logger.operation` wraps nearly every function. It logs completion, exception types, and Flask HTTP outcomes (4xx = warning, 5xx = failed, with an allowlisted public code, never the response body). It never logs request bodies or model inputs. Chat/feedback logs omit message IDs and rating values. `RETENTION_LOG_DAYS=0` suppresses DB logs and their fallback copies. Otherwise this writes one DB row per decorated call, including every search and embedding call.
- Decorator order for routes: `@app.route(...)` on top, `@logger.operation` below it.

## 7. HTTP API

### User app (port 5000, explicit `UI_ALLOWED_ORIGINS` CORS)
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

### Admin app (port 8000, authenticated, same-origin only)
| Method | Path | Handler |
| --- | --- | --- |
| GET | `/` | `admin_index` (serves `backend/dashboard.html`) |
| GET, POST | `/login` | `admin_login` (pre-login form CSRF proof required for POST) |
| POST | `/logout` | `admin_logout` (authenticated, CSRF-protected; GET never logs out) |
| GET | `/admin/api/session` | `admin_session` (current user, session-bound `csrf_token`, server-computed `allowed_pages`) |
| GET, POST | `/admin/api/admin-users` | `admin_users_collection` (administrator-only) |
| PATCH, DELETE | `/admin/api/admin-users/<int:user_id>` | `admin_user_item` (administrator-only) |
| GET | `/admin-csrf.js` | `dashboard_asset` (shared CSRF-aware request helper) |
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

A chat "session" in the admin inspector is a `(user_id, ip_address)` group in `chat_history`; IP is metadata, not ownership proof. New `user_id` values are server-derived public labels, never bearer credentials. The actual anonymous owner is established by the signed chat cookie. There is no public conversation read/delete API; administrator conversation inspection/deletion retains its explicit authenticated `chats` permission, not knowledge of a message or user ID.
All admin mutations (every method except GET/HEAD/OPTIONS) require CSRF proof after authentication and before operation authorization or handlers, including routes registered by `controller_api`. Use `X-CSRF-Token` or a `csrf_token` form field, never a query parameter. Failed proof returns 403; unauthenticated APIs/logout return 401. Login POST also requires its pre-login form token. `admin_policy.py` maps exact endpoints and methods to named operations, exact page/asset filenames to view operations, and explicit controller subactions to their own operations. Missing mappings deny access even for administrators. All account-management pages/assets/APIs require the `is_admin` role, never a username or `user_control`/wildcard grant. Generic static serving is disabled to prevent alternate-path asset bypasses. The complete matrix and test command are in `backend/admin-users/PERMISSIONS.md`.
## 8. Frontend (vanilla JS, no build step)

- **User UI** `frontend/`: `index.html`, `style.css` (Liquid Glass theme), `theme.js` (persisted dark/light toggle, `localStorage` key `liquid-theme`), `script.js`.
  - `script.js`: `BACKEND_URL` = page origin; its legacy `file:` fallback is unsupported because opaque origins are denied. Serve the frontend through the user app. Chat and feedback use same-origin cookies; the browser no longer generates or submits `USER_ID`. Reloads preserve ownership while the server-issued cookie remains valid.
  - Chat: `sendMessage(messageOverride, speakReply)` -> POST `/chat`; `addMessage` (typewriter effect); `showTyping`/`hideTyping`; `addFeedbackButtons` and `sendFeedback` -> `/feedback`; `showRecommendedQuestions`, `sendQuickQuestion`.
  - Health: `checkBackendConnection` (`/health`); `checkVoiceBackend` (`/voice/health`, polled every 3 s).
  - Voice: `startVoiceRecording`, `stopVoiceRecording`, `connectVoiceStream`, `voiceTranscript`, `voiceMessage`, `speakVoiceReply`, `setVoiceStatus`, `updateVoiceControls`, `failVoiceSession`, `releaseVoiceMicrophone`. The worklet is loaded from `/voice/audio-worklet.js` (`backend/STT/audio-worklet.js`).
- **Admin UI**: `backend/dashboard.html` is the shell. Its sidebar items (`data-page`) set the `src` of iframe `#pageFrame` (default `admin/admin.html`). Every page loads `../theme.js` (`backend/theme.js`). Pages call the API on the same origin, or on `http://localhost:8000` when opened as `file:`.
  - The shell and all admin pages load `admin-csrf.js` before page scripts and use `adminFetch(url, options)` rather than raw fetch. It obtains proof from `/admin/api/session`, attaches `X-CSRF-Token` to unsafe methods, preserves multipart bodies/JSON headers, rejects cross-origin destinations, and never retries mutations automatically. Failed logout stays on the dashboard with an error rather than falsely indicating success. File-opened admin pages are unsupported.
  - `admin.*` JSON Q&A: `api`, `loadStats`, `handleFile`, `showResult`, `loadDocuments`, `deleteDoc`, pagination and search.
  - `admin_pdf.*` PDF: `api`, `handleFile` (iframe preview), upload, `loadStats`, `loadDocuments`, `deletePdf`, `statusText`, `formatBytes`.
  - `adminmodel.*` model selection: `loadModels`, `renderModels`, `selectModel`.
  - `adminchat.*` chat inspector: `loadSessions`, `renderSessions`, `selectSession`, `loadMessages`, `loadStats`, `renderThread`, `bubble`, `setupAutoRefresh`. Its API uses the served page origin, including HTTPS.
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
| `UI_ALLOWED_ORIGINS`, `ADMIN_ALLOWED_ORIGINS` | Exact comma-separated HTTP(S) browser origins; no wildcards, `null`, or URL paths | required in production; localhost/127.0.0.1/[::1] on each service's port in development |
| `TRUSTED_PROXY_HOPS` | Exact trusted forwarding-chain length, 0-10 | 0 (ignore forwarding headers) |
| `TRUSTED_PROXY_CIDRS` | Trusted socket/intermediary proxy IPs or IPv4/IPv6 CIDRs | empty; required when hop count is nonzero |

Run: `python backend\main.py` (run from anywhere; imports are flat, relative to `backend/`), or `.\run_gpu.cmd` (requires `.venv`, asserts CUDA, sets `GEN_DEVICE=cuda`). User UI at `http://localhost:5000`, admin dashboard at `http://localhost:8000`.
`admin.py` and `user.py` each have a `__main__` block for running one app alone in explicit development mode. Both runners use `serve_development()`, which refuses production. Use `backend/main.py` for production initialization and shared serving. See README runtime profiles for HTTPS/WebSocket proxy requirements.

## 10. Conventions and gotchas

- Decorate new functions and routes with `@logger.operation`, and log through `logger.logDrop`.
- DB pattern: `conn = get_conn()`, `with conn.cursor()`, `conn.close()` in `finally`. Autocommit is on; existing code still calls `conn.commit()` on writes.
- Modules import each other by flat names (`from database import ...`), so `backend/` must be the script directory or on `sys.path`.
- `database.py` is imported by almost every module (the logger lives there), so keep it dependency-light to avoid circular imports.
- Import graph: `generator` -> `searchForAnswer`, `model_registry`; `interface` -> `model_registry`; `user` -> `interface`, `generator`, `searchForAnswer`, `voice`; `admin` -> `generator`, `interface`, `model_registry`, `database`; `controller_api` -> `admin`, `user`, `controller`, `generator`, `interface`, `voice`.
- Feature changes that touch the user page or model lifecycle should go through `controller_api.py` hooks, which follows the existing "don't modify the core modules" pattern.
- Security and production caveats: both apps default to loopback with debug disabled and require explicit development selection to use Flask's server. Production uses threaded Gunicorn on Linux/WSL and requires explicit browser-origin lists. Unapproved/opaque origins are denied before handlers on both apps; admin remains same-origin only. See README for CORS, proxy trust, credential, and network restrictions.
- `backend/tests/test_core_units.py` covers hashing, current Persian lexical normalization, chunking, scoring/retrieval, permissions and parsing with scoped model/database doubles. Install only `backend/tests/requirements.txt`, then run `python -B backend/tests/run_tests.py --repeat 2`; this discovers all default regressions while forbidding real network/database work, model dependency imports and model-directory reads. See `backend/tests/TESTING.md` for clean-environment setup and focused discovery. `test_admin_authorization.py` retains the complete operation-policy and Flask authorization matrix. Standard `python -B -m unittest discover -s backend/tests -v` also works. `backend/test.py` remains a separate manual retrieval check, not a regression test.
- `user.py`, `generator.py` and `searchForAnswer.py` contain commented-out older versions and duplicate imports. The live code is the uncommented one.
- Persian strings in source can show as mojibake in some Windows consoles. Files are UTF-8; keep that encoding when editing.
- `.gitignore` excludes model folders, `model_selection.json`, `controller_state.json`, `datasets/`, `__pycache__/` and STT model/UI assets. `backend/__pycache__/*.pyc` files are still tracked in git despite the ignore rule.

## Admin user authentication

`backend/admin_users.py` owns PBKDF2 password hashing, login verification, user serialization, and user-management persistence. `backend/admin.py` applies session, CSRF, and explicit `admin_policy.py` operation checks to dashboard pages, APIs, and controller routes, and exposes `/login`, `POST /logout`, `/admin/api/session`, and `/admin/api/admin-users`. Current feature grants remain compatible, but only a true `is_admin` role grants account-management access; usernames confer no authority. The session endpoint supplies permitted navigation pages from the server policy, not a separate client role map. Account deletion forbids self-deletion by ID, not deletion of a special username. `database.init_admin_users_table()` creates the backtick-quoted MySQL table `admin-users`, migrates existing accounts to include `session_version`, and creates `admin_sessions`; it never seeds accounts. `backend/setup_admin.py` provides local interactive first-account setup. `backend/admin-users/login.html` is the login view; `backend/admin/admin-user-control.html` is the administrator-only management view.

`backend/admin_sessions.py` issues random opaque IDs (only SHA-256 hashes are stored in MySQL), checks the fixed expiry and idle deadline, and deletes sessions on logout. After login, signed cookies contain only the ID and Flask's permanent flag; current user data lives in `g.admin_user`, not the cookie. Defaults are 8 hours absolute (`ADMIN_SESSION_LIFETIME_SECONDS`, 60-86400) and 30 minutes idle (`ADMIN_SESSION_IDLE_SECONDS`, 60-absolute lifetime). Requests update activity but never renew cookies or absolute expiry. `update_user()` atomically increments `session_version` with any password, permission, active-status, or administrator-status update; every older session fails even if a change is later reverted. User deletion cascades to sessions. Old pre-upgrade cookies are rejected. Direct SQL security changes must increment `session_version` atomically too.

`backend/admin_csrf.py` derives authenticated CSRF tokens using HMAC-SHA256 over the opaque session ID with a domain-separated label and the session signing secret; no schema change or cookie renewal is needed. `/admin/api/session` returns proof only after validating the active session. A pre-login random token lives in the signed cookie and hidden login form field; validation does not issue missing tokens, and login clears this state before creating the authenticated session. Tokens are compared in constant time; proof from a different, expired, rotated, or revoked session is not accepted. The central session guard enforces proof on all mutations, including login and logout, before invoking handlers. User-facing chat/feedback routes never use admin cookies; their anonymous ownership proof is separate.

Explicit development uses an HTTP-compatible `local_chatbot_admin_dev` cookie; production uses a Secure `__Host-local_chatbot_admin` cookie and refuses non-HTTPS admin requests. Both are host-only, Path `/`, HttpOnly, SameSite=Lax, with fixed expiry and no refresh per request. Admin responses are no-store; production HTTPS responses include HSTS. Admin pages must be served on their own origin, not opened as files. `ADMIN_ALLOWED_ORIGINS` is a configured allowlist in addition to the same-origin requirement; no admin CORS is enabled. User CORS and voice handshakes use `UI_ALLOWED_ORIGINS`; malformed/opaque/unapproved origins receive 403, while originless HTTP health/native requests remain supported.

Both apps use `security.TrustedProxyMiddleware`. Forwarding is disabled by default (`TRUSTED_PROXY_HOPS=0`). Enabling it also requires explicit `TRUSTED_PROXY_CIDRS`: the actual socket peer and every intermediary in the configured rightmost `X-Forwarded-For` suffix must be trusted proxy addresses. Only then may that suffix's client IP update `REMOTE_ADDR` and its corresponding HTTP(S) `X-Forwarded-Proto` entry update the scheme. Invalid/short/untrusted address chains preserve both socket identity and scheme. `get_client_ip()` never reads headers directly. Host, port, prefix, `Forwarded`, and `X-Real-IP` are never used for forwarding; Gunicorn's implicit scheme trust stays disabled. Keep upstream listeners private, have the public proxy overwrite incoming forwarding headers, and configure subsequent trusted proxies consistently with the fixed hop count. See README for deployment examples.

`abuse_controls.py` uses that same validated `request.remote_addr` (normalizing IPv4-mapped IPv6), never usernames, cookies, or chat `user_id`. Login POST admits 5 attempts/minute; chat 20/minute; JSON/PDF uploads share 6 attempts/5 minutes; voice handshakes 6/minute; voice-health polling 60/minute. Each group allows only one in-flight request per IP. Origin/session/CSRF/permission guards remain in place; admission decorators run before expensive handlers. Teardown releases concurrency slots even on failures and disconnects. Rejections return 429, `Retry-After`, no-store, and retry guidance (HTML with the existing CSRF form for login, JSON otherwise). Active voice limits send `error` messages with `code=rate_limited` and `retry_after`; pending transcription retains the existing global semaphore until completion. Sliding windows use a monotonic clock and are not extended by rejected attempts. Each group stores at most 4096 client entries, reclaims expired inactive entries at capacity, and refuses new clients temporarily rather than evicting live limits. State is thread-safe, process-local, and resets on worker restart; this matches the required single-worker runtime. There are no account-level lockouts or account mutations. See README's abuse-control section for recovery and scaling caveats.

## Request validation and resource limits

`request_validation.py` provides strict schemas and a bounded Flask request class on both apps. The origin guard runs first, then declared body/query-size checks before session lookup; existing session/CSRF/permission and admission checks remain required. Non-upload bodies are capped at 64 KiB. Multipart uploads allow their file quota (20 MiB JSON, 50 MiB PDF) plus 64 KiB framing overhead, with at most 8 parts and 128 KiB form-field/parser memory. Per-file write quotas apply during multipart parsing (including form-based CSRF), spooling to disk after 512 KiB; handlers also use `read(limit + 1)`. Request teardown closes even partially parsed upload streams. Unknown-length input is bounded through Werkzeug when the WSGI server marks it terminated; without that signal, Werkzeug refuses unbounded reads. Upstream connection/time/body limits remain necessary.

Accepted JSON must be an object with exact field types and no unexpected or duplicate keys. Malformed UTF-8/JSON, non-finite constants, and excessive nesting are rejected. Chat text is a nonblank string up to 4096 characters; feedback IDs are positive signed-BIGINT JSON integers (never bool/float/string). Model/action IDs are nonblank strings up to 64 characters. Credential usernames/passwords are capped at 64/1024 characters; account creation requires 3/8 minimums, and passwords are never trimmed. Account flags require booleans and permission lists allow at most 32 names of 64 characters (legacy `"*"` remains accepted). JSON imports validate all rows before model work, with 256-row and 4096/16000-character question/answer bounds. PDFs enforce page/text/chunk quotas before embedding or replacement. See ingestion pipelines above.

Document query schemas reject (rather than clamp) `page` outside 1-10000 or `per_page` outside 1-100. Chat-session `limit` is 1-1000, search `q` is at most 256 characters, and total query strings are at most 8 KiB. Conversation identifiers and resource IDs are bounded before database queries; duplicate and unknown query fields are rejected on these operations. Input failures share JSON `{error, code}`: 400 `invalid_request`, 413 `payload_too_large`, or 415 `unsupported_media_type`. Authentication/authorization/rate-limit failures retain their own status. `backend/tests/test_request_validation.py` exercises real routes with model/database forbidders, including streamed bodies, parser quotas/cleanup, mixed-invalid imports and successful bounded ingestion.

## Anonymous message ownership

`chat_sessions.py` establishes a random 256-bit chat identity when the served user page loads, or on the first valid chat POST for native clients. A dedicated signed cookie holds the opaque identity; its signing key is HMAC-derived from `ADMIN_SESSION_SECRET` with a separate chat-only domain. Chat cookies cannot be substituted for administrator cookies or vice versa. Cookies are host-only, HttpOnly, Path `/`, SameSite=Lax, expire after a fixed 24 hours, and do not refresh on reads. Development uses `local_chatbot_chat_dev`; production uses Secure `__Host-local_chatbot_chat` over HTTPS. No credentialed cross-origin CORS is enabled; serve the browser UI from the user service's origin, and retain cookies in native API clients.

Each new message stores only a domain-separated SHA-256 `owner_hash`, never the raw identity. The public grouping label is `chat_` plus the hash's first 32 hex characters; neither that label nor the hash is accepted as proof from request fields. The current signed cookie determines every feedback query's owner. Ownership survives page reloads and client IP changes, not cookie loss/expiry or signing-secret rotation. `init_history_table()` adds the nullable ASCII `owner_hash` column on upgrades without rewriting history; legacy client-chosen `user_id` values cannot safely establish ownership, so old rows remain unowned and cannot receive anonymous feedback. Authorized administrators may still inspect/delete those rows. New public conversation operations must scope queries by the verified owner hash; only explicitly authorized administrative operations may cross owner boundaries.

## Public errors and browser headers (F011)

`public_errors.py` defines stable public codes/messages. `security.configure_security()` normalizes every HTTP failure on both apps to allowlisted `{code, error}` JSON, discarding arbitrary error text, nested details, and exception descriptions rather than relying on secret redaction. Only known voice `status` and bounded rate-limit `retry_after` fields survive. HTTP status and protocol/CORS headers remain intact. Failures are no-store and carry `X-Error-Code`; the known HTML login 401/429 forms retain their HTML and expose the code in that header. Voice health/WebSocket failures use explicit public codes; controller asynchronous failures and persisted PDF ingestion failures also use codes. The standalone STT demo no longer returns raw exception strings.

Both apps enforce nosniff, `strict-origin-when-cross-origin`, and a Permissions Policy that disables unused devices while permitting the user app's same-origin microphone. Admin framing is SAMEORIGIN with enforced CSP `frame-ancestors 'self'`; user framing is DENY with `'none'`. Production HTTPS on either service receives HSTS, using only the trusted scheme. Do not use `Referrer-Policy: no-referrer`: browser form submissions can then send an opaque Origin and fail the login origin guard.

The rest of CSP is sent only as `Content-Security-Policy-Report-Only`. The candidate allows local scripts/worklets, styles (including current inline styles), same-origin fetch and configured WebSocket origins, and same-origin/blob iframe PDF previews. No telemetry endpoint is exposed; evaluation uses browser console/`securitypolicyviolation` events without uploading URLs/samples. Chrome and Edge smoke evaluation found only inline-script/handler reports, with no enforced violations across chat/feedback, fake microphone + real worklet/voice socket, login/logout, admin iframe/pages, CSRF mutations, and blob PDF preview. The controller-injected feature-state script and existing inline click handlers need migration/nonces/hashes before full CSP enforcement. See README for rollout gates and commands.

`backend/tests/test_safe_errors_headers.py` covers injected exception/response leaks, protocol headers, authentication/validation codes, voice failures, MIME compatibility, iframe policy and production-only trusted-scheme HSTS. `backend/tests/browser_security_smoke.py` + `.cjs` optionally exercise installed Chrome/Edge with Playwright and offline model/DB doubles on loopback ports 5000/8000. They are not part of default unittest discovery and never use real model weights/accounts.

## Privacy and retention (F012)

`settings.RetentionPolicy` / `retention_settings()` validate finite environment-controlled lifetimes: `RETENTION_CHAT_DAYS=30`, `RETENTION_IP_DAYS=0`, `RETENTION_FEEDBACK_DAYS=30`, `RETENTION_LOG_DAYS=14` (each 0-3650). Chat/replies support authorized quality/support review, feedback supports answer evaluation, optional IPs support troubleshooting (not ownership), and logs support security/operations. Zero disables new storage for that category and purges old data, never means forever. Whole-chat deletion also erases grouping/owner/IP/feedback; IP/feedback ages are anchored to message creation and cannot outlive their chat row. Existing schema is sufficient: erased IPs use the non-null empty-string sentinel, and feedback/timestamp are cleared together. Application logs no longer copy message IDs, rating values, or arbitrary error bodies.

`retention.run_retention()` obtains a database-scoped named lock, sets its connection to UTC, takes one DB-clock cutoff, and hard-deletes expired `chat_history` / `adminlogger` rows and clears expired IP/feedback fields. Null legacy timestamps expire; zero-day categories purge even future-dated rows. Fixed SQL allowlists and ordered LIMIT batches avoid reading personal data into Python. Each batch commits independently; failures roll back the current batch and retain only committed counts. Defaults: 1000 rows/batch, 100 batches/category, 3600 seconds between sweeps (`RETENTION_BATCH_SIZE`, `RETENTION_MAX_BATCHES`, `RETENTION_INTERVAL_SECONDS`). A full final batch conservatively reports `partial` for monitoring/retry. One shared daemon starts in `main.create_apps()` after initialization; `serve_development()` idempotently starts it for standalone runners too. The master/import paths never start cleanup.

`RETENTION_DELETE_ENABLED=0` pauses automatic/manual retention-job mutations, not collection or existing authorized admin erasure routes; it is not record-level legal holds. `python -B backend/retention.py --dry-run` (also the CLI default) previews independent aggregate counts without DB writes; `--apply` honors that pause. No models load. Each invocation emits a `privacy_retention` completion record with timestamp/status/counts/flags/fixed failure code only, never text/IPs/identifiers/SQL/exception messages. Applied-job records also use the normal expiring DB logger when enabled/available; Python log output remains available otherwise. No indefinitely retained special archive is created. Monitor failed/partial/locked sweeps; expiry can lag until successful cleanup. Defaults apply to existing rows on next startup; preview first or explicitly pause before upgrade.

`ui_index()` now renders `frontend/index.html` with the effective policy; `/index.html` redirects to `/` so it does not serve the template as a static page. The Persian privacy notice explains purposes/ages, pause status, cookie lifetime, authorized early-deletion requests, and external backup/log policies. The visible voice notice distinguishes server-side local Whisper (bounded RAM audio, no recording files; submitted transcripts follow chat retention) from potentially network-backed browser speech playback. Playback is opt-in per page, unchecked by default, and unchecking cancels queued speech. Default development/Gunicorn access logging is disabled to avoid another visitor-IP/query-ID copy. Infrastructure logs, database backups/binlogs, exports and speech-provider retention remain operator-managed; restored snapshots require cleanup before serving. Knowledge documents and administrator accounts are not age-deleted.

`backend/tests/test_retention.py` runs translated cleanup SQL against an in-memory store, including expiry/null boundaries, pause/zero-day policies, independent field ages, dry-run, caps/idempotence, lock collisions, rollback/committed counts, safe completion records, configuration bounds and startup wiring. `test_privacy.py` covers actual routes' collection controls and rendered notice plus privacy-safe logs. The optional browser smoke also checks notice visibility and playback opt-in/cancellation. See README for purpose/configuration tables and operational recovery limits.

## Flask/MySQL integration tests (F014)

`python -B backend/tests/run_mysql_tests.py --repeat 2` provisions a fresh MySQL 8.4 Docker container using only `backend/tests/mysql/compose.yaml` and an explicit empty Compose env file. Install `backend/tests/mysql/requirements.txt` (the offline dependencies plus PyMuPDF and PyMySQL's RSA authentication extra). Each invocation uses a random project/schema/password and random loopback-only host port; the non-root test account is scoped to that schema. Inherited deployment `DB_*`/`COMPOSE_*`/`F014_*` settings are ignored. PyMySQL factories and sockets reject every unprovisioned target, including attempts swallowed by Flask's error handlers. The data directory is tmpfs, never a developer volume. Before destructive per-test cleanup, fixtures verify both the provisioned config and `SELECT DATABASE()`. A `finally` removes only that run's project even on failure; no external-database override exists.

`backend/tests/mysql/test_api.py` uses the real table initializers and Flask clients against real MySQL for login/session revocation, forbidden/CSRF actions, JSON/PDF ingestion and dedup/replacement/cascades, knowledge-backed chat persistence/ownership, feedback, and zero retention. Embeddings/generation and speech are doubled, but parsing, PyMuPDF extraction, retrieval, authentication, authorization, logging and SQL are real. Temporary MySQL triggers inject rejected writes; tests check status/public codes, actual stored effects (including failed-PDF state under current autocommit), and recovery. Connection failures are separately injected at connection boundaries. This directory is excluded from default unittest discovery; offline `test_mysql_isolation.py` validates the safety machinery without Docker or network. Missing prerequisites fail explicitly rather than skipping. See `backend/tests/TESTING.md` for setup, coverage and interrupted-run cleanup.

## Browser regression tests (F015)

`python -B backend/tests/browser_smoke.py --channel chrome --repeat 2` runs 18 Playwright scenarios against two ephemeral loopback servers. Install the offline Python test dependencies plus `npm ci --prefix backend/tests/browser`; Node 20+ and installed Chrome/Edge (or separately installed Chromium with `--channel chromium`) are required. The npm lockfile pins Playwright. Missing dependencies/browsers, failed assertions and empty test selection fail explicitly. No real DB, models, microphone, accounts or configured application service is used. Deployment settings are replaced; DB/model access and outbound Python sockets are forbidden. Fresh fixture tokens guard test-only reset/state routes, and the runner owns/cleans its sockets and threads. Browser requests stay on the fixture's two origins.

`browser/journeys.spec.cjs` covers desktop/mobile RTL rendering and sending, quick/recommended questions and pending controls, login/logout and replay rejection, admin/PDF-only/no-grant navigation, theme persistence and origin/tab/iframe behavior, feedback failure/retry/overwrite, and unavailable/unsupported/denied/missing/disconnected microphone states. `browser/audio.cjs` doubles browser capture/worklet/WebSocket APIs to test draft/partial-transcript preservation, cleanup and final-transcript sending without hardware. Real Flask guards and served UI code remain in the loop; persistence/model/voice adapters are in-memory doubles supplied by `browser_support.py`. Each case resets its own fixture state, uses a fresh browser context and has no automatic retries. `test_browser_fixture.py` checks the adapters and isolation in default offline discovery.

The suite detected and now protects fixes for message text inheriting LTR from the bubble container and the unauthenticated login theme script being blocked. `.message` explicitly sets RTL text while preserving bubble alignment. `/login-theme.js` serves the shared backend theme script as an exact public login asset; `/theme.js` and other dashboard assets stay authenticated. The operation-policy matrix includes the new alias.

Each run prints an ignored `backend/tests/browser-artifacts/<id>` folder containing a JSON report and screenshots/traces/error context for failures. `--grep` focuses cases; `--channel msedge` tests Edge. See `backend/tests/TESTING.md` for complete setup and diagnostic instructions. The earlier fixed-port `browser_security_smoke.py`/`.cjs` remains a separate optional CSP/real-worklet/WebSocket compatibility check, not a substitute for this journey suite.
