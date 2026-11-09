"""Create a private MySQL SQL dump without loading applications or changing the schema."""

import argparse
import csv
import getpass
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import warnings
from datetime import datetime, timezone
from pathlib import Path

from settings import ConfigurationError, SETTING_REGISTRY, database_config


REPOSITORY = Path(__file__).resolve().parent.parent


class BackupError(RuntimeError):
    pass


def load_environment(filename):
    allowed = set(SETTING_REGISTRY)
    allowed.update(alias for definition in SETTING_REGISTRY.values() for alias in definition.aliases)
    values = {}
    for number, line in enumerate(Path(filename).read_text(encoding='utf-8-sig').splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)\s*=\s*(.*)', line)
        if match is None:
            raise BackupError(f'Invalid environment file at line {number}; use one KEY=value per line.')
        name, value = match.groups()
        if name not in allowed:
            continue
        if value.startswith(('"', "'")):
            quote = value[0]
            position = 1
            characters = []
            while position < len(value) and value[position] != quote:
                character = value[position]
                if quote == '"' and character == '\\' and position + 1 < len(value):
                    following = value[position + 1]
                    escapes = {'n': '\n', 'r': '\r', 't': '\t', '\\': '\\', '"': '"'}
                    if following in escapes:
                        character = escapes[following]
                        position += 1
                characters.append(character)
                position += 1
            remainder = value[position + 1:].strip()
            if position == len(value) or (remainder and not remainder.startswith('#')):
                raise BackupError(f'Invalid quoted environment value at line {number}.')
            value = ''.join(characters)
        else:
            value = re.split(r'\s+#', value, maxsplit=1)[0].rstrip()
        values[name] = value
    os.environ.update(values)


def find_mysqldump(executable=None):
    if executable:
        candidate = shutil.which(str(executable))
        if candidate:
            return candidate
        raise BackupError('Cannot find --mysqldump. Supply the full path to mysqldump.exe or mysqldump.')
    candidate = shutil.which('mysqldump')
    if candidate:
        return candidate
    if os.name == 'nt':
        for variable in ('ProgramFiles', 'ProgramFiles(x86)'):
            root = os.environ.get(variable)
            if root:
                for pattern in ('MySQL/MySQL Server */bin/mysqldump.exe', 'MySQL/MySQL Workbench */mysqldump.exe'):
                    candidates = sorted(Path(root).glob(pattern), reverse=True)
                    if candidates:
                        return str(candidates[0])
    raise BackupError('Install MySQL client tools, or pass --mysqldump with the full executable path.')


def protect_directory(directory):
    if os.name != 'nt':
        directory.chmod(0o700)
        return
    system = Path(os.environ.get('SYSTEMROOT', 'C:/Windows')) / 'System32'
    try:
        identity = subprocess.run([str(system / 'whoami.exe'), '/user', '/fo', 'csv', '/nh'],
                                  capture_output=True, text=True, check=True, timeout=15)
        sid = next(csv.reader(identity.stdout.splitlines()))[-1]
        if not re.fullmatch(r'S-\d+(?:-\d+)+', sid):
            raise ValueError()
        subprocess.run([str(system / 'icacls.exe'), str(directory), '/inheritance:r',
                        '/grant:r', f'*{sid}:(OI)(CI)F'], check=True, timeout=15,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValueError, StopIteration):
        raise BackupError('Cannot restrict backup directory access to the current Windows user.') from None


def option_value(value):
    value = str(value)
    if '\x00' in value:
        raise BackupError('Database settings must not contain a null character.')
    for original, replacement in (('\\', '\\\\'), ('"', '\\"'), ('\n', '\\n'), ('\r', '\\r'), ('\t', '\\t')):
        value = value.replace(original, replacement)
    return '"' + value + '"'


def dump_failure(diagnostic):
    if b'1045' in diagnostic or b'when trying to connect' in diagnostic:
        return 'MySQL rejected the backup login. Check DB_USER/DB_PASS and database access in the selected environment file.'
    if (any(code in diagnostic for code in (b'1044', b'1142', b'1227', b'1370'))
            or b'privilege' in diagnostic.lower() or b'access denied' in diagnostic.lower()):
        return 'The backup account lacks dump privileges. Use a MySQL backup/administrator account with --user NAME --ask-password, or the VS Code backup profile.'
    if b'2003' in diagnostic or b"Can't connect" in diagnostic:
        return 'Cannot connect to MySQL. Check the service, DB_HOST and DB_PORT.'
    return 'mysqldump failed. Check client/server version compatibility and backup-account privileges; no completed backup was created.'


def create_backup(directory, *, executable=None, timeout=600, user=None, password=None):
    config = database_config()
    if user is not None:
        config['user'] = user
    if password is not None:
        config['password'] = password
    executable = find_mysqldump(executable)
    try:
        directory = Path(directory).expanduser().resolve()
    except RuntimeError:
        raise BackupError('Cannot determine the home directory; pass an absolute --directory outside the repository.') from None
    if directory.is_relative_to(REPOSITORY):
        raise BackupError('Choose a backup directory outside the repository, such as ~/Local_chatbot_backups.')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    created = datetime.now(timezone.utc)
    folder = Path(tempfile.mkdtemp(prefix=created.strftime('chatbot-%Y%m%dT%H%M%SZ-'), dir=directory))
    options = folder / 'credentials.cnf'
    partial = folder / 'backup.incomplete'
    destination = folder / 'backup.sql'
    errors = folder / 'client-errors.log'
    manifest = folder / 'manifest.json'
    completed = False
    try:
        protect_directory(folder)
        with options.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write('[client]\n')
            for key in ('host', 'port', 'user', 'password'):
                stream.write(f'{key}={option_value(config[key])}\n')
        options.chmod(0o600)
        command = [executable, f'--defaults-file={options}', '--protocol=TCP', '--single-transaction',
                   '--routines', '--triggers', '--events', '--hex-blob', '--no-tablespaces',
                   '--set-gtid-purged=OFF', '--column-statistics=0', '--default-character-set=utf8mb4',
                   f'--result-file={partial}', '--', config['database']]
        environment = {name: value for name, value in os.environ.items() if name.upper() in {
            'PATH', 'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'USERPROFILE', 'HOME', 'LOCALAPPDATA', 'TEMP', 'TMP',
        }}
        with errors.open('xb') as diagnostic:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=diagnostic, env=environment, timeout=timeout,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            with errors.open('rb') as diagnostic:
                raise BackupError(dump_failure(diagnostic.read(16384)))
        if not partial.is_file() or partial.stat().st_size == 0:
            raise BackupError('mysqldump returned no SQL data; no completed backup was created.')
        partial.chmod(0o600)
        with partial.open('rb') as stream:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        metadata = {'created_utc': created.isoformat(), 'bytes': partial.stat().st_size,
                    'sha256': checksum, 'restore_tested': False}
        with manifest.open('x', encoding='utf-8') as stream:
            json.dump(metadata, stream, indent=2)
            stream.write('\n')
        manifest.chmod(0o600)
        partial.replace(destination)
        completed = True
        return {'backup': str(destination), **metadata}
    except subprocess.TimeoutExpired:
        raise BackupError('Backup timed out; its incomplete SQL file was removed. Increase --timeout if needed.') from None
    finally:
        options.unlink(missing_ok=True)
        errors.unlink(missing_ok=True)
        if not completed:
            partial.unlink(missing_ok=True)
            manifest.unlink(missing_ok=True)
            folder.rmdir()


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path('~/Local_chatbot_backups'))
    parser.add_argument('--mysqldump', help='Optional full path to the matching MySQL dump executable.')
    parser.add_argument('--env-file', type=Path, help='Explicit local environment file; overrides registered settings for this process.')
    parser.add_argument('--development', action='store_true', help='Explicitly select local development settings.')
    parser.add_argument('--user', help='Backup-only MySQL account; requires --ask-password.')
    parser.add_argument('--ask-password', action='store_true', help='Prompt for the backup password without echoing or storing it in shell history.')
    parser.add_argument('--timeout', type=int, default=600, help='Dump timeout in seconds (1-3600, default 600).')
    options = parser.parse_args(arguments)
    if not 1 <= options.timeout <= 3600:
        parser.error('--timeout must be between 1 and 3600 seconds')
    if options.user is not None and not options.ask_password:
        parser.error('--user requires --ask-password')
    try:
        if options.env_file:
            load_environment(options.env_file)
        if options.development:
            os.environ['APP_ENV'] = 'development'
        password = None
        if options.ask_password:
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                try:
                    password = getpass.getpass('MySQL backup password: ')
                except getpass.GetPassWarning:
                    raise BackupError('A hidden password prompt requires an interactive terminal or the VS Code backup profile.') from None
        result = create_backup(options.directory, executable=options.mysqldump, timeout=options.timeout,
                               user=options.user, password=password)
        print(f"Backup created: {result['backup']}")
        print(f"Size: {result['bytes']} bytes")
        print(f"SHA-256: {result['sha256']}")
        print('Restore-test this SQL dump into an isolated database before using its path with upgrade --backup.')
        return 0
    except (BackupError, ConfigurationError) as error:
        print(f'Backup error: {error}', file=sys.stderr)
    except (OSError, subprocess.SubprocessError, UnicodeError):
        print('Backup error: Cannot read configuration, run the client, or write the protected backup directory. Check paths and permissions.', file=sys.stderr)
    except KeyboardInterrupt:
        print('Backup canceled; incomplete output was removed.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
