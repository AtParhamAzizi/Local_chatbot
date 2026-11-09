"""Authoritative, dependency-free definitions for application environment settings."""

import argparse
import json
import math
import os
import re
import secrets
import sys
from dataclasses import dataclass, field
from datetime import timedelta
from ipaddress import ip_address, ip_network
from pathlib import Path
from types import MappingProxyType
from urllib.parse import quote, quote_plus, urlsplit


DEVELOPMENT_ENVIRONMENTS = frozenset({"development", "dev", "local", "test"})
ADMIN_PORT = 8000
UI_PORT = 5000
BASE_DIR = Path(__file__).resolve().parent
MODEL_IDS = ("qwen3.5-2b", "qwen3-4b")
_development_session_secret = secrets.token_urlsafe(32)


class ConfigurationError(RuntimeError):
    """A safe configuration diagnostic that never includes setting values."""


@dataclass(frozen=True)
class Setting:
    kind: str
    default: object = field(default=None, repr=False)
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple = ()
    forbidden: tuple = ()
    aliases: tuple = ()
    scopes: tuple = ("app",)
    public: bool = False
    secret: bool = False
    required_in_production: bool = False
    allow_blank: bool = False
    relative_to: str = "backend"


def _development_default(resolver, value):
    return value if resolver.get("APP_ENV") in DEVELOPMENT_ENVIRONMENTS else None


def _local_origins(resolver, port):
    return _development_default(
        resolver, f"http://localhost:{port},http://127.0.0.1:{port},http://[::1]:{port}",
    )


def _tts_python(resolver):
    isolated = BASE_DIR.parent / ".venv-tts" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(isolated) if isolated.is_file() else sys.executable


SETTING_REGISTRY = MappingProxyType({
    "APP_ENV": Setting("choice", "production", choices=("production", *sorted(DEVELOPMENT_ENVIRONMENTS)),
                       aliases=("ENVIRONMENT", "FLASK_ENV"), public=True),
    "APP_HOST": Setting("host", "127.0.0.1", public=True),
    "DB_HOST": Setting("text", "localhost"),
    "DB_PORT": Setting("integer", 3306, 1, 65535, public=True),
    "DB_USER": Setting("text", lambda resolver: _development_default(resolver, "root"), required_in_production=True),
    "DB_PASS": Setting("text", "", secret=True, allow_blank=True, required_in_production=True),
    "DB_NAME": Setting("text", lambda resolver: _development_default(resolver, "chatbot_db"), required_in_production=True),
    "ADMIN_SESSION_SECRET": Setting("text", "", minimum=32, forbidden=("local-chatbot-change-this-secret",),
                                    secret=True, allow_blank=True, required_in_production=True),
    "ADMIN_SESSION_LIFETIME_SECONDS": Setting("integer", 28800, 60, 86400, public=True),
    "ADMIN_SESSION_IDLE_SECONDS": Setting("integer", 1800, 60, 86400, public=True),
    "UI_ALLOWED_ORIGINS": Setting("origins", lambda resolver: _local_origins(resolver, UI_PORT),
                                  public=True, required_in_production=True),
    "ADMIN_ALLOWED_ORIGINS": Setting("origins", lambda resolver: _local_origins(resolver, ADMIN_PORT),
                                     public=True, required_in_production=True),
    "TRUSTED_PROXY_HOPS": Setting("integer", 0, 0, 10, public=True),
    "TRUSTED_PROXY_CIDRS": Setting("networks", "", allow_blank=True, public=True),
    "RETENTION_CHAT_DAYS": Setting("integer", 30, 0, 3650, public=True),
    "RETENTION_IP_DAYS": Setting("integer", 0, 0, 3650, public=True),
    "RETENTION_FEEDBACK_DAYS": Setting("integer", 30, 0, 3650, public=True),
    "RETENTION_LOG_DAYS": Setting("integer", 14, 0, 3650, public=True),
    "RETENTION_DELETE_ENABLED": Setting("boolean", True, public=True),
    "RETENTION_INTERVAL_SECONDS": Setting("integer", 3600, 60, 86400, public=True),
    "RETENTION_BATCH_SIZE": Setting("integer", 1000, 1, 10000, public=True),
    "RETENTION_MAX_BATCHES": Setting("integer", 100, 1, 1000, public=True),
    "EMBEDDING_BATCH_SIZE": Setting("integer", 8, 1, 64, public=True),
    "PDF_OCR_ENABLED": Setting("boolean", False, public=True),
    "PDF_OCR_EXECUTABLE": Setting("text", "tesseract"),
    "PDF_OCR_TESSDATA": Setting("path", None),
    "PDF_OCR_DPI": Setting("integer", 150, 96, 300, public=True),
    "PDF_OCR_TIMEOUT_SECONDS": Setting("integer", 30, 1, 60, public=True),
    "PDF_OCR_MIN_CONFIDENCE": Setting("integer", 75, 1, 100, public=True),
    "PDF_OCR_MIN_TEXT_CHARS": Setting("integer", 32, 1, 4096, public=True),
    "INGESTION_WORKERS": Setting("integer", 1, 1, 4, public=True),
    "INGESTION_QUEUE_CAPACITY": Setting("integer", 16, 1, 256, public=True),
    "INGESTION_POLL_SECONDS": Setting("float", 1.0, 0.05, 30, public=True),
    "GEN_DEVICE": Setting("choice", "auto", choices=("auto", "cpu", "cuda"), public=True),
    "GEN_QUANTIZATION": Setting("choice", "auto", choices=("auto", "4bit", "none"), public=True),
    "GEN_MODEL_ID": Setting("choice", MODEL_IDS[0], choices=MODEL_IDS, public=True),
    "GEN_MODEL": Setting("path", None),
    "QWEN_EMBEDDING_PATH": Setting("path", "model/Qwen3-Embedding-0.6B"),
    "QWEN35_GENERATOR_PATH": Setting("path", lambda resolver: resolver.get("GEN_MODEL") or "modelgen/qwen3.5-2B"),
    "QWEN_GENERATOR_PATH": Setting("path", lambda resolver: resolver.get("GEN_MODEL") or "modelgen"),
    "STT_MODEL": Setting("text", "base", scopes=("app", "stt")),
    "STT_DEVICE": Setting("choice", "cpu", choices=("cpu", "cuda", "auto"), scopes=("app", "stt"), public=True),
    "STT_COMPUTE_TYPE": Setting(
        "choice", lambda resolver: "float16" if resolver.get("STT_DEVICE") == "cuda" else "int8",
        choices=("default", "auto", "int8", "int8_float32", "int8_float16", "int8_bfloat16", "int16",
                 "float16", "bfloat16", "float32"), scopes=("app", "stt"), public=True,
    ),
    "STT_LANGUAGE": Setting(
        "choice", "fa", scopes=("app", "stt"), public=True,
        choices=tuple(("auto af am ar as az ba be bg bn bo br bs ca cs cy da de el en es et eu fa fi fo fr gl gu "
                       "ha haw he hi hr ht hu hy id is it ja jw ka kk km kn ko la lb ln lo lt lv mg mi mk ml mn "
                       "mr ms mt my ne nl nn no oc pa pl ps pt ro ru sa sd si sk sl sn so sq sr su sv sw ta te "
                       "tg th tk tl tr tt uk ur uz vi yi yo zh yue").split()),
    ),
    "STT_VOICE_THRESHOLD": Setting("float", 0.008, 0, 1, scopes=("app", "stt"), public=True),
    "STT_CPU_THREADS": Setting("integer", lambda resolver: min(4, os.cpu_count() or 1), 1, 256,
                               scopes=("app", "stt"), public=True),
    "STT_LOCAL_FILES_ONLY": Setting("boolean", False, scopes=("app", "stt"), public=True),
    "STT_HOST": Setting("host", "127.0.0.1", scopes=("stt",), public=True),
    "STT_PORT": Setting("integer", UI_PORT, 1, 65535, scopes=("stt",), public=True),
    "TTS_PYTHON": Setting("executable", _tts_python, relative_to="repository"),
    "TTS_CPU_THREADS": Setting("integer", 1, 1, 256, scopes=("app", "tts"), public=True),
    "ENGLISH_MODEL_FILE": Setting("filename", "model_quantized.onnx", scopes=("app", "tts")),
    "ENGLISH_VOICE": Setting("identifier", "af_heart", scopes=("app", "tts"), public=True),
    "PERSIAN_G2P_REPO": Setting("text", "mehdi-hf/Homo-GE2PE-Persian-HF", scopes=("app", "tts")),
    "PERSIAN_VOICE_PROMPT": Setting("path", "voices/prompt_hello.wav", scopes=("app", "tts"), relative_to="tts"),
    "HOST": Setting("host", "127.0.0.1", scopes=("tts",), public=True),
    "PORT": Setting("integer", UI_PORT, 1, 65535, scopes=("tts",), public=True),
})


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


class _Resolver:
    def __init__(self, environ=None, tts_base=None):
        self.environ = dict(os.environ if environ is None else environ)
        self.tts_base = BASE_DIR / "TTS" if tts_base is None else Path(tts_base)
        self.values = {}

    def get(self, name):
        if name in self.values:
            return self.values[name]
        definition = SETTING_REGISTRY[name]
        source = next((key for key in (name, *definition.aliases) if key in self.environ), None)
        raw = self.environ[source] if source else definition.default
        if callable(raw):
            raw = raw(self)
        value = None if raw is None and source is None else self._parse(name, definition, raw)
        self.values[name] = value
        return value

    def _parse(self, name, definition, raw):
        def invalid(guidance):
            raise ConfigurationError(f"{name} {guidance}. Set {name} in the process environment before starting.") from None

        kind = definition.kind
        if not isinstance(raw, str) and (kind not in {"integer", "float", "boolean"}
                                         or not isinstance(raw, (int, float, bool))):
            invalid("must be a string environment value")
        if isinstance(raw, str) and any(ord(character) < 32 for character in raw):
            invalid("must not contain control characters")
        value = str(raw).strip()
        if not value and not definition.allow_blank:
            invalid("must not be blank; unset it to use its default")
        if value in definition.forbidden:
            invalid("must be newly generated; known example values are not allowed")
        if kind in {"integer", "float"}:
            try:
                if isinstance(raw, bool):
                    raise ValueError()
                number = int(value) if kind == "integer" else float(value)
                if not math.isfinite(number) or not definition.minimum <= number <= definition.maximum:
                    raise ValueError()
                return number
            except (ValueError, OverflowError):
                invalid(f"must be {'an integer' if kind == 'integer' else 'a finite number'} between "
                        f"{definition.minimum} and {definition.maximum}")
        if kind == "boolean":
            if value.lower() not in {"0", "1", "false", "true"}:
                invalid("must be 0, 1, false, or true")
            return value.lower() in {"1", "true"}
        if kind == "choice":
            if value.lower() not in definition.choices:
                invalid("must be one of " + ", ".join(definition.choices))
            return value.lower()
        if kind == "host":
            try:
                if value != "localhost":
                    ip_address(value)
            except ValueError:
                invalid("must be localhost or an IPv4/IPv6 address")
        elif kind == "origins":
            origins = tuple(normalize_origin(part.strip()) for part in value.split(","))
            if None in origins:
                invalid("must contain only explicit HTTP(S) origins without paths, wildcards, or credentials")
            return frozenset(origins)
        elif kind == "networks":
            try:
                return tuple(ip_network(part.strip(), strict=False) for part in value.split(",")) if value else ()
            except ValueError:
                invalid("must contain comma-separated IP addresses or CIDRs")
        elif kind == "identifier":
            if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
                invalid("must contain only letters, digits, underscores, or hyphens")
        elif kind == "filename":
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.onnx", value):
                invalid("must be an ONNX filename without directories")
        elif kind in {"path", "executable"}:
            base = {"backend": BASE_DIR, "repository": BASE_DIR.parent, "tts": self.tts_base}[definition.relative_to]
            try:
                path = (base / value).resolve()
                if kind == "executable" and not path.is_file():
                    invalid("must point to an existing Python executable")
            except (OSError, ValueError):
                invalid("must be a valid filesystem path")
            return str(path)
        elif kind != "text":
            raise AssertionError(f"Unknown setting type: {kind}")
        if value and definition.minimum is not None and len(raw) < definition.minimum:
            invalid(f"must contain at least {definition.minimum} characters")
        return raw if definition.secret and value else value


def setting_value(name, *, environ=None, tts_base=None):
    return _Resolver(environ, tts_base).get(name)


def environment_name():
    return setting_value("APP_ENV")


def is_development():
    try:
        return environment_name() in DEVELOPMENT_ENVIRONMENTS
    except ConfigurationError:
        return False


def bind_host():
    return setting_value("APP_HOST")


def session_lifetime_seconds():
    return setting_value("ADMIN_SESSION_LIFETIME_SECONDS")


def session_idle_seconds():
    resolver = _Resolver()
    idle = resolver.get("ADMIN_SESSION_IDLE_SECONDS")
    _validate_session_timeouts(idle, resolver.get("ADMIN_SESSION_LIFETIME_SECONDS"))
    return idle


def _validate_session_timeouts(idle, lifetime):
    if idle > lifetime:
        raise ConfigurationError("ADMIN_SESSION_IDLE_SECONDS must not exceed ADMIN_SESSION_LIFETIME_SECONDS")


@dataclass(frozen=True)
class RetentionPolicy:
    chat_days: int = SETTING_REGISTRY["RETENTION_CHAT_DAYS"].default
    ip_days: int = SETTING_REGISTRY["RETENTION_IP_DAYS"].default
    feedback_days: int = SETTING_REGISTRY["RETENTION_FEEDBACK_DAYS"].default
    log_days: int = SETTING_REGISTRY["RETENTION_LOG_DAYS"].default
    delete_enabled: bool = SETTING_REGISTRY["RETENTION_DELETE_ENABLED"].default
    interval_seconds: int = SETTING_REGISTRY["RETENTION_INTERVAL_SECONDS"].default
    batch_size: int = SETTING_REGISTRY["RETENTION_BATCH_SIZE"].default
    max_batches: int = SETTING_REGISTRY["RETENTION_MAX_BATCHES"].default


def retention_settings():
    resolver = _Resolver()
    return RetentionPolicy(**{name: resolver.get(f"RETENTION_{name.upper()}") for name in RetentionPolicy.__dataclass_fields__})


def trusted_proxy_hops():
    return setting_value("TRUSTED_PROXY_HOPS")


def trusted_proxy_networks():
    return setting_value("TRUSTED_PROXY_CIDRS")


def browser_origins(service):
    name = {"ui": "UI_ALLOWED_ORIGINS", "admin": "ADMIN_ALLOWED_ORIGINS"}[service]
    origins = setting_value(name)
    if not origins:
        raise ConfigurationError(f"{name} must contain explicit HTTP(S) browser origins")
    return origins


def load_settings(*, environ=None, scope="app", tts_base=None):
    if scope not in {"app", "stt", "tts"}:
        raise ValueError("Unknown configuration scope")
    resolver = _Resolver(environ, tts_base)
    values = {name: resolver.get(name) for name, definition in SETTING_REGISTRY.items() if scope in definition.scopes}
    if scope == "app":
        _validate_session_timeouts(values["ADMIN_SESSION_IDLE_SECONDS"], values["ADMIN_SESSION_LIFETIME_SECONDS"])
        if values["TRUSTED_PROXY_HOPS"] and not values["TRUSTED_PROXY_CIDRS"]:
            raise ConfigurationError("TRUSTED_PROXY_HOPS requires explicit TRUSTED_PROXY_CIDRS")
        if values["GEN_DEVICE"] == "cpu" and values["GEN_QUANTIZATION"] == "4bit":
            raise ConfigurationError("GEN_QUANTIZATION=4bit requires GEN_DEVICE=cuda or auto and a CUDA runtime")
        if values["APP_ENV"] not in DEVELOPMENT_ENVIRONMENTS:
            required = (name for name in values if SETTING_REGISTRY[name].required_in_production)
            missing = [name for name in required if not values[name] or not str(values[name]).strip()]
            if missing:
                raise ConfigurationError("Missing required settings: " + ", ".join(missing)
                                         + ". Supply deployment settings, or explicitly select APP_ENV=development for local use.")
            if values["DB_USER"].lower() == "root":
                raise ConfigurationError("DB_USER must be a restricted application account, not root")
    return MappingProxyType(values)


def validate_settings(*, scope="app"):
    load_settings(scope=scope)


def effective_settings(*, environ=None, scope="app"):
    values = load_settings(environ=environ, scope=scope)
    return {
        name: sorted(str(item) for item in value) if isinstance(value, (tuple, frozenset)) else value
        for name, value in values.items() if SETTING_REGISTRY[name].public and not SETTING_REGISTRY[name].secret
    }


def database_config():
    values = load_settings()
    return {
        "host": values["DB_HOST"], "port": values["DB_PORT"], "user": values["DB_USER"],
        "password": values["DB_PASS"], "database": values["DB_NAME"],
    }


def session_secret():
    return load_settings()["ADMIN_SESSION_SECRET"] or _development_session_secret


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


def redact_secrets(message):
    message = str(message)
    values = {_development_session_secret}
    for name, definition in SETTING_REGISTRY.items():
        if not definition.secret:
            continue
        value = os.getenv(name)
        if value:
            values.update((value, quote(value, safe=""), quote_plus(value),
                           repr(value)[1:-1], json.dumps(value)[1:-1]))
    for value in sorted(values, key=len, reverse=True):
        message = message.replace(value, "[REDACTED]")
    return message


def safe_error_message(error):
    return type(error).__name__ if isinstance(error, BaseException) else redact_secrets(error)


def main(arguments=None):
    parser = argparse.ArgumentParser(description="Validate configuration without importing services or opening connections.")
    parser.add_argument("--diagnostics", action="store_true", help="Print only non-secret effective settings as JSON")
    parser.add_argument("--scope", choices=("app", "stt", "tts"), default="app")
    options = parser.parse_args(arguments)
    try:
        report = effective_settings(scope=options.scope)
    except ConfigurationError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True, indent=2) if options.diagnostics else "Configuration is valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
