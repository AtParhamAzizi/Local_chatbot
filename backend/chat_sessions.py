"""Server-issued anonymous chat identity, separate from administrator sessions."""

import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from flask import request, session

from database import logger
from settings import is_development, session_secret


def configure_chat_sessions(app):
    app.secret_key = hmac.new(
        session_secret().encode('utf-8'), b'local-chatbot/chat-session/v1', hashlib.sha256,
    ).digest()
    app.config.update(
        SESSION_COOKIE_NAME='local_chatbot_chat_dev' if is_development() else '__Host-local_chatbot_chat',
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=not is_development(),
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_PATH='/',
        SESSION_COOKIE_DOMAIN=None,
        PERMANENT_SESSION_LIFETIME=timedelta(days=1),
        SESSION_REFRESH_EACH_REQUEST=False,
    )

    @app.after_request
    def protect_chat_response(response):
        if request.endpoint in {'ui_index', 'chat', 'feedback'}:
            response.headers['Cache-Control'] = 'no-store'
        return response


@logger.operation
def chat_owner_hash(*, create=False):
    token = session.get('chat_owner')
    if not isinstance(token, str) or re.fullmatch(r'[A-Za-z0-9_-]{43}', token) is None:
        if not create:
            return None
        session.clear()
        token = secrets.token_urlsafe(32)
        session['chat_owner'] = token
        session.permanent = True
    return hashlib.sha256(b'chat-owner\x00' + token.encode('ascii')).hexdigest()
