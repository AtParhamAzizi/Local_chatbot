"""Validated runtime settings. Development must be explicitly selected."""

import json
import os
import secrets
from urllib.parse import quote, quote_plus


DEVELOPMENT_ENVIRONMENTS = {"development", "dev", "local", "test"}
_development_session_secret = secrets.token_urlsafe(32)


def environment_name():
    return (
        os.getenv("APP_ENV") or os.getenv("ENVIRONMENT")
        or os.getenv("FLASK_ENV") or "production"
    ).strip().lower()


def is_development():
    return environment_name() in DEVELOPMENT_ENVIRONMENTS


def _value(name, default=None):
    value = os.getenv(name)
    return default if value is None or not value.strip() else value


def validate_settings():
    if not is_development():
        required = ("DB_USER", "DB_PASS", "DB_NAME", "ADMIN_SESSION_SECRET")
        missing = [name for name in required if _value(name) is None]
        if missing:
            raise RuntimeError("Missing required settings: " + ", ".join(missing))
        if _value("DB_USER").strip().lower() == "root":
            raise RuntimeError("DB_USER must be a restricted application account, not root")
        if len(_value("ADMIN_SESSION_SECRET")) < 32:
            raise RuntimeError("ADMIN_SESSION_SECRET must contain at least 32 characters")
        if _value("ADMIN_SESSION_SECRET") == "local-chatbot-change-this-secret":
            raise RuntimeError("ADMIN_SESSION_SECRET must be newly generated")
    try:
        port = int(_value("DB_PORT", "3306"))
    except ValueError:
        raise RuntimeError("DB_PORT must be an integer between 1 and 65535") from None
    if not 1 <= port <= 65535:
        raise RuntimeError("DB_PORT must be an integer between 1 and 65535")
    for name in ("DB_HOST", "DB_USER", "DB_NAME"):
        if name in os.environ and _value(name) is None:
            raise RuntimeError(name + " must not be blank")


def database_config():
    validate_settings()
    return {
        "host": _value("DB_HOST", "localhost"),
        "port": int(_value("DB_PORT", "3306")),
        "user": _value("DB_USER", "chatbot_app"),
        "password": _value("DB_PASS", ""),
        "database": _value("DB_NAME", "chatbot_db"),
    }


def session_secret():
    validate_settings()
    return _value("ADMIN_SESSION_SECRET") or _development_session_secret


def redact_secrets(message):
    message = str(message)
    values = {_development_session_secret}
    for name in ("DB_PASS", "ADMIN_SESSION_SECRET"):
        value = os.getenv(name)
        if value:
            values.update((value, quote(value, safe=""), quote_plus(value),
                           repr(value)[1:-1], json.dumps(value)[1:-1]))
    for value in sorted(values, key=len, reverse=True):
        message = message.replace(value, "[REDACTED]")
    return message


def safe_error_message(error):
    return type(error).__name__ if isinstance(error, BaseException) else redact_secrets(error)
