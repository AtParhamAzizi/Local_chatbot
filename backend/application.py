"""Application assembly only; importing factories does not initialize services."""


def create_ui_app():
    from settings import validate_settings
    validate_settings()

    from flask import Flask
    from chat_sessions import configure_chat_sessions
    from security import configure_security
    from user import FRONTEND_DIR, register_ui_routes
    from voice import configure_voice

    app = Flask('ui', static_folder=FRONTEND_DIR, static_url_path='', template_folder=FRONTEND_DIR)
    configure_security(app, service='ui')
    configure_chat_sessions(app)
    configure_voice(app)
    register_ui_routes(app)
    return app


def create_admin_app():
    from settings import admin_session_config, session_secret, validate_settings
    validate_settings()

    from flask import Flask
    from admin import register_admin_routes
    from security import configure_security

    app = Flask('admin', static_folder=None)
    app.secret_key = session_secret()
    app.config.update(admin_session_config())
    configure_security(app, service='admin')
    register_admin_routes(app)
    return app


def create_apps():
    """Build fresh apps and register shared controller hooks without starting work."""
    ui_app = create_ui_app()
    admin_app = create_admin_app()

    from controller_api import register
    register(ui_app, admin_app)
    return ui_app, admin_app
