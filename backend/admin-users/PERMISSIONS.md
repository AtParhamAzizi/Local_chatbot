# Administrator permission matrix

`backend/admin_policy.py` is the authoritative operation registry. The common
admin guard resolves the exact Flask endpoint and HTTP method, then checks the
operation's policy against the current server-loaded account. It never infers
authority from a URL prefix, basename, or username. Unknown endpoints, methods,
assets, or operation IDs fail closed, including for administrators.

## Roles and compatibility

- Public operations require no login; submitting login still requires pre-login CSRF proof.
- Authenticated operations require a current active session but no feature grant.
- Feature operations require their named permission or `is_admin=true`.
- Account management requires `is_admin=true`. A username of `administrator`, a
  `user_control` grant, or a wildcard grant does not satisfy this requirement.
- Existing feature grants (`json_upload`, `pdf_upload`, `models`, `chats`,
  `system_reports`, `controller`) retain their meaning; no database migration is
  needed. Legacy `*` grants cover only explicitly registered feature operations,
  never account management or unregistered operations. Inactive accounts fail.
- Role checks never waive authentication, HTTPS/origin policy, or mutation CSRF
  proof. Session revocation after role/permission changes remains unchanged.

## HTTP operations

GET operations also authorize HEAD and automatic OPTIONS. Mutation-only routes
authorize automatic OPTIONS with their owning policy, without executing the
mutation. An additional method is not authorized unless it has a registry entry.
Every POST/PATCH/DELETE requires CSRF proof before handlers or side effects.

| Method | Route | Operation | Required authority |
| --- | --- | --- | --- |
| GET / POST | `/login` | `login.view` / `login.submit` | Public; POST requires pre-login CSRF |
| GET | `/login.css`, `/login.js` | `login.assets` | Public |
| GET | `/` | `dashboard.view` | Authenticated |
| GET | `/dashboard.css`, `/dashboard.js`, `/theme.js`, `/admin-csrf.js` | `dashboard.assets` | Authenticated |
| GET | `/admin/api/session` | `session.read` | Authenticated |
| POST | `/logout` | `session.logout` | Authenticated |
| GET / POST | `/admin/api/admin-users` | `users.list` / `users.create` | Administrator role |
| PATCH / DELETE | `/admin/api/admin-users/<id>` | `users.update` / `users.delete` | Administrator role |
| GET | `/admin/api/stats` | `qa.stats` | `json_upload` |
| GET | `/admin/api/documents` | `qa.list` | `json_upload` |
| POST | `/admin/api/upload` | `qa.upload` | `json_upload` |
| DELETE | `/admin/api/documents/<id>` | `qa.delete` | `json_upload` |
| GET | `/admin/api/pdf/stats` | `pdf.stats` | `pdf_upload` |
| GET | `/admin/api/pdf/documents` | `pdf.list` | `pdf_upload` |
| POST | `/admin/api/pdf/upload` | `pdf.upload` | `pdf_upload` |
| DELETE | `/admin/api/pdf/documents/<id>` | `pdf.delete` | `pdf_upload` |
| GET / POST | `/admin/api/models` | `models.list` / `models.select` | `models` |
| GET | `/admin/api/chat/sessions` | `chats.sessions` | `chats` |
| GET | `/admin/api/chat/messages` | `chats.messages` | `chats` |
| GET | `/admin/api/chat/stats` | `chats.stats` | `chats` |
| DELETE | `/admin/api/chat/sessions` | `chats.delete_session` | `chats` |
| DELETE | `/admin/api/chat/logs` | `chats.delete_message` | `chats` |
| GET | `/admin/api/logs` | `logs.list` | `system_reports` |
| GET | `/controller` | `controller.view` | `controller` |
| GET | `/admin/api/controller/status` | `controller.status` | `controller` |
| POST | `/admin/api/controller/action` | `controller.dispatch` plus an explicit action operation | `controller` |

Account deletion rejects the caller's own account by ID. Other accounts have no
username-based deletion exemption, including the bootstrap account. This is not
a last-active-administrator guarantee; avoid removing the remaining administrator
role. The directory API returns `current_user_id` for the UI's self-delete guard.

## Pages and their assets

Only the following exact `/admin/` filenames are registered. Each listed stem's
`.html`, `.css`, and `.js` files share its view operation. There is no generic
Flask static-file fallback, nested-path/basename fallback, or wildcard asset
policy. The standalone `/controller` alias has the same policy as its HTML page.

| Stem under `/admin/` | View operation | Required authority |
| --- | --- | --- |
| `admin` | `qa.view` | `json_upload` |
| `admin_pdf` | `pdf.view` | `pdf_upload` |
| `adminmodel` | `models.view` | `models` |
| `adminchat` | `chats.view` | `chats` |
| `adminlogger` | `logs.view` | `system_reports` |
| `controller` | `controller.view` | `controller` |
| `admin-user-control` | `users.view` | Administrator role |

`GET /admin/api/session` returns server-computed `allowed_pages`. The dashboard
uses this list for navigation visibility rather than maintaining its own role
or username bypasses. Hiding navigation is not the access-control boundary.

## Controller subactions

All require `controller` and have individual registered operation IDs:

- `voice_enable`, `voice_disable`, `chat_enable`, `chat_disable` map to
  `controller.<action>` and may only then update feature state.
- `generator_load`, `generator_unload`, `embedder_load`, `embedder_unload`,
  `voice_load`, `voice_unload` map to `controller.<action>` and may only then
  dispatch a background model action.
- Unknown/unregistered actions return 403, even if added to a dispatch dictionary
  and even for an administrator. Add a policy deliberately before enabling one.

User-side `/controller/features` and `/controller/features.js` remain public
feature flags; they neither expose administrative status nor mutate state.

## Allow/deny regression tests

From the repository root, run:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s backend/tests -v
```

On other platforms use `python -B -m unittest discover -s backend/tests -v`.
The standard-library suite uses Flask clients and isolated database/model doubles;
it never uses deployment credentials, connects to MySQL, loads weights, starts
model threads, or writes controller state. Its independent expected matrix covers
every registered route/method and page asset, each single-feature role, empty
grants, legacy wildcards, the unprivileged `administrator` username, renamed true
administrators, and anonymous users. It tests each controller action's allow/deny
and side effects, unknown route/method/action denial for administrators, static
alias bypasses, CSRF, session rejection, and origin-check ordering. Coverage
checks fail if a route, asset, or controller action is added without a policy and
an expected matrix entry. These tests are not a live MySQL/browser/model suite.
