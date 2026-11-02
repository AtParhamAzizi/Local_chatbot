"""Expiring, revocable administrator sessions backed by MySQL."""

import hashlib
import secrets
import time

from admin_users import serialize_user
from database import get_conn, logger
from settings import session_idle_seconds, session_lifetime_seconds


def _token_hash(token):
    if not isinstance(token, str) or len(token) != 43:
        return None
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


@logger.operation
def create_admin_session(user):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'DELETE FROM admin_sessions WHERE expires_at <= %s OR last_seen_at <= %s',
                (now, now - session_idle_seconds()),
            )
            cur.execute(
                'INSERT INTO admin_sessions (token_hash, user_id, session_version, expires_at, last_seen_at) '
                'VALUES (%s, %s, %s, %s, %s)',
                (_token_hash(token), user['id'], user['session_version'], now + session_lifetime_seconds(), now),
            )
        conn.commit()
    finally:
        conn.close()
    return token


@logger.operation
def load_admin_session(token):
    token_hash = _token_hash(token)
    if token_hash is None:
        return None
    now = int(time.time())
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT u.*, s.expires_at, s.last_seen_at FROM admin_sessions s '
                'JOIN `admin-users` u ON u.id = s.user_id AND u.session_version = s.session_version '
                'WHERE s.token_hash = %s AND u.is_active = 1',
                (token_hash,),
            )
            row = cur.fetchone()
            if not row or row['expires_at'] <= now or row['last_seen_at'] <= now - session_idle_seconds():
                cur.execute('DELETE FROM admin_sessions WHERE token_hash = %s', (token_hash,))
                conn.commit()
                return None
            cur.execute(
                'UPDATE admin_sessions SET last_seen_at = GREATEST(last_seen_at, %s) WHERE token_hash = %s',
                (now, token_hash),
            )
        conn.commit()
        return serialize_user(row)
    finally:
        conn.close()


@logger.operation
def revoke_admin_session(token):
    token_hash = _token_hash(token)
    if token_hash is None:
        return
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM admin_sessions WHERE token_hash = %s', (token_hash,))
        conn.commit()
    finally:
        conn.close()
