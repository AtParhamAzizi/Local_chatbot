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

For local VS Code use, select **Chatbot: Check database schema** in Run and Debug
and press F5. This profile reads the same root `.env` as the application debugger,
so credentials need not be copied into a terminal. After stopping all application
writers and preparing the verified backup below, select **Chatbot: Upgrade database
schema**, press F5, and enter its absolute backup path and a MySQL administrator
account/password when prompted. Run the check
profile again before restarting **Chatbot: Local development**. These profiles
explicitly select development mode; use deployment settings for production.

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

Successful output includes `"schema_version": 9`, `"latest_version": 9`, empty
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

   For local development, the repository includes a backup helper. From the root:

   ```powershell
   python -B backend/backup_database.py --env-file .env --development --user root --ask-password
   ```

   Or select **Chatbot: Back up database** in VS Code Run and Debug, then press
   F5. Both methods reuse the host, port and database from your local `.env`.
   Use the backup/administrator account that can export this database (replace
   `root` above if yours differs). Its password prompt is hidden. The VS Code
   backup and upgrade profiles prompt for that account and a hidden password;
   they do not change the restricted serving account or your `.env`. When the
   configured account already has dump permissions, omit `--user/--ask-password`.
   A successful application login does not imply export or ALTER privileges.
   The helper discovers
   `mysqldump` on PATH or in standard Windows MySQL Server/Workbench locations;
   it never installs tools or asks for passwords in command arguments. It creates
   a new private folder under `~/Local_chatbot_backups` for every run and prints
   the absolute `backup.sql` path, byte count and SHA-256. On this Windows machine
   the default parent is `%USERPROFILE%/Local_chatbot_backups`. Its manifest records
   `restore_tested: false`; restoration is a separate operator check below.

   Choose another directory or executable explicitly if needed:

   ```powershell
   python -B backend/backup_database.py --env-file .env --development --user root --ask-password --directory 'D:/private-backups' --mysqldump 'C:/Program Files/MySQL/MySQL Server 8.0/bin/mysqldump.exe'
   ```

   Backup directories must be outside the repository. A Windows ACL or POSIX
   directory mode restricts each new backup folder to the current user. Passwords
   use a temporary private MySQL option file, removed with client diagnostics on
   exit; failed, empty, interrupted and timed-out dumps are removed instead of
   published as SQL backups. The default dump timeout is 600 seconds; `--timeout`
   accepts 1-3600. A hard OS kill can leave the private incomplete folder: do not
   use it as a backup. Use a MySQL dump client compatible with the database server
   and an account with the privileges described below. The script changes no
   schema, runs no upgrade and does not verify restoration.

   `--env-file` is explicit and applies registered application settings only to
   the backup process; it does not export them into your terminal. Files support
   single-line `KEY=value` entries, optional `export`, quotes and comments, with
   literal values and no variable interpolation or multiline strings. Existing
   process settings are overridden by that file; `--development` explicitly
   selects local mode afterward. Production should use its deployment environment
   and omit `--development`. After restore-testing, paste the printed `backup.sql`
   path into the **Chatbot: Upgrade database schema** debugger prompt. Leaving
   that prompt blank produces the `--backup: expected one argument` error.

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
| 5 | `005_ingestion_jobs.sql` | Creates durable ingestion job metadata and chunked upload payloads; existing knowledge is unchanged |
| 6 | `006_ingestion_progress.sql` | Adds nullable durable stage/count progress; existing rows and knowledge stay intact |
| 7 | `007_qa_embedding_profile.sql` | Adds nullable JSON embedding metadata to Q&A; old identities remain unknown |
| 8 | `008_pdf_embedding_profile.sql` | Adds nullable JSON embedding metadata to PDF documents; originals/vectors remain intact |
| 9 | `009_chunk_embedding_profile.sql` | Adds nullable JSON embedding metadata to PDF chunks; old identities remain unknown |
| 10 | `010_embedding_indexes.sql` | Creates durable shadow generations/vectors and serving/request/rollback state; originals and legacy vectors remain unchanged |

For F035, upgrade through **10** before serving. The background builder freshly
embeds source text into resumable generations and validates before switching;
it never guesses legacy vector identities. Preserve immutable old model paths
for serving and rollback. See [index migration operations](../../docs/embedding-index-migration.md).

For F034, stop writers and apply all versions through 9 before serving. Existing
vectors have no provable embedding profile and remain NULL: migration does not
label or regenerate them. They stay visible in document administration but are
excluded from retrieval, even if their dimensions match. F035 freshly embeds
their source text into separate shadow vectors; explicit replacement imports
also remain available to generate profiled base vectors. Preserve
original answers when refreshing Q&A vectors only. Do not manually copy the active
profile into legacy rows. See [embedding compatibility](../../docs/embedding-profiles.md).

For F027, stop/drain the F026 workers before upgrading through version 6. New
publication and job completion commit atomically. Old started jobs without F027
progress are ineligible for checkpoint retry: their earlier autocommit writes
could be partial and require manual inspection/cleanup. Queued old uploads are
safe to process. No upgrade can reconstruct which old JSON rows were partially
written. Follow [job operations](../../docs/operations.md#durable-ingestion-jobs-f026)
for cancellation bounds, retry windows and payload cleanup.

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
