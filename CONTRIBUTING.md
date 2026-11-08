# Contributing

Read [ARCHITECTURE.md](ARCHITECTURE.md) first. It maps the code; the
[README](README.md) gives application setup. A first contribution can use the
offline environment below without MySQL, Docker, CUDA, model weights or speech
downloads. The repository uses Python and vanilla JavaScript; there is no
frontend build step.

## Start with the offline environment

Clone your repository checkout and open a terminal at its root. Use CPython
3.12 or 3.13, Git and Node.js 20+; the source checker invokes Node for JavaScript
syntax. These are contributor prerequisites, distinct from the application's
[native dependency-lock targets](requirements/README.md).

PowerShell:

```powershell
python -m venv .venv-tests
.\.venv-tests\Scripts\python.exe -m pip install -r backend/tests/requirements.txt
.\.venv-tests\Scripts\python.exe -B backend/tests/check_source.py
.\.venv-tests\Scripts\python.exe -B backend/tests/run_tests.py --repeat 2
```

Linux:

```sh
python3.12 -m venv .venv-tests
.venv-tests/bin/python -m pip install -r backend/tests/requirements.txt
.venv-tests/bin/python -B backend/tests/check_source.py
.venv-tests/bin/python -B backend/tests/run_tests.py --repeat 2
```

Dependency installation needs an index or prepared wheel cache. Test execution
uses offline guards that forbid real database/network work, model imports and
model-directory reads. Do not point tests at an application database or install
the full CUDA requirements merely to run unit tests. Keep `.venv-tests`, the
application `.venv`, and optional `.venv-tts` separate. Missing prerequisites and
empty test selections fail explicitly.

For the interactive application, follow [native setup](README.md#setup) or
[local Compose setup](README.md#docker-setup-windows-macos-and-linux), including
explicit migration and first-admin setup. Native Python does not load `.env`.
Use `backend/main.py` for both services; neither standalone speech demo is the
chatbot launcher.

## Find the owning module

| Change | Owner and related documentation |
| --- | --- |
| User interaction and local voice playback | `frontend/`, `backend/voice.py`, `backend/speech.py` |
| Admin authentication, CSRF, pages and APIs | `backend/admin_routes/`, `admin_sessions.py`, `admin_csrf.py`, [permission matrix](backend/admin-users/PERMISSIONS.md) |
| Permission or controller action | `backend/admin_policy.py` and the owning route; UI visibility alone never authorizes access |
| Ingestion, retrieval or model lifecycle | `backend/services/`; external DB/model/PDF adapters stay behind that boundary |
| Shared configuration | `backend/settings.py`, [configuration guide](README.md#validated-configuration-f021); define defaults/types once |
| Database schema | New ordered files in `backend/migrations/`, the migration runner and [migration guide](backend/migrations/README.md) |
| HTTP or WebSocket contract | [API schemas and compatibility guide](docs/api/README.md), handlers and contract tests |
| Deployment/runtime constraints | `backend/serving.py`, `bootstrap.py`, [operations](docs/operations.md), [decisions](docs/decisions/README.md) |

Imports use flat backend names. Run supported scripts from the repository root;
test fixtures add `backend/` to the import path. Importing factories must not
load models, bind sockets, migrate tables or start retention. HTTP modules call
services; services must remain independent of Flask requests. Use injected
adapters/factory overrides for tests instead of patching old `admin.py` globals.

## Scope and verify a change

Keep cleanup separate from behavior changes. Preserve UTF-8 Persian text and RTL
behavior. Add a meaningful regression for changed behavior; documentation-only
or mechanical cleanup does not need an implementation-mirroring test. Do not
edit released migrations or silently relax authentication, origin checks, CSRF,
anonymous ownership, permission policies or request limits to make a test pass.

The default checks above are the starting point. Focused discovery is useful
while editing, for example:

```text
python -B backend/tests/run_tests.py --pattern test_admin_authorization.py -v
python -B backend/tests/run_tests.py --pattern test_api_contracts.py -v
```

Use the test environment's interpreter in place of `python`. For SQL behavior,
run the disposable MySQL suite; for UI interaction, run browser journeys. Their
setup, isolation guarantees and commands are in [TESTING.md](backend/tests/TESTING.md).
They provision their own fixtures and never accept a deployment DB override.
Real GPU/model and physical microphone checks are separate manual evaluations;
report them only when actually performed. The legacy security browser smoke has
outdated privacy/playback assertions; use the current journey suite for UI work.

The source gate checks syntax, identical imports in uninterrupted blocks,
tracked bytecode and changed-line whitespace. It is not a general formatter,
linter or type checker. Keep caches, environments, model weights, databases,
private `.env` files, credentials and browser artifacts out of commits. Use
`git diff --check`, `git status --short`, and review the staged diff before
committing; the gate rejects even force-added `.pyc`/`.pyo`/`__pycache__` files.

## Keep documentation tied to code

Update the README's setup/access behavior and the architecture guide when their
claims change. For new routes, update the OpenAPI contract, exact operation
policy and independent permission/contract regressions together. Preserve v1
compatibility or follow the published major-version process. Add an
[architecture decision record](docs/decisions/README.md) for durable changes to
process layout, persistence, identity, model/dependency policy or public APIs.

In a pull request, describe the concrete before/after behavior, reason, tests
actually run and any remaining limitations. Link relevant decisions and migration
or operations changes. Never paste passwords, session/CSRF cookies, user messages
or private dumps into issues or test reports. Follow the repository's [license](LICENSE).
