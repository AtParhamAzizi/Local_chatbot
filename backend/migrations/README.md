# Versioned MySQL migrations (F020)

Application startup and administrator setup **only check** the recorded schema;
they never create or alter tables. Importing `schema_migrations` does no database,
model, application, server or configuration work. Run these commands from the
repository root with the deployment's validated environment and dependencies:

VS Code's `launch.json` environment applies only to the launched debugger process,
not standalone terminal commands. For **local development only**, first run
`$env:APP_ENV = 'development'` in the same PowerShell terminal to match the local
debugger profile. Production commands must retain the real deployment settings;
the migration CLI never silently falls back to development. Configuration failures
report setting names without their values, before any database access.

```powershell
python -B backend/schema_migrations.py status
python -B backend/schema_migrations.py upgrade
```

An empty, already-created MySQL database needs no backup. Existing databases,
including unversioned or partially initialized installations, require a nonempty
backup file **before any migration DDL or ledger writes**:

```powershell
python -B backend/schema_migrations.py upgrade --backup C:/private-backups/chatbot-before.sql
python -B backend/schema_migrations.py status
```

Successful output includes `"schema_version": 4`, `"latest_version": 4`, empty
`pending` and `incomplete` lists. An unversioned database reports version 0 without
being modified. `status` is a read-only inspection command and exits successfully
even when upgrades are pending; application startup requires the latest version
and fails closed with instructions otherwise. Connection errors and incompatible
or tampered history fail both commands. No models are loaded by either command.

## Before upgrading an existing database

1. Stop **all** application writers, including old releases, retention workers,
   standalone launchers and management commands. In Compose, use
   `docker compose stop chatbot`, leaving MySQL running. A migration advisory lock
   prevents simultaneous migration commands, not normal application writes.
2. Use the MySQL client tools matching your server and a protected backup location
   outside the repository. For example (one command, password entered at the prompt):

   ```powershell
   mysqldump --host=127.0.0.1 --port=3306 --user=chatbot_migrator --password --single-transaction --routines --triggers --events --hex-blob --no-tablespaces --set-gtid-purged=OFF --default-character-set=utf8mb4 --result-file=C:/private-backups/chatbot-before.sql chatbot_db
   ```

   Check the exit code. `--result-file` avoids PowerShell stdout encoding changes;
   do not use PowerShell `>` to create SQL dumps. Do not include `--databases` or
   `--add-drop-database`: the dump must be restorable into an isolated database.
   Never put passwords in command arguments, source code or logs. Restrict backup
   access: dumps contain credentials, sessions, messages and knowledge documents.
   Account privileges must cover the objects being dumped, including any custom
   triggers/routines/events. For container MySQL, run the dump inside the database
   container and copy its result file to protected host storage; do not rely on a
   temporary container filesystem as your only backup.
3. **Restore-test the dump into an isolated database** with no application traffic
   and separate credentials. With that database pre-created, a client invocation
   can use `mysql --host=... --user=... --password --database=chatbot_restore_check
   --execute="SOURCE C:/private-backups/chatbot-before.sql"`. Compare per-table row
   counts and representative Unicode text, embedding bytes, account hashes,
   permissions and feedback. Keep a copy of the matching old application release.
   The migration command checks file accessibility/nonzero size and records its
   SHA-256 and absolute path, **not** dump validity, freshness or restore success;
   the operator must establish those before supplying `--backup`.
4. Run `upgrade --backup ...` using a schema-scoped migration account with
   `SELECT`, `INSERT`, `UPDATE`, `CREATE`, `ALTER`, `INDEX` and `REFERENCES` access.
   Ordinary serving no longer needs `CREATE`/`ALTER`; retain the application's
   normal DML permissions and `SELECT` on `schema_migrations`. Use the same DB_NAME
   and server for the migration and the application. Tables are InnoDB on MySQL 8.4.
5. Check `status`, compare original records as above, then restart the application.
   Retention resumes on startup and may legitimately expire old chat/log records;
   compare migration preservation **before** starting it. Restrict and expire
   backups under your privacy policy; they do not inherit application retention.

## Versions and guarantees

| Version | Migration | Existing-record behavior |
| --- | --- | --- |
| 1 | `001_initial_schema.sql` | Creates missing original application tables, never inserts accounts or replaces existing tables |
| 2 | `002_admin_sessions.sql` | Adds missing `session_version` with default 1 and creates `admin_sessions`; existing hashes, permissions and session versions remain intact |
| 3 | `003_log_message_text.sql` | Widens legacy CHAR/VARCHAR log messages to TEXT; existing TEXT/MEDIUMTEXT/LONGTEXT is left alone |
| 4 | `004_chat_ownership.sql` | Adds missing nullable ASCII `owner_hash`; legacy messages remain unowned, existing ownership and feedback remain intact |

The baseline accepts the documented legacy table columns, including databases
that already received some or all earlier opportunistic changes. Missing tables
are created in dependency order. Unsupported legacy layouts fail before DDL.
Existing columns and rows are not dropped or rewritten; migration-only guards
make interrupted `CREATE`/`ADD` steps resumable. Table/column presence checks are
part of explicit migrations, **never startup**. This is not a general repair tool
for arbitrary manually altered schemas or a validation of every constraint/type.

`schema_migrations` records ordered versions, names, SHA-256 checksums, running/
applied/failed state, start/completion times and backup identity. A record is
marked applied only after all its DDL succeeds. MySQL DDL commits implicitly:
the runner does not pretend that rollback can undo an interrupted upgrade. The
named lock is connection-held and released on close, including process death.
Checksums include SQL and step metadata; never edit released migrations or mark
an incomplete version applied by hand. Add a new ordered migration instead.
Unknown, gapped, newer or checksum-mismatched history fails closed.

## Failure, interruption and recovery

These migrations are **forward-only**. There is intentionally no downgrade
command: dropping ownership/session columns or narrowing log text after new
writes can lose data. Backups and this recovery procedure are mandatory for
existing installations even though the current upgrades are additive/widening.

1. Keep writers stopped. Inspect `status` and the database's `schema_migrations`
   rows with a privileged local client. A failed command records `failed` when
   the connection permits it; process/connection loss may leave `running`.
   Neither state permits serving. Do not delete history to bypass this check.
2. Fix the underlying problem (permissions, storage, connection, unsupported
   schema). Inspect the affected DDL to establish which steps committed. Preserve
   the original pre-upgrade backup, and do not edit the released SQL/checksum.
3. If the documented idempotent steps are safe to complete, explicitly run:

   ```powershell
   python -B backend/schema_migrations.py upgrade --resume --backup C:/private-backups/chatbot-before.sql
   python -B backend/schema_migrations.py status
   ```

   Applied versions are not rerun. The incomplete version is retried with
   column/table guards; later versions run only after it succeeds. Merely rerunning
   `upgrade` without `--resume` cannot silently accept an interrupted version.
4. If forward completion is unsafe, restore the tested pre-upgrade dump into a
   **clean replacement database**, not on top of the partially upgraded schema.
   Verify records and point the matching old application release/credentials at
   that database while writers remain stopped. A pre-versioning dump has no
   migration ledger: use its old release, or migrate the restored copy before
   starting this release. Retain the failed database for restricted diagnostics
   until recovery is verified. Never issue blanket DROP commands against a
   computed deployment target. Restore loses any writes made since the backup,
   which is why all writers must stay stopped during the operation.

## Containers and tests

For a fresh Compose deployment, run `docker compose build chatbot`, then
`docker compose run --rm chatbot python backend/schema_migrations.py upgrade`
(its dependency starts/waits for MySQL), then `docker compose up -d chatbot`.
For upgrades, stop the app first, mount the protected backup read-only into the
one-off container and pass its container path to `--backup`; preserve the host
copy. The image's normal entry point still serves only after a schema check.

`python -B backend/tests/run_tests.py --repeat 2` checks read-only startup/status,
version/checksum enforcement, backup requirements, lock exclusion, failure and
explicit resumption with offline doubles. `python -B backend/tests/run_mysql_tests.py
--repeat 2` provisions disposable MySQL and upgrades a populated legacy fixture
with Unicode, embeddings, PDF/chunks, account permissions/hashes, history/feedback
and logs. It also adopts an already-current unversioned schema without altering
ownership or active sessions. This suite requires Docker; it never targets a
configured deployment database.
