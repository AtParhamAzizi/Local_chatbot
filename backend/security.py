"""Shared proxy trust and HTTP error handling for both Flask services."""

from ipaddress import ip_address

from flask import g, jsonify, request
from werkzeug.exceptions import HTTPException

from database import logger
from abuse_controls import configure_abuse_controls
from settings import (
    browser_origins, normalize_origin, redact_secrets, safe_error_message,
    trusted_proxy_hops, trusted_proxy_networks, validate_settings,
)


class TrustedProxyMiddleware:
    """Accept a fixed forwarding suffix only from an explicitly trusted chain."""

    def __init__(self, app, hops, networks):
        self.app = app
        self.hops = hops
        self.networks = networks

    def _trusted(self, address):
        return any(address in network for network in self.networks)

    def _client_address(self, environ):
        try:
            peer = environ.get('REMOTE_ADDR', '')
            if '%' in peer or not self._trusted(ip_address(peer)):
                return None
            forwarded = environ.get('HTTP_X_FORWARDED_FOR', '').split(',')
            if len(forwarded) < self.hops:
                return None
            addresses = [ip_address(value.strip()) for value in forwarded[-self.hops:]]
            if any('%' in str(address) for address in addresses):
                return None
            if not all(self._trusted(address) for address in addresses[1:]):
                return None
            return str(addresses[0])
        except ValueError:
            return None

    def __call__(self, environ, start_response):
        if self.hops:
            client = self._client_address(environ)
            if client is not None:
                environ['REMOTE_ADDR'] = client
                schemes = environ.get('HTTP_X_FORWARDED_PROTO', '').split(',')
                if len(schemes) >= self.hops:
                    scheme = schemes[-self.hops].strip()
                    if scheme in {'http', 'https'}:
                        environ['wsgi.url_scheme'] = scheme
        return self.app(environ, start_response)


def _redact_payload(payload):
    if isinstance(payload, str):
        return redact_secrets(payload)
    if isinstance(payload, list):
        return [_redact_payload(value) for value in payload]
    if isinstance(payload, dict):
        return {key: _redact_payload(value) for key, value in payload.items()}
    return payload


def configure_security(app, *, service):
    validate_settings()
    configure_abuse_controls(app, service=service)
    app.config.update(DEBUG=False, PROPAGATE_EXCEPTIONS=False)
    origins = browser_origins(service)
    app.wsgi_app = TrustedProxyMiddleware(
        app.wsgi_app, trusted_proxy_hops(), trusted_proxy_networks(),
    )

    @app.before_request
    def enforce_browser_origin():
        origin = request.headers.get('Origin')
        if origin is None:
            if service == 'ui' and request.path == '/voice-stream':
                return jsonify(error='A permitted browser origin is required.'), 403
            return None
        normalized = normalize_origin(origin)
        if normalized not in origins or (
            service == 'admin' and normalized != normalize_origin(request.host_url.rstrip('/'))
        ):
            return jsonify(error='Browser origin is not permitted.'), 403
        if service == 'ui' and request.method == 'OPTIONS':
            method = request.headers.get('Access-Control-Request-Method')
            headers = request.headers.get('Access-Control-Request-Headers', '')
            requested_headers = {value.strip().lower() for value in headers.split(',') if value.strip()}
            if (method and method not in {'GET', 'HEAD', 'POST', 'OPTIONS'}) or (
                requested_headers - {'content-type'}
            ):
                return jsonify(error='Cross-origin method or headers are not permitted.'), 403
        g.browser_origin = origin
        return None

    @app.after_request
    def browser_origin_headers(response):
        if service == 'ui':
            response.vary.add('Origin')
            origin = g.get('browser_origin')
            if origin is not None:
                response.headers['Access-Control-Allow-Origin'] = origin
                response.headers['Access-Control-Expose-Headers'] = 'Retry-After'
                if request.method == 'OPTIONS':
                    response.headers['Access-Control-Allow-Methods'] = 'GET, HEAD, POST, OPTIONS'
                    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
            if request.method == 'OPTIONS':
                response.vary.update(('Access-Control-Request-Method', 'Access-Control-Request-Headers'))
        return response

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
