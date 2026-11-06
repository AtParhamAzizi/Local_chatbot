"""Explicit MySQL upgrades; importing this module never connects or changes data."""

import argparse
import hashlib
import json
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


MIGRATION_DIR = Path(__file__).resolve().parent / 'migrations'
RECOVERY_GUIDE = 'backend/migrations/README.md'


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    table: str = ''
    column: str = ''
    widen_text: bool = False

    def sql(self):
        return (MIGRATION_DIR / f'{self.version:03}_{self.name}.sql').read_text(encoding='utf-8')

    def checksum(self):
        definition = json.dumps((self.version, self.name, self.table, self.column, self.widen_text))
        return hashlib.sha256((definition + '\n' + self.sql()).encode('utf-8')).hexdigest()

    def apply(self, cursor):
        statements = [statement.strip() for statement in self.sql().split(';') if statement.strip()]
        if self.column:
            cursor.execute(f'SHOW COLUMNS FROM `{self.table}` LIKE %s', (self.column,))
            existing = cursor.fetchone()
            if self.widen_text:
                if existing is None:
                    raise MigrationError('Required log message column is missing.')
                column_type = existing['Type'].lower()
                if column_type.startswith(('char(', 'varchar(')):
                    cursor.execute(statements[0])
                elif column_type not in ('text', 'mediumtext', 'longtext'):
                    raise MigrationError('Unsupported log message column type; inspect the schema before upgrading.')
            elif existing is None:
                cursor.execute(statements[0])
            statements = statements[1:]
        for statement in statements:
            cursor.execute(statement)


MIGRATIONS = (
    Migration(1, 'initial_schema'),
    Migration(2, 'admin_sessions', 'admin-users', 'session_version'),
    Migration(3, 'log_message_text', 'adminlogger', 'message', True),
    Migration(4, 'chat_ownership', 'chat_history', 'owner_hash'),
)
LATEST_VERSION = MIGRATIONS[-1].version
REQUIRED_COLUMNS = {
    'qa_documents': ('id', 'question', 'answer', 'question_hash', 'embedding', 'embedding_dim', 'created_at', 'updated_at'),
    'admin-users': ('id', 'username', 'password_hash', 'is_active', 'is_admin', 'permissions', 'created_at', 'updated_at'),
    'adminlogger': ('id', '_level', '_from', 'message', 'warninglevel', 'created_at'),
    'pdf_documents': ('id', 'filename', 'document_hash', 'file_size', 'page_count', 'chunk_count',
                      'embedded_chunk_count', 'embedding_dim', 'status', 'error_message', 'created_at', 'updated_at'),
    'pdf_chunks': ('id', 'document_id', 'chunk_index', 'page_start', 'page_end', 'content',
                   'embedding', 'embedding_dim', 'created_at'),
    'chat_history': ('id', 'user_id', 'ip_address', 'user_message', 'bot_response', 'feedback', 'feedback_at', 'created_at'),
}
LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INT PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    checksum CHAR(64) CHARACTER SET ascii NOT NULL,
    status ENUM('running', 'applied', 'failed') NOT NULL,
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    applied_at TIMESTAMP NULL DEFAULT NULL,
    backup_path TEXT NULL,
    backup_sha256 CHAR(64) CHARACTER SET ascii NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def connect_database():
    """Bypass the application audit logger, including on failed status checks."""
    import pymysql
    from database import DB_CONFIG

    return pymysql.connect(**DB_CONFIG)


def _tables(cursor):
    cursor.execute('SELECT TABLE_NAME AS name FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()')
    return {row['name'] for row in cursor.fetchall()}


def _records(cursor, tables):
    if 'schema_migrations' not in tables:
        return []
    cursor.execute('SELECT version, name, checksum, status FROM schema_migrations ORDER BY version')
    records = list(cursor.fetchall())
    if len(records) > len(MIGRATIONS):
        raise MigrationError('Database schema is newer than this application; use the matching release.')
    for index, record in enumerate(records):
        migration = MIGRATIONS[index]
        if record['version'] != migration.version or record['name'] != migration.name:
            raise MigrationError('Migration history is unknown or out of order; inspect it before proceeding.')
        if record['checksum'] != migration.checksum():
            raise MigrationError('Migration checksum mismatch; restore the original migration files, do not edit history.')
        if record['status'] != 'applied' and index != len(records) - 1:
            raise MigrationError('Incomplete migration is followed by later history; manual recovery is required.')
    return records


def _report(records):
    applied = [record['version'] for record in records if record['status'] == 'applied']
    return {
        'schema_version': applied[-1] if applied else 0,
        'latest_version': LATEST_VERSION,
        'pending': [migration.version for migration in MIGRATIONS if migration.version not in applied],
        'incomplete': [record['version'] for record in records if record['status'] != 'applied'],
    }


def schema_status(*, connect=None):
    connection = (connect or connect_database)()
    try:
        with connection.cursor() as cursor:
            return _report(_records(cursor, _tables(cursor)))
    finally:
        connection.close()


def require_current_schema(*, connect=None):
    report = schema_status(connect=connect)
    if report['pending'] or report['incomplete']:
        raise MigrationError(
            f"Database schema version {report['schema_version']}; required {LATEST_VERSION}. "
            f'Run python backend/schema_migrations.py upgrade before serving. See {RECOVERY_GUIDE}.'
        )
    return report


@contextmanager
def _migration_lock(cursor):
    cursor.execute('SELECT DATABASE() AS name')
    database = cursor.fetchone()['name']
    if not database:
        raise MigrationError('Select an existing database before migrating.')
    lock_name = 'chatbot-schema-' + hashlib.sha256(database.encode('utf-8')).hexdigest()[:40]
    cursor.execute('SELECT GET_LOCK(%s, 0) AS acquired', (lock_name,))
    if cursor.fetchone()['acquired'] != 1:
        raise MigrationError('Another migration holds the schema lock; retry after it finishes.')
    try:
        yield
    finally:
        cursor.execute('SELECT RELEASE_LOCK(%s)', (lock_name,))


def _backup_identity(backup):
    if backup is None:
        raise MigrationError(f'Existing databases require --backup with a tested pre-upgrade dump. See {RECOVERY_GUIDE}.')
    try:
        path = Path(backup).resolve(strict=True)
        if not path.is_file() or path.stat().st_size == 0:
            raise OSError('Backup must be a nonempty file')
        with path.open('rb') as stream:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    except (OSError, ValueError) as error:
        raise MigrationError('Backup must be an accessible, nonempty regular file.') from error
    return str(path), checksum


def _validate_existing(cursor, tables):
    for table, required in REQUIRED_COLUMNS.items():
        if table not in tables:
            continue
        cursor.execute(f'SHOW COLUMNS FROM `{table}`')
        columns = {row['Field'] for row in cursor.fetchall()}
        if not set(required).issubset(columns):
            raise MigrationError('Existing schema is not a supported legacy layout; inspect it before migrating.')


def upgrade(*, backup=None, resume=False, connect=None):
    """Apply forward-only DDL under a named lock, recording interrupted attempts."""
    connection = (connect or connect_database)()
    try:
        connection.autocommit(True)
        with connection.cursor() as cursor, _migration_lock(cursor):
            tables = _tables(cursor)
            records = _records(cursor, tables)
            report = _report(records)
            if not report['pending']:
                return report
            if report['incomplete'] and not resume:
                raise MigrationError(f'An upgrade was interrupted. Follow {RECOVERY_GUIDE} before using --resume.')
            backup_path, backup_sha256 = _backup_identity(backup) if tables or backup is not None else (None, None)
            _validate_existing(cursor, tables)
            cursor.execute(LEDGER_SQL)
            for migration in MIGRATIONS:
                if migration.version not in report['pending']:
                    continue
                cursor.execute(
                    'INSERT INTO schema_migrations (version, name, checksum, status, backup_path, backup_sha256) '
                    "VALUES (%s, %s, %s, 'running', %s, %s) "
                    "ON DUPLICATE KEY UPDATE status = 'running', started_at = CURRENT_TIMESTAMP, "
                    'applied_at = NULL, backup_path = VALUES(backup_path), backup_sha256 = VALUES(backup_sha256)',
                    (migration.version, migration.name, migration.checksum(), backup_path, backup_sha256),
                )
                try:
                    migration.apply(cursor)
                except BaseException:
                    cursor.execute("UPDATE schema_migrations SET status = 'failed' WHERE version = %s", (migration.version,))
                    raise
                cursor.execute(
                    "UPDATE schema_migrations SET status = 'applied', applied_at = CURRENT_TIMESTAMP WHERE version = %s",
                    (migration.version,),
                )
            return _report(_records(cursor, _tables(cursor)))
    finally:
        connection.close()


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('status', help='Read recorded schema version without writing anything')
    apply = commands.add_parser('upgrade', help='Apply pending migrations; stop all application writers first')
    apply.add_argument('--backup', type=Path, help='Existing, restore-tested pre-upgrade dump (required for existing databases)')
    apply.add_argument('--resume', action='store_true', help='Retry interrupted idempotent steps after following the recovery guide')
    options = parser.parse_args(arguments)
    try:
        from settings import validate_settings
        validate_settings()
        report = schema_status() if options.command == 'status' else upgrade(backup=options.backup, resume=options.resume)
        print(json.dumps(report, sort_keys=True))
        return 0
    except MigrationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception as error:
        print(f'Migration command failed ({type(error).__name__}); inspect status and {RECOVERY_GUIDE}.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
