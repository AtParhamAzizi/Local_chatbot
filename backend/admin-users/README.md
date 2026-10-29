# Admin users

The application creates the MySQL table named `admin-users` at startup but never
seeds an account or password. From the repository root, with the same database
environment variables used by the application, run:

```powershell
python backend/setup_admin.py
```

Use an interactive terminal on the server. Setup creates `administrator` with a
password you choose and confirm (at least 12 characters); input is hidden, and
passwords are not accepted as arguments or written to logs. Until setup completes,
there is no initial account to sign in with. There is no unauthenticated web setup.

Setup inserts the account in a single transaction and serializes concurrent setup
commands with a database-scoped MySQL advisory lock, released on connection close.
If interrupted before commit, rerun the command. If commit already succeeded,
rerunning detects the existing account and changes nothing. No partial account is
saved, even if setup is interrupted during password confirmation.

Upgrades preserve every existing account, password hash, status, and permission.
Setup refuses to create an account if **any** account exists, even when it is
disabled or named something other than `administrator`. It is not a password reset
or recovery tool. Existing installations that still use the old shared password
should change it through **Admin user control**; upgrades do not reset it silently.
