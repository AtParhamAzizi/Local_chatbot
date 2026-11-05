"""Explicit development serving and a single-worker production runtime."""

import os

from settings import ADMIN_PORT, UI_PORT, bind_host, is_development, validate_settings
from werkzeug.serving import WSGIRequestHandler


class NoAccessLogRequestHandler(WSGIRequestHandler):
    def log_request(self, code='-', size='-'):
        return


def serve_development(app, port):
    validate_settings()
    if not is_development():
        raise RuntimeError(
            "The Flask development server requires APP_ENV=development. "
            "Use python backend/main.py for production."
        )
    from retention import start_retention_worker
    start_retention_worker(app)
    app.run(
        host=bind_host(),
        port=port,
        debug=False,
        use_debugger=False,
        use_reloader=False,
        threaded=True,
        request_handler=NoAccessLogRequestHandler,
    )


class PortDispatcher:
    """Select the service by the listening socket port, not the Host header."""

    def __init__(self, ui_app, admin_app):
        self.apps = {str(UI_PORT): ui_app, str(ADMIN_PORT): admin_app}

    def __call__(self, environ, start_response):
        app = self.apps.get(environ.get("SERVER_PORT"))
        if app is None:
            start_response("404 Not Found", [("Content-Type", "text/plain")])
            return [b"Unknown service port."]
        return app(environ, start_response)


def serve_production(create_apps):
    validate_settings()
    if os.name == "nt":
        raise SystemExit(
            "Production serving requires Linux/WSL and requirements-production.txt. "
            "For local Windows development, explicitly set APP_ENV=development."
        )
    try:
        from gunicorn.app.base import BaseApplication
    except ImportError:
        raise SystemExit(
            "Install the production server with: "
            "python -m pip install -r requirements-production.txt"
        ) from None

    host = bind_host()
    address = f"[{host}]" if ":" in host else host

    class ProductionApplication(BaseApplication):
        def load_config(self):
            options = {
                "bind": [f"{address}:{UI_PORT}", f"{address}:{ADMIN_PORT}"],
                "workers": 1,
                "worker_class": "gthread",
                "threads": 8,
                "timeout": 300,
                "preload_app": False,
                "reload": False,
                "forwarded_allow_ips": "",
                "accesslog": None,
            }
            for name, value in options.items():
                self.cfg.set(name, value)

        def load(self):
            try:
                return PortDispatcher(*create_apps())
            except Exception as error:
                raise RuntimeError(
                    f"Service initialization failed ({type(error).__name__}). "
                    "Check database and model configuration."
                ) from None

    ProductionApplication().run()
