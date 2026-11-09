"""Backup isolation, credentials, cleanup and environment-file boundaries."""

import hashlib
import importlib
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core_support import BACKEND_DIR


PRIVATE = 'backup-only-private-password-"\\#=%$'
SQL = "CREATE TABLE example (message TEXT, embedding BLOB);\nINSERT INTO example VALUES ('سلام', X'0000FF');\n".encode()


class DatabaseBackupTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(sys, 'path', [str(BACKEND_DIR), *sys.path]))
        self.module = importlib.import_module('backup_database')
        self.directory = Path(self.enterContext(TemporaryDirectory()))
        self.output = self.directory / 'backups'
        self.enterContext(patch.object(self.module, 'REPOSITORY', self.directory / 'source'))
        self.enterContext(patch.dict(os.environ, {'APP_ENV': 'test', 'DB_HOST': '127.0.0.1', 'DB_PORT': '3306',
                                                'DB_USER': 'backup_account', 'DB_NAME': 'isolated_backup',
                                                'DB_PASS': PRIVATE, 'ADMIN_SESSION_SECRET': 'backup-tests-admin-secret-not-a-deployment-key'}, clear=True))
        self.real_find = self.module.find_mysqldump
        self.real_protect = self.module.protect_directory
        self.find = self.enterContext(patch.object(self.module, 'find_mysqldump', return_value='fake-mysqldump'))
        self.protect = self.enterContext(patch.object(self.module, 'protect_directory'))
        self.observed = []
        self.run = self.enterContext(patch.object(self.module.subprocess, 'run', side_effect=self.client))

    def client(self, command, **options):
        filename = Path(next(argument.split('=', 1)[1] for argument in command if argument.startswith('--result-file=')))
        credentials = Path(command[1].split('=', 1)[1])
        self.observed.append((command, options, credentials.read_text(encoding='utf-8')))
        self.assertTrue(self.protect.called)
        self.assertFalse(filename.with_suffix('.sql').exists())
        filename.write_bytes(SQL)
        return SimpleNamespace(returncode=0)

    def test_dump_preserves_raw_bytes_and_only_publishes_a_completed_private_backup(self):
        result = self.module.create_backup(self.output)
        destination = Path(result['backup'])
        self.assertEqual(destination.read_bytes(), SQL)
        self.assertEqual(result['sha256'], hashlib.sha256(SQL).hexdigest())
        self.assertEqual(result['bytes'], len(SQL))
        self.assertFalse(result['restore_tested'])
        self.assertEqual({item.name for item in destination.parent.iterdir()}, {'backup.sql', 'manifest.json'})
        metadata = json.loads((destination.parent / 'manifest.json').read_text())
        self.assertEqual(metadata['sha256'], result['sha256'])
        command, options, credentials = self.observed[0]
        self.assertIn('password=' + self.module.option_value(PRIVATE), credentials)
        self.assertNotIn(PRIVATE, str(command) + str(options['env']))
        self.assertNotIn('DB_PASS', options['env'])
        self.assertNotIn('ADMIN_SESSION_SECRET', options['env'])
        self.assertNotIn('shell', options)
        self.assertEqual(command[-2:], ['--', 'isolated_backup'])
        for flag in ('--single-transaction', '--routines', '--triggers', '--events', '--hex-blob',
                     '--no-tablespaces', '--set-gtid-purged=OFF', '--default-character-set=utf8mb4'):
            self.assertIn(flag, command)
        self.assertNotIn('--databases', command)

    def test_repeated_backups_do_not_overwrite_the_previous_dump(self):
        first = self.module.create_backup(self.output)
        second = self.module.create_backup(self.output)
        self.assertNotEqual(first['backup'], second['backup'])
        self.assertEqual(Path(first['backup']).read_bytes(), SQL)

    def test_client_failure_removes_partial_sql_and_credentials_and_redacts_diagnostics(self):
        for code in (b'1045', b'1142', b'2003', b'unknown client failure'):
            def failed(command, **options):
                self.client(command, **options)
                options['stderr'].write(code + b' ' + PRIVATE.encode())
                return SimpleNamespace(returncode=2)

            self.run.side_effect = failed
            with self.subTest(code=code), self.assertRaises(self.module.BackupError) as raised:
                self.module.create_backup(self.output)
            self.assertNotIn(PRIVATE, str(raised.exception))
            self.assertEqual(list(self.output.iterdir()), [])

    def test_timeout_and_cancellation_remove_all_incomplete_output(self):
        for error in (subprocess.TimeoutExpired('mysqldump', 5), KeyboardInterrupt()):
            def interrupted(command, **options):
                self.client(command, **options)
                raise error

            self.run.side_effect = interrupted
            expected = self.module.BackupError if isinstance(error, subprocess.TimeoutExpired) else KeyboardInterrupt
            with self.subTest(error=type(error).__name__), self.assertRaises(expected):
                self.module.create_backup(self.output, timeout=5)
            self.assertEqual(list(self.output.iterdir()), [])

    def test_empty_success_and_directory_protection_failure_are_not_backups(self):
        self.run.side_effect = lambda *args, **kwargs: SimpleNamespace(returncode=0)
        with self.assertRaisesRegex(self.module.BackupError, 'no SQL data'):
            self.module.create_backup(self.output)
        self.protect.side_effect = self.module.BackupError('Cannot restrict access')
        with self.assertRaisesRegex(self.module.BackupError, 'restrict access'):
            self.module.create_backup(self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_repository_targets_and_missing_client_fail_before_starting_dump(self):
        with self.assertRaisesRegex(self.module.BackupError, 'outside the repository'):
            self.module.create_backup(self.directory / 'source' / 'backups')
        self.find.side_effect = self.module.BackupError('Install MySQL client tools')
        with self.assertRaisesRegex(self.module.BackupError, 'Install MySQL'):
            self.module.create_backup(self.output)
        self.run.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_environment_file_preserves_literal_passwords_and_ignores_unregistered_variables(self):
        environment = self.directory / 'local.env'
        environment.write_text("\ufeff# fixture\nexport DB_USER='file_account' # comment\n"
                               "DB_PASS='literal # = % $ \\ password'\nDB_NAME=fixture # comment\n"
                               'PATH=untrusted-client-path\nDB_HOST="local\\\\host"\n', encoding='utf-8')
        previous_path = os.environ.get('PATH')
        self.module.load_environment(environment)
        self.assertEqual(os.environ['DB_USER'], 'file_account')
        self.assertEqual(os.environ['DB_PASS'], 'literal # = % $ \\ password')
        self.assertEqual(os.environ['DB_NAME'], 'fixture')
        self.assertEqual(os.environ['DB_HOST'], 'local\\host')
        self.assertEqual(os.environ.get('PATH'), previous_path)

    def test_malformed_environment_file_never_partially_changes_settings_or_prints_its_content(self):
        environment = self.directory / 'bad.env'
        for value in ("DB_USER=replaced\nDB_PASS='" + PRIVATE, 'invalid ' + PRIVATE):
            environment.write_text(value, encoding='utf-8')
            output = io.StringIO()
            with self.subTest(value=value), redirect_stderr(output):
                result = self.module.main(['--env-file', str(environment)])
            self.assertEqual(result, 1)
            self.assertNotIn(PRIVATE, output.getvalue())
            self.assertEqual(os.environ['DB_USER'], 'backup_account')
        self.run.assert_not_called()

    def test_cli_explicit_environment_and_development_select_the_requested_database(self):
        environment = self.directory / 'local.env'
        environment.write_text('APP_ENV=production\nDB_NAME=file_database\n', encoding='utf-8')
        output = io.StringIO()
        with redirect_stdout(output):
            result = self.module.main(['--env-file', str(environment), '--development', '--directory', str(self.output)])
        self.assertEqual(result, 0)
        self.assertEqual(self.observed[0][0][-1], 'file_database')
        self.assertIn('Restore-test', output.getvalue())
        self.assertNotIn(PRIVATE, output.getvalue())
        self.assertNotIn('CREATE TABLE', output.getvalue())

    def test_administrator_password_is_prompted_and_does_not_change_application_credentials(self):
        output = io.StringIO()
        with patch.object(self.module.getpass, 'getpass', return_value='backup-admin-private-password') as prompt, redirect_stdout(output):
            result = self.module.main(['--development', '--directory', str(self.output), '--user', 'backup_admin', '--ask-password'])
        self.assertEqual(result, 0)
        prompt.assert_called_once()
        command, options, credentials = self.observed[0]
        self.assertIn('user="backup_admin"', credentials)
        self.assertIn('password="backup-admin-private-password"', credentials)
        self.assertNotIn('backup-admin-private-password', str(command) + str(options['env']) + output.getvalue())
        self.assertEqual(os.environ['DB_PASS'], PRIVATE)
        self.assertEqual(os.environ['DB_USER'], 'backup_account')

    def test_dump_permission_failure_is_distinct_from_bad_login(self):
        denied = self.module.dump_failure(b"Couldn't execute SHOW EVENTS: Access denied (1044)")
        self.assertIn('lacks dump privileges', denied)
        login = self.module.dump_failure(b'Got error: 1045 when trying to connect')
        self.assertIn('rejected the backup login', login)

    def test_windows_access_control_removes_inherited_access_before_creating_credentials(self):
        sid = 'S-1-5-21-123-456-789-1001'
        windows = SimpleNamespace(name='nt', environ={'SYSTEMROOT': str(self.directory / 'Windows')})
        self.run.side_effect = [SimpleNamespace(stdout=f'"host\\user","{sid}"\n'), SimpleNamespace(returncode=0)]
        with patch.object(self.module, 'os', windows):
            self.real_protect(self.directory)
        command = self.run.call_args.args[0]
        self.assertIn('/inheritance:r', command)
        self.assertIn(f'*{sid}:(OI)(CI)F', command)
        self.assertIn(str(self.directory), command)
        self.run.reset_mock()
        self.run.side_effect = [SimpleNamespace(stdout='unexpected identity')]
        with patch.object(self.module, 'os', windows), self.assertRaises(self.module.BackupError):
            self.real_protect(self.directory)
        self.assertEqual(self.run.call_count, 1)

    def test_windows_client_discovery_finds_an_installed_server_without_changing_path(self):
        executable = self.directory / 'Program Files' / 'MySQL' / 'MySQL Server 8.0' / 'bin' / 'mysqldump.exe'
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b'client discovery fixture')
        windows = SimpleNamespace(name='nt', environ={'ProgramFiles': str(self.directory / 'Program Files')})
        with patch.object(self.module, 'os', windows), patch.object(self.module.shutil, 'which', return_value=None):
            self.assertEqual(self.real_find(), str(executable))
            with self.assertRaises(self.module.BackupError):
                self.real_find('missing-client.exe')

    def test_option_file_escaping_preserves_quotes_backslashes_and_control_characters(self):
        self.assertEqual(self.module.option_value('A\\B"C\nD\rE\tF'), '"A\\\\B\\"C\\nD\\rE\\tF"')
        with self.assertRaises(self.module.BackupError):
            self.module.option_value('invalid\x00password')


if __name__ == '__main__':
    unittest.main()
