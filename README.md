# Local RAG Chatbot

A local, Persian-language retrieval-augmented generation (RAG) chatbot with a user chat interface and an administrator dashboard. It stores question-and-answer records, PDF knowledge-base chunks, chat history, feedback, and application logs in MySQL.

Start with [contributor setup](CONTRIBUTING.md) for code or documentation work;
it needs no database or model downloads. For a running application, choose
[Docker setup](#docker-setup-windows-macos-and-linux) or [native setup](#setup).
See [operations](docs/operations.md), [troubleshooting](docs/troubleshooting.md),
[architecture](ARCHITECTURE.md), [API contracts](docs/api/README.md), and
[architecture decisions](docs/decisions/README.md) for the corresponding guides.

## User interfaces

The project now includes a refreshed **Liquid Glass** visual experience:

- Translucent glass panels with blur, gradients, soft borders, and luminous accents across the user chat and administrator pages.
- A clickable dark/light theme toggle on the frontend, dashboard, chat inspector, PDF/JSON tools, and system logs.
- Theme selection persists across reloads and stays synchronized between admin pages.
- The authenticated Model Selection page supports `qwen3.5-2b` (the default) and `qwen3-4b` when their local model files are available. Gemma folders and loader compatibility do not make Gemma an active catalog entry.
- Responsive layouts have been refined for desktop and mobile screens.

## Features

- Semantic retrieval using the local `Qwen/Qwen3-Embedding-0.6B` embedding model.
- Answer generation using the local `Qwen/Qwen3.5-2B` model by default.
- JSON question-and-answer ingestion through the administrator dashboard.
- PDF upload, text extraction, chunking, embedding, search, and deletion.
- Source metadata, recommended questions, chat-history recording, and feedback.
- Separate user interface (`http://localhost:5000`) and administrator dashboard (`http://localhost:8000`).

## Requirements

- CPython 3.13 x64 on Windows or CPython 3.12 x86-64 on Linux for the recorded dependency locks; other targets are not in the tested lock matrix.
- A running MySQL server; the integration suite and local Compose use MySQL 8.4.
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

3. Build, explicitly migrate the fresh database, then start from the repository root:

   ```sh
   docker compose build chatbot
   docker compose run --rm chatbot python backend/schema_migrations.py upgrade
   docker compose up -d chatbot
   docker compose logs -f chatbot
   ```

   For an existing database, stop the chatbot and follow the backup/upgrade
   procedure in `backend/migrations/README.md` instead of the fresh upgrade command.
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

On a compatible NVIDIA host with GPU access configured for Docker:

Complete the configuration, model directories and explicit migration steps
above first, using both `-f compose.yaml -f compose.gpu.yaml` on each Compose
command when choosing the GPU image. Once the schema is current:

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

   On Linux, use the recorded CPython 3.12 interpreter:

   ```sh
   python3.12 -m venv .venv
   . .venv/bin/activate
   ```

   The following `python` commands assume that environment is active. If
   PowerShell blocks activation, invoke `.\.venv\Scripts\python.exe` explicitly
   instead. Run commands from the repository root.

2. Install Python dependencies.

   ```powershell
   python -B scripts/dependencies.py install --profile cpu
   python -B scripts/dependencies.py verify --profile cpu
   ```

   Use a fresh environment, not an existing application's environment. For NVIDIA GPU support on older GPUs such as the 4 GB GTX 1050, select the separate CUDA 11.8 profile **instead of** installing the CPU profile:

   ```powershell
   .\.venv\Scripts\python.exe -B scripts/dependencies.py install --profile cu118
   .\.venv\Scripts\python.exe -B scripts/dependencies.py verify --profile cu118
   ```

   This profile hash-pins PyTorch and TorchAudio to 2.7.1+cu118 plus the complete native dependency graph. GPU verification must pass actual FP16/NF4 execution, not just detect a CUDA build. The wheel includes its CUDA runtime; a separate CUDA Toolkit installation is not required. An NVIDIA driver compatible with CUDA 11.8 is required. If CUDA initialization fails, check the driver. Do not install the GPU profile into a shared environment with incompatible `torchvision`/`torchaudio` versions; use a fresh project virtual environment. A mismatched TorchAudio installation can cause `WinError 127` even during text-model loading because Transformers imports optional audio utilities. Keep packages with conflicting Transformers requirements, such as `qwen-tts`, in a separate virtual environment.

   The generator selects FP16 compute on GPUs without native BF16 support, including the GTX 1050. Qwen3.5 loads only its text-generation weights, not its unused vision encoder. On GPUs with 6 GiB VRAM or less, it automatically uses 4-bit NF4 weights through bitsandbytes to leave room for CUDA and generation; even text-only FP16 weights need about 3.51 GiB before runtime memory. The CUDA profile pins bitsandbytes 0.47.0 for CUDA 11.8/Pascal support. `GEN_QUANTIZATION` accepts `auto` (default), `4bit`, or `none`. Quantization can slightly change model outputs. Long prompts can still exhaust a small GPU; close other GPU applications or shorten the context if needed.

   See `requirements/README.md` for the Windows/Linux lock matrix, offline wheelhouses, upgrade/rollback process, other-platform limits and recorded validation. The legacy bounded `requirements*.txt` files are resolver inputs, not release locks. x86-64 Docker builds use these same hash-pinned profiles; ARM64 CPU builds retain a separately documented constrained route.

3. Have a database administrator create the database, serving account and
   separate migration account using the SQL in [Deployment security](#deployment-security).
   For a fresh local installation, configure the environment **before** running
   any configuration, migration or account-setup command. `.env` is consumed by
   Compose, but is not loaded by native Python or `run_gpu.cmd`.

   | Variable | Default / requirement |
   | --- | --- |
   | `APP_ENV` | `production`; explicitly set `development` for local use |
   | `APP_HOST` | `127.0.0.1` for both services; LAN binding requires an explicit IP address such as `0.0.0.0` |
   | `DB_HOST` | `localhost` |
   | `DB_PORT` | `3306`; integer from 1 to 65535 |
   | `DB_USER` | Required outside development; local default `root` (configure a restricted account explicitly) |
   | `DB_PASS` | Required outside development; no password fallback |
   | `DB_NAME` | Required outside development; local default `chatbot_db` |
   | `ADMIN_SESSION_SECRET` | Required outside development; at least 32 random characters |
   | `ADMIN_SESSION_LIFETIME_SECONDS` | `28800` (8 hours); integer from 60 to 86400; absolute login lifetime |
   | `ADMIN_SESSION_IDLE_SECONDS` | `1800` (30 minutes); integer from 60 to the absolute lifetime |
   | `UI_ALLOWED_ORIGINS` | Required in production; comma-separated exact HTTP(S) browser origins for the user service |
   | `ADMIN_ALLOWED_ORIGINS` | Required in production; comma-separated exact HTTP(S) dashboard origins; admin requests remain same-origin only |
   | `TRUSTED_PROXY_HOPS` | `0`; exact trusted chain length for `X-Forwarded-For` and `X-Forwarded-Proto` (0-10); nonzero requires `TRUSTED_PROXY_CIDRS` |
   | `TRUSTED_PROXY_CIDRS` | Empty; comma-separated trusted reverse-proxy IP addresses or IPv4/IPv6 CIDRs |

   PowerShell, initially using the migration account:

   ```powershell
   $env:APP_ENV = 'development'
   $env:DB_HOST = '127.0.0.1'
   $env:DB_PORT = '3306'
   $env:DB_NAME = 'chatbot_db'
   $env:DB_USER = 'chatbot_migrator'
   $env:DB_PASS = [System.Net.NetworkCredential]::new('', (Read-Host 'Migration database password' -AsSecureString)).Password
   $env:ADMIN_SESSION_SECRET = python -c "import secrets; print(secrets.token_urlsafe(48))"
   python -B backend/settings.py
   ```

   Bash on Linux:

   ```sh
   export APP_ENV=development DB_HOST=127.0.0.1 DB_PORT=3306
   export DB_NAME=chatbot_db DB_USER=chatbot_migrator
   read -r -s -p 'Migration database password: ' DB_PASS
   printf '\n'
   export DB_PASS
   export ADMIN_SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
   python -B backend/settings.py
   ```

   Stop and resolve any nonzero exit before continuing. These examples generate
   a development signing key for this shell session. Keep a stable key in your
   private launch environment if cookies must survive restarts; generating a new
   key invalidates both admin and anonymous chat cookies. If omitted in
   development, the application generates a per-process key. Production needs
   a stable secret injected by the deployment's secret mechanism.

   Explicitly initialize an empty database before startup:

   ```powershell
   python -B backend/schema_migrations.py upgrade
   python -B backend/schema_migrations.py status
   ```

   Existing databases require a tested backup passed with `upgrade --backup PATH`.
   See `backend/migrations/README.md` for backup, restore and interruption recovery.
   Startup only checks the recorded schema version; it never creates/alters tables.

   Confirm `status` reports `schema_version: 9`, `latest_version: 9`, and empty
   `pending`/`incomplete` lists. A zero exit from `status` alone does not mean the
   schema is current. Then switch back to the serving account, keeping the same
   host, port, database and application profile. In PowerShell:

   ```powershell
   $env:DB_USER = 'chatbot_app'
   $env:DB_PASS = [System.Net.NetworkCredential]::new('', (Read-Host 'Serving database password' -AsSecureString)).Password
   ```

   In Bash:

   ```sh
   export DB_USER=chatbot_app
   read -r -s -p 'Serving database password: ' DB_PASS
   printf '\n'
   export DB_PASS
   ```

   Before the first admin login, run interactive setup from that terminal:

   ```powershell
   python -B backend/setup_admin.py
   ```

   Choose and confirm a unique administrator password (at least 12 characters). There is no default password, and startup never seeds an account. You may safely rerun setup after interruption: uncommitted creation rolls back, and existing accounts are never modified. On upgrades, skip this step and use your existing credentials.

4. Download the model files into the expected local directories:

   ```text
   backend/model/Qwen3-Embedding-0.6B/    Qwen/Qwen3-Embedding-0.6B
   backend/modelgen/qwen3.5-2B/          Qwen/Qwen3.5-2B
   ```

   Both directories must contain the corresponding Hugging Face model files,
   including `config.json`, tokenizer assets and every weight shard. Paths are
   resolved relative to `backend/`, regardless of the working directory.
   `QWEN35_GENERATOR_PATH` overrides `GEN_MODEL` for Qwen3.5;
   `QWEN_GENERATOR_PATH` overrides it for Qwen3-4B. Set only the needed overrides:
   `GEN_MODEL` supplies a shared fallback for both generator entries.
   `QWEN_EMBEDDING_PATH` selects the shared embedding directory.

   With the default folders present, startup selects Qwen3.5-2B and the Qwen
   embedder. Sign in to the dashboard and choose **Model Selection**, or open
   `http://localhost:8000/admin/adminmodel.html` with a `models` grant or the
   administrator role. The old `/adminmodel.html` path is not an alias.

   Selection precedence is a recognized saved ID in `backend/model_selection.json`,
   then `GEN_MODEL_ID` when there is no recognized saved ID. If that requested
   entry is unavailable, the first available catalog entry wins (Qwen3.5 before
   Qwen3-4B). If none is available, the requested/default ID is retained and
   startup model loading fails. Catalog availability checks only `config.json`
   in both directories; it does not verify complete weights or sufficient memory.
   A successful selection persists the ID and resets the generator for lazy
   loading. Embedding changes build a separate index in the background while
   the current compatible model/index continue serving. Validated cutover and
   rollback preserve exact profile isolation, including equal-dimension changes.
   Schema version 10 adds durable checkpoints; legacy source text is freshly
   embedded without rewriting original knowledge. Keep old model snapshots
   immutable in separate directories. Qwen3-4B defaults to `backend/modelgen/`
   and shares the embedder. See [index status, retry, rollback and upgrade limits](docs/embedding-index-migration.md).

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
microphone button to start recording. After speech, a 1.5-second pause stops
capture and sends the final transcript automatically; click again to finish
earlier. Live
transcripts appear in the input, and the final transcript (plus any existing
draft) goes through the normal `/chat` endpoint, including history and feedback.
Silence without speech and interrupted recordings are not sent. The voice status
shows model loading, missing dependencies, microphone errors, and completion.

Replies to voice messages are read automatically by the local Persian/English
TTS models in `backend/TTS`. The audio-reply checkbox and stop button cancel
playback; starting another recording also stops the previous answer. Audio
controls remain available if the browser requires a manual play click. Typed
messages remain text-only. Missing speech dependencies leave the text reply
available. `STT_LANGUAGE` changes transcription. Use `STT_LOCAL_FILES_ONLY=1` to
disallow Whisper model downloads after caching the model. Do not launch
`backend/STT/STT.py` alongside the chatbot on its default port: the standalone
demo also defaults to port 5000. The integrated voice endpoints are
`/voice/health` and `/voice-stream`; one transcription runs at a time.

Install the optional speech runtime separately from the chatbot environment:

```powershell
python -m venv .venv-tts
.\.venv-tts\Scripts\python.exe -m pip install torch==2.7.1 --index-url https://download.pytorch.org/whl/cpu
.\.venv-tts\Scripts\python.exe -m pip install -r requirements-tts.txt
```

Keep the existing `backend/TTS/English_model` and `Persian_model` folders in
place. The application starts a private worker automatically on first voice
use; no second web server is needed. It selects `.venv-tts/Scripts/python.exe`
on Windows or `.venv-tts/bin/python` on Linux, then falls back to the application
interpreter if that environment does not exist. Set `TTS_PYTHON` to select
another prepared interpreter. `/tts/health` starts model loading;
`POST /tts/synthesize` accepts `{"text": "..."}` (1–5,000 characters) and returns
a noncached WAV. One synthesis runs at a time, with no unbounded job queue.
The worker reuses its models across requests. The Persian G2P loader may download
on first use; prepare its cache and the local voice prompt before offline use.
Run `python -B scripts/check_speech.py --offline` to verify real Persian/English
audio from cached models without starting either web service or touching MySQL.

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

After completing database, administrator, model, dependency verification and
secret setup above, run from the repository root in the activated Linux/WSL
environment. Do not rerun `install` in an already populated environment:

```sh
export APP_ENV=production
# Inject DB_USER, DB_PASS, DB_NAME, and ADMIN_SESSION_SECRET securely first.
# Replace these examples with the browser-facing HTTPS origins of your proxy.
export UI_ALLOWED_ORIGINS=https://chat.example.com
export ADMIN_ALLOWED_ORIGINS=https://admin.example.com
# Configure TRUSTED_PROXY_HOPS/CIDRS for that proxy as described below.
python -B backend/settings.py
python -B backend/main.py
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
address becomes `request.remote_addr`, used by admission limits and optional
chat IP metadata. Anonymous ownership comes from the chat cookie, never the IP.
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

See the [v1 API contract and compatibility policy](docs/api/README.md) and
[OpenAPI schemas](docs/api/openapi.json) for all supported payloads, error codes,
pagination, authentication and CSRF requirements. Existing routes remain v1 and
responses advertise `API-Version: 1`. Breaking changes require a parallel major
version and at least 180 days of published deprecation notice.

The user service exposes:

- `GET /health` — fixed liveness response (`{"status":"ok"}`). Its audit decorator may attempt a database log write, but logging failure does not turn it into a failed readiness probe. It does not establish database, model or speech readiness.
- `POST /chat` — retrieves knowledge and generates a response. Send JSON with `message`; the server establishes the anonymous session. An optional legacy `user_id` string (at most 64 characters) is accepted but ignored; wrong field types still fail validation. Retained responses include a saved chat-history `message_id`, including fallback replies; stateless mode returns `message_id: null` and `feedback_enabled: false`. Keep the response's chat cookie for feedback and subsequent messages.
- `POST /feedback` — send `{"message_id": 123, "feedback": "like"}` (or `"dislike"`) using the ID returned by `/chat` and the same chat cookie. Only the originating session can rate the message; submitting another rating updates its previous feedback. Invalid requests return 400, missing/invalid/expired session proof returns 401, unknown or non-owned messages return the same 404, and database failures return 503 so the UI can offer a retry.

Administrator endpoints are available under `/admin/api/` on port 8000 for knowledge-base management, PDFs, logs, and chat history.

Both import pages offer **بررسی و پیش‌نمایش بدون انتشار** before publication:
inspect duplicate counts, extraction samples, expected chunks, estimated local
embedding work and actionable record/page errors. Preview validates the original
bounded JSON/PDF without creating a job, embedding or changing searchable knowledge.
Changing the file or replacement option requires another preview before the
publication button is enabled. API clients can POST multipart files to
`/admin/api/upload/preflight` or `/admin/api/pdf/upload/preflight`; see the
[preflight contract](docs/api/README.md#ingestion-preflight-13-f029). Estimates are
advisory work counts; publication always revalidates and rechecks current duplicates.

Scanned Persian PDFs can use optional local OCR. Install Tesseract 5 with `fas`
and `eng` data, set `PDF_OCR_ENABLED=1`, and restart. Preflight shows extraction
method, confidence and samples by original page; uncertain OCR blocks publication
until the source is corrected. Containers additionally need `INSTALL_OCR=1` at
build time. See [OCR setup, limits and review](docs/ocr.md). It is off by default.

Embedded-text PDFs use layout-aware extraction in both preview and publication:
columns follow their reading direction, headings and ruled table cells retain
structure, and recurring margin headers/page counters are suppressed. Page
citations keep their original numbers. Existing documents need replacement with
the same PDF to adopt the new extraction. Preview complex layouts before importing;
borderless tables and unusual layouts still need review. See
[PDF layout behavior and evaluation](docs/pdf-layout.md).

Persian retrieval and Q&A deduplication share one normalization policy. Arabic
yeh/kaf variants, Persian/Arabic digits, whitespace and half-spaces use equivalent
comparison/model inputs; original questions, answers, PDF text and page citations
remain available for display. Preview also recognizes equivalent existing
questions with legacy hashes. Replace/re-import knowledge to refresh older
embeddings with the new preprocessing. See [normalization policy and limits](docs/persian-normalization.md).

The complete route inventory is in the API contract. These access boundaries
apply before the handlers:

| Service and route group | Access and behavior |
| --- | --- |
| User `/`, `/health`, `/chat`, `/voice/*`, `/voice-stream`, `/tts/*`, `/controller/features`, `/controller/features.js` | No administrator login; origin validation and route-specific limits still apply. WebSocket requires an allowed `Origin`. Controller feature routes only expose public flags. |
| User `/feedback` | Valid anonymous chat cookie and an owned message; disabled with 403 when chat or feedback retention is zero. |
| Admin `/login`, `/login.css`, `/login.js`, `/login-theme.js` | Public login resources; login POST needs its pre-login cookie and CSRF token. Production HTTPS applies here too. |
| Admin `/`, shared dashboard assets, `/admin/api/session`, POST `/logout` | Active administrator-service session; any active account can enter the shell. Logout requires CSRF and revokes that session. |
| Admin Q&A, PDF, models, chats, logs, controller | Respective `json_upload`, `pdf_upload`, `models`, `chats`, `system_reports`, `controller` grants, or the administrator role. Page HTML/CSS/JS and APIs share these restrictions. |
| Admin account management | `is_admin=true`; the `administrator` username, `user_control` or a wildcard grant alone is insufficient. |

Admin APIs and logout return 401 when unauthenticated; protected HTML pages
redirect to `/login`. Authenticated requests missing a registered permission or
valid mutation CSRF proof receive 403. All admin mutations require CSRF,
including login; there is no bearer-token/API-key alternative or GET logout.
The [permission matrix](backend/admin-users/PERMISSIONS.md) lists exact paths
and operations. Unregistered assets/actions have no role-based bypass.

On the user service, `/index.html` redirects to `/`. `/voice/health` starts lazy
Whisper loading; `/tts/health` independently starts the private TTS worker.
Both report 503 while loading/unavailable. `POST /tts/synthesize` accepts only
`{"text":"..."}` (1-5000 characters) and returns WAV, 429 when busy/rate-limited,
or 503 when unavailable. These public speech routes do not create an admin
session. The controller's voice switch gates `/voice*`; it does not gate `/tts/*`.
The chat switch gates `/chat`, not feedback or TTS.

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

Explicit migration 4 adds nullable `chat_history.owner_hash` on existing
installations using a schema-scoped migration account. Existing history
and feedback are preserved. Older rows cannot safely be assigned an owner from
their formerly client-chosen IDs, so they remain unowned and no longer accept
anonymous feedback; authorized administrators retain inspection/deletion access.
Back up and run `python backend/schema_migrations.py upgrade --backup PATH`
before restarting through `backend/main.py`; see `backend/migrations/README.md`.

## Application factories and initialization

With `backend/` on `sys.path`, import `create_apps` from `application` (also
available from `main`). Importing the factories requires no deployment settings,
database, model dependencies or model files. Calling `create_apps()` requires
valid settings and installed application dependencies; it returns fresh
`(ui_app, admin_app)` instances with routes, security and controller hooks, but
does not create tables, write logs, load/download models or start workers/servers.
The individual `create_ui_app()` / `create_admin_app()` factories register only
their service's routes; `create_apps()` additionally registers the controller.
Requests can still trigger database/model work, so use test adapters for those.
Configure the environment before constructing apps; shared database/model
configuration and model/controller state remain process-scoped, not tenant-scoped.

For management work, `from bootstrap import initialize_database` imports safely;
calling `initialize_database()` checks the recorded schema without creating or
altering tables or importing model/application modules. Use the explicit
`schema_migrations.py upgrade` command for DDL. `initialize_models()` is separate.
Normal launchers use `main.create_serving_apps()`, which assembles both apps,
checks the schema version, reads controller state, loads embedding/generation models,
and finally starts the shared retention worker. Production does this inside
the Gunicorn worker, never its master. Keep using `python backend/main.py` for
serving; passing assembly-only factories directly to a WSGI server bypasses the
required initialization. The low-level serving helpers no longer start retention.

### Validated configuration (F021)

`backend/settings.py:SETTING_REGISTRY` defines every supported application setting
once, including its type, default, bounds/choices, legacy aliases and diagnostic
visibility. The database, generator, model catalog, integrated voice and both
standalone speech demos use that registry. The shared JSON-upload limit is defined
in `request_validation.py`; `interface.MAX_UPLOAD_BYTES` is a compatibility alias.

Check configuration before starting services, using the same interpreter and
environment as the application:

```text
python -B backend/settings.py
python -B backend/settings.py --diagnostics
python -B backend/settings.py --scope stt
python -B backend/settings.py --scope tts --diagnostics
```

These commands need only Python's standard library. They never import services,
connect to MySQL, load/download models or start servers. Diagnostics emit JSON
containing only explicitly public effective values; passwords, session secrets,
database identity/host, free-form model locations and filesystem paths are omitted.
There is no public configuration endpoint. In Python, `load_settings()` returns an
immutable configuration containing private values for internal use;
`effective_settings()` returns the safe diagnostic view.

The application validates all its settings before importing routes or initializing
database/models, including settings used only during lazy speech loading. Unknown
environment names, malformed numbers, non-finite thresholds, unsupported choices,
and invalid ranges fail with the setting name and correction guidance, never the
supplied value. Production reports missing required names together. `APP_ENV`
overrides legacy `ENVIRONMENT`, then `FLASK_ENV`; absent selectors mean production.
Only `production`, `development`, `dev`, `local`, and `test` are accepted. Explicit
blank settings fail except optional development secrets and an empty proxy list;
unset a variable to use its default. Development has no hard-coded DB password.

In addition to the deployment/session/retention tables, the registry validates:

| Settings | Type / accepted values |
| --- | --- |
| `GEN_DEVICE`, `GEN_QUANTIZATION`, `GEN_MODEL_ID` | Declared choices; explicit `cpu` with `4bit` is rejected |
| `EMBEDDING_BATCH_SIZE` | Integer, 1-64; default 8 inputs per JSON/PDF embedding call; restart after changing |
| `INGESTION_WORKERS` | Integer, 1-4; default 1 background ingestion worker; restart after changing |
| `INGESTION_QUEUE_CAPACITY` | Integer, 1-256; default 16 queued plus running jobs; full queues return 503 |
| `INGESTION_POLL_SECONDS` | Number, 0.05-30; default 1 second between queue/election polls |
| `GEN_MODEL`, `QWEN35_GENERATOR_PATH`, `QWEN_GENERATOR_PATH`, `QWEN_EMBEDDING_PATH` | Nonblank paths; relative to `backend/`; dedicated generator paths override `GEN_MODEL` |
| `STT_MODEL` | Nonblank model identifier or CTranslate2 directory |
| `STT_DEVICE`, `STT_COMPUTE_TYPE`, `STT_LANGUAGE` | Declared device/precision choices and Whisper language codes (`auto` detects language) |
| `STT_VOICE_THRESHOLD` | Finite number, 0-1; default 0.008 |
| `STT_CPU_THREADS`, `TTS_CPU_THREADS` | Integers, 1-256; defaults up to 4 and 1 respectively |
| `STT_LOCAL_FILES_ONLY`, `RETENTION_DELETE_ENABLED` | `0`, `1`, `false`, or `true`, case-insensitive |
| `TTS_PYTHON` | Existing interpreter file; relative to the repository root; default `.venv-tts` interpreter when installed, otherwise current Python |
| `ENGLISH_MODEL_FILE`, `ENGLISH_VOICE` | ONNX filename without directories; voice identifier using letters/digits/underscores/hyphens |
| `PERSIAN_G2P_REPO`, `PERSIAN_VOICE_PROMPT` | Nonblank G2P identifier/path; prompt path relative to `backend/TTS/` |
| `STT_HOST`, `HOST` (standalone STT/TTS) | `localhost` or IPv4/IPv6 address; loopback by default |
| `STT_PORT`, `PORT` (standalone STT/TTS) | Integer, 1-65535; default 5000 |

Only the selected standalone scope is checked for a speech demo; database and
browser-origin deployment credentials are unnecessary there. No model files are
required for configuration checks. Model availability and hardware/dependency
compatibility are still checked by their loaders. Restart after environment
changes; saved model selection and controller toggles remain runtime state.

### Backend service boundaries

`admin.py` only composes focused modules in `admin_routes/`: authentication/CSRF,
accounts, pages/assets, Q&A knowledge, PDFs, model selection, chat history and logs.
Each module has its own `register_routes(app)` and can be imported/registered in
isolation. The normal app factory installs all groups plus the common guards.
Do not serve an isolated group without those guards. Paths, HTTP methods, Flask
endpoint names, permission policies and client response formats stay unchanged.

`services.ingestion.IngestionService` accepts bounded upload bytes through
`ingest_qa(raw, replace=False)` and `ingest_pdf(raw, filename, replace=False)`.
It owns value validation, extraction/chunking, embedding and persistence; it
returns ordinary dictionaries and raises `IngestionError` for controlled
ingestion failures. Existing value-validation exceptions retain their status.
Multipart parsing, authorization, admission control and JSON responses stay in
the HTTP modules. Publication uses an explicit transaction: all JSON writes or
the complete PDF replacement become visible together. Failed imports roll back;
an existing PDF remains searchable until its replacement commits.

JSON questions and PDF chunks use actual embedding micro-batches controlled by
`EMBEDDING_BATCH_SIZE` (default 8). Vectors are staged in a disk-backed temporary
file and read back in input order; each batch array is released before the next
model call. All embedding batches must succeed before knowledge writes begin.
Set a smaller batch on machines with less spare RAM. The OS temporary directory
must be writable and have space for the vector staging file. See
[operations](docs/operations.md#embedding-upload-capacity) for sizing and
[the reproducible memory checks](backend/tests/TESTING.md#embedding-micro-batches-f025).

The service's optional `progress=` callback receives embedding counts at zero
and after each staged batch; the existing admin log also records partial counts
without input text. These counts describe embedding, before publication. JSON
totals include existing records that the database phase may skip.

The admin upload pages use durable background jobs (F026). POST multipart files
to `/admin/api/upload/jobs` (JSON) or `/admin/api/pdf/upload/jobs` (PDF). Submission
returns **202** `{job_id, kind, status: 'queued'}` after committing the upload to
MySQL, with a `Location` for GET progress or DELETE queued/running cancellation. Extraction
and embedding run in the bounded worker pool. Job states and uploads survive a
service restart; interrupted running jobs become failed with `ingestion_interrupted`,
while queued jobs continue. Polling shows extraction, chunking, embedding and
publishing counts. The admin pages offer cancellation and checkpoint retries,
and reconnect to their saved job after reload. POST `Location/retry` with an
empty body retries failed/canceled work from its saved upload within 24 hours;
embeddings are recomputed. Publication and successful job completion commit
together, so retries cannot replay an ambiguously published import. Completed
payloads are deleted at commit; failed/canceled bytes expire after 24 hours.
Cancellation fences publication when acknowledged and computation stops at the
next safe checkpoint (one in-flight page/batch/record; native calls are not
preempted). Apply migration 6 and read the documented bound and legacy-job caveat.
The original `/admin/api/upload` and `/admin/api/pdf/upload` paths submit to the
same queue and wait up to 240 seconds for their legacy response. Prefer job
submission to avoid occupying an HTTP thread or timing out. See
[job operations](docs/operations.md#durable-ingestion-jobs-f026) for recovery and sizing.

`services.retrieval.RetrievalService.retrieve(query, limit=6)` owns embedding,
QA/PDF search and threshold/ranking. `services.lifecycle.ModelLifecycleService`
owns model selection, warmup, load/unload, monitoring and per-model asynchronous
actions. The controller keeps HTTP authorization and persisted feature toggles.
Services use injectable adapters and run without a Flask application/request
context. Defaults import external adapters lazily, not during service imports.

For tests, pass `ingestion_service=` / `model_lifecycle=` to `create_admin_app()`
or `retrieval_service=` to `create_ui_app()`. They are available under the same
names in `app.extensions`. Ingestion/retrieval instances are per-app; the default
model lifecycle and actual models remain process-shared, including startup.
Replace service adapters rather than patching the old `admin` module's globals.

## Project layout

```text
backend/
  main.py             Initializes shared services and selects the runtime profile
  application.py      Fresh Flask app factories with no implicit service startup
  bootstrap.py        Read-only schema check, model and background-worker initialization
  schema_migrations.py Explicit versioned database upgrade/status CLI
  migrations/         Ordered SQL, backup requirements and recovery guide
  serving.py          Local Flask serving and threaded production Gunicorn runtime
  settings.py         Validated environment, loopback binding, and credentials
  user.py             User-facing chat API
  admin.py            Administrator route composition and standalone launcher
  admin_routes/       Focused administrator HTTP modules, preserving all route names
  services/           Injectable ingestion, retrieval and shared model lifecycle
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

Severity codes are **1 = information**, **2 = warning**, and **3 = failed**. The existing four-argument `logger.logDrop(level, log_from, message, warninglevel)` calls remain supported, but file and function names are inferred automatically. `@logger.operation` records completed decorated operations, exceptions, and HTTP outcomes (4xx as warnings and 5xx as failures) without logging request bodies or model inputs. Coverage includes explicit runtime-start completion, chat/feedback, retrieval, embeddings, generation, model changes, JSON/PDF tools and selected administrator APIs. Migration/status/schema-guard operations deliberately bypass DB audit writes. Decorated health requests can attempt logging, but a log failure does not change the fixed liveness response.

Explicit migration 3 widens existing `CHAR`/`VARCHAR` message columns to `TEXT` without removing existing logs. Startup never alters logging tables. Database logging failures fall back to the console and do not interrupt application requests. View and filter logs on the admin **System Logs** page.

## Notes

- Models are loaded locally; the generator is configured not to download model files at runtime.
- Non-development deployments require explicit `DB_USER`, `DB_PASS`, `DB_NAME`, and `ADMIN_SESSION_SECRET` values. Never use the MySQL `root` account for the application.
- Debug mode and exception propagation are disabled. `APP_ENV=production` uses the production server; Flask development serving requires explicit `APP_ENV=development`.


## Admin authentication

The admin service runs on port 8000 and opens a login page before serving `backend/dashboard.html`. Explicit migrations create `admin-users` without inserting accounts; startup and setup only verify the schema version. Run `python backend/setup_admin.py` locally after migrating to create `administrator` with your own password on a clean installation. Setup requires an interactive terminal and refuses to run if any account already exists, including disabled accounts. It cannot reset or overwrite upgrade accounts. If interrupted, rerun it safely; creation is transactional and concurrent setup commands are serialized. Existing installations retain their credentials and permissions; if still using the old shared password, change it in **Admin user control**. This menu also lets administrators create users and assign menu permissions. See `backend/admin-users/README.md` for setup and resumption details.

### Session lifecycle and cookies

- A login expires after 8 hours, or after 30 minutes without an authenticated request, whichever comes first. Active dashboard polling counts as activity but never extends the absolute deadline. Both timeouts are configurable with the settings above; restarting is required after changing configuration.
- After login, the browser receives a signed, expiring cookie containing only an opaque random session ID. MySQL `admin_sessions` stores its SHA-256 hash and deadlines. Normal requests do not renew the cookie. Expired or missing records fail closed; expired records are removed on access and during login.
- `POST /logout` revokes the server-side session before clearing the cookie, so replaying a copied cookie fails. Signing in again rotates and revokes that browser's previous session. Other browsers remain signed in until expiry or an account security change.
- Password resets and changes to permissions, administrator status, or active status through `update_user()` atomically increment the account's `session_version`, invalidating every existing login, including the caller's own login. Re-enabling an account or restoring old permissions does not restore old sessions. Deleting a user cascades deletion of their session records. Operational SQL password/security changes must also increment `session_version` in the same statement.
- All cookies are host-only, `HttpOnly`, `SameSite=Lax`, and scoped to `/`. Explicit local development uses `local_chatbot_admin_dev` without `Secure` for `http://localhost:8000`. Production uses `__Host-local_chatbot_admin` with `Secure` and requires HTTPS. There is no production switch to permit insecure cookies. Admin responses use `Cache-Control: no-store`.
- Open admin pages through the served dashboard on the same origin, not as `file:` pages. Credentialed `Origin: null` CORS is no longer enabled. The chat inspector now uses the dashboard's origin, including HTTPS, rather than a hard-coded localhost API.

Explicit migration 2 adds `session_version` to existing accounts and creates
`admin_sessions` without changing passwords or permissions. Legacy session
cookies are not accepted; users must sign in again after upgrading. The existing
schema-scoped migration grants cover these changes; run the recorded migration
command before startup, not with the ordinary serving account.

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
| Local TTS synthesis | 30 per 60 seconds | 1 |
| TTS-health polling | 60 per 60 seconds | 1 |

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
not a legal-compliance guarantee. The current `frontend/index.html` has the
detailed privacy summary and retention text inside HTML comments, so it does
**not** show visitors the configured lifetimes or paused-deletion warning.
Publish that information through your deployment's visible notice before use;
the visible voice notice only describes server-side audio processing. Restoring
or changing the UI notice is a separate behavior change from this documentation.

| Data | Purpose and access | Default and deletion |
| --- | --- | --- |
| Chat text, replies, anonymous grouping/owner hash | Authorized `chats` operators review answer quality and support issues; not automatic model training | 30 days from message creation, then hard-delete the entire row |
| Feedback and its timestamp | Authorized quality review of the linked answer | 30 days from message creation, never longer than its chat row; clear both fields on expiry |
| Chat IP address | Optional short-lived troubleshooting metadata; never identity/ownership proof | Not stored in new chat history (`''` sentinel); clear existing addresses on cleanup |
| Application audit logs | Authorized `system_reports` operators diagnose errors and security events | 14 days, then hard-delete; new chat/feedback logs omit message IDs, text and rating values |
| Microphone audio | Transcribe speech on the application server using local Whisper | Bounded in-memory buffers only, not stored as audio files; submitted transcripts follow chat policy |
| Anonymous cookie | Prove message/feedback ownership | Fixed 24-hour validity; removing the cookie does not delete stored history |

Speech playback uses the local Persian/English models on the application server;
answer text is not sent to a browser/OS speech provider. Playback for voice
messages is enabled by default and can be stopped or disabled in the visible
voice panel. Microphone and generated audio stay in memory; WAV responses use
`Cache-Control: no-store`. Local STT means processing on
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
erasure. Database backups, binary logs, replicas, proxy/container/service logs
and exported admin data need their own bounded
retention and erasure procedures. Rotate/expire backups and reapply cleanup
before serving a restored database; old snapshots otherwise restore deleted
data. Application sweeps cannot delete those external copies. Default development
access logging is suppressed and production Gunicorn access logging is disabled
to avoid creating another copy of visitor IPs and query identifiers; configure
any upstream logging deliberately.

Offline tests execute translated cleanup SQL against an in-memory database and
cover boundary/null timestamps, zero-day policies, deletion pause, dry-run,
bounded progress, overlapping locks, committed counts/rollback, safe records,
startup wiring, collection defaults, and rendered policy strings. Current browser
journeys cover local playback/cancellation. They do not establish completeness of
the currently commented-out retention disclosure. The older fixed-port security
smoke still expects that notice to be visible and browser-speech opt-in; update
those stale assertions before using it to certify the current UI.

## Deployment security

The default environment is `production`. Only explicit `APP_ENV=development` (or `dev`, `local`, `test`) enables development behavior; other values fail closed. Production requires `DB_USER`, `DB_PASS`, `DB_NAME`, `ADMIN_SESSION_SECRET`, `UI_ALLOWED_ORIGINS` and `ADMIN_ALLOWED_ORIGINS`; missing/blank values fail startup, and `DB_USER=root` is rejected. Unexpected errors return generic messages. Audit logs redact configured database/session secrets, and exception logs record error types rather than raw exception text.

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
background failures expose `model_action_failed`; failed ingestion jobs store
allowlisted public error codes and roll back knowledge writes. The standalone STT demo also avoids
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

That dated evaluation predates the current local-TTS and notice behavior; it is
not a current end-to-end pass. Run `python -B backend/tests/run_tests.py` in the
test environment for offline regressions, and use the current browser journeys
under [Automated tests](#automated-tests) for UI validation. The older
`browser_security_smoke.py`/`.cjs` utility binds fixed ports 5000/8000 and uses
offline DB/model doubles plus optional voice dependencies. Its stale privacy
and browser-speech assertions need updating before it can provide new acceptance
evidence. Full CSP enforcement still needs a fresh deployment-origin evaluation.

Have a database administrator provision a database and separate serving/migration
accounts. The following are SQL templates: replace both password placeholders
privately and use the account host matching your deployment. The native setup
examples use these names; application administrator logins are separate from
MySQL accounts.

```sql
CREATE DATABASE chatbot_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'chatbot_app'@'localhost' IDENTIFIED BY '<strong-random-password>';
GRANT SELECT, INSERT, UPDATE, DELETE
  ON chatbot_db.* TO 'chatbot_app'@'localhost';
CREATE USER 'chatbot_migrator'@'localhost' IDENTIFIED BY '<different-strong-random-password>';
GRANT SELECT, INSERT, UPDATE, CREATE, ALTER, INDEX, REFERENCES
  ON chatbot_db.* TO 'chatbot_migrator'@'localhost';
```

Use a separate schema-scoped migration account with `SELECT`, `INSERT`, `UPDATE`,
`CREATE`, `ALTER`, `INDEX` and `REFERENCES` for explicit upgrades. The serving
account needs only ordinary DML, including `SELECT` on the migration ledger;
startup no longer changes schemas. No global, account-management, `FILE`, or
grant-option permissions are needed by the application or migration runner.
Backup tools may require additional object-specific permissions; see
`backend/migrations/README.md`. For a remote database, replace `localhost` with
the specific application host, not `%`. After migration, use `chatbot_app` for
`backend/setup_admin.py` and serving; setup needs DML, not MySQL account-creation
rights. The local Compose image provisions its own database user with broader
schema rights to run migrations; that convenience is not the production grant
model above.

Rotate the formerly embedded database password and session signing key on existing deployments; removing them from source does not revoke credentials. Changing the session key invalidates existing admin sessions. Keep passwords out of version control, command-line arguments, and logs.

Set `DB_USER=chatbot_app`, `DB_PASS=<strong-random-password>`, and `DB_NAME=chatbot_db`. Do not grant `ALL PRIVILEGES`, and do not run the application as `root`.

## Automated tests

The lightweight offline suite needs no MySQL, Docker, GPU or model files:

```sh
python -m pip install -r backend/tests/requirements.txt
python -B backend/tests/run_tests.py --repeat 2
```

For Flask API integration against real MySQL, start Docker with Linux containers:

```sh
python -m pip install -r backend/tests/mysql/requirements.txt
python -B backend/tests/run_mysql_tests.py --repeat 2
```

The integration runner creates its own MySQL 8.4 container, random schema,
credentials and loopback port. It ignores deployment database settings and `.env`,
blocks unprovisioned connections, stores data only in tmpfs, and removes its
unique Docker project on exit. Never point destructive fixtures at developer or
production data. Model adapters are doubles; Flask, SQL, PDF extraction and
database effects are real. Missing prerequisites fail instead of silently skipping.
See `backend/tests/TESTING.md` for isolation guarantees, coverage and cleanup.

For primary browser journeys, install Node.js 20+, Chrome (or Edge), and the pinned
Playwright dependencies:

```sh
npm ci --prefix backend/tests/browser
python -B backend/tests/browser_smoke.py --channel chrome --repeat 2
```

The browser cases cover desktop/mobile RTL chat, login/logout, role-based
navigation, theme persistence, feedback failure/retry and mocked microphone
failures/recovery. The runner serves disposable in-memory fixtures on random
loopback ports; it never uses live accounts, databases, models or physical audio.
The running chatbot can stay up. Use `--channel msedge` for Edge or
`--grep "feedback|microphone"` for focused cases. Failed cases retain screenshots,
traces and a JSON report in ignored `backend/tests/browser-artifacts/` directories.
See `backend/tests/TESTING.md` for setup, Chromium installation, coverage and limits.

### Continuous integration

GitHub Actions runs syntax/changed-whitespace checks, the offline suite on Linux
and Windows, disposable MySQL integration and Chromium browser journeys on pull
requests, `main` pushes and merge queues. Every suite runs twice; all checks are
CPU-only with no production credentials, configured database or real models.
The aggregate `Merge checks` fails if any prerequisite fails or is skipped.
Enable **`Merge checks`** as a required status check in the `main` branch ruleset
to prevent failed changes from merging; the workflow cannot set repository rules.
Run `python -B backend/tests/check_source.py` locally for syntax, repeated-import,
tracked-bytecode and changed-whitespace checks. These use the Python standard
library, Git and Node; no general formatter or type checker is configured.
See `backend/tests/TESTING.md` for the
full commands, negative-control verification and separate GPU evaluation policy.
