"""Application assembly only; importing factories does not initialize services."""


def create_ui_app(*, retrieval_service=None, speech_service=None):
    from settings import validate_settings
    validate_settings()

    from flask import Flask
    from chat_sessions import configure_chat_sessions
    from security import configure_security
    from user import FRONTEND_DIR, register_ui_routes
    from voice import configure_voice
    from speech import configure_speech
    from services.retrieval import RetrievalService

    app = Flask('ui', static_folder=FRONTEND_DIR, static_url_path='', template_folder=FRONTEND_DIR)
    configure_security(app, service='ui')
    configure_chat_sessions(app)
    configure_voice(app)
    configure_speech(app, service=speech_service)
    app.extensions['retrieval_service'] = RetrievalService() if retrieval_service is None else retrieval_service
    register_ui_routes(app)
    return app


def create_admin_app(*, ingestion_service=None, model_lifecycle=None):
    from settings import admin_session_config, session_secret, validate_settings
    validate_settings()

    from flask import Flask
    from admin import register_admin_routes
    from security import configure_security
    from services.ingestion import IngestionService
    from services.lifecycle import model_lifecycle as shared_lifecycle

    app = Flask('admin', static_folder=None)
    app.secret_key = session_secret()
    app.config.update(admin_session_config())
    configure_security(app, service='admin')
    app.extensions['ingestion_service'] = IngestionService() if ingestion_service is None else ingestion_service
    app.extensions['model_lifecycle'] = shared_lifecycle if model_lifecycle is None else model_lifecycle
    register_admin_routes(app)
    return app


def create_apps():
    """Build fresh apps and register shared controller hooks without starting work."""
    ui_app = create_ui_app()
    admin_app = create_admin_app()
    ui_app.extensions['model_lifecycle'] = admin_app.extensions['model_lifecycle']

    from controller_api import register
    register(ui_app, admin_app)
    return ui_app, admin_app
