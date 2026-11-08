# Troubleshooting

Use the [README setup](../README.md#setup) and [operations guide](operations.md)
for the supported sequence. Run native commands from the repository root with
the intended environment active. Keep passwords, cookies, CSRF tokens, messages
and database dumps out of diagnostics shared with others.

## Establish which layer failed

1. `python -B backend/settings.py` validates the launch environment before DB or
   model work. `--diagnostics` shows only approved public settings. Native Python
   does not read `.env`, and VS Code launch settings do not configure a terminal.
2. `python -B backend/schema_migrations.py status` checks the configured DB and
   ledger without loading models. Current means version 4 with empty `pending`
   and `incomplete`, not merely a zero exit code.
3. Inspect startup/service logs and GET `/health` on the user origin. Startup
   warms text models before binding either port. A running `/health` route only
   proves liveness, not DB/model/speech readiness. Its audit decorator can attempt
   a DB log write without making logging failure fail the health response.
4. Diagnose the failing route's status and public `code`, plus `Retry-After` for
   429 responses. Client-facing errors deliberately omit filesystem/exception
   details. Use local logs and the owning module for further diagnosis.

Use the relevant interpreter explicitly if activation is uncertain:
`.\.venv\Scripts\python.exe` on Windows, `.venv/bin/python` on Linux, or the
separate `.venv-tests` equivalents for offline tests.

## Startup, database and dependency failures

| Symptom | What to check and do |
| --- | --- |
| Missing production settings during local setup/migration | Set `APP_ENV=development` in the same terminal before the command; configure DB variables there. Production instead needs DB credentials, session secret and both allowed-origin lists. Do not use a development profile to bypass production requirements. |
| Native Windows says production requires Linux/WSL | Use explicit local development on Windows or provision a separate Linux/WSL environment for production. Do not reuse a Windows virtual environment inside WSL. |
| Installer refuses the environment | It accepts fresh environments only. Create another environment with the recorded Python/architecture and select one CPU/cu118 profile; do not install over the current application or test environment. |
| Hash/inventory, optional audio imports or `WinError 127` | Recreate the matching locked profile; keep unrelated TorchAudio/torchvision/qwen-tts packages elsewhere. Use [dependency verification and rollback](../requirements/README.md); do not blanket-upgrade Torch/Transformers. |
| DB connection/access denied | Check host, port, account host grant and database in the launch context. Use migration credentials only for upgrade, serving credentials for setup/serving. The app does not create databases or MySQL accounts. |
| Schema pending, interrupted, future or checksum-mismatched | Stop all writers and follow the [migration recovery procedure](../backend/migrations/README.md). Existing data needs a restore-tested backup; do not edit released SQL, erase the ledger or force a version to applied. |
| Ports remain closed after starting | Inspect configuration/schema/model startup first; both text models must load before serving. Also check for port conflicts, including the standalone STT/TTS demos. |
| Compose model mount is missing | Create the host `backend/model/` and `backend/modelgen/` directories with complete downloads before starting. The bind mounts disable automatic host-directory creation. |
| Changed Compose `.env` password has no effect | MySQL image initialization variables affect a fresh volume only. Rotate the existing DB account through an authorized DB operation and update the application secret together; deleting volumes loses data. |

## Login, permissions and browser requests

| Symptom | What to check and do |
| --- | --- |
| No initial login/password works | There is no seeded password. Migrate first, then run interactive `backend/setup_admin.py` against that DB. Setup creates `administrator` only if no account exists. |
| Setup says accounts already exist | Sign in using existing credentials. Disabled accounts count too; rerunning setup is not a reset. If access is lost, another active administrator can reset/reactivate the account. There is no built-in unauthenticated recovery path; preserve backups and use controlled operator recovery when no administrator remains. |
| Admin HTTPS error (400) behind a proxy | Use browser HTTPS, preserve Host and configure the actual trusted proxy peer CIDRs and exact hop count. The validated `X-Forwarded-For` chain must also support the forwarded scheme; adding `X-Forwarded-Proto` alone is insufficient. |
| Origin rejected (403), including login or WebSocket | Match scheme, host and port in the service's allowed list. Admin additionally requires same-origin. Use served pages instead of `file:`; `localhost` and `127.0.0.1` are different browser origins/cookie hosts. `/voice-stream` always needs an allowed Origin. |
| Admin API/logout returns 401 | Retain the cookie jar and sign in again. Sessions can expire by absolute/idle deadline or be revoked on logout/security changes. Protected HTML redirects to `/login` instead of returning the API's 401. |
| Login POST or authenticated mutation returns 403 | GET `/login` first with a cookie jar and submit its hidden pre-login CSRF token. After login, GET `/admin/api/session` for the new session token; send `X-CSRF-Token` or a multipart/form token on mutations. Tokens are never query parameters. Check feature authority as well. |
| Dashboard is blank or a page returns 403 | The shell needs authentication; its pages also need grants. A no-grant account may enter the shell but has no permitted page. Account management needs `is_admin`, never a username, `user_control` or wildcard alone. Check the [exact permission matrix](../backend/admin-users/PERMISSIONS.md). |
| Model Selection at `/adminmodel.html` fails | Sign in and use `/admin/adminmodel.html` with the `models` grant or administrator role. Legacy root page paths are not aliases. The Settings template item has no registered policy/file and is hidden; an authenticated direct request is denied. |
| Login stops working after changing a role/password | Security changes increment `session_version` and revoke all existing sessions, including the caller's. Sign in again; restoring the old grant does not restore old cookies. |
| Feedback is rejected | 403 can mean zero chat/feedback retention; 401 means missing/invalid chat proof; 404 treats foreign, unknown and legacy-unowned messages identically. Retain the original anonymous chat cookie. A supplied user ID or IP does not recover ownership. |
| Requests return 429 | Honor `Retry-After` and allow active work to finish. Rate limits use the validated client IP, and speech has process-wide slots too. Do not retry mutations automatically or weaken guards to resolve admission failures. |

For scripts, administrator login is a form/cookie/CSRF flow, not an API key.
See the [API guide](api/README.md#authentication-and-authorization). User chat
and TTS do not need an administrator session. Cookies are host-only and secure
in production; rotation of the signing secret invalidates both session domains.

## Retrieval, models and speech

| Symptom | What to check and do |
| --- | --- |
| Environment-selected model is ignored | A recognized saved ID in `backend/model_selection.json` takes precedence. Use authorized Model Selection to change it. If its configs are absent, the registry falls back to the first available Qwen entry. |
| Catalog says available but loading fails | Both configs exist, but weights/tokenizer shards, compatible dependencies or sufficient memory may be missing. Check the actual directories and local loader logs. The catalog is not a complete model validation. |
| Gemma does not appear | The active IDs are `qwen3.5-2b` and `qwen3-4b`. Local Gemma folders/model-class compatibility do not register another entry. A new catalog choice needs a deliberate code/contract/test change. |
| CUDA unavailable/out of memory | Verify the matching cu118 profile on supported hardware, driver and actual FP16/NF4 kernels. `run_gpu.cmd` and `GEN_DEVICE=cuda` fail instead of falling back; the normal `auto` path can fall back with a warning. Reduce competing GPU use or choose CPU; memory capacity is not certified by config presence. |
| No hits after changing embeddings | Re-ingest knowledge using the new embedder. Different dimensions are skipped; equal dimensions from different models are still semantically incompatible. Model selection does not re-embed stored vectors. |
| JSON/PDF embedding exhausts RAM | Lower `EMBEDDING_BATCH_SIZE` (default 8, range 1-64), restart, and rerun the [memory checks](../backend/tests/TESTING.md#embedding-micro-batches-f025). Budget for model weights, the generator and concurrent work as well as a batch. This setting does not raise upload quotas. |
| Upload fails after partial embedding progress | Check memory/model availability and writable/free OS temporary space. Completed counts represent staged vectors, before knowledge writes. A failed embedding batch leaves the knowledge unchanged; there is no automatic checkpoint resume. A failure during later DB writes retains the existing autocommit/replacement behavior. |
| Fixed fallback answer despite successful startup | Check that valid knowledge was imported and scores meet the threshold. Generation failure can fall back to a retrieved answer. `/health` alone cannot establish answer quality or ingestion success. |
| Voice health returns 503 | Loading is lazy; distinguish `voice_loading` from unavailable/model-failed codes. Check Whisper dependencies/cache and `STT_LOCAL_FILES_ONLY`; the first download is disallowed when that flag is set. |
| Microphone unavailable or WebSocket fails | Use localhost/HTTPS, allow microphone access, check browser AudioWorklet support and the proxy upgrade/timeout settings. Voice disabled by controller returns 403. Another active transcription can be rate-limited. |
| Text works but no spoken answer | The checkbox applies to voice messages and is checked by default. Check `/tts/health`, separate `.venv-tts`/`TTS_PYTHON`, local models and caches. TTS is independent of Whisper. Browser autoplay denial leaves manual audio controls; unavailable TTS leaves the text answer. |
| Offline TTS tries to download | Whisper's local-only flag does not control TTS. Prepare its local English/Persian weights and voice prompt, plus the G2P cache; verify them with `scripts/check_speech.py --offline` in the prepared speech setup. |
| Unloading or disabling voice does not stop TTS | Controller voice actions concern Whisper and `/voice*`. `/tts/*` is a separate public, rate-limited API; chat/voice feature switches do not authorize or disable it. |

## Known documentation and test limits

The detailed retention disclosure is currently HTML-commented in
`frontend/index.html`. Rendered policy strings can pass an offline assertion
while remaining invisible. Publish visible policy/contact information separately;
this guide does not claim the UI already provides it.

Use `backend/tests/browser_smoke.py` and the current journey suite for browser
regressions. The older `browser_security_smoke.py`/`.cjs` contains stale notice
visibility and browser-`speechSynthesis` opt-in assertions. Its historical CSP
results are not a current end-to-end pass; update that fixture before using it
as an acceptance gate. Offline tests use adapters/doubles and cannot certify
real-model quality, GPU capacity or physical microphone behavior.
