from settings import (
    ADMIN_PORT, UI_PORT, ConfigurationError, bind_host, is_development, validate_settings,
)
from serving import serve_production


def create_apps():
    """Initialize both services and shared models inside the serving process."""
    validate_settings()

    from interface import get_embedder
    from admin import admin_app
    from user import ui_app
    from database import (
        init_db, init_admin_users_table, init_logger_table, logger,
        init_documents_chunk_table, init_documents_table, init_history_table,
    )
    from generator import get_generator
    from retention import start_retention_worker
    import controller_api

    init_logger_table()
    logger.logDrop('Starting local chatbot services')
    init_db()
    init_admin_users_table()
    init_documents_table()
    init_documents_chunk_table()
    init_history_table()
    get_embedder()
    get_generator()
    start_retention_worker(ui_app, admin_app)

    logger.logDrop('Database and local models initialized')

    host = bind_host()
    address = f'[{host}]' if ':' in host else host
    print(f'[admin] http://{address}:{ADMIN_PORT}')
    print(f'[ui]    http://{address}:{UI_PORT}')
    return ui_app, admin_app


def main():
    validate_settings()
    if not is_development():
        serve_production(create_apps)
        return

    import threading
    from serving import serve_development

    ui_app, admin_app = create_apps()
    user_thread = threading.Thread(
        target=serve_development, args=(ui_app, UI_PORT), daemon=True
    )
    user_thread.start()
    serve_development(admin_app, ADMIN_PORT)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nbye')
    except ConfigurationError as error:
        print(f'Startup configuration error: {error}')
        if not is_development():
            print('For local development, explicitly set APP_ENV=development before starting.')
        raise SystemExit(1) from None
    except Exception as error:
        print(f'Startup failed ({type(error).__name__}). Check service configuration.')
        raise SystemExit(1) from None
