"""Explicit database, model and background-worker initialization."""


def initialize_database():
    """Verify the pre-migrated schema without DDL, audit writes or model imports."""
    from settings import validate_settings
    validate_settings()

    from schema_migrations import require_current_schema

    return require_current_schema()


def initialize_models():
    from services.lifecycle import model_lifecycle

    model_lifecycle.warmup()


def initialize_runtime(ui_app, admin_app):
    """Initialize shared services in the serving process, before accepting requests."""
    initialize_database()

    from controller import _load_state
    from database import logger
    from retention import start_retention_worker
    from ingestion_worker import start_ingestion_worker

    _load_state()
    initialize_models()
    start_ingestion_worker(ui_app, admin_app)
    start_retention_worker(ui_app, admin_app)
    logger.logDrop('Database schema verified and local models initialized')
