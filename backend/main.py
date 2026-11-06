from settings import (
    ADMIN_PORT, UI_PORT, ConfigurationError, bind_host, is_development, validate_settings,
)
from serving import serve_production
from application import create_apps


def create_serving_apps():
    """Explicitly initialize both services inside the serving process."""
    validate_settings()
    from bootstrap import initialize_runtime

    ui_app, admin_app = create_apps()
    initialize_runtime(ui_app, admin_app)

    host = bind_host()
    address = f'[{host}]' if ':' in host else host
    print(f'[admin] http://{address}:{ADMIN_PORT}')
    print(f'[ui]    http://{address}:{UI_PORT}')
    return ui_app, admin_app


def main():
    validate_settings()
    if not is_development():
        serve_production(create_serving_apps)
        return

    import threading
    from serving import serve_development

    ui_app, admin_app = create_serving_apps()
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
