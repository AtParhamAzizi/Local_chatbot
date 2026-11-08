"""Disposable MySQL provisioning and fail-closed connection boundaries for F014."""

import os
import re
import secrets
import socket
import subprocess
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import pymysql.cursors


MYSQL_DIR = Path(__file__).resolve().parent
_active_target = None


@dataclass(frozen=True)
class DatabaseTarget:
    name: str
    port: int
    password: str = field(repr=False)

    def __post_init__(self):
        if not re.fullmatch(r'chatbot_f014_[0-9a-f]{32}', self.name):
            raise ValueError('Only randomly named F014 databases are allowed')
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError('A provisioned MySQL port is required')
        if not self.password or len(self.password) < 32:
            raise ValueError('A fresh test password is required')

    def config(self):
        return {
            'host': '127.0.0.1', 'port': self.port, 'user': 'f014_app',
            'password': self.password, 'database': self.name, 'charset': 'utf8mb4',
            'cursorclass': pymysql.cursors.DictCursor, 'autocommit': True,
            'connect_timeout': 5, 'read_timeout': 10, 'write_timeout': 10,
        }


class DisposableMySQL:
    def __init__(self):
        run_id = secrets.token_hex(16)
        self.project = 'chatbot-f014-' + run_id
        self.name = 'chatbot_f014_' + run_id
        self.password = secrets.token_hex(32)
        self.environment = {
            key: value for key, value in os.environ.items()
            if not key.startswith(('COMPOSE_', 'DB_', 'F014_'))
        }
        self.environment.update(
            F014_DB_NAME=self.name, F014_DB_PASSWORD=self.password,
            F014_ROOT_PASSWORD=secrets.token_hex(32),
        )
        self.command = [
            'docker', 'compose', '--project-name', self.project,
            '--env-file', str(MYSQL_DIR / 'compose.env'),
            '--file', str(MYSQL_DIR / 'compose.yaml'),
        ]

    def run(self, *arguments, timeout=240):
        return subprocess.run(
            [*self.command, *arguments], env=self.environment, cwd=MYSQL_DIR,
            check=True, capture_output=True, text=True, timeout=timeout,
        ).stdout.strip()

    @contextmanager
    def provision(self):
        try:
            print(f'Starting disposable MySQL project {self.project}', flush=True)
            self.run('up', '--detach', '--wait', '--wait-timeout', '210')
            published = self.run('port', 'mysql', '3306', timeout=20)
            match = re.fullmatch(r'127\.0\.0\.1:([0-9]+)', published)
            if match is None:
                raise RuntimeError('MySQL must publish exactly one IPv4 loopback port')
            yield DatabaseTarget(self.name, int(match[1]), self.password)
        finally:
            print(f'Removing disposable MySQL project {self.project}', flush=True)
            self.run('down', '--volumes', '--remove-orphans', timeout=90)


def guarded_connect(original, target, violations):
    def connect(*arguments, **keywords):
        if arguments or keywords != target.config():
            violations.append('Unprovisioned database connection blocked')
            raise AssertionError(violations[-1])
        return original(**keywords)
    return connect


@contextmanager
def database_sandbox(target):
    global _active_target
    if _active_target is not None:
        raise RuntimeError('Nested MySQL sandboxes are not supported')
    violations = []

    def check_address(address):
        if address != ('127.0.0.1', target.port):
            violations.append('Network access outside disposable MySQL blocked')
            raise AssertionError(violations[-1])

    original_create_connection = socket.create_connection
    original_getaddrinfo = socket.getaddrinfo
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def create_connection(address, *arguments, **keywords):
        check_address(address)
        return original_create_connection(address, *arguments, **keywords)

    def getaddrinfo(host, port, *arguments, **keywords):
        check_address((host, port))
        return original_getaddrinfo(host, port, *arguments, **keywords)

    def connect(connection, address):
        check_address(address)
        return original_connect(connection, address)

    def connect_ex(connection, address):
        check_address(address)
        return original_connect_ex(connection, address)

    def sendto(*arguments, **keywords):
        violations.append('Datagram network access blocked')
        raise AssertionError(violations[-1])

    with ExitStack() as resources:
        factory = guarded_connect(pymysql.connect, target, violations)
        resources.enter_context(patch.object(pymysql, 'connect', factory))
        resources.enter_context(patch.object(pymysql, 'Connect', factory))
        resources.enter_context(patch.object(pymysql, 'Connection', factory))
        resources.enter_context(patch.object(pymysql.connections, 'Connection', factory))
        resources.enter_context(patch.object(socket, 'create_connection', create_connection))
        resources.enter_context(patch.object(socket, 'getaddrinfo', getaddrinfo))
        resources.enter_context(patch.object(socket.socket, 'connect', connect))
        resources.enter_context(patch.object(socket.socket, 'connect_ex', connect_ex))
        resources.enter_context(patch.object(socket.socket, 'sendto', sendto))
        _active_target = target
        try:
            yield violations
        finally:
            _active_target = None


def require_target():
    if _active_target is None:
        raise RuntimeError('Run backend/tests/run_mysql_tests.py; configured DB_* is never accepted')
    return _active_target
