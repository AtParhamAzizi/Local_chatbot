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
- Only the real application's table initializers create the schema. Fixtures
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

The integration directory intentionally has no `__init__.py`, so default
`unittest` discovery and `run_tests.py` do not enter it or provision Docker.
Direct integration discovery fails without the runner's in-memory provisioned
target; it never falls back to `DB_*`. `test_mysql_isolation.py` belongs to the
offline suite and checks provisioning/cleanup, ignored deployment variables,
connection/socket restrictions and patch restoration without contacting Docker
or MySQL.
