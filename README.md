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

PDF uploads are limited to 50 MB. JSON knowledge-base uploads are limited to 20 MB.

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
- `POST /chat` — retrieves knowledge and generates a response. Send JSON with `message` and an optional `user_id`. Every successful response includes a saved chat-history `message_id`, including fallback replies.
- `POST /feedback` — send `{"message_id": 123, "feedback": "like"}` (or `"dislike"`) using the ID returned by `/chat`. Saves the rating and its timestamp in `chat_history`; submitting another rating updates the same message. Invalid requests return 400, missing messages return 404, and database failures return 503 so the UI can offer a retry.

Administrator endpoints are available under `/admin/api/` on port 8000 for knowledge-base management, PDFs, logs, and chat history.

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

## Deployment security

The default environment is `production`. Only explicit `APP_ENV=development` (or `dev`, `local`, `test`) enables development behavior; other values fail closed. Startup fails closed when any of these settings are missing: `DB_USER`, `DB_PASS`, `DB_NAME`, or `ADMIN_SESSION_SECRET`. Unexpected errors return generic messages. Audit logs redact configured database/session secrets, and exception logs record error types rather than raw exception text.

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
