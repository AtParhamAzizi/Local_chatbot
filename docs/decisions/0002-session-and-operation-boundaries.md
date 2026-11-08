# 0002: Separate sessions, ownership and operation authorization

Date recorded: 2026-11-08

Status: Implemented; recorded from the current code

## Context and decision

Public chat, anonymous feedback and privileged administration have different
authority. An IP address, public user label, menu visibility or username cannot
serve as an authorization credential.

[admin_sessions.py](../../backend/admin_sessions.py) stores hashed opaque admin
session IDs, fixed/idle deadlines and account session versions in MySQL. Signed
host-only cookies carry the opaque ID; role/grants come from the active account
loaded on each request. Logout revokes the session; security changes increment
its version and revoke all account sessions. Production admin access requires
HTTPS, including login resources.

[admin_policy.py](../../backend/admin_policy.py) maps exact operations, methods,
assets and controller actions to policies. Unknown operations are denied;
account management requires `is_admin`, including for wildcard-granted accounts.
[admin_routes/auth.py](../../backend/admin_routes/auth.py) and
[admin_csrf.py](../../backend/admin_csrf.py) enforce mutation CSRF, including
pre-login proof. Origin checks add a transport boundary, not authentication.

[chat_sessions.py](../../backend/chat_sessions.py) uses a separate cookie/key
domain to establish anonymous ownership. Feedback queries constrain message ID
and the current owner hash. Foreign, unknown and legacy-unowned messages share
the same 404. Privileged chat operators intentionally have cross-owner review
and deletion authority; chat cookies never grant that authority.

## Alternatives and consequences

Client-selected user IDs/IPs would allow cross-user feedback. Username or menu
checks would leave alternate routes/assets unprotected. Cookie-only sessions
without server revocation would retain authority after logout/account changes.
The current design requires a DB session lookup and a cookie/CSRF form flow for
integrations; there is no API-key/bearer-token alternative. Anonymous cookie
loss/expiry or signing-key rotation cannot reclaim ownership of older messages.

Public TTS and feature flags are not administrator endpoints. Voice controller
switches do not gate TTS, and hiding controls is not an access-control change.
See the [permission matrix](../../backend/admin-users/PERMISSIONS.md) and
[API authentication contract](../api/README.md#authentication-and-authorization).

## Verification and reconsideration

[test_admin_authorization.py](../../backend/tests/test_admin_authorization.py),
[test_chat_ownership.py](../../backend/tests/test_chat_ownership.py) and
[test_api_contracts.py](../../backend/tests/test_api_contracts.py) exercise denial,
revocation, CSRF, ownership and contract boundaries using isolated adapters.
Any new identity, conversation or integration-authentication scheme needs an
explicit policy/contract and regressions before changing these guarantees.
