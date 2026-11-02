"""Validated runtime settings. Development must be explicitly selected."""

import json
import os
import secrets
from datetime import timedelta
from ipaddress import ip_address
from urllib.parse import quote, quote_plus


DEVELOPMENT_ENVIRONMENTS = {"development", "dev", "local", "test"}
ADMIN_PORT = 8000
UI_PORT = 5000
_development_session_secret = secrets.token_urlsafe(32)


class ConfigurationError(RuntimeError):
    """A safe configuration diagnostic that never includes setting values."""


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


def bind_host():
    host = os.getenv("APP_HOST", "127.0.0.1").strip()
    if host != "localhost":
        try:
            ip_address(host)
        except ValueError:
            raise ConfigurationError("APP_HOST must be localhost or an IPv4/IPv6 address") from None
    return host


def _integer_setting(name, default, minimum, maximum):
    try:
        value = int(_value(name, str(default)))
    except ValueError:
        raise ConfigurationError(f"{name} must be an integer between {minimum} and {maximum}") from None
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


def session_lifetime_seconds():
    return _integer_setting("ADMIN_SESSION_LIFETIME_SECONDS", 28800, 60, 86400)


def session_idle_seconds():
    return _integer_setting("ADMIN_SESSION_IDLE_SECONDS", 1800, 60, session_lifetime_seconds())


def trusted_proxy_hops():
    return _integer_setting("TRUSTED_PROXY_HOPS", 0, 0, 10)


def admin_session_config():
    return {
        "SESSION_COOKIE_NAME": "local_chatbot_admin_dev" if is_development() else "__Host-local_chatbot_admin",
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SECURE": not is_development(),
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_PATH": "/",
        "SESSION_COOKIE_DOMAIN": None,
        "PERMANENT_SESSION_LIFETIME": timedelta(seconds=session_lifetime_seconds()),
        "SESSION_REFRESH_EACH_REQUEST": False,
    }


def validate_settings():
    bind_host()
    session_idle_seconds()
    trusted_proxy_hops()
    if not is_development():
        required = ("DB_USER", "DB_PASS", "DB_NAME", "ADMIN_SESSION_SECRET")
        missing = [name for name in required if _value(name) is None]
        if missing:
            raise ConfigurationError("Missing required settings: " + ", ".join(missing))
        if _value("DB_USER").strip().lower() == "root":
            raise ConfigurationError("DB_USER must be a restricted application account, not root")
        if len(_value("ADMIN_SESSION_SECRET")) < 32:
            raise ConfigurationError("ADMIN_SESSION_SECRET must contain at least 32 characters")
        if _value("ADMIN_SESSION_SECRET") == "local-chatbot-change-this-secret":
            raise ConfigurationError("ADMIN_SESSION_SECRET must be newly generated")
    try:
        port = int(_value("DB_PORT", "3306"))
    except ValueError:
        raise ConfigurationError("DB_PORT must be an integer between 1 and 65535") from None
    if not 1 <= port <= 65535:
        raise ConfigurationError("DB_PORT must be an integer between 1 and 65535")
    for name in ("DB_HOST", "DB_USER", "DB_NAME"):
        if name in os.environ and _value(name) is None:
            raise ConfigurationError(name + " must not be blank")


def database_config():
    validate_settings()
    return {
        "host": _value("DB_HOST", "localhost"),
        "port": int(_value("DB_PORT", "3306")),
        "user": _value("DB_USER", "root"),
        "password": _value("DB_PASS", "159632@qw"),
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
