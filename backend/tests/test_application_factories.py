"""Factory imports and assembly must not initialize external services."""

import importlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from flask import Flask

from core_support import BACKEND_DIR, OfflineCoreFixture


class FactoryImportTests(unittest.TestCase):
    def test_factory_and_bootstrap_imports_need_no_configuration_or_model_dependencies(self):
        script = r'''
import importlib.abc
import socket
import sys
import threading

def forbidden(*args, **kwargs):
    raise AssertionError('Import attempted external work')

class RejectServices(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {
            'database', 'user', 'admin', 'controller', 'controller_api', 'retention',
            'interface', 'generator', 'voice', 'torch', 'transformers', 'faster_whisper',
        }:
            raise AssertionError('Unexpected service import: ' + fullname)

sys.meta_path.insert(0, RejectServices())
socket.socket.connect = forbidden
socket.create_connection = forbidden
threading.Thread.start = forbidden
from application import create_apps, create_admin_app, create_ui_app
from bootstrap import initialize_database, initialize_models, initialize_runtime
from main import create_apps as entry_factory
assert entry_factory is create_apps
'''
        script = 'import sys; sys.path.insert(0, ' + repr(str(BACKEND_DIR)) + ')\n' + script
        environment = {
            key: value for key, value in os.environ.items()
            if key.upper() in {
                'PATH', 'SYSTEMROOT', 'SYSTEMDRIVE', 'TEMP', 'TMP', 'WINDIR', 'PROGRAMDATA',
                'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'HOMEDRIVE', 'HOMEPATH', 'COMSPEC',
            }
        }
        environment.update(APP_ENV='production', DB_PORT='invalid', PYTHONDONTWRITEBYTECODE='1')
        with TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, '-B', '-c', script], cwd=directory, env=environment,
                capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, '')


class ApplicationAssemblyTests(OfflineCoreFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        for name in ('controller', 'controller_api'):
            sys.modules.pop(name, None)
        self.application = importlib.import_module('application')

    def test_route_imports_do_not_construct_apps_or_register_hooks(self):
        with patch.object(Flask, '__init__', side_effect=AssertionError('App created on import')), \
                patch.object(self.database.logger, '_write', side_effect=AssertionError('Import wrote a log')):
            user = importlib.import_module('user')
            admin = importlib.import_module('admin')
            controller = importlib.import_module('controller_api')
        self.assertFalse(hasattr(user, 'ui_app'))
        self.assertFalse(hasattr(admin, 'admin_app'))
        self.assertTrue(callable(controller.register))

    def test_repeated_factories_build_fresh_apps_without_initializing_services(self):
        controller = importlib.import_module('controller')
        bootstrap = importlib.import_module('bootstrap')
        with TemporaryDirectory() as directory:
            state_file = Path(directory) / 'controller_state.json'
            state_file.write_text('{corrupt', encoding='utf-8')
            with patch.object(controller, 'STATE_FILE', str(state_file)), \
                    patch.object(controller, '_load_state') as load_state, \
                    patch.object(bootstrap, 'initialize_database') as database_init, \
                    patch.object(bootstrap, 'initialize_models') as model_init, \
                    patch.object(self.database.logger, '_write', side_effect=AssertionError('Factory wrote a log')), \
                    patch('threading.Thread.start', side_effect=AssertionError('Factory started a thread')), \
                    patch.object(Flask, 'run', side_effect=AssertionError('Factory started a server')):
                first = self.application.create_apps()
                second = self.application.create_apps()
            self.assertEqual(state_file.read_text(encoding='utf-8'), '{corrupt')
        load_state.assert_not_called()
        database_init.assert_not_called()
        model_init.assert_not_called()
        for first_app, second_app in zip(first, second):
            self.assertIsNot(first_app, second_app)
            self.assertIsNot(first_app.config, second_app.config)
            self.assertIsNot(first_app.extensions['abuse_controls'], second_app.extensions['abuse_controls'])
            self.assertNotIn('retention_worker', first_app.extensions)
            self.assertEqual(
                {(rule.rule, rule.endpoint, frozenset(rule.methods)) for rule in first_app.url_map.iter_rules()},
                {(rule.rule, rule.endpoint, frozenset(rule.methods)) for rule in second_app.url_map.iter_rules()},
            )
        self.assertIn('chat', first[0].view_functions)
        self.assertIn('controller_features', first[0].view_functions)
        self.assertIn('admin_login', first[1].view_functions)
        self.assertIn('controller_action', first[1].view_functions)
        self.assertEqual(first[1].before_request_funcs[None][-1].__name__, 'require_admin_session')

    def test_controller_registration_is_idempotent_for_each_app_not_just_the_pair(self):
        controller_api = importlib.import_module('controller_api')
        ui_app, admin_app = self.application.create_apps()
        ui_rules = len(list(ui_app.url_map.iter_rules()))
        admin_rules = len(list(admin_app.url_map.iter_rules()))
        controller_api.register(ui_app, admin_app)
        self.assertEqual(len(list(ui_app.url_map.iter_rules())), ui_rules)
        self.assertEqual(len(list(admin_app.url_map.iter_rules())), admin_rules)
        another_admin = self.application.create_admin_app()
        controller_api.register(ui_app, another_admin)
        self.assertIn('controller_action', another_admin.view_functions)
        another_ui = self.application.create_ui_app()
        controller_api.register(another_ui, admin_app)
        self.assertIn('controller_features', another_ui.view_functions)

    def test_invalid_configuration_fails_before_route_imports_or_initialization(self):
        with patch.dict(os.environ, {'DB_PORT': 'invalid'}), \
                patch.object(self.database.logger, '_write') as audit:
            with self.assertRaises(importlib.import_module('settings').ConfigurationError):
                self.application.create_apps()
        audit.assert_not_called()
        self.assertNotIn('user', sys.modules)
        self.assertNotIn('admin', sys.modules)

    def test_controller_state_is_loaded_only_explicitly(self):
        controller = importlib.import_module('controller')
        with TemporaryDirectory() as directory:
            state_file = Path(directory) / 'controller_state.json'
            state_file.write_text(json.dumps({'chat_enabled': False}), encoding='utf-8')
            with patch.object(controller, 'STATE_FILE', str(state_file)):
                self.application.create_apps()
                self.assertTrue(controller._state['chat_enabled'])
                controller._load_state()
                self.assertFalse(controller._state['chat_enabled'])


class StartupBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.path = patch.object(sys, 'path', [str(BACKEND_DIR), *sys.path])
        self.path.start()
        self.addCleanup(self.path.stop)
        self.main = importlib.import_module('main')

    def test_production_passes_initialized_factory_without_running_it_in_master(self):
        with patch.object(self.main, 'validate_settings'), \
                patch.object(self.main, 'is_development', return_value=False), \
                patch.object(self.main, 'create_serving_apps') as factory, \
                patch.object(self.main, 'serve_production') as serve:
            self.main.main()
        factory.assert_not_called()
        serve.assert_called_once_with(factory)

    def test_failed_initialization_never_starts_retention_or_servers(self):
        bootstrap = importlib.import_module('bootstrap')
        apps = (Flask('failed_ui'), Flask('failed_admin'))
        retention = Mock()
        with patch.object(self.main, 'validate_settings'), \
                patch.object(self.main, 'create_apps', return_value=apps), \
                patch.object(bootstrap, 'initialize_database', side_effect=RuntimeError('DB failed')), \
                patch.dict(sys.modules, {'retention': retention}), \
                patch.object(Flask, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'DB failed'):
                self.main.create_serving_apps()
        retention.start_retention_worker.assert_not_called()
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
