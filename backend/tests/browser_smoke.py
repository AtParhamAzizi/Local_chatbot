"""Run primary browser journeys on isolated ephemeral loopback Flask servers."""

import argparse
import os
import secrets
import shutil
import subprocess
import sys
import threading
from contextlib import ExitStack
from pathlib import Path

from werkzeug.serving import WSGIRequestHandler, make_server

from browser_support import BrowserApps


TEST_DIR = Path(__file__).resolve().parent


class QuietRequests(WSGIRequestHandler):
    def log_request(self, code='-', size='-'):
        pass


def not_ready(environ, start_response):
    raise RuntimeError('Browser fixture is not ready')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--channel', choices=('chrome', 'msedge', 'chromium'), default='chrome')
    parser.add_argument('--grep', help='Run only browser cases matching this regular expression')
    options = parser.parse_args()
    if options.repeat < 1:
        parser.error('--repeat must be positive')
    node = shutil.which('node')
    if node is None:
        parser.error('Node.js is required; install it and the browser test dependencies first')
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(('DB_', 'ADMIN_SESSION_', 'BROWSER_'))
    }
    artifacts = TEST_DIR / 'browser-artifacts' / secrets.token_hex(16)
    try:
        with ExitStack() as resources:
            servers = []
            for service in ('ui', 'admin'):
                server = make_server('127.0.0.1', 0, not_ready, threaded=True, request_handler=QuietRequests)
                servers.append(server)
                resources.callback(server.server_close)
            ui_origin, admin_origin = (f'http://127.0.0.1:{server.server_port}' for server in servers)
            apps = resources.enter_context(BrowserApps(ui_origin, admin_origin))
            for server, app in zip(servers, (apps.ui_app, apps.admin_app)):
                server.app = app
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                resources.callback(worker.join, 5)
                resources.callback(server.shutdown)
            environment.update(
                BROWSER_UI_URL=ui_origin, BROWSER_ADMIN_URL=admin_origin,
                BROWSER_SMOKE_TOKEN=apps.token, BROWSER_REPEAT=str(options.repeat),
                BROWSER_CHANNEL=options.channel, BROWSER_ARTIFACT_DIR=str(artifacts),
            )
            command = [node, str(TEST_DIR / 'browser' / 'run.cjs'), 'test',
                       '--config', str(TEST_DIR / 'browser' / 'playwright.config.cjs')]
            if options.grep:
                command.extend(('--grep', options.grep))
            print(f'Browser artifacts: {artifacts}', flush=True)
            result = subprocess.run(command, env=environment, timeout=300 * options.repeat, check=False)
            return result.returncode
    except (OSError, subprocess.SubprocessError) as error:
        print(f'Browser smoke failed ({type(error).__name__}); see backend/tests/TESTING.md.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
