"""Configuration boundaries and private, service-free diagnostics."""

import ast
import importlib
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
PRIVATE = "private-password-0123456789-keep-this-hidden"


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.path = patch.object(sys, 'path', [str(BACKEND_DIR), *sys.path])
        self.path.start()
        self.addCleanup(self.path.stop)
        self.settings = importlib.import_module('settings')
        self.environment = {
            'APP_ENV': 'production', 'DB_USER': 'chatbot_app', 'DB_PASS': PRIVATE,
            'DB_NAME': 'chatbot_test', 'ADMIN_SESSION_SECRET': PRIVATE,
            'UI_ALLOWED_ORIGINS': 'https://chat.example.test',
            'ADMIN_ALLOWED_ORIGINS': 'https://admin.example.test',
        }

    def test_every_supported_setting_rejects_invalid_input_without_echoing_it(self):
        invalid_values = {
            'integer': PRIVATE, 'float': 'nan', 'boolean': PRIVATE, 'choice': PRIVATE,
            'text': PRIVATE + '\n', 'host': PRIVATE, 'origins': 'https://user:' + PRIVATE + '@example.test',
            'networks': PRIVATE, 'path': PRIVATE + '\0', 'executable': PRIVATE + '\0',
            'filename': '../' + PRIVATE, 'identifier': PRIVATE + '/',
        }
        for name, definition in self.settings.SETTING_REGISTRY.items():
            with self.subTest(name=name):
                environment = {**self.environment, name: invalid_values[definition.kind]}
                scope = 'app' if 'app' in definition.scopes else definition.scopes[0]
                with self.assertRaises(self.settings.ConfigurationError) as failure:
                    self.settings.load_settings(environ=environment, scope=scope)
                self.assertIn(name, str(failure.exception))
                self.assertNotIn(PRIVATE, str(failure.exception))
                self.assertIn('process environment', str(failure.exception))

    def test_every_numeric_setting_enforces_type_and_both_range_boundaries(self):
        for name, definition in self.settings.SETTING_REGISTRY.items():
            if definition.kind not in {'integer', 'float'}:
                continue
            for invalid in (definition.minimum - 1, definition.maximum + 1, 'inf', '-inf', 'NaN', True):
                with self.subTest(name=name, invalid=invalid):
                    with self.assertRaises(self.settings.ConfigurationError):
                        self.settings.setting_value(name, environ={name: invalid})
            for boundary in (definition.minimum, definition.maximum):
                with self.subTest(name=name, boundary=boundary):
                    value = self.settings.setting_value(name, environ={name: str(boundary)})
                    self.assertEqual(value, boundary)
                    self.assertIs(type(value), int if definition.kind == 'integer' else float)
            if definition.kind == 'integer':
                with self.assertRaises(self.settings.ConfigurationError):
                    self.settings.setting_value(name, environ={name: '1.5'})

    def test_choices_and_boolean_spellings_resolve_to_declared_types(self):
        for name, definition in self.settings.SETTING_REGISTRY.items():
            if definition.kind == 'choice':
                for choice in definition.choices:
                    with self.subTest(name=name, choice=choice):
                        self.assertEqual(self.settings.setting_value(name, environ={name: choice.upper()}), choice)
            elif definition.kind == 'boolean':
                for raw, expected in (('true', True), ('FALSE', False), ('1', True), ('0', False)):
                    self.assertIs(self.settings.setting_value(name, environ={name: raw}), expected)

    def test_blank_values_are_explicit_and_do_not_silently_replace_defaults(self):
        for name, definition in self.settings.SETTING_REGISTRY.items():
            if definition.allow_blank:
                continue
            with self.subTest(name=name), self.assertRaises(self.settings.ConfigurationError):
                self.settings.setting_value(name, environ={name: ' '})
        for name in ('DB_PASS', 'ADMIN_SESSION_SECRET'):
            with self.subTest(name=name), self.assertRaises(self.settings.ConfigurationError):
                self.settings.load_settings(environ={**self.environment, name: ' '})
        self.assertEqual(self.settings.setting_value('TRUSTED_PROXY_CIDRS', environ={}), ())

    def test_production_requires_all_credentials_and_origins_and_rejects_root(self):
        for name in ('DB_USER', 'DB_PASS', 'DB_NAME', 'ADMIN_SESSION_SECRET',
                     'UI_ALLOWED_ORIGINS', 'ADMIN_ALLOWED_ORIGINS'):
            environment = self.environment.copy()
            del environment[name]
            with self.subTest(name=name), self.assertRaises(self.settings.ConfigurationError) as failure:
                self.settings.load_settings(environ=environment)
            self.assertIn(name, str(failure.exception))
        for changes in ({'DB_USER': 'ROOT'}, {'ADMIN_SESSION_SECRET': 'short'},
                        {'ADMIN_SESSION_SECRET': 'local-chatbot-change-this-secret'}):
            with self.subTest(changes=changes), self.assertRaises(self.settings.ConfigurationError):
                self.settings.load_settings(environ={**self.environment, **changes})

    def test_development_defaults_and_retention_share_registry_values(self):
        values = self.settings.load_settings(environ={'APP_ENV': 'test'})
        self.assertEqual(values['DB_PASS'], '')
        self.assertEqual(values['UI_ALLOWED_ORIGINS'], frozenset({
            'http://localhost:5000', 'http://127.0.0.1:5000', 'http://[::1]:5000',
        }))
        with patch.dict(os.environ, {'APP_ENV': 'test'}, clear=True):
            self.assertEqual(self.settings.RetentionPolicy(), self.settings.retention_settings())
            self.assertGreaterEqual(len(self.settings.session_secret()), 32)
            self.assertEqual(self.settings.database_config()['password'], '')

    def test_environment_alias_precedence_and_unknown_environments_fail_closed(self):
        for environment, expected in (({}, 'production'), ({'FLASK_ENV': 'test'}, 'test'),
                                      ({'FLASK_ENV': 'test', 'ENVIRONMENT': 'dev'}, 'dev'),
                                      ({'APP_ENV': 'production', 'ENVIRONMENT': 'dev'}, 'production')):
            self.assertEqual(self.settings.setting_value('APP_ENV', environ=environment), expected)
        for name in ('APP_ENV', 'ENVIRONMENT', 'FLASK_ENV'):
            with self.subTest(name=name), self.assertRaises(self.settings.ConfigurationError):
                self.settings.load_settings(environ={name: 'developmnt'})
        with patch.dict(os.environ, {'APP_ENV': 'developmnt'}, clear=True):
            self.assertFalse(self.settings.is_development())

    def test_dependent_settings_reject_contradictory_values(self):
        cases = (
            {'ADMIN_SESSION_IDLE_SECONDS': '1801', 'ADMIN_SESSION_LIFETIME_SECONDS': '1800'},
            {'TRUSTED_PROXY_HOPS': '1'}, {'GEN_DEVICE': 'cpu', 'GEN_QUANTIZATION': '4bit'},
        )
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(self.settings.ConfigurationError):
                self.settings.load_settings(environ={**self.environment, **changes})
        with patch.dict(os.environ, {'ADMIN_SESSION_LIFETIME_SECONDS': '60'}, clear=True):
            with self.assertRaises(self.settings.ConfigurationError):
                self.settings.session_idle_seconds()
        values = self.settings.load_settings(environ={**self.environment, 'TRUSTED_PROXY_HOPS': '1',
                                                       'TRUSTED_PROXY_CIDRS': '192.0.2.9/24,::1'})
        self.assertEqual([str(network) for network in values['TRUSTED_PROXY_CIDRS']], ['192.0.2.0/24', '::1/128'])

    def test_relative_paths_fallbacks_and_precision_defaults_are_consistent(self):
        values = self.settings.load_settings(environ={'APP_ENV': 'test', 'GEN_MODEL': 'custom-generator'})
        expected = str(BACKEND_DIR / 'custom-generator')
        self.assertEqual(values['QWEN35_GENERATOR_PATH'], expected)
        self.assertEqual(values['QWEN_GENERATOR_PATH'], expected)
        values = self.settings.load_settings(environ={'APP_ENV': 'test', 'GEN_MODEL': 'custom-generator',
                                                       'QWEN35_GENERATOR_PATH': 'specific'})
        self.assertEqual(values['QWEN35_GENERATOR_PATH'], str(BACKEND_DIR / 'specific'))
        self.assertEqual(self.settings.setting_value('STT_COMPUTE_TYPE', environ={'STT_DEVICE': 'cuda'}), 'float16')
        self.assertEqual(self.settings.setting_value('STT_COMPUTE_TYPE', environ={}), 'int8')
        self.assertEqual(self.settings.setting_value('TTS_PYTHON', environ={'TTS_PYTHON': sys.executable}),
                         str(Path(sys.executable).resolve()))
        with self.assertRaises(self.settings.ConfigurationError):
            self.settings.setting_value('TTS_PYTHON', environ={'TTS_PYTHON': 'missing-python-executable'})
        with TemporaryDirectory() as directory:
            values = self.settings.load_settings(environ={}, scope='tts', tts_base=directory)
            self.assertEqual(values['PERSIAN_VOICE_PROMPT'], str(Path(directory).resolve() / 'voices/prompt_hello.wav'))

    def test_scopes_allow_standalone_speech_without_database_configuration(self):
        for scope in ('tts', 'stt'):
            values = self.settings.load_settings(environ={'DB_PORT': 'invalid'}, scope=scope)
            self.assertNotIn('DB_PASS', values)
        for name, value in (('STT_LANGUAGE', 'xx'), ('STT_LOCAL_FILES_ONLY', 'yes'),
                            ('STT_VOICE_THRESHOLD', 'NaN'), ('ENGLISH_MODEL_FILE', '../model.onnx')):
            with self.subTest(name=name), self.assertRaises(self.settings.ConfigurationError):
                self.settings.load_settings(environ={name: value}, scope='stt' if name.startswith('STT') else 'tts')

    def test_registry_and_resolved_settings_cannot_be_mutated(self):
        with self.assertRaises(TypeError):
            self.settings.SETTING_REGISTRY['DB_PORT'] = None
        with self.assertRaises(FrozenInstanceError):
            self.settings.SETTING_REGISTRY['DB_PORT'].default = 1
        with self.assertRaises(TypeError):
            self.settings.load_settings(environ={'APP_ENV': 'test'})['DB_PORT'] = 1

    def test_diagnostics_only_include_explicitly_public_effective_values(self):
        environment = {**self.environment, 'DB_HOST': PRIVATE, 'DB_NAME': PRIVATE, 'DB_USER': PRIVATE,
                       'QWEN_EMBEDDING_PATH': PRIVATE, 'PERSIAN_G2P_REPO': PRIVATE,
                       'UNREGISTERED_SECRET': PRIVATE, 'DB_PORT': ' 3307 '}
        report = self.settings.effective_settings(environ=environment)
        self.assertEqual(report['DB_PORT'], 3307)
        self.assertIs(report['STT_LOCAL_FILES_ONLY'], False)
        self.assertNotIn(PRIVATE, json.dumps(report))
        expected = {name for name, definition in self.settings.SETTING_REGISTRY.items()
                    if definition.public and not definition.secret and 'app' in definition.scopes}
        self.assertEqual(set(report), expected)
        for name in ('DB_PASS', 'ADMIN_SESSION_SECRET', 'DB_HOST', 'DB_USER', 'DB_NAME', 'TTS_PYTHON',
                     'QWEN_EMBEDDING_PATH', 'PERSIAN_G2P_REPO', 'UNREGISTERED_SECRET'):
            self.assertNotIn(name, report)

    def test_cli_diagnostics_and_failure_messages_are_safe(self):
        with patch.dict(os.environ, self.environment, clear=True), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.settings.main(['--diagnostics']), 0)
        self.assertEqual(json.loads(output.getvalue())['APP_ENV'], 'production')
        self.assertNotIn(PRIVATE, output.getvalue())
        with patch.dict(os.environ, {**self.environment, 'DB_PORT': PRIVATE}, clear=True), \
                redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(self.settings.main(['--diagnostics']), 1)
        self.assertEqual(output.getvalue(), '')
        self.assertIn('DB_PORT', errors.getvalue())
        self.assertNotIn(PRIVATE, errors.getvalue())

    def test_cli_runs_with_only_standard_library_and_returns_failure_status(self):
        environment = {key: value for key, value in os.environ.items() if key.upper() in {
            'PATH', 'SYSTEMROOT', 'SYSTEMDRIVE', 'TEMP', 'TMP', 'WINDIR', 'USERPROFILE',
        }}
        environment.update(self.environment)
        command = [sys.executable, '-I', '-S', '-B', str(BACKEND_DIR / 'settings.py'), '--diagnostics']
        with TemporaryDirectory() as directory:
            success = subprocess.run(command, cwd=directory, env=environment,
                                     capture_output=True, text=True, timeout=30)
            environment['STT_VOICE_THRESHOLD'] = 'nan'
            failure = subprocess.run(command, cwd=directory, env=environment,
                                     capture_output=True, text=True, timeout=30)
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(json.loads(success.stdout)['GEN_MODEL_ID'], 'qwen3.5-2b')
        self.assertEqual(success.stderr, '')
        self.assertEqual(failure.returncode, 1)
        self.assertEqual(failure.stdout, '')
        self.assertIn('STT_VOICE_THRESHOLD', failure.stderr)
        self.assertNotIn('Traceback', failure.stderr)
        self.assertNotIn(PRIVATE, success.stdout + success.stderr + failure.stdout + failure.stderr)

    def test_consumers_do_not_define_environment_defaults_outside_registry(self):
        paths = ('generator.py', 'interface.py', 'model_registry.py', 'voice.py', 'speech.py',
                 'TTS/app.py', 'TTS/chat_worker.py', 'TTS/tts_engine.py', 'STT/STT.py')
        for filename in paths:
            tree = ast.parse((BACKEND_DIR / filename).read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                function = ast.unparse(node.func)
                with self.subTest(filename=filename, function=function):
                    self.assertNotIn(function, ('os.getenv', 'os.environ.get'))
