# Operating the chatbot

The [README](../README.md) owns installation commands and configuration examples;
this guide covers order, acceptance checks and recovery. Read the
[architecture](../ARCHITECTURE.md), [migration procedure](../backend/migrations/README.md)
and [troubleshooting guide](troubleshooting.md) for implementation and diagnosis.

## Choose the deployment path

| Path | Runtime and boundaries |
| --- | --- |
| Native Windows development | Recorded CPython 3.13 x64 environment; explicit `APP_ENV=development`; two threaded Flask listeners in one process, debugger/reloader off |
| Native Linux/WSL | Recorded CPython 3.12 x86-64 environment; development as above, or production Gunicorn with one worker/eight threads serving both ports |
| Local Compose | Linux containers, MySQL 8.4, development profile inside the chatbot container; both host ports published only on loopback |

The image defaults to production, but checked-in `compose.yaml` overrides it for
local HTTP. The local Compose database user can migrate its schema; production
must use the separate migration/serving grants documented in the README. Native
Windows cannot serve production; use a separately provisioned Linux/WSL environment.
ARM64 CPU container dependencies are outside the recorded x86-64 lock matrix.

Native Python and `run_gpu.cmd` read process environment variables, not `.env`.
Compose reads `.env` and injects only the variables declared by its service
configuration; adding an arbitrary setting to `.env` does not automatically pass
it to Python. VS Code launch settings apply to the debugger process, not a
separate migration terminal. Set the intended environment in every command's
launch context. Keep secrets stable/private and application paths writable only
where runtime state is expected.

## Embedding upload capacity

Set `EMBEDDING_BATCH_SIZE` before startup (default 8, accepted integers 1-64).
Native launches read the process environment; Compose passes the value from
`.env`. Restart after changing it. The count bounds each model invocation for
both JSON and PDF imports. Input quotas still cap uploads at 256 unique JSON
questions or 256 PDF chunks; increasing the batch size does not raise quotas.

Size batches against spare CPU RAM after accounting for embedding weights,
the generator, optional speech models, and concurrent work. This is a fixed
input-count limit, not an automatic RAM cap. Lower it to 1-4 when memory is tight;
the 512-token tokenizer limit remains in force. Validate your hardware/profile
with the optional [real-model benchmark](../backend/tests/TESTING.md#embedding-micro-batches-f025).
Its isolated-process budget excludes a running generator and concurrent requests.

Vectors stage in the OS temporary directory before database publication. Reserve
`input_count * embedding_dimension * 4` bytes per import (1 MiB at 256 x 1024),
plus filesystem overhead, and ensure the directory is writable. Staging files
close on every normal success/failure path; they are not durable job checkpoints.
Embedding failure, including a failed later batch or staging write, precedes
knowledge writes/replacement. Existing DB autocommit and PDF failure-state
behavior still apply once persistence begins.

With audit retention enabled, authorized system-report operators can see
`json/pdf embedding progress: completed=N, total=M` in admin logs during an
upload. No input text appears in these progress entries. Final embedding counts
do not establish successful publication; wait for the upload response. There is
no job polling endpoint or automatic resume. Service consumers can receive the
same counts using `IngestionService(progress=callback)` without a Flask context.

## Provision and start

1. Install/verify one dependency profile in a fresh application environment and
   prepare complete local embedding/generator files. Keep the optional TTS
   environment separate; text chat does not require it. Catalog `available`
   checks config files only, not complete weights or hardware capacity.
2. Provision the database and distinct serving/migration credentials. Validate
   configuration with `python -B backend/settings.py` using the same environment
   as the next command. `--diagnostics` emits only registry-approved public
   values; it omits secrets, database identity and model/interpreter paths.
3. For an empty database, use migration credentials for
   `python -B backend/schema_migrations.py upgrade`. For existing data, stop all
   writers and follow the restore-tested backup/upgrade procedure instead.
   Inspect `status`: current version is 4, with empty `pending` and `incomplete`.
   Its exit status alone does not establish currency.
4. Switch to the serving credentials. On a clean installation, run
   `python -B backend/setup_admin.py` interactively. It creates `administrator`
   with your chosen password only if no account exists. Existing accounts,
   including disabled ones, are left unchanged. There is no default password,
   public setup page or setup-based password reset.
5. Set the intended retention policy before serving. Automatic cleanup starts
   immediately after schema/model initialization. For existing/restored data,
   first inspect `python -B backend/retention.py --dry-run`. To pause retention
   job mutations while investigating, set `RETENTION_DELETE_ENABLED=0`; this
   does not pause collection or authorized admin deletions.
6. Start `python -B backend/main.py` with the serving credentials and keep its
   terminal or service logs available. Startup validates settings, constructs
   fresh apps, checks schema, reads feature state, warms the embedder/generator,
   starts retention, then accepts traffic. A text-model or schema failure prevents
   both listeners from becoming available.

For local Compose the fresh sequence is `docker compose build chatbot`,
`docker compose run --rm chatbot python backend/schema_migrations.py upgrade`,
then `docker compose up -d chatbot` and interactive admin setup. Follow the
README for model mounts and private `.env` values. Existing data requires the
migration guide's stopped-writer/backup procedure, not the fresh shortcut.
Use both Compose files for every GPU command when choosing `compose.gpu.yaml`.

## Accept the running service

Check the intended browser-facing origins, not just container logs:

- GET `/health` on the user origin returns 200 and `{"status":"ok"}`. It is
  liveness only; validate retrieval/generation and DB writes separately.
- Open `/login` on the admin origin. In production it must be HTTPS. Sign in
  with the created account and confirm the permitted menu/pages. A non-admin
  feature account must not gain account-management access.
- Import a small non-sensitive JSON Q&A, send its question through the served
  chat page, inspect the returned source/reply and apply feedback with the same
  chat cookie. No-hit fallback is also retained when chat retention is enabled.
- Confirm unauthorized admin API requests return 401 and missing-CSRF mutations
  are rejected with 403 using a disposable test record/account. An allowed
  origin alone never supplies authentication or authorization.
- If using speech, allow microphone access on localhost/HTTPS. `/voice/health`
  returns 503 while loading then 200 when ready; the browser should upgrade
  `/voice-stream` with 101. Independently verify `/tts/health` and audible local
  playback; unavailable TTS must leave the text answer usable.

The voice checkbox is checked by default. The detailed retention notice is
currently commented out in the template; publish visible lifetimes, pause
status and operator contact information separately. The voice notice does not
replace that disclosure. Automated template assertions do not verify visibility.

## Production transport and process policy

Supply explicit restricted DB credentials, a stable session secret and both
allowed-origin lists. Bind to loopback when the HTTPS proxy is local. Configure
the exact trusted proxy peer networks/hop count, preserve Host and the upstream
listening port, and forward WebSocket upgrades; see the README's
[reverse-proxy procedure](../README.md#reverse-proxies). Without a validated
forwarded scheme, production admin requests over upstream HTTP fail with 400.
Origin approval does not enable cross-origin admin CORS or credentialed user CORS.

Use `backend/main.py` under a service manager. One serving worker owns the shared
models, controller state, admission limits and retention worker. Additional
workers/preload, separate app launchers or an arbitrary `flask run`/Gunicorn
factory command do not preserve this supported startup contract. Each voice
WebSocket consumes one worker thread. Optional TTS is a private child process,
not another public web server. Controller voice/chat switches gate `/voice*`
and `/chat` respectively; they do not disable `/tts/*` or feedback.

## Change, back up and recover

Before an update, preserve the matching release, private launch configuration,
model identity/files and a restore-tested DB dump. Never edit applied migration
files or mark an incomplete ledger row applied manually. Follow the migration
guide for `--resume` and recovery into a clean replacement database; there is
no schema downgrade command. Reconcile retention before exposing a restore.

Model selection and feature state are runtime JSON files under `backend/`.
Native restarts preserve them if the directory persists. Compose restarts keep
them in the container layer, but replacing the container resets them unless you
explicitly persist them. MySQL, Whisper and Hugging Face caches use named
volumes; `docker compose down` preserves them, while `down -v` deletes them.
Changing `.env` does not rotate passwords in an existing MySQL volume.

A recognized saved model ID outranks `GEN_MODEL_ID`; unavailable requests fall
back to the first config-present catalog entry. Authorized selection resets
loaded text models and reloads lazily. Re-ingest all knowledge after changing
the embedder, even when vector dimensions match. Preserve the old environment
until the replacement passes dependency and application acceptance checks.

Monitor service-manager logs, public error codes, authorized System Logs and
`privacy_retention` completion status (`failed`/`partial` need attention).
`/health` alone cannot detect DB loss, a failed lazy speech loader or model
quality problems. Infrastructure/access logs, dumps/binlogs and exports have
independent retention. Cookie loss/key rotation cannot recover anonymous
ownership; account security changes revoke admin sessions. If all admin access
is lost, use a controlled operator recovery with backups rather than deleting
accounts or bypassing guards. See [troubleshooting](troubleshooting.md).
