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
do not establish successful publication; poll the job until `completed`. Service consumers can receive the
same counts using `IngestionService(progress=callback)` without a Flask context.

## Durable ingestion jobs (F026)

Apply migration 5 before deploying; follow the backup/stopped-writer procedure
in the migration guide. Serving accounts need SELECT/INSERT/UPDATE/DELETE on
`ingestion_jobs` and `ingestion_job_payloads`, without DDL privileges.
Startup launches the ingestion supervisor only after schema/model validation.
Application factories and imports start no workers.

The admin pages submit JSON to `POST /admin/api/upload/jobs` and PDFs to
`POST /admin/api/pdf/upload/jobs`. A successful **202** means the bounded file
bytes and metadata committed to MySQL. Submission does no content extraction or
embedding; file transfer and durable storage still take time. Poll the response's
`Location` for `queued`, `running`, `completed`, `failed` or `canceled`, UTC
timestamps, safe errors and the final `result`. Validation of JSON rows and PDF
contents happens in the worker, so invalid contents are reported as failed jobs.
Keep the job ID/Location when disconnecting; reconnect using the same authorized
admin session or a fresh login. The upload page shows the ID and polls while open.
DELETE that Location cancels queued jobs and frees their payload; repeated
cancellation succeeds. Running/finished jobs return 409. JSON and PDF jobs require
their respective upload grants; POST/DELETE also require CSRF.

`INGESTION_WORKERS=1` bounds concurrent jobs (1-4). A MySQL connection-held named
lock elects one pool per database; standby processes retry election. Claims use
row locks and `SKIP LOCKED`. `INGESTION_POLL_SECONDS=1` controls idle polling
(0.05-30). Lost lock connections stop further claims and are checked between
embedding batches and before publication. Size RAM/model/staging capacity for
the worker count **plus** simultaneous chat/speech work.

`INGESTION_QUEUE_CAPACITY=16` caps queued plus running jobs (1-256); full or busy
admission returns 503. Worst-case active payload storage is 50 MiB times that
capacity, plus database overhead (800 MiB by default). Payloads use 1 MiB chunks
to stay below ordinary MySQL packet limits. Raw bytes are removed on terminal
transitions; job metadata/results persist, without automatic age deletion.
Database backups/binlogs can retain those bytes and need normal protection.

On restart/takeover, queued work remains queued and is processed. Previously
running jobs become `failed` with code `ingestion_interrupted`, and their payloads
are removed. Completed, failed and canceled states remain unchanged. Jobs are
not automatically replayed: current knowledge publication can have partial
autocommitted writes, including PDF replacement. Inspect knowledge before
resubmitting interrupted work. Check safe worker logs, database connectivity,
disk space and job errors when a queue stops advancing. Graceful shutdown drains
active work while holding the pool lock; abrupt termination follows recovery
above. Checkpoints, running-job cancellation and atomic replacements remain F027/F028.

Legacy upload endpoints use the same queue but wait up to 240 seconds. A 503
timeout does not cancel durable work; use asynchronous submission for clients
that need a job ID and reliable polling across HTTP disconnects.

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
