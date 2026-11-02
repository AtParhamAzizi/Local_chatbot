"""Session-bound CSRF proof for administrator forms and API mutations."""

import hashlib
import hmac
import secrets

from flask import current_app, g, request, session

from database import logger


def _expected_token():
    if g.get('admin_user'):
        secret = current_app.secret_key
        if isinstance(secret, str):
            secret = secret.encode('utf-8')
        message = b'admin-csrf\x00' + session['admin_session'].encode('utf-8')
        return hmac.new(secret, message, hashlib.sha256).hexdigest()
    return session.get('_login_csrf_token')


@logger.operation
def csrf_token():
    token = _expected_token()
    if not token:
        token = secrets.token_urlsafe(32)
        session['_login_csrf_token'] = token
    return token


@logger.operation
def valid_csrf_token():
    expected = _expected_token()
    supplied = request.headers.get('X-CSRF-Token')
    if supplied is None:
        supplied = request.form.get('csrf_token')
    return (
        isinstance(expected, str)
        and isinstance(supplied, str)
        and supplied.isascii()
        and hmac.compare_digest(expected, supplied)
    )
