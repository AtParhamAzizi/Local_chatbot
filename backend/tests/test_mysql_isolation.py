"""Offline checks for the destructive integration suite's safety boundaries."""

import importlib
import os
import socket
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pymysql

from run_mysql_tests import RejectModelImportsExceptPDF


MYSQL_DIR = Path(__file__).resolve().parent / 'mysql'
with patch.object(sys, 'path', [str(MYSQL_DIR), *sys.path]):
    mysql_support = importlib.import_module('mysql_support')


class MySQLIsolationTests(unittest.TestCase):
    def setUp(self):
        self.target = mysql_support.DatabaseTarget('chatbot_f014_' + 'a' * 32, 13306, 'b' * 64)
        self.enterContext(patch.object(mysql_support, 'print', create=True))

    def test_no_target_is_available_from_developer_environment(self):
        with patch.dict(os.environ, {'DB_HOST': 'production.invalid', 'DB_NAME': 'production'}):
            with self.assertRaisesRegex(RuntimeError, 'configured DB_'):
                mysql_support.require_target()

    def test_targets_require_disposable_names_valid_ports_and_fresh_passwords(self):
        for name, port, password in (
            ('chatbot_db', 3306, 'b' * 64), ('mysql', 3306, 'b' * 64),
            (self.target.name, 0, 'b' * 64), (self.target.name, True, 'b' * 64),
            (self.target.name, 13306, 'short'),
        ):
            with self.subTest(name=name, port=port), self.assertRaises(ValueError):
                mysql_support.DatabaseTarget(name, port, password)
        self.assertNotIn(self.target.password, repr(self.target))

    def test_driver_guard_forwards_only_exact_provisioned_configuration(self):
        original = Mock()
        violations = []
        connect = mysql_support.guarded_connect(original, self.target, violations)
        self.assertIs(connect(**self.target.config()), original.return_value)
        original.assert_called_once_with(**self.target.config())
        original.reset_mock()
        for altered in (
            {}, {'host': 'production.invalid'}, {'database': 'chatbot_db'},
            {'port': 3306}, {'user': 'root'}, {'password': 'developer-password'},
            {'unix_socket': '/var/run/mysqld/mysqld.sock'}, {'read_default_file': 'my.cnf'},
            {'autocommit': False},
        ):
            config = self.target.config() if altered else {}
            config.update(altered)
            with self.subTest(altered=altered), self.assertRaises(AssertionError):
                connect(**config)
        with self.assertRaises(AssertionError):
            connect(self.target.config())
        original.assert_not_called()
        self.assertEqual(len(violations), 10)

    def test_socket_and_driver_guards_reject_other_targets_and_restore_process_state(self):
        originals = (pymysql.connect, pymysql.Connect, pymysql.Connection, pymysql.connections.Connection, socket.create_connection,
                     socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex, socket.socket.sendto)
        with mysql_support.database_sandbox(self.target) as violations:
            self.assertIs(mysql_support.require_target(), self.target)
            for connect in (pymysql.connect, pymysql.Connect, pymysql.Connection, pymysql.connections.Connection):
                with self.assertRaises(AssertionError):
                    connect(host='production.invalid', database='chatbot_db')
            with self.assertRaises(AssertionError):
                socket.create_connection(('127.0.0.1', 3306))
            with self.assertRaises(AssertionError):
                socket.getaddrinfo('production.invalid', 3306)
            with socket.socket() as client:
                for method in (client.connect, client.connect_ex):
                    with self.assertRaises(AssertionError):
                        method(('127.0.0.1', 3306))
                with self.assertRaises(AssertionError):
                    client.sendto(b'blocked', ('127.0.0.1', self.target.port))
            self.assertEqual(len(violations), 9)
        restored = (pymysql.connect, pymysql.Connect, pymysql.Connection, pymysql.connections.Connection, socket.create_connection,
                    socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex, socket.socket.sendto)
        for original, current in zip(originals, restored):
            self.assertIs(current, original)
        with self.assertRaises(RuntimeError):
            mysql_support.require_target()

    def test_allowed_socket_is_forwarded_without_reaching_a_real_server(self):
        with patch.object(socket.socket, 'connect') as original:
            with mysql_support.database_sandbox(self.target) as violations, socket.socket() as client:
                client.connect(('127.0.0.1', self.target.port))
                original.assert_called_once_with(client, ('127.0.0.1', self.target.port))
                self.assertEqual(violations, [])

    def test_provisioning_ignores_deployment_env_uses_unique_projects_and_always_cleans_up(self):
        with patch.dict(os.environ, {
            'DB_HOST': 'production.invalid', 'DB_NAME': 'chatbot_db', 'DB_PASS': 'developer-secret',
            'COMPOSE_FILE': 'compose.yaml', 'COMPOSE_PROJECT_NAME': 'production',
        }):
            disposable = mysql_support.DisposableMySQL()
            other = mysql_support.DisposableMySQL()
        self.assertNotEqual(disposable.project, other.project)
        self.assertNotEqual(disposable.name, other.name)
        self.assertNotEqual(disposable.password, other.password)
        for key in ('DB_HOST', 'DB_NAME', 'DB_PASS', 'COMPOSE_FILE', 'COMPOSE_PROJECT_NAME'):
            self.assertNotIn(key, disposable.environment)
        self.assertEqual(disposable.command[-1], str(MYSQL_DIR / 'compose.yaml'))
        self.assertIn(str(MYSQL_DIR / 'compose.env'), disposable.command)
        completed = Mock(stdout='')
        port = Mock(stdout='127.0.0.1:13306\n')
        with patch.object(subprocess, 'run', side_effect=[completed, port, completed]) as run:
            with disposable.provision() as target:
                self.assertEqual(target.name, disposable.name)
                self.assertEqual(target.config()['host'], '127.0.0.1')
                self.assertEqual(target.port, 13306)
            self.assertEqual(run.call_args_list[-1].args[0],
                             [*disposable.command, 'down', '--volumes', '--remove-orphans'])
            self.assertEqual(run.call_args_list[0].kwargs['env'], disposable.environment)

    def test_startup_and_test_failures_still_remove_only_the_new_project(self):
        disposable = mysql_support.DisposableMySQL()
        completed = Mock(stdout='')
        failure = subprocess.CalledProcessError(1, ['docker'], stderr='unavailable')
        for outputs, body_failure in (
            ([failure, completed], False),
            ([completed, Mock(stdout='0.0.0.0:13306'), completed], False),
            ([completed, Mock(stdout='127.0.0.1:13306'), completed], True),
        ):
            with self.subTest(body_failure=body_failure), patch.object(subprocess, 'run', side_effect=outputs) as run:
                with self.assertRaises((subprocess.CalledProcessError, RuntimeError)):
                    with disposable.provision():
                        if body_failure:
                            raise RuntimeError('Test body failed')
                self.assertEqual(run.call_args_list[-1].args[0],
                                 [*disposable.command, 'down', '--volumes', '--remove-orphans'])

    def test_integration_import_guard_allows_real_pdf_but_not_model_dependencies(self):
        guard = RejectModelImportsExceptPDF()
        for package in ('torch', 'transformers', 'faster_whisper', 'huggingface_hub'):
            with self.subTest(package=package), self.assertRaises(AssertionError):
                guard.find_spec(package)
        self.assertIsNone(guard.find_spec('fitz'))
        self.assertIsNone(guard.find_spec('pymupdf'))
