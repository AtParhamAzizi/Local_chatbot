# Offline unit-test foundation

Use Python 3.12 or 3.13 on Windows or Linux. From the repository root, create
a separate environment with only the lightweight test dependencies:

```powershell
python -m venv .venv-tests
.venv-tests\Scripts\python -m pip install -r backend/tests/requirements.txt
.venv-tests\Scripts\python -B backend/tests/run_tests.py --repeat 2
```

On Linux, use `.venv-tests/bin/python` instead of `.venv-tests\Scripts\python`.
Dependency installation needs a package index or a pre-populated wheel cache;
test execution does not need network access. Do not install the application's
full `requirements.txt` just to run these tests.

The default runner discovers every `test_*.py` here, including existing Flask
security/validation regressions. It fails on real database or socket access,
real model/PDF dependency imports, and reads from the repository's model
directories. No MySQL server, CUDA runtime, GPU, model files, PDF library,
speech engine, or deployment credentials are required. Model, speech and PDF
adapters are doubles; scoring still uses real NumPy. Browser smoke scripts and
the manual `backend/test.py` retrieval script are deliberately excluded.

`--repeat 2` rediscovers and runs the suite twice in one interpreter to catch
leaked module patches, environment settings and mutable fixtures. Add `-v` for
individual test names, or `--pattern test_core_units.py` for the focused units.
Standard discovery also works:

```text
python -B -m unittest discover -s backend/tests -v
```

`test_core_units.py` covers:

- Question/document SHA-256 and salted PBKDF2 password hashes.
- Current Persian lexical normalization: Arabic yeh/kaf mapping, Unicode
  case-folding, unique terms and empty inputs. It does not introduce the broader
  shared normalization pipeline planned in F033.
- PDF extraction noise, bounded chunks, overlap and natural text boundaries.
- Cosine scores, incompatible/zero embeddings, lexical overlap, PDF weighting,
  ranking/top-k, source metadata and connection cleanup using in-memory rows.
- Retrieval threshold/merge behavior with an explicit deterministic embedding
  double; all unconfigured model operations fail immediately.
- Strict permission flags, permitted pages, stored grants and JSON/schema
  parsing boundaries. The existing authorization suite retains the complete
  endpoint/role allow-deny matrix.

`core_support.py` supplies scoped application/model doubles and restores
`sys.modules`, `sys.path`, environment variables and patched functions after
each unit. Keep future tests deterministic: fixed vectors and clocks, no live
services, downloads, production data or actual model loaders. These tests do
not certify real model quality, MySQL compatibility or PDF extraction itself;
those belong in separately provisioned integration suites.

## Factory and startup boundaries (F018)

`test_ingestion_jobs.py` (F026) runs the production MySQL job store's statements
through a disk-backed SQLite adapter, translating MySQL time/lock syntax. It
checks durable queued and terminal states across fresh service/store instances,
interrupted-running recovery, payload chunk/checksum handling, bounded admission,
queued cancellation, safe failures, bounded worker concurrency and standby
takeover, plus real Flask submission/polling/auth/CSRF without model work during
submission. This is offline protocol evidence, not MySQL syntax certification.
The disposable MySQL suite covers actual migrations, payload persistence and
background ingestion using real SQL/PDF extraction and deterministic embeddings.
Run the focused offline cases with `--pattern test_ingestion_jobs.py --repeat 2`.
Browser journeys named `background ... upload` check queued/running status and
ID display, completion, safe failure and restored controls using mocked job HTTP
responses. The PDF case doubles preview blob creation to keep Chrome's private
viewer resources outside the browser fixture. These cases test the real upload
pages and shared polling/CSRF helper; they do not certify database durability.

`test_settings.py` checks invalid input for every F021 registry setting, all numeric
range endpoints, choice/boolean parsing, alias/path precedence, production and
cross-setting requirements, immutable definitions, standalone speech scopes, and
secret-free diagnostic output/errors. Run it with `--pattern test_settings.py`.
Factory tests also reject invalid generator/STT/TTS settings before route imports
or database/model work. The registry and its diagnostic CLI use only the standard
library; neither should import services or start any external work.

`test_application_factories.py` checks clean-process imports without valid
configuration or model/service imports; fresh app assembly with database, audit,
model, thread and server work forbidden; stable endpoint names, per-app hooks
and limiters; explicit controller-state loading; per-app idempotent controller
registration; invalid-configuration failure; and worker-only production startup.
`test_retention.py` checks the explicit serving bootstrap's table/state/model/
retention order and that low-level development serving starts no cleanup worker.

Fixtures import handlers from `user` / `admin_routes` and construct apps explicitly via
`application.create_ui_app()` / `create_admin_app()`. There are no module-global
`user.ui_app` or `admin.admin_app` objects. Use `application.create_apps()` when
controller hooks are needed too. Application construction itself performs no
database/model initialization; route execution still needs the scoped adapters.

## Focused modules and services (F019)

`test_service_boundaries.py` imports/registers each administrator responsibility
without importing its siblings and compares the complete app to the frozen
pre-split paths/methods/endpoint names in `admin_route_contract.py`. It also tests
HTTP-to-service dispatch through injected replacements, including unchanged
chat/model/upload response shapes and controlled ingestion failures.

Direct service tests run without Flask contexts or valid deployment settings:
JSON whole-batch validation, dedup/replacement/skip counts, PDF quotas/cleanup,
duplicate handling, embed-before-replace ordering, page metadata/float32 storage,
connection cleanup and safe persisted failure state; lifecycle warmup/selection
order, rejected selections, unload/reset, status snapshots, per-model exclusion,
asynchronous failure/retry and thread-start failures. `test_core_units.py` now
tests chunking and retrieval services directly, retaining real NumPy scoring.

Use factory service overrides or patch the service's injected adapters for route
tests, not `admin.py` globals. Auth and account doubles target their respective
`admin_routes` modules. `core_support.clear_admin_routes()` clears cached route
modules inside scoped fixtures so imports cannot capture a previous fixture's
database/logger/auth dependencies. The original authorization, validation,
privacy, MySQL and browser suites remain the client-compatibility regressions.

## Embedding micro-batches (F025)

`test_embedding_batches.py` checks actual adapter call sizes, uneven final
batches, input/vector/page ordering, JSON deduplication, environment validation,
partial progress callbacks/audit counts, malformed later-batch output, and
temporary-file cleanup after inference/connection/persistence failure. A failed
later batch cannot start knowledge writes or delete a previous PDF. A weakref
check ensures the prior activation array is released before the next model call.

The offline memory acceptance budget is **32 MiB of additional tracked Python/
NumPy allocations per ingestion** at batch size 8. Both maximum-size fixtures
contain 256 inputs: JSON uses 4096-character questions and 16000-character
answers; PDF uses 128 extracted pages with 256 chunks through the real splitter.
The embedding double allocates and touches an `8 x 512 x 1024` float32 activation
(16 MiB) for each batch. Tests assert 32 calls, exactly 256 persisted vectors,
ordered vector markers, and progress at every batch boundary. NumPy allocation
tracking must be active or the test fails. On November 8, 2026, measured peaks
were 21.11 MiB for JSON and 16.86 MiB for PDF. This budget excludes prebuilt raw
fixtures, real model weights/native workspaces, HTTP parsing, and concurrent
requests; it verifies the bounded service pipeline, not deployment RSS.

```powershell
.venv-tests\Scripts\python -B backend/tests/run_tests.py --pattern test_embedding_batches.py -v
```

An optional acceptance runner uses the installed local Qwen embedder on CPU and
real PyMuPDF extraction, with database writes captured in memory and database/
network access forbidden. It never starts the apps or loads the generator. Use
the full application environment and complete local weights; no downloads or
extra test dependencies are required. Declare the whole-process peak resident
memory budget before running:

```powershell
.venv\Scripts\python -B backend/tests/embedding_benchmark.py --memory-budget-mib 6144 --batch-size 8 --threads 4
```

On Linux use `.venv/bin/python`. This exercises 256 mixed-length JSON questions
(the first eight reach the real 512-token limit) and a generated 128-page PDF
yielding 256 chunks. Add `--long-json-count 256` for sustained full-length JSON
stress; that extended CPU run is separate from the default acceptance fixture.
It checks batch sizes, vector counts, page/order metadata, and first/boundary/last
vectors against single-input inference. It reports elapsed time and OS peak
resident memory (Windows peak working set or Unix `ru_maxrss`) and fails if the
declared budget is exceeded. The budget is an acceptance assertion, not an OS
memory limit; batch size controls the workload. Peak RSS includes imported
libraries and embedding weights, excludes generator/speech/concurrent requests,
and is cumulative across both fixtures in the isolated process. Recheck after
changing hardware, weights, dependencies, or batch size.

Acceptance recorded November 8, 2026: Windows AMD64, AMD Ryzen 5 3500U,
Python 3.13.14, torch 2.7.1+cu118 (embedding on CPU), transformers 5.20.2,
PyMuPDF 1.28.2 and NumPy 2.5.4. The default command above passed, including the
single-input vector comparisons, with dimension 1024 and four inference threads:

| Fixture | Persisted vectors | Model batches | Ingestion + comparison time | Cumulative peak resident memory | Declared budget |
| --- | --- | --- | --- | --- | --- |
| JSON, 8 long + 248 short questions | 256 | 32 | 120.47 s | 4413.31 MiB | 6144 MiB |
| PDF, 128 pages | 256 | 32 | 310.85 s | 4413.31 MiB | 6144 MiB |

Heavy dependency import time precedes the per-fixture timer. These measurements
cover captured database writes, not a live MySQL server or a serving application.

## API contracts (F022)

`docs/api/openapi.json` defines the v1 payload schemas and route requirements;
`docs/api/README.md` defines versioning, compatibility, authentication, errors,
pagination and the optional voice protocol. Run their offline checks with:

```powershell
.venv-tests\Scripts\python -B backend/tests/run_tests.py --pattern test_api_contracts.py -v
```

`test_api_contracts.py` exercises every documented HTTP success status through
real Flask handlers and guards, using doubles only for database/model/speech
adapters. It checks populated/empty collections, legacy wrappers and routes,
dates, request rejection, permissions, CSRF, errors, redirects, binary speech,
version headers and WebSocket frame shapes. It also compares the API inventory
to registered routes and the existing frozen legacy administrator inventory.
Contract checks run in default offline CI discovery without new dependencies.

`api_contract_support.py` asserts only the JSON Schema subset used by this
document, rejects unsupported keywords and external references, and includes
negative controls for missing/extra fields, wrong types and invalid variants.
It does not replace a general OpenAPI validator. Update schemas, compatibility
notes and representative route cases together when intentionally changing the
contract; new success statuses must have exercised cases before the suite passes.

## Flask + disposable MySQL integration (F014)

Install the small integration dependency profile in the same test environment:

```powershell
.venv-tests\Scripts\python -m pip install -r backend/tests/mysql/requirements.txt
.venv-tests\Scripts\python -B backend/tests/run_mysql_tests.py --repeat 2
```

On Linux, use `.venv-tests/bin/python`. Docker Engine with Compose v2 (or Docker
Desktop in Linux-container mode) must be running. The first run pulls `mysql:8.4`
and therefore needs registry access; cached-image runs need only the local Docker
daemon. Allow memory for MySQL and up to 1 GiB of temporary database storage.
Missing Docker/dependencies, failed startup, empty discovery, failed isolation,
failed assertions or failed cleanup produce a nonzero exit, never a successful
skip. Start Docker Desktop yourself if it is stopped.

The runner always provisions its own MySQL container; it has **no external-DB
mode** and accepts no database URL or deployment credentials. In particular:

- A random Compose project, random `chatbot_f014_<32 hex>` schema, fresh passwords
  and a random **127.0.0.1-only** host port are generated for every invocation.
  The non-root `f014_app` account has access only to that schema.
- `mysql/compose.yaml` is independent of the application's `compose.yaml`.
  An explicit empty `mysql/compose.env` replaces automatic developer `.env`
  loading, and inherited `DB_*`, `COMPOSE_*` and `F014_*` variables are discarded.
  Application imports happen under a cleared, test-only environment.
- All PyMySQL connection factories require the exact provisioned configuration;
  socket/DNS access is limited to that loopback port. Blocked attempts are recorded
  and fail the run even if an application error handler catches the exception.
- The real migration runner explicitly creates the application schema. Fixtures
  verify `SELECT DATABASE()` and the provisioned configuration before clearing
  a fixed table allowlist. Each test clears committed data before and after its
  run; a transaction rollback alone cannot isolate autocommitted route writes.
- MySQL's data directory is **tmpfs**, with no developer bind mounts or persistent
  data volumes. `finally` removes only this unique project with
  `docker compose ... down --volumes --remove-orphans`, including after setup/test
  failures. Never run destructive fixtures against an existing database.

If the process is forcibly killed or the daemon disappears, normal cleanup
cannot run. The runner prints the exact `chatbot-f014-<32 hex>` project name.
Inspect only its containers with
`docker ps -a --filter "label=com.docker.compose.project=<printed-project>"`.
After verifying the labels, remove those container IDs using
`docker rm --force --volumes <verified-container-id>`. Find its network with
`docker network ls --filter "label=com.docker.compose.project=<printed-project>"`
and remove that verified ID with `docker network rm <verified-network-id>`.
Do not substitute the application's project name. Stopping/removing the test
container discards its tmpfs data; do not stop the developer's MySQL service.

`mysql/test_api.py` uses Flask clients with real session, CSRF, authorization,
parsing, upload, retrieval, logging and persistence code. It verifies both HTTP
statuses/public error codes and separately queried MySQL effects:

- Valid/invalid/disabled logins, hashed opaque sessions, logout replay rejection
  and session invalidation after account security changes.
- Anonymous, CSRF-invalid and insufficient-role mutations, with unchanged data.
- UTF-8 JSON ingestion, duplicate/replace semantics, stored float32 embeddings,
  malformed/mixed-invalid imports and real PyMuPDF extraction of generated PDFs.
- PDF duplicate/replacement handling and foreign-key cascade deletion of chunks.
- Knowledge-backed and fallback chat replies, committed history, separate cookie
  owners, ignored client identity, and zero-retention behavior.
- Feedback persistence/overwrite/repeated ratings plus anonymous, foreign,
  unknown, legacy and invalid requests that leave ratings unchanged.
- Actual MySQL `SIGNAL` trigger failures during session creation, JSON insertion,
  PDF chunk insertion, chat insertion, feedback update and administrator chat
  deletion. Assertions cover safe errors, no rejected writes, failed-PDF state
  under current autocommit behavior, and recovery after removing the trigger.
  Separate connection-boundary `OperationalError` injection covers unavailable
  connections without stopping another service or changing deployment credentials.

Only embedding/generation and speech adapters are deterministic doubles; model
loaders/imports and model-directory reads remain forbidden. These tests do not
evaluate real model quality, voice, browser behavior, or guarantee atomic
multi-row imports (the application currently uses autocommit).

`mysql/test_schema.py` independently rebuilds only that provisioned database
using the released baseline and populated legacy fixtures. It checks upgrade
preservation of every original column/row (including Unicode, embedding BLOBs,
account hashes/permissions, PDF relationships, history/feedback and logs), schema
version/backup records, nullable legacy ownership, fresh initialization, adoption
of already-upgraded unversioned schemas and existing sessions, no-op repeated
upgrades, backup enforcement, real advisory-lock exclusion, and explicit recovery
after a failure following implicitly committed DDL. Tests compare records before
any application retention worker starts. Offline `test_schema_migrations.py`
covers protocol/failure boundaries without Docker. Operational backup and restore
requirements are in `backend/migrations/README.md`.

The integration directory intentionally has no `__init__.py`, so default
`unittest` discovery and `run_tests.py` do not enter it or provision Docker.
Direct integration discovery fails without the runner's in-memory provisioned
target; it never falls back to `DB_*`. `test_mysql_isolation.py` belongs to the
offline suite and checks provisioning/cleanup, ignored deployment variables,
connection/socket restrictions and patch restoration without contacting Docker
or MySQL.

## Browser regression smoke suite (F015)

Use Python 3.12/3.13 with the offline test dependencies, Node.js 20 or newer,
and an installed Chrome or Edge. From the repository root:

```powershell
.venv-tests\Scripts\python -m pip install -r backend/tests/requirements.txt
npm ci --prefix backend/tests/browser
.venv-tests\Scripts\python -B backend/tests/browser_smoke.py --channel chrome --repeat 2
```

Use `.venv-tests/bin/python` on Linux. The committed npm lockfile pins the
Playwright test dependencies; package installation requires registry access or
a populated cache. Execution uses installed browsers, with no model downloads,
MySQL, Docker, microphone hardware or application credentials. Edge runs with
`--channel msedge`. To use Playwright's downloaded Chromium instead:

```text
node backend/tests/browser/node_modules/@playwright/test/cli.js install chromium
python -B backend/tests/browser_smoke.py --channel chromium
```

On Linux, provision the browser's required system libraries as well (Playwright's
`install --with-deps chromium` command can install them with suitable privileges).
An existing installation can be reused by setting `PLAYWRIGHT_MODULE` to an
absolute **@playwright/test** package directory, not just `playwright-core`.
Leave that variable unset to use this repository's pinned installation.

The named scenarios exercise the actual served HTML/CSS/JavaScript:

- Persian document/input/message direction, mixed Persian/Latin text rendered as
  text rather than HTML, bubble placement and no horizontal overflow at desktop
  (1280 px) and mobile (390 px) widths; keyboard sending and recommendations.
- Quick-question filling, visible pending-request state, disabled controls while
  sending, and usable text/voice controls after completion.
- Failed feedback with no optimistic selection or saved rating, retry by clicking
  the same button, successful selection, and replacing a rating.
- Invalid login, password visibility, successful form login with real CSRF
  handling, logout and rejection of a replayed signed cookie.
- Administrator, PDF-only and no-grant navigation: allowed items and initial iframe,
  real page switching, blank no-grant content and direct forbidden-page/API checks.
- Theme persistence across reloads, login and iframe changes, same-origin tab/frame
  synchronization, actual color changes and separate user/admin origin storage.
- Microphone permission denial, missing device, worklet/connection failure,
  unavailable backend and unsupported APIs, preserving drafts and text chat.
  Mid-recording device disconnection keeps the partial transcript and releases
  capture; a successful mocked stop sends one final transcript and closes audio.
- Local TTS playback after a voice transcript, silence-based submission,
  disabling playback, unavailable synthesis, stopping playback for another
  recording and manual audio controls after autoplay denial. Speech synthesis
  is a deterministic fixture adapter; real speech/model quality remains separate.

Each scenario gets a fresh browser context and resets only the fixture's
in-memory accounts/sessions/history, faults and limiters. Cases run serially with
zero automatic retries; `--repeat 2` repeats them with fresh contexts. Locators
and network responses are awaited instead of fixed sleeps. Focus a flow with
`--grep "feedback|microphone"`; no matching cases is a failure, not a successful skip.
Unhandled errors from pages/iframes or unexpected external application requests
also fail the suite. Required packages/browsers must exist; they are never
silently skipped or automatically installed by the runner.

The runner binds two **ephemeral IPv4 loopback ports**, never an existing user or
admin service, so the running chatbot need not be stopped. There is no target-URL
override. A fresh runner-issued token authorizes fixture reset/state endpoints,
and every case verifies that handshake before navigation. These endpoints exist
only in `browser_support.py`, not in the deployed application. The server imports
applications with a cleared test environment; deployment database/session secrets
are also removed from the browser subprocess environment. All real DB connection
factories, model imports, model-file reads and outbound Python sockets are
forbidden. The fixture asserts that real DB/model adapters were not called even
if an error handler swallowed their exceptions. Servers, threads and process
patches are cleaned up on success, failure and runner timeout.

Only account lookup/session persistence, model/retrieval adapters, admin read data,
voice health and chat persistence are doubles. Login credential verification,
cookies, CSRF, authorization, static pages, chat/feedback routes and browser UI
behavior are real. Feedback failure is injected at the fixture's persistence
boundary; happy-path chat/login/feedback responses are not fulfilled by browser
route mocks. Deterministic AudioContext, media tracks, worklet and WebSocket doubles
exercise the real frontend lifecycle without recording physical audio; browser
fake-device flags provide a second safeguard. This suite does not evaluate actual
MySQL (use F014), STT quality, physical microphone permissions, real audio transport,
or production HTTPS/proxy behavior.

Every invocation prints its unique ignored `backend/tests/browser-artifacts/<id>`
directory. `report.json` lists case results; failed cases retain screenshots,
Playwright traces, browser-error attachments and assertion context. To inspect a
failure, use:

```text
node backend/tests/browser/node_modules/@playwright/test/cli.js show-trace <path-to-trace.zip>
```

Artifacts contain only dummy fixture data, including disposable test credentials,
but should still be handled as test diagnostics rather than deployed assets.
Old artifact directories are not pruned automatically. `test_browser_fixture.py`
validates credential/permission checks, fault injection, fixture controls and patch
restoration in the default offline suite without launching a browser.

The smoke suite initially caught real LTR message-text inheritance and a login
theme script that redirected to login because it used an authenticated asset URL.
Messages now explicitly use RTL text without changing bubble order. Login loads
the same shared script through the exact public `/login-theme.js` alias; dashboard
assets remain authenticated, with authorization-matrix coverage.

The older `browser_security_smoke.py` / `.cjs` remains a separate F011/F012 CSP,
worklet and WebSocket compatibility check on fixed ports 5000/8000 with its own
optional speech dependencies. It is not the primary journey regression runner
and is not invoked by this suite or default unittest discovery. Its notice
visibility and browser-`speechSynthesis` opt-in assertions predate the current
commented notice and local TTS UI. Update that fixture before treating it as a
current acceptance check; historical results do not certify current UI behavior.

## Continuous integration (F016)

`.github/workflows/ci.yml` runs on every pull request, pushes to `main`, merge
queue groups, and manual dispatch. There are no path filters or successful skips
for missing prerequisites. All jobs use disposable GitHub-hosted CPU runners,
read-only repository permissions, SHA-pinned actions and no deployment secrets.
Pull requests use `pull_request`, never privileged `pull_request_target` execution.

The independent required jobs are:

| Check | What runs |
| --- | --- |
| Source checks | Python 3.12 compiles all Git-tracked/nonignored new `.py` files in memory, without importing them or writing bytecode; Node 22 checks all `.js`, `.cjs`, `.mjs` files with `node --check`; Git checks whitespace in proposed changes. |
| Offline | Python 3.12/3.13 on Ubuntu 24.04 and Python 3.13 on Windows 2025, each running the entire guarded default suite twice. Only `tests/requirements.txt` is installed. |
| Flask / disposable MySQL | Python 3.13, the small integration dependency profile and Docker Compose; the real Flask/MySQL suite runs twice against its own tmpfs MySQL 8.4 container. No configured database is used. |
| Browser smoke (Chromium) | Python 3.13, Node 22, `npm ci` from the lockfile, and separately provisioned Chromium/system libraries; all primary browser journeys run twice against ephemeral fixtures. |

`Merge checks` always runs after these jobs and fails unless **every** result is
`success`, including every offline matrix entry. Failed assertions, test errors,
empty discovery, failed setup/cleanup, cancellation and skipped prerequisite jobs
cannot produce a green gate. Test commands are not piped through tools that hide
exit codes and do not use `continue-on-error`. Browser JSON reports, screenshots
and traces are uploaded even after failure and retained for seven days. Logs
and artifacts contain dummy test data; do not introduce deployment credentials.

### Enable the merge requirement

After the workflow first runs, a repository administrator must configure a
GitHub branch ruleset/protection rule for `main`: require pull requests and the
**`Merge checks`** status check from GitHub Actions before merging. Disable bypass
for administrators if the project requires that guarantee. Workflow files alone
cannot enable repository protection. Require the aggregate name, not individual
matrix names, so Python/platform updates do not silently remove the requirement.
Keep `merge_group` enabled if using a merge queue. Canceled superseded runs are
not passing runs; the newest commit must pass.

### Reproduce checks locally

From the repository root, with the dependency profiles/prerequisites above:

```text
python -B backend/tests/check_source.py
python -B backend/tests/run_tests.py --repeat 2 -v
python -B backend/tests/run_mysql_tests.py --repeat 2
python -B backend/tests/browser_smoke.py --channel chromium --repeat 2
```

Source checks require Git, Python and Node, but no Python third-party packages.
They use Git's inventory rather than recursively traversing ignored environments,
node_modules or model assets. Deleted files are excluded. The Python check never
imports application startup or model loaders. Whitespace policy checks trailing
spaces, blank lines at EOF and spaces before tabs, on changed lines only; it does
not reformat legacy code.

The F023 maintenance checks also reject identical Python imports (including
aliases) repeated within an uninterrupted import block. Each function, class
and conditional branch is checked independently; imports separated by other
statements are allowed because names may have been rebound. This small AST check
does not infer unused imports, enforce import order or validate types. No general
formatter, linter or type checker is configured.

The source gate separately reads the Git index without ignore filtering and
rejects tracked `__pycache__` contents, `.pyc` and `.pyo` files, even if missing
from disk. Repository-wide ignore rules keep regenerated bytecode out of normal
adds; the index check catches forced adds. To untrack an accidentally added file
while keeping the local cache, use `git rm --cached -- <path>`. Run checks with
`python -B` to avoid creating bytecode during validation. `test_ci_checks.py`
exercises both repeated-import failures and a temporary Git repository with
ignored, force-added and subsequently untracked bytecode.

`--base <commit>` chooses the whitespace comparison base. Local runs default to
`HEAD` (including staged, unstaged and nonignored untracked changes). CI uses the
PR base SHA, merge-group base SHA or previous push SHA, with full Git history;
manual dispatch uses `HEAD^`. An initial push's zero SHA means the empty tree.
Invalid/missing bases fail rather than silently omitting whitespace checks.

### Prove the gate rejects regressions

On a disposable local branch/worktree, deliberately change
`RequestValidationTests.assert_error` in `test_request_validation.py` to expect
HTTP 200 instead of 400. Run the **same CI command**
`python -B backend/tests/run_tests.py --repeat 2 -v`: it must exit nonzero with
API-contract failures. Restore the change before continuing. Similarly, change
the `qa.stats` operation policy in `backend/admin_policy.py` from `json_upload`
to `PUBLIC`; the permission matrix must fail the
same command. Never commit either deliberate break into the real change.
For a remote end-to-end acceptance check, submit such a temporary PR and verify
that `Offline` and `Merge checks` are red and the protected merge is unavailable,
then close the PR. `test_ci_checks.py` also guards assertion/error exit-code
propagation, empty discovery and fail-closed syntax/whitespace prerequisites.

Local acceptance on November 5, 2026: source checks and actionlint 1.7.12 passed;
176 offline tests, 18 MySQL tests and 18 Chrome journeys each passed twice.
The deliberate API expectation and permission-policy breaks each made the exact
offline CI command exit 1, and both changes were restored. All 256 combinations
of success/failure/cancelled/skipped prerequisite results were tested against the
workflow's actual aggregate shell script; only all-success exited 0. Hosted
GitHub execution and repository rules were not changed here. Local Linux package
provisioning was blocked by PyPI DNS failures, and the Chromium download returned
a CDN location restriction; browser validation used installed Chrome instead.

### GPU and real-model evaluations are not merge checks

No merge job installs the full application/CUDA requirements, downloads weights,
loads real embedding/generation/Whisper models, starts `backend/main.py`, or runs
the manual retrieval script `backend/test.py`. Offline import/file/socket guards
remain active; MySQL and browser runners keep their separate strict boundaries.
CUDA visibility is disabled and model-hub offline flags are set as defense in
depth. Package, browser and container provisioning still require public registry
access; test isolation does not mean dependency installation is network-free.

Real-model/GPU quality or hardware/audio evaluations remain separate manual work
in an explicitly provisioned environment with approved models and disposable
evaluation data. There is no automated quality evaluator yet. A future evaluation
workflow should be manually dispatched, separate from `Merge checks`, and never
run unreviewed PR code on a credentialed/self-hosted GPU runner. The older optional
fixed-port browser compatibility smoke is also outside the primary merge suite.

## Dependency-profile regression checks (F017)

The default offline suite includes `test_dependency_profiles.py`. It uses the
small `packaging` test dependency to check active requirement ranges against
all four native CPU/CUDA locks, plus exact/hash syntax, shared constraints,
CPU/GPU graph separation and the older-GPU pins. Installer/recorder tests use
scoped doubles and temporary files: no wheels, models, CUDA or databases are
installed/contacted by these tests. Wrong/missing/extra distributions, unsupported
targets, profile mixups and failed pip commands fail closed.

Actual fresh application-profile recreation uses `scripts/dependencies.py`, not
this offline suite. Follow `requirements/README.md` for native interpreters,
hash-locked installation, CPU verification and explicit GPU hardware verification.
`requirements/VALIDATION.md` records the tested versions and limits. The manual
`Dependency profiles` workflow is deliberately separate from the fast merge gate;
hosted CUDA import-only results never certify GPU hardware or real-model quality.
