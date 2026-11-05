"""Validated runtime settings. Development must be explicitly selected."""

import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from ipaddress import ip_address, ip_network
from urllib.parse import quote, quote_plus, urlsplit


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


@dataclass(frozen=True)
class RetentionPolicy:
    chat_days: int = 30
    ip_days: int = 0
    feedback_days: int = 30
    log_days: int = 14
    delete_enabled: bool = True
    interval_seconds: int = 3600
    batch_size: int = 1000
    max_batches: int = 100


def retention_settings():
    deletion = _value('RETENTION_DELETE_ENABLED', '1').strip().lower()
    if deletion not in {'0', '1', 'false', 'true'}:
        raise ConfigurationError('RETENTION_DELETE_ENABLED must be 0, 1, false, or true')
    return RetentionPolicy(
        chat_days=_integer_setting('RETENTION_CHAT_DAYS', 30, 0, 3650),
        ip_days=_integer_setting('RETENTION_IP_DAYS', 0, 0, 3650),
        feedback_days=_integer_setting('RETENTION_FEEDBACK_DAYS', 30, 0, 3650),
        log_days=_integer_setting('RETENTION_LOG_DAYS', 14, 0, 3650),
        delete_enabled=deletion in {'1', 'true'},
        interval_seconds=_integer_setting('RETENTION_INTERVAL_SECONDS', 3600, 60, 86400),
        batch_size=_integer_setting('RETENTION_BATCH_SIZE', 1000, 1, 10000),
        max_batches=_integer_setting('RETENTION_MAX_BATCHES', 100, 1, 1000),
    )


def trusted_proxy_hops():
    return _integer_setting("TRUSTED_PROXY_HOPS", 0, 0, 10)


def trusted_proxy_networks():
    configured = os.getenv("TRUSTED_PROXY_CIDRS", "").strip()
    if not configured:
        return ()
    try:
        return tuple(ip_network(value.strip(), strict=False) for value in configured.split(","))
    except ValueError:
        raise ConfigurationError("TRUSTED_PROXY_CIDRS must contain comma-separated IP addresses or CIDRs") from None


def normalize_origin(value):
    if not value or not re.fullmatch(
        r"https?://(?:\[[0-9a-f:.]+\]|[a-z0-9.-]+)(?::[0-9]+)?", value, re.IGNORECASE,
    ):
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None or parsed.path
        ):
            return None
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{ip_address(host)}]"
        elif not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
            return None
        port = parsed.port
        if parsed.netloc.endswith(":") or port == 0:
            return None
    except ValueError:
        return None
    default_port = 443 if parsed.scheme == "https" else 80
    suffix = f":{port}" if port is not None and port != default_port else ""
    return f"{parsed.scheme}://{host}{suffix}"


def browser_origins(service):
    port = {"ui": UI_PORT, "admin": ADMIN_PORT}[service]
    name = f"{service.upper()}_ALLOWED_ORIGINS"
    configured = os.getenv(name)
    if configured is None and is_development():
        configured = f"http://localhost:{port},http://127.0.0.1:{port},http://[::1]:{port}"
    if not configured or not configured.strip():
        raise ConfigurationError(f"{name} must contain explicit HTTP(S) browser origins")
    origins = tuple(normalize_origin(value.strip()) for value in configured.split(","))
    if None in origins:
        raise ConfigurationError(f"{name} must contain only HTTP(S) origins without paths, wildcards, or credentials")
    return frozenset(origins)


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
    retention_settings()
    session_idle_seconds()
    hops = trusted_proxy_hops()
    networks = trusted_proxy_networks()
    if hops and not networks:
        raise ConfigurationError("TRUSTED_PROXY_HOPS requires explicit TRUSTED_PROXY_CIDRS")
    browser_origins("ui")
    browser_origins("admin")
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
