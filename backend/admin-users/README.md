# Admin users

Explicit migrations create the MySQL table named `admin-users` but never seed
an account or password. Startup and setup only check the recorded schema version.
From the repository root, configure the same intended `APP_ENV`, `DB_HOST`,
`DB_PORT` and `DB_NAME` as the application. For native local development,
explicitly set `APP_ENV=development` in the terminal before these commands;
neither Python nor this setup script reads `.env`. See the
[native setup examples](../../README.md#setup) for protected credential input.
Using the separate migration DB account, initialize a fresh, empty database:

```powershell
python -B backend/schema_migrations.py upgrade
python -B backend/schema_migrations.py status
```

Confirm version 4 and empty pending/incomplete lists. Switch `DB_USER` and
`DB_PASS` to the restricted serving account, keeping the same database and
profile, before creating the application administrator:

```powershell
python -B backend/setup_admin.py
```

The MySQL migration/serving accounts are distinct from the web administrator
created here. Setup requires ordinary DML privileges, not schema or MySQL
account-creation rights.

For existing databases, first follow `backend/migrations/README.md`: upgrades
require a tested backup and an explicit migration command, never startup DDL.

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
