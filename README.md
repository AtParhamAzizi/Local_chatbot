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

## Docker setup (Windows, macOS, and Linux)

The container includes Python 3.12, an isolated virtual environment at `/opt/venv`,
the main and speech-to-text Python dependencies, and the production server. Compose
also starts MySQL 8.4; you do not need Python or MySQL installed on the host.
Install Docker Engine with Docker Compose v2, or Docker Desktop, and use **Linux
containers**. The default image uses CPU inference, including on Apple Silicon;
allow enough container memory for both models (start with at least 12 GB for CPU
use and increase it if model loading exhausts memory).

1. Copy `.env.example` to `.env` (`Copy-Item .env.example .env` in PowerShell,
   or `cp .env.example .env` on macOS/Linux). Fill in `DB_PASS`,
   `MYSQL_ROOT_PASSWORD`, and `ADMIN_SESSION_SECRET` with distinct, strong random
   values. Use at least 32 characters for the session secret. Keep this file
   private; it is excluded from Git and the Docker build context. Compose reads
   it and injects the configured values; the Python application still does not
   load `.env` files itself.

2. Keep the complete local model downloads in these directories on the host:

   ```text
   backend/model/Qwen3-Embedding-0.6B/
   backend/modelgen/qwen3.5-2B/
   ```

   Both need `config.json`, tokenizer files, and all model weights. Compose mounts
   `backend/model/` and `backend/modelgen/` read-only instead of copying large model
   files into the image. These directories must already exist. The application
   loads these text models offline and does not download missing weights.

3. Build and start both services from the repository root:

   ```sh
   docker compose up --build -d
   docker compose logs -f chatbot
   ```

   Initial dependency installation and model loading can take several minutes.
   Compose waits for the application database account to connect successfully
   before starting the chatbot. Open `http://localhost:5000` for chat and
   `http://localhost:8000` for administration once initialization completes.

4. Create your first administrator interactively (password input is hidden):

   ```sh
   docker compose exec chatbot python backend/setup_admin.py
   ```

   If model initialization is still in progress, you can instead run the same
   setup independently with `docker compose run --rm chatbot python backend/setup_admin.py`.

This Compose configuration is for **local use**: it explicitly enables the
application's development mode for localhost HTTP and publishes both ports only
on host loopback. The Docker image itself defaults to production mode. For a
production deployment, override `APP_ENV=production`, supply both allowed-origin
settings and the trusted HTTPS proxy configuration described below, and use a
least-privilege database account. Do not expose this local configuration publicly.

MySQL data (including accounts and chat history), Whisper downloads, and the Hugging
Face cache use named volumes. `docker compose down` preserves them; adding `-v`
**deletes them**. MySQL initialization variables only apply to a fresh database
volume; changing `.env` does not rotate an existing database password. Existing
host MySQL data is not migrated automatically. Model-selection and controller
preferences stored in the container's backend directory survive a restart but
reset when that container is replaced. Whisper may download its model on first
voice use; the text models remain local. To move to another computer, transfer
the repository and model directories, configure private credentials there, and
back up/restore the database separately if you want to retain existing data.

### Optional NVIDIA GPU container

On a compatible NVIDIA host with GPU access configured for Docker, use:

```sh
docker compose -f compose.yaml -f compose.gpu.yaml up --build -d
docker compose -f compose.yaml -f compose.gpu.yaml exec chatbot python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

The GPU override selects the existing CUDA 11.8 dependency profile, including
matching PyTorch/TorchAudio, Accelerate, and bitsandbytes, and requires an NVIDIA
GPU rather than silently falling back to CPU. This optional profile targets
Linux x86-64 containers and needs a compatible NVIDIA driver and container GPU
runtime (on Windows, use Docker Desktop's supported WSL2 GPU setup). macOS users
should use the default CPU configuration; this does not provide Apple Metal GPU
acceleration. Voice inference stays on CPU because the pinned text-generation
CUDA profile does not provide the separate CUDA/cuDNN setup required by current
faster-whisper GPU inference. Use both `-f` options for subsequent GPU Compose
commands, including administrator setup, logs, and shutdown.

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

3. Create the MySQL database and a restricted account as described in **Deployment security**. Configure the connection and session signing secret through the process environment or your deployment secret manager; `.env` files are not loaded automatically.

   | Variable | Default / requirement |
   | --- | --- |
   | `APP_ENV` | `production`; explicitly set `development` for local use |
   | `APP_HOST` | `127.0.0.1` for both services; LAN binding requires an explicit IP address such as `0.0.0.0` |
   | `DB_HOST` | `localhost` |
   | `DB_PORT` | `3306`; integer from 1 to 65535 |
   | `DB_USER` | Required outside development; local default `chatbot_app` |
   | `DB_PASS` | Required outside development; no password fallback |
   | `DB_NAME` | Required outside development; local default `chatbot_db` |
   | `ADMIN_SESSION_SECRET` | Required outside development; at least 32 random characters |
   | `ADMIN_SESSION_LIFETIME_SECONDS` | `28800` (8 hours); integer from 60 to 86400; absolute login lifetime |
   | `ADMIN_SESSION_IDLE_SECONDS` | `1800` (30 minutes); integer from 60 to the absolute lifetime |
   | `UI_ALLOWED_ORIGINS` | Required in production; comma-separated exact HTTP(S) browser origins for the user service |
   | `ADMIN_ALLOWED_ORIGINS` | Required in production; comma-separated exact HTTP(S) dashboard origins; admin requests remain same-origin only |
   | `TRUSTED_PROXY_HOPS` | `0`; exact trusted chain length for `X-Forwarded-For` and `X-Forwarded-Proto` (0-10); nonzero requires `TRUSTED_PROXY_CIDRS` |
   | `TRUSTED_PROXY_CIDRS` | Empty; comma-separated trusted reverse-proxy IP addresses or IPv4/IPv6 CIDRs |

   Inject `DB_PASS` and `ADMIN_SESSION_SECRET` securely rather than saving them in source code or shell history. Generate the session secret with `python -c "import secrets; print(secrets.token_urlsafe(48))"` in a private terminal. Do not paste its output into logs. Local development without a session secret uses a per-process random key, so restarting invalidates existing admin sessions.

   The application creates its tables automatically at startup.

   Before the first admin login, run local interactive setup using the same database configuration:

   ```powershell
   python backend/setup_admin.py
   ```

   Choose and confirm a unique administrator password (at least 12 characters). There is no default password, and startup never seeds an account. You may safely rerun setup after interruption: uncommitted creation rolls back, and existing accounts are never modified. On upgrades, skip this step and use your existing credentials.

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

### Local development (Windows or Linux)

From the repository root, explicitly select the local profile:

```powershell
$env:APP_ENV = 'development'
python backend\main.py
```

On Linux, use `APP_ENV=development python backend/main.py` instead. Both services
bind only to `127.0.0.1` by default. Debug mode, the interactive debugger, and the
automatic reloader stay disabled, even if `FLASK_DEBUG=1` is set.

For VS Code, select **Chatbot: Local development** in Run and Debug and press
F5. The supplied `.vscode/launch.json` explicitly sets the development profile
and loopback address, using your selected Python interpreter. Activate/select
the project `.venv` first. Launching the file without that configuration still
defaults to production and requires the production settings; startup now lists
missing configuration names without printing secret values.

To require GPU generation and use the project CUDA environment for local development, run:

```powershell
$env:APP_ENV = 'development'
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

PDF uploads are limited to 50 MiB, 200 pages and 256 chunks. JSON knowledge-base
uploads are limited to 20 MiB and 256 records; every row must be valid. See
"Request schemas and resource limits" for text, body and pagination bounds.

### Production (Linux or WSL)

The default `APP_ENV=production` profile uses Gunicorn, never Flask's development
server. Native Windows is supported for local development only; production
startup there exits with guidance rather than falling back to Flask. In WSL,
create a separate Linux virtual environment rather than reusing Windows `.venv`.

After completing database, administrator, model, and secret setup above, run
from the repository root in a Linux/WSL shell:

```sh
python -m pip install -r requirements-production.txt
export APP_ENV=production
# Inject DB_USER, DB_PASS, DB_NAME, and ADMIN_SESSION_SECRET securely first.
python backend/main.py
```

Both existing ports remain available: user HTTP and `/voice-stream` WebSocket
on `127.0.0.1:5000`, and the admin dashboard/API on `127.0.0.1:8000`.
One Gunicorn worker with eight native threads serves both listeners and selects
the app by the socket's local port, not the client-supplied Host header. Database
initialization and model loading happen inside that worker, after fork; CUDA
is not initialized in the master. Both apps share the same loaded models,
controller state, and transcription limit. Do not run separate workers/apps or
enable preloading: this duplicates model memory and splits in-process state.
Each open WebSocket occupies a thread; the remaining threads can serve HTTP.
The worker boot/silence timeout is 300 seconds. Run under your service manager
for automatic restart and process supervision; stop with Ctrl+C or SIGTERM.

LAN access is **opt-in** in either profile: set `APP_HOST` to a specific local
interface IP, or `0.0.0.0` for all IPv4 interfaces (`::` for IPv6). For example,
`export APP_HOST=192.168.1.10` on Linux or
`$env:APP_HOST = '192.168.1.10'` in PowerShell. This exposes **both** services;
restrict port 8000 with firewall rules and do not expose the admin dashboard
publicly. Invalid/blank addresses fail startup. To restore loopback binding,
unset `APP_HOST`. The standalone STT demo is not the production entry point.

### Browser origins

Production requires explicit `UI_ALLOWED_ORIGINS` and `ADMIN_ALLOWED_ORIGINS`,
for example `https://chat.example.com` and `https://admin.example.com` respectively.
List origins as `scheme://host[:port]`, separated by commas, without trailing
slashes, paths, credentials, query strings, or fragments. Wildcards, regular
expressions, `null`, and `file:` origins are not accepted. Scheme and port are
part of the policy; subdomains are not implicitly approved. Invalid or empty
lists fail startup. Nonstandard browser-facing ports must be included.

Explicit development defaults to `http://localhost:5000`,
`http://127.0.0.1:5000`, and `http://[::1]:5000` for the user service, with the
same hosts on port 8000 for admin. Setting a list replaces these defaults.
LAN deployments must configure the actual browser origins as well as `APP_HOST`.
Open both UIs through the served pages, not by opening HTML files directly.

Requests carrying an unapproved, opaque, or malformed `Origin` receive 403
before route handlers, login/session checks, model work, or WebSocket upgrade.
Approved user origins receive explicit CORS response headers (never `*` or
credentialed CORS), including on application errors. User preflights allow only
GET/HEAD/POST/OPTIONS and the `Content-Type` request header. Admin permits only
listed origins that also match the request's own scheme, host, and port; it
does not enable cross-origin CORS or bypass CSRF proof. HTTP requests without an
`Origin` remain available for health checks, native clients, and same-origin
navigation; origin checks are not authentication. `/voice-stream` requires an
approved `Origin`, enforced before the handshake using the same user policy.

### Reverse proxies

For access beyond localhost, terminate HTTPS at a reverse proxy such as nginx;
browser microphone access requires HTTPS. Keep `APP_HOST=127.0.0.1` when the
proxy runs on the same machine. Proxy the user origin to port 5000 and a
separately restricted admin origin to port 8000. Preserve the original `Host`
(including any nonstandard port), use HTTP/1.1 upstream, and forward the
`Upgrade` and `Connection` upgrade headers for `/voice-stream`. Disable response
buffering for that route and allow at least a 300-second proxy read timeout.
Do not substitute an HTTP-only server such as Waitress for the user listener:
the existing Flask-Sock voice endpoint needs WebSocket support.

Production administrator access requires HTTPS; plain HTTP requests are rejected
before login credentials are processed. For one local TLS-terminating proxy, set
`TRUSTED_PROXY_HOPS=1` and `TRUSTED_PROXY_CIDRS=127.0.0.1/32,::1/128`. For a
remote proxy, list its actual socket address/network instead. Hop-only legacy
configuration now fails startup. Keep upstream ports private and restrict trust
to reverse proxies you control, never entire client networks.

The proxy must overwrite incoming client forwarding headers with authoritative
values. For a single nginx proxy, use:

```nginx
proxy_set_header Host $http_host;
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header X-Forwarded-Proto $scheme;
```

The middleware first checks the actual socket peer against `TRUSTED_PROXY_CIDRS`.
For multi-proxy deployments it also validates each intermediary in the rightmost
configured `X-Forwarded-For` suffix; missing, malformed, short, or untrusted chains
leave the socket client address and scheme unchanged. The first public proxy
must overwrite forwarding headers; subsequent trusted proxies must append to
both forwarding chains, with the configured hop count matching the deployment.
Attacker-prepended values outside that suffix are ignored. The validated client
address becomes `request.remote_addr`, used by chat-history/audit identity.
Forwarded scheme is accepted only with a validated address chain and a matching
HTTP(S) scheme entry; it cannot independently bypass the admin HTTPS requirement.

The default hop count `0` ignores all forwarding headers. `Forwarded`,
`X-Real-IP`, and forwarded host/port/prefix headers are never trusted. Host and
listening port are never rewritten; preserving the upstream listening port is
required by the app dispatcher. Gunicorn's implicit forwarded-scheme trust is
disabled so both services use this policy. Redirect external HTTP to HTTPS at
the proxy, not at the application. Production admin HTTPS responses set HSTS
for one year.

Smoke-check `GET /health` on port 5000 and the login page on port 8000. Then
open the served chat page on localhost or HTTPS, wait for `/voice/health` to
report ready (503 while loading), and record/send a voice message. The browser
network panel should show a 101 upgrade for `/voice-stream`; check that text
chat still works while the WebSocket is open and that the final transcript
uses the normal `/chat` path. These checks need the configured DB and models.

## API

The user service exposes:

- `GET /health` — service health check.
- `POST /chat` — retrieves knowledge and generates a response. Send JSON with `message`; the server establishes the anonymous session and ignores any supplied `user_id`. Every successful response includes a saved chat-history `message_id`, including fallback replies. Keep the response's chat cookie for feedback and subsequent messages.
- `POST /feedback` — send `{"message_id": 123, "feedback": "like"}` (or `"dislike"`) using the ID returned by `/chat` and the same chat cookie. Only the originating session can rate the message; submitting another rating updates its previous feedback. Invalid requests return 400, missing/invalid/expired session proof returns 401, unknown or non-owned messages return the same 404, and database failures return 503 so the UI can offer a retry.

Administrator endpoints are available under `/admin/api/` on port 8000 for knowledge-base management, PDFs, logs, and chat history.

### Message ownership and upgrade behavior

The served user page initializes a server-issued anonymous chat session; native
clients can establish it with their first valid `/chat` POST. Retain the cookie
jar across chat and feedback requests. The browser UI uses same-origin cookies,
not client-generated user IDs. No public conversation read/delete API is exposed.
Administrator conversation inspection and deletion still require an authenticated
account with the `chats` permission (or the administrator role); knowing message
IDs or public user labels does not grant that access.

The dedicated chat cookie is signed with a chat-specific key derived from the
configured session secret, is host-only and HttpOnly, uses SameSite=Lax and
Path `/`, and has a fixed 24-hour lifetime without refresh on reads. Production
uses Secure `__Host-local_chatbot_chat` and requires HTTPS for cookie use;
explicit local development uses `local_chatbot_chat_dev`. Administrator cookies
and signatures cannot be substituted for chat proof. Serve the browser UI from
the user service's origin; credentialed cross-origin CORS is not enabled.

Only a hash of the random chat identity is stored with each message. Ownership
is not based on IP addresses, supplied `user_id` fields, or message IDs. Page
reloads and network changes preserve ownership while the cookie remains valid.
Clearing/losing the cookie, its expiry, or rotating `ADMIN_SESSION_SECRET`
prevents feedback on the old session's messages; those messages cannot be
reclaimed by supplying their public user ID. Treat the cookie as a credential.

Normal startup automatically adds nullable `chat_history.owner_hash` on existing
installations using the documented schema-scoped `ALTER` grant. Existing history
and feedback are preserved. Older rows cannot safely be assigned an owner from
their formerly client-chosen IDs, so they remain unowned and no longer accept
anonymous feedback; authorized administrators retain inspection/deletion access.
Restart through `backend/main.py` to apply the migration before serving requests.

## Project layout

```text
backend/
  main.py             Initializes shared services and selects the runtime profile
  serving.py          Local Flask serving and threaded production Gunicorn runtime
  settings.py         Validated environment, loopback binding, and credentials
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
- Non-development deployments require explicit `DB_USER`, `DB_PASS`, `DB_NAME`, and `ADMIN_SESSION_SECRET` values. Never use the MySQL `root` account for the application.
- Debug mode and exception propagation are disabled. `APP_ENV=production` uses the production server; Flask development serving requires explicit `APP_ENV=development`.


## Admin authentication

The admin service runs on port 8000 and opens a login page before serving `backend/dashboard.html`. Startup creates the MySQL table named `admin-users` without inserting accounts. Run `python backend/setup_admin.py` locally to create `administrator` with your own password on a clean installation. Setup requires an interactive terminal and refuses to run if any account already exists, including disabled accounts. It cannot reset or overwrite upgrade accounts. If interrupted, rerun it safely; creation is transactional and concurrent setup commands are serialized. Existing installations retain their credentials and permissions; if still using the old shared password, change it in **Admin user control**. This menu also lets administrators create users and assign menu permissions. See `backend/admin-users/README.md` for setup and resumption details.

### Session lifecycle and cookies

- A login expires after 8 hours, or after 30 minutes without an authenticated request, whichever comes first. Active dashboard polling counts as activity but never extends the absolute deadline. Both timeouts are configurable with the settings above; restarting is required after changing configuration.
- After login, the browser receives a signed, expiring cookie containing only an opaque random session ID. MySQL `admin_sessions` stores its SHA-256 hash and deadlines. Normal requests do not renew the cookie. Expired or missing records fail closed; expired records are removed on access and during login.
- `POST /logout` revokes the server-side session before clearing the cookie, so replaying a copied cookie fails. Signing in again rotates and revokes that browser's previous session. Other browsers remain signed in until expiry or an account security change.
- Password resets and changes to permissions, administrator status, or active status through `update_user()` atomically increment the account's `session_version`, invalidating every existing login, including the caller's own login. Re-enabling an account or restoring old permissions does not restore old sessions. Deleting a user cascades deletion of their session records. Operational SQL password/security changes must also increment `session_version` in the same statement.
- All cookies are host-only, `HttpOnly`, `SameSite=Lax`, and scoped to `/`. Explicit local development uses `local_chatbot_admin_dev` without `Secure` for `http://localhost:8000`. Production uses `__Host-local_chatbot_admin` with `Secure` and requires HTTPS. There is no production switch to permit insecure cookies. Admin responses use `Cache-Control: no-store`.
- Open admin pages through the served dashboard on the same origin, not as `file:` pages. Credentialed `Origin: null` CORS is no longer enabled. The chat inspector now uses the dashboard's origin, including HTTPS, rather than a hard-coded localhost API.

Upgrading automatically adds `session_version` to existing accounts and creates
`admin_sessions` without changing passwords or permissions. Legacy session
cookies are not accepted; users must sign in again after upgrading. The existing
schema-scoped migration grants cover these changes.

### Explicit operation authorization

`backend/admin_policy.py` maps exact endpoints/methods, page assets, and controller
actions to named operation policies. Unregistered operations are denied even to
administrators; URL prefixes and menu filenames never imply permission. Existing
feature grants remain compatible, but account-management pages and APIs require
the actual `is_admin` role. Neither the `administrator` username nor wildcard or
`user_control` grants confer that role. Generic static-file aliases are disabled.
The dashboard uses server-computed `allowed_pages` from the session API. Account
deletion prevents deleting the caller by ID instead of protecting a special name.

See `backend/admin-users/PERMISSIONS.md` for the full page/API/controller matrix,
upgrade behavior, and offline allow/deny tests. Run the tests from the repository
root with `.\.venv\Scripts\python.exe -B -m unittest discover -s backend/tests -v`
(or `python -B -m unittest discover -s backend/tests -v` on other platforms).
They use isolated doubles, not your database or model files.

### Administrator CSRF protection

All cookie-authenticated administrator mutations require CSRF proof, including
JSON/PDF uploads, document/chat deletions, model selection, account creation and
updates, controller actions, and `POST /logout`. The common request guard checks
every method except `GET`, `HEAD`, and `OPTIONS`, before dispatching mutations or
granting administrator permission bypasses. Missing or invalid proof returns
HTTP 403; missing, expired, or revoked authentication returns HTTP 401 for APIs
and logout. GET requests never sign users out.

For an API client, retain the login cookies, fetch `GET /admin/api/session`, and
send the returned `csrf_token` in the `X-CSRF-Token` header with every mutation.
HTML/multipart forms may instead send a `csrf_token` form field. Query-string
tokens are not accepted. Tokens are bound to the current login, checked with
constant-time comparison, and stop working after logout, session rotation,
expiry, or account-security revocation. Never put tokens in URLs or logs.

The served dashboard and every admin page load `admin-csrf.js`. Its same-origin
`adminFetch(url, options)` helper obtains and caches proof, preserves JSON headers
and multipart bodies, and attaches proof to mutations without retrying them.
Reload pages after deploying this change; old open pages without proof receive
403 rather than bypassing protection. Login is protected too: first GET `/login`
to obtain its pre-login cookie and hidden `csrf_token`, then submit that field
with the credentials. The pre-login token is discarded on successful login.
These flows work in both explicit localhost development and HTTPS production.

### Login and expensive-request abuse controls

Both services enforce per-client sliding-window limits before expensive handlers:

| Operation | Limit per client IP | Concurrent requests per IP |
| --- | --- | --- |
| Login POST (all credential attempts) | 5 per 60 seconds | 1 |
| Chat | 20 per 60 seconds | 1 |
| JSON and PDF uploads, combined | 6 per 300 seconds | 1 |
| Voice WebSocket handshakes | 6 per 60 seconds | 1 |
| Voice-health polling | 60 per 60 seconds | 1 |

Excess HTTP traffic receives **429** with a positive `Retry-After` delay in
seconds, `Cache-Control: no-store`, and a retry message. APIs also return
`{"code":"rate_limited","retry_after":60,"error":"..."}` (delay varies).
Login re-renders the form with the delay and preserves its CSRF proof. Chat
shows the retry message and restores the unsent text; upload pages display the
API error. Nothing automatically retries mutations. Wait the indicated time
and submit again; a concurrency rejection may require waiting for the current
request to finish. Voice handshake rejection happens before upgrade; browsers
cannot read its HTTP body, so the chat UI advises waiting 60 seconds if limited.

Active voice recordings are limited to 120 seconds of wall time and 120 seconds
of PCM audio, so fast senders cannot bypass the audio budget. Recording limits
and the existing global transcription-busy response send WebSocket `error`
messages with `code: "rate_limited"`, `retry_after`, and a retry message. Slots
are released on completion, disconnect, or error; an already-running
transcription keeps its global slot until it finishes.

Limits use only the socket IP or the IP validated by the trusted-proxy policy,
not arbitrary forwarding headers, usernames, cookies, or chat `user_id`.
Clients behind the same NAT share limits. Login throttling does not change or
lock any account: attacking an account from one IP does not prevent another IP
from signing in. Rejected requests never extend the sliding-window deadline.
Successful attempts also count; clearing cookies does not reset the budget.

Counters are thread-safe, bounded to 4096 client entries per operation group,
and expire automatically. When full, expired inactive entries are reclaimed;
otherwise new clients receive a temporary 429 instead of evicting live limits.
State is in memory and resets when the worker restarts. This matches the
supported single-worker runtime, not a distributed quota. Before adding workers
or replicas, use a shared limiter or enforce matching limits at the trusted
edge. Configure upstream body-size, connection, and request-time limits too;
application throttling is not a substitute for network-level DoS protection.

Offline regression tests cover expiry/recovery without sleeping, concurrency,
bounded storage, spoofed identity, account isolation, authentication/CSRF
preservation, and voice budgets. Run the backend test command documented above.

### Request schemas and resource limits

Both apps use `backend/request_validation.py` to bound bodies before handlers
and validate accepted inputs before retrieval, generation, password hashing,
model changes, or embedding. Existing origin, authentication, CSRF, permission,
and rate checks still apply; their 401/403/429 responses take precedence where
applicable. Declared oversized bodies are rejected before reading or session lookup.

| Input | Limit |
| --- | --- |
| Non-upload request body | 64 KiB |
| JSON / PDF upload file | 20 MiB / 50 MiB |
| Multipart request envelope | File limit plus 64 KiB |
| Multipart parsing | 8 parts; 128 KiB per form field/parser buffer; filename 255 characters |
| Chat message / imported question / imported answer | 4096 / 4096 / 16000 characters; nonblank strings |
| JSON import | 1-256 question/answer objects; every row must be valid |
| PDF processing | 200 pages; 500000 extracted characters; 256 chunks |
| Document pagination | `page` 1-10000; `per_page` 1-100; search `q` at most 256 characters |
| Chat-session listing | `limit` 1-1000; search `q` at most 256 characters |
| Query string | 8 KiB |
| Login/account credentials | Username at most 64 characters; password at most 1024 (whitespace preserved) |

JSON APIs require a JSON object with the documented fields and exact field
types. Unknown or duplicate keys, invalid UTF-8, non-finite JSON constants,
wrong types, and out-of-range values are rejected instead of coerced or clamped.
Feedback IDs must be positive JSON integers, not strings or booleans.
The optional legacy chat `user_id` must be a string of at most 64 characters
and is still ignored for identity. Account flags must be JSON booleans;
permissions must be an array of at most 32 nonblank names (64 characters each)
or the legacy `"*"` value. New usernames/passwords require 3/8 characters.

Uploads require exactly one multipart `file`, with only optional `replace` and
`csrf_token` fields. `replace` accepts `true/false`, `1/0`, `yes/no`, or `on/off`.
File quotas apply while spooling multipart input, including CSRF form parsing,
and reads are capped at the file limit plus one byte. Temporary files close on
success and parser failure. Unknown-length bodies remain bounded when the WSGI
server marks the stream terminated (as for supported chunked requests); without
that signal, Werkzeug does not read an unbounded stream. Keep upstream body-size,
connection and timeout limits as defense in depth.

Client errors consistently return JSON with `error` and `code`:
**400** `invalid_request` for invalid schemas/content, **413**
`payload_too_large` for body/file/processing quotas, and **415**
`unsupported_media_type` for the wrong request content type. Invalid JSON-import
rows now reject the whole upload before any embedding or writes; successful
responses retain `errors: []`. PDF limits are checked before embedding or
replacement. Correct the input, reduce its size, or split a large import before
resubmitting. Offline tests verify these rejections never invoke model work.

## Privacy defaults and retention (F012)

Chatbot operators must publish their own contact details and choose lifetimes
appropriate to their deployment. These defaults are data-minimization choices,
not a legal-compliance guarantee. The served Persian UI explains the actual
configured policy, including a warning when automatic deletion is paused.

| Data | Purpose and access | Default and deletion |
| --- | --- | --- |
| Chat text, replies, anonymous grouping/owner hash | Authorized `chats` operators review answer quality and support issues; not automatic model training | 30 days from message creation, then hard-delete the entire row |
| Feedback and its timestamp | Authorized quality review of the linked answer | 30 days from message creation, never longer than its chat row; clear both fields on expiry |
| Chat IP address | Optional short-lived troubleshooting metadata; never identity/ownership proof | Not stored in new chat history (`''` sentinel); clear existing addresses on cleanup |
| Application audit logs | Authorized `system_reports` operators diagnose errors and security events | 14 days, then hard-delete; new chat/feedback logs omit message IDs, text and rating values |
| Microphone audio | Transcribe speech on the application server using local Whisper | Bounded in-memory buffers only, not stored as audio files; submitted transcripts follow chat policy |
| Anonymous cookie | Prove message/feedback ownership | Fixed 24-hour validity; removing the cookie does not delete stored history |

Browser speech playback is separate from Whisper STT. It uses the browser/OS
`speechSynthesis` voices, prefers a voice reporting `localService`, and can fall
back to a network-backed voice. Playback is **unchecked by default** and the
visible notice warns that enabling it may send answer text to a browser/OS
provider. Turning it off cancels queued playback. Local STT means processing on
the application server, not necessarily the user's device. Whisper can download
model files on first use unless `STT_LOCAL_FILES_ONLY=1`; that is separate from
uploading audio to a transcription vendor. Avoid sending sensitive information.

Configure these environment variables before startup; restart after changes.
Native Python does not automatically load `.env`; export the values in the
launch environment. Docker Compose passes the `.env.example` retention settings
through to the chatbot service.

| Setting | Default | Allowed values / meaning |
| --- | --- | --- |
| `RETENTION_CHAT_DAYS` | `30` | Integer 0-3650; `0` disables new history and purges existing history when deletion runs |
| `RETENTION_IP_DAYS` | `0` | Integer 0-3650; `0` avoids new persisted IPs and clears legacy IPs; positive values opt into storage |
| `RETENTION_FEEDBACK_DAYS` | `30` | Integer 0-3650; `0` disables new feedback and clears existing feedback/timestamps |
| `RETENTION_LOG_DAYS` | `14` | Integer 0-3650; `0` disables new application DB audit logs/fallback copies and purges old DB logs |
| `RETENTION_DELETE_ENABLED` | `1` | `0/1/false/true`; false pauses retention mutations, not collection or authorized manual erasure |
| `RETENTION_INTERVAL_SECONDS` | `3600` | Integer 60-86400 between sweeps |
| `RETENTION_BATCH_SIZE` | `1000` | Integer 1-10000 rows per mutation/commit |
| `RETENTION_MAX_BATCHES` | `100` | Integer 1-1000 batches per category per sweep |

Zero never means indefinite retention. Positive IP/feedback lifetimes are also
bounded by the parent chat lifetime. Changes to feedback do not reset its age.
Deletion uses the database's clock with a UTC session and an inclusive expiry
boundary (`created_at <= cutoff`); missing legacy timestamps are treated as
expired. Zero-day policies purge that category even for future-dated legacy
rows. Whole-chat deletion takes precedence and removes its text, reply, grouping
label, ownership hash, IP and feedback together. No detached feedback copies are
kept. In-memory per-client abuse controls still use validated IPs temporarily;
those bounded process-local entries expire/reclaim or disappear on restart.

**Upgrade behavior:** deletion is enabled by default in both development and
production and applies to existing rows on the next startup. Preview with the
same deployment environment before starting the upgraded application. To pause
first, explicitly set `RETENTION_DELETE_ENABLED=0`; the UI then states that old
data remains pending. This operational pause is not a record-level legal-hold
system. Existing authenticated/CSRF-protected admin conversation/message deletes
still hard-delete the selected rows and associated feedback immediately; they
do not archive them in logs or wait for an expiry job. Users can request early
erasure from an authorized operator. There is no unauthenticated deletion API.

`backend/retention.py` runs automatically in the shared serving worker after
schema/model initialization, immediately and then at the configured interval.
The standalone development runners also start it. Importing modules or running
tests does not start cleanup. Each sweep uses a database-scoped MySQL named lock
so scheduled/manual processes do not overlap, fixed table/column allowlists,
bounded ordered mutations, and one transaction per batch. It never reads chat
text, addresses, owner hashes or individual IDs into Python. It does not touch
knowledge documents, administrator accounts, or model files. Each committed
batch survives a later failure; only the current batch rolls back.

For an external scheduled job or a manual preview, use the same environment as
the server (no model weights are loaded):

```powershell
python -B backend/retention.py --dry-run
python -B backend/retention.py --apply
```

No flags also means dry-run. Dry-run performs only session/lock operations and
aggregate counts, and does not write database audit records. Its per-category
counts can overlap (a chat eligible for deletion can also have an expired IP or
feedback); actual sweeps delete chats first. `--apply` honors the deletion pause.
Exit code 1 reports failure, a possible remaining backlog (`partial`), or an
overlapping job (`skipped_locked`); completed previews/sweeps and acknowledged
policy pauses return 0. A capped sweep conservatively reports `partial` even if
its last full batch happened to clear the backlog. Rerun or increase the batch
budget until a subsequent sweep reports `completed`. Expired rows can remain
until the next successful sweep; monitor failures and backlogs, not just process
uptime. While the service is stopped, use an external scheduler if expiry must
continue.

Each invocation emits a structured `privacy_retention` record containing only
completion time, status, dry-run flag, aggregate committed counts (candidate
counts for previews), batch-limit
flag, and a fixed error code on failure. No text, IPs, user/message IDs, hashes,
SQL, credentials, paths or raw exceptions are included. Records go to the
`chatbot.retention` Python log (CLI also prints JSON); applied-job records also
go to `adminlogger` when DB logging is enabled/available and expire under the
same log policy. Records still reach the Python log when DB logging is disabled
or unavailable. They are not a separate indefinitely retained audit archive.

Deletion is logical deletion from live application tables, not secure media
erasure. Database backups, binary logs, replicas, proxy/container/service logs,
browser/OS speech providers, and exported admin data need their own bounded
retention and erasure procedures. Rotate/expire backups and reapply cleanup
before serving a restored database; old snapshots otherwise restore deleted
data. Application sweeps cannot delete those external copies. Default development
access logging is suppressed and production Gunicorn access logging is disabled
to avoid creating another copy of visitor IPs and query identifiers; configure
any upstream logging deliberately.

Offline tests execute translated cleanup SQL against an in-memory database and
cover boundary/null timestamps, zero-day policies, deletion pause, dry-run,
bounded progress, overlapping locks, committed counts/rollback, safe records,
startup wiring, collection defaults, and rendered notices. The optional browser
smoke test also verifies the visible notice and speech opt-in/cancellation.

## Deployment security

The default environment is `production`. Only explicit `APP_ENV=development` (or `dev`, `local`, `test`) enables development behavior; other values fail closed. Startup fails closed when any of these settings are missing: `DB_USER`, `DB_PASS`, `DB_NAME`, or `ADMIN_SESSION_SECRET`. Unexpected errors return generic messages. Audit logs redact configured database/session secrets, and exception logs record error types rather than raw exception text.

### Public errors and browser headers (F011)

Both services normalize failure responses to allowlisted `{code, error}` JSON.
Clients should branch on `code`, not the human-readable message. Codes include
`invalid_request` (400), `authentication_required` (401), `forbidden` (403),
`not_found` (404), `method_not_allowed` (405), `conflict` (409),
`payload_too_large` (413), `unsupported_media_type` (415), `rate_limited` (429),
`internal_error` (500), and `service_unavailable` (503). Other HTTP statuses retain
their status with `request_failed` or `internal_error`. Exceptions, HTTP exception
descriptions, SQL, paths, and arbitrary `details`/nested diagnostics are never
copied into failures. Safe voice `status` values and bounded rate-limit
`retry_after` values remain available. Existing `Allow`, `Retry-After`, and CORS
headers are preserved. All failures are no-store and include `X-Error-Code`,
including HEAD responses and the login form's HTML 401/429 responses.

Voice failures use `voice_loading`, `voice_unavailable`, or `voice_model_failed`
(503); WebSocket error messages include a public `code` alongside `message`,
including `transcription_failed` and `rate_limited`. Unhealthy voice responses
do not expose model configuration or installation instructions. Controller
background failures expose `model_action_failed`; failed PDF ingestion stores
`pdf_processing_failed`, not exception text. The standalone STT demo also avoids
returning raw model/transcription exceptions.

The following headers apply to pages, assets, redirects, and errors on both apps:

| Header | Policy |
| --- | --- |
| `X-Content-Type-Options` | `nosniff`; served scripts/worklet keep JavaScript MIME types |
| `X-Frame-Options` | Admin `SAMEORIGIN` preserves its dashboard iframe; user app `DENY` |
| Enforced `Content-Security-Policy` | Only `frame-ancestors 'self'` for admin, `'none'` for user |
| `Referrer-Policy` | `strict-origin-when-cross-origin`; preserves same-origin login form origin checks |
| `Permissions-Policy` | Microphone `(self)` on user, disabled on admin; camera, geolocation, payment, USB disabled |
| `Strict-Transport-Security` | `max-age=31536000` only on production HTTPS, using the validated connection/proxy scheme |

The full candidate CSP is **report-only**, not enforced. It allows same-origin
scripts (including the audio worklet), same-origin fetch plus WebSocket origins
derived from each service's configured origin allowlist, same-origin/blob frames
for the dashboard and PDF preview, local/blob media/workers, data/blob images,
and inline styles used by the current UI. It does not permit inline scripts or
`eval`. It deliberately does not force HTTPS upgrades on local development.
There is no report upload endpoint: inspect browser console messages or collect
`securitypolicyviolation` events locally, avoiding storage of potentially
sensitive document URLs or script samples in server logs.

Report-mode evaluation on November 5, 2026, using offline Chrome and Edge smoke tests:
chat, feedback, microphone permission, AudioWorklet loading, voice WebSocket
ready/stop/done, login/logout, dashboard iframe navigation, CSRF mutations, all
admin page scripts, and blob PDF preview passed with **zero enforced-policy
violations**. The candidate reported `script-src-elem` for the controller's
injected inline feature-state script and `script-src-attr` for quick-question
handlers. Admin table delete buttons also use inline handlers. Full CSP
enforcement must wait for these to move to external scripts/listeners or suitable
nonces/hashes and for repeat testing on deployment origins/HTTPS and supported
browsers. Do not copy the report-only policy wholesale into enforcement.

Run the offline regressions with
`python -B -m unittest discover -s backend/tests -v`. For the optional real-browser
smoke test, provide Node.js, an installed Chrome (or `PLAYWRIGHT_CHANNEL=msedge`),
and Playwright. Set `PLAYWRIGHT_MODULE` to an absolute installed `playwright` or
`@playwright/test` module directory if it is outside the normal Node module path,
then run `python -B backend/tests/browser_security_smoke.py`. Stop the running
chatbot first: the smoke test binds loopback ports 5000 and 8000 and uses only
test database/model/account doubles, not live accounts or model weights.

Use a dedicated MySQL account with only the permissions needed by this application. Run database creation and account setup as an administrator, then configure the application with the restricted account:

```sql
CREATE DATABASE chatbot_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'chatbot_app'@'localhost' IDENTIFIED BY '<strong-random-password>';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES
  ON chatbot_db.* TO 'chatbot_app'@'localhost';
```

The schema-scoped `CREATE`, `ALTER`, `INDEX`, and `REFERENCES` grants support automatic table initialization and the logger column upgrade. No global, account-management, `FILE`, or grant-option permissions are needed. For a remote database, replace `localhost` with the specific application host, not `%`.

Rotate the formerly embedded database password and session signing key on existing deployments; removing them from source does not revoke credentials. Changing the session key invalidates existing admin sessions. Keep passwords out of version control, command-line arguments, and logs.

Set `DB_USER=chatbot_app`, `DB_PASS=<strong-random-password>`, and `DB_NAME=chatbot_db`. Do not grant `ALL PRIVILEGES`, and do not run the application as `root`.
