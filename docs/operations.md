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
knowledge writes/replacement. F027 publishes knowledge and successful job
completion atomically; publication failure rolls back the whole import.

With audit retention enabled, authorized system-report operators can see
`json/pdf embedding progress: completed=N, total=M` in admin logs during an
upload. No input text appears in these progress entries. Final embedding counts
do not establish successful publication; poll the job until `completed`. Service consumers can receive the
same counts using `IngestionService(progress=callback)` without a Flask context.

## Inspect an import before publication (F029)

On either admin import page, select a JSON/PDF file and the replacement option,
then choose **بررسی و پیش‌نمایش بدون انتشار**. Inspect duplicates, expected
chunks, estimated embedding work, samples and record/page issues. Correct errors
and preview again; warnings identify pages that will contribute no text. OCR
scanned pages before importing when their content should be searchable.
Only then use the separate publication button. Changing the file or replacement
option requires another preview. Preview never submits a job or changes searchable
knowledge; normal request audit metadata may still be recorded.

The complete bounded file is validated/extracted, but samples show only first,
middle and last prepared records/chunks, at most 240 characters per text field.
There are at most 20 issues per report. Use the listed one-based record/page
numbers to find errors and repeat preview after correction. Estimates count local
embedding inputs, batches and characters. They do not predict seconds, GPU RAM
or money; compare work counts against your hardware measurements. Existing JSON
questions are still embedded even when skipped. No model is loaded by preview.

Preview is synchronous, transient and advisory; its database checks can become
stale before publication, which validates again. It uses the existing upload
byte/page/text/chunk quotas and six-attempt/five-minute client budget shared
with publication/retry. It does not reserve queue capacity. Do not retry 429
immediately; obey `Retry-After`. See the [API details](api/README.md#ingestion-preflight-13-f029).

## Durable ingestion jobs (F026)

Apply migrations through 6 before deploying; follow the backup/stopped-writer procedure
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
admin session or a fresh login. The upload page shows the ID, stage/counts and
Cancel/Retry controls. It remembers the last job Location in tab session storage
and reconnects after reload/navigation. JSON and PDF jobs require their respective
upload grants; POST/DELETE also require CSRF.

GET includes `progress: {stage, source, completed, total}`, `checkpoint: 'upload'`
when a usable saved upload exists, and `retryable`. Stages are `extraction`,
`chunking`, `embedding`, `publishing`. PDF extraction/chunking count pages;
JSON extraction counts one parse and chunking counts validation/deduplication
rows. Embedding and publishing count unique questions/PDF chunks. Counts belong
to their stage and reset on retry. Publishing at total still requires `completed`
to establish a committed result.

DELETE the Location cancels queued **or running** work; repeating succeeds.
Completed/failed jobs return 409. Cancellation and the final publication commit
serialize on the job row: cancellation winning means the entire knowledge
transaction rolls back; publication winning means DELETE returns 409 and the
complete import/result is retained. Old PDF versions stay searchable until commit.

Cancellation bound: after a successful DELETE, no later publication can commit.
Worker cancellation is cooperative, checked at each 1 MiB upload restore chunk,
JSON validation row, PDF extraction page, page chunking operation, embedding
batch (at most `EMBEDDING_BATCH_SIZE`, 64 inputs maximum), and publication record.
At most the currently started unit finishes before the attempt stops and its
temporary vectors/transaction close. A publication record includes its SQL and
audit calls. Upload parsing/hashing and PDF opening are also single bounded-input
units (20/50 MiB upload limits). These are **work-unit bounds, not a guaranteed
number of seconds**: an in-flight native PDF/model operation, SQL/driver wait or
audit call is not preempted. Smaller embedding batches reduce cancellation latency.
A hung native call requires serving-process restart; its canceled attempt remains
fenced, and the new pool cannot replay it without an explicit retry. Do not use
thread termination to interrupt shared models. Pool capacity remains occupied by
an in-flight call until it returns, even though logical cancellation frees queue
admission immediately.

POST `Location/retry` with an empty body retries failed/canceled work using the
same ID and saved upload. It returns 202 with Location/Retry-After; repeating while
queued is idempotent. Running/completed or expired/missing checkpoints return 409;
queue capacity still applies. Every claim has a new attempt token, so an old
canceled worker cannot overwrite progress or publish after a retry. Extraction,
chunking and embeddings are recomputed: the durable safe checkpoint is the
checksum-verified upload, not model-specific intermediate vectors. A crash before
commit rolls back all knowledge; a crash after commit retains the completed job
and result, avoiding ambiguous replay. Independent duplicate submissions retain
the existing unique-question/PDF-hash rules; transport idempotency for a lost
initial submission response remains a separate feature.

`INGESTION_WORKERS=1` bounds concurrent jobs (1-4). A MySQL connection-held named
lock elects one pool per database; standby processes retry election. Claims use
row locks and `SKIP LOCKED`. `INGESTION_POLL_SECONDS=1` controls idle polling
(0.05-30). Lost lock connections stop further claims and are checked between
embedding batches and before publication. Size RAM/model/staging capacity for
the worker count **plus** simultaneous chat/speech work.

`INGESTION_QUEUE_CAPACITY=16` caps queued plus running jobs (1-256); full or busy
admission returns 503. Worst-case active payload storage is 50 MiB times that
capacity, plus database overhead (800 MiB by default). Payloads use 1 MiB chunks
to stay below ordinary MySQL packet limits. Completed jobs delete bytes in the
publication transaction. Failed/canceled jobs retain bytes for 24 hours after
their terminal timestamp for retry, then become ineligible even before cleanup.
The worker removes up to 1024 expired payload chunks per claim/poll; submissions
and retries also sweep expired checkpoints. Cleanup waits for a live worker or
submission when serving is stopped. Retried jobs retain their active upload.
Allow storage for up to 24 hours of failed/canceled uploads in addition to active
capacity; queue capacity does not cap that retained history. Job metadata/results
persist, without automatic age deletion.
Database backups/binlogs can retain those bytes and need normal protection.

On restart/takeover, queued work remains queued and is processed. Previously
running jobs become `failed` with code `ingestion_interrupted`, keeping the upload
checkpoint for an explicit retry within 24 hours. Terminal states stay unchanged.
Check safe worker logs, database connectivity,
disk space and job errors when a queue stops advancing. Graceful shutdown drains
active work while holding the pool lock; abrupt termination follows recovery
above. Before upgrading from F026, drain/stop old workers. Already interrupted
pre-F027 attempts may have partial autocommitted knowledge; inspect/clean those
manually. A legacy started job without F027 progress is never offered a retry
checkpoint. Migration 6 cannot reconstruct ownership of old partial knowledge.

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
