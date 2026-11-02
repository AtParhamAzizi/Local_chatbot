"""Shared proxy trust and HTTP error handling for both Flask services."""

from flask import jsonify
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from database import logger
from settings import redact_secrets, safe_error_message, trusted_proxy_hops, validate_settings


def _redact_payload(payload):
    if isinstance(payload, str):
        return redact_secrets(payload)
    if isinstance(payload, list):
        return [_redact_payload(value) for value in payload]
    if isinstance(payload, dict):
        return {key: _redact_payload(value) for key, value in payload.items()}
    return payload


def configure_security(app):
    validate_settings()
    app.config.update(DEBUG=False, PROPAGATE_EXCEPTIONS=False)
    app.wsgi_app = ProxyFix(
        app.wsgi_app, x_for=0, x_proto=trusted_proxy_hops(), x_host=0, x_port=0, x_prefix=0,
    )

    @app.errorhandler(Exception)
    def unexpected_error(error):
        if isinstance(error, HTTPException):
            response = error.get_response()
            response.set_data(app.json.dumps({'error': error.name}))
            response.content_type = 'application/json'
            return response
        logger.logDrop(f'Request failed: {safe_error_message(error)}', logger.FAILED)
        return jsonify(error='Request failed. Please try again.'), 500

    @app.after_request
    def sanitize_error_response(response):
        if response.status_code >= 400 and response.is_json:
            response.set_data(app.json.dumps(_redact_payload(response.get_json())))
        return response
