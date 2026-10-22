"""Admin authentication and user-management helpers."""

import hashlib
import hmac
import json
import secrets

from database import get_conn


DEFAULT_USERNAME = 'administrator'
DEFAULT_PASSWORD = 'P@ssw0rd'
ALL_PERMISSIONS = '*'


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 240_000)
    return f'pbkdf2_sha256$240000${salt.hex()}${digest.hex()}'


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_hex, digest_hex = encoded.split('$', 3)
        if algorithm != 'pbkdf2_sha256':
            return False
        candidate = hashlib.pbkdf2_hmac(
            'sha256', password.encode('utf-8'), bytes.fromhex(salt_hex), int(rounds)
        )
        return hmac.compare_digest(candidate.hex(), digest_hex)
    except (TypeError, ValueError):
        return False


def _permissions(value):
    if value == ALL_PERMISSIONS:
        return ALL_PERMISSIONS
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = []
    return sorted({str(item) for item in (value or []) if str(item).strip()})


def serialize_user(row):
    return {
        'id': int(row['id']),
        'username': row['username'],
        'is_active': bool(row['is_active']),
        'is_admin': bool(row['is_admin']),
        'permissions': _permissions(row.get('permissions')),
        'created_at': row['created_at'].isoformat(sep=' ') if row.get('created_at') else None,
        'updated_at': row['updated_at'].isoformat(sep=' ') if row.get('updated_at') else None,
    }


def find_user(username):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM `admin-users` WHERE username = %s LIMIT 1', (username,))
            return cur.fetchone()
    finally:
        conn.close()


def authenticate(username, password):
    row = find_user(username)
    if not row or not row['is_active'] or not verify_password(password, row['password_hash']):
        return None
    return serialize_user(row)


def create_user(username, password, permissions=None, is_admin=False):
    username = str(username or '').strip()
    if len(username) < 3 or len(username) > 64:
        raise ValueError('Username must be between 3 and 64 characters.')
    if len(password or '') < 8:
        raise ValueError('Password must contain at least 8 characters.')
    if not is_admin and not permissions:
        permissions = []
    encoded_permissions = json.dumps([ALL_PERMISSIONS]) if is_admin else json.dumps(_permissions(permissions))
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'INSERT INTO `admin-users` '
                '(username, password_hash, is_active, is_admin, permissions) '
                'VALUES (%s, %s, 1, %s, %s)',
                (username, _hash_password(password), int(bool(is_admin)), encoded_permissions),
            )
            user_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return user_id


def update_user(user_id, password=None, permissions=None, is_active=None, is_admin=None):
    assignments = []
    values = []
    if password:
        if len(password) < 8:
            raise ValueError('Password must contain at least 8 characters.')
        assignments.append('password_hash = %s')
        values.append(_hash_password(password))
    if permissions is not None:
        assignments.append('permissions = %s')
        values.append(json.dumps([ALL_PERMISSIONS]) if is_admin else json.dumps(_permissions(permissions)))
    if is_active is not None:
        assignments.append('is_active = %s')
        values.append(int(bool(is_active)))
    if is_admin is not None:
        assignments.append('is_admin = %s')
        values.append(int(bool(is_admin)))
    if not assignments:
        return
    values.append(user_id)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE `admin-users` SET {', '.join(assignments)} WHERE id = %s", values)
        conn.commit()
    finally:
        conn.close()
