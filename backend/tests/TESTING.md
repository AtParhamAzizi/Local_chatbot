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
