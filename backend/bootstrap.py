"""Explicit database, model and background-worker initialization."""


def initialize_database():
    from settings import validate_settings
    validate_settings()

    from database import (
        init_db, init_admin_users_table, init_logger_table, logger,
        init_documents_chunk_table, init_documents_table, init_history_table,
    )

    init_logger_table()
    logger.logDrop('Starting local chatbot services')
    init_db()
    init_admin_users_table()
    init_documents_table()
    init_documents_chunk_table()
    init_history_table()


def initialize_models():
    from services.lifecycle import model_lifecycle

    model_lifecycle.warmup()


def initialize_runtime(ui_app, admin_app):
    """Initialize shared services in the serving process, before accepting requests."""
    initialize_database()

    from controller import _load_state
    from database import logger
    from retention import start_retention_worker

    _load_state()
    initialize_models()
    start_retention_worker(ui_app, admin_app)
    logger.logDrop('Database and local models initialized')
