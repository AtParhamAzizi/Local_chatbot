"""Run the optional Chrome smoke test against offline doubles, never a real DB/model."""

import importlib
import os
import shutil
import subprocess
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from flask import jsonify
from flask_sock import Sock
from simple_websocket import ConnectionClosed
from werkzeug.serving import make_server

import test_abuse_controls as fixtures


class BrowserFixture(fixtures.AbuseEndpointFixture, unittest.TestCase):
    pass


def main():
    servers = []
    browser_environment = os.environ.copy()
    node = shutil.which('node')
    if node is None:
        raise RuntimeError('The browser smoke test requires Node.js on PATH.')
    try:
        with patch.object(fixtures, 'FakeSock', Sock), patch.object(fixtures, 'TestConnectionClosed', ConnectionClosed):
            BrowserFixture.setUpClass()
        admin = BrowserFixture.admin
        user = BrowserFixture.user
        voice = BrowserFixture.voice
        account = {'id': 1, 'username': 'browser-test', 'is_active': True, 'is_admin': True,
                   'permissions': [], 'session_version': 1}
        admin.authenticate = Mock(return_value=account)
        admin.create_admin_session = Mock(return_value='browser-test-session')
        admin.load_admin_session = lambda token: account if token == 'browser-test-session' else None
        admin.revoke_admin_session = Mock()
        user._retrieve_top = Mock(return_value=[])
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.lastrowid = 1
        user.get_conn = Mock(return_value=connection)
        voice._model_status = lambda: ('ready', None)
        voice._start_model_loading = Mock()

        controller = types.ModuleType('controller')
        controller._state = {'voice_enabled': True, 'chat_enabled': True}
        controller._set_state = lambda **values: controller._state.update(values)
        controller._free_memory = Mock()
        controller._gpu_info = Mock(return_value={})
        sys.modules['controller'] = controller
        generator = sys.modules['generator']
        generator._gen_model = None
        generator._gen_device = None
        generator.MODEL_ID = 'test'
        interface = sys.modules['interface']
        interface._model = None
        interface._dim = None
        interface.MODEL_ID = 'test'
        importlib.import_module('controller_api')

        responses = {
            'admin_stats': {'total_documents': 0, 'embedded_documents': 0, 'dim': 0},
            'admin_documents': {'items': [], 'total': 0, 'page': 1, 'per_page': 10},
            'admin_pdf_stats': {'total_documents': 0, 'total_chunks': 0, 'embedded_chunks': 0, 'dim': 0},
            'admin_pdf_documents': {'items': [], 'total': 0, 'page': 1, 'per_page': 10},
            'admin_models': {'models': [], 'active_model': 'test'},
            'admin_set_model': {'ok': True},
            'admin_chat_sessions': {'sessions': [], 'count': 0},
            'admin_chat_stats': {'total_messages': 0, 'total_sessions': 0, 'total_ips': 0, 'likes': 0, 'dislikes': 0},
            'get_admin_logs': [],
            'admin_users_collection': {'users': [], 'current_user_id': 1},
        }
        for endpoint, payload in responses.items():
            admin.admin_app.view_functions[endpoint] = lambda payload=payload: jsonify(payload)

        for app, port in ((user.ui_app, 5000), (admin.admin_app, 8000)):
            server = make_server('127.0.0.1', port, app, threaded=True)
            servers.append(server)
            threading.Thread(target=server.serve_forever, daemon=True).start()
        script = Path(__file__).with_suffix('.cjs')
        result = subprocess.run([node, str(script)], env=browser_environment, timeout=60, check=False)
        return result.returncode
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        BrowserFixture.doClassCleanups()


if __name__ == '__main__':
    raise SystemExit(main())
