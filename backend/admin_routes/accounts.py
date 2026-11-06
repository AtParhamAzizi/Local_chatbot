"""Administrator accounts HTTP routes."""

from flask import g, jsonify, request, session
from pymysql.err import IntegrityError

from admin_users import create_user, serialize_user, update_user
from request_validation import USER_FIELDS, USER_UPDATE_FIELDS, bounded_ids, json_object
from services.adapters import connect_database as get_conn


def admin_users_collection():
    if request.method == 'POST':
        data = json_object(USER_FIELDS, required=('username', 'password'))
        try:
            user_id = create_user(
                data['username'], data['password'], data.get('permissions'), data.get('is_admin', False)
            )
            return jsonify({'ok': True, 'id': user_id}), 201
        except ValueError:
            return jsonify({'error': 'Invalid account details.'}), 400
        except IntegrityError as error:
            if error.args and error.args[0] == 1062:
                return jsonify({'error': 'That username already exists.'}), 409
            raise
        except Exception:
            return jsonify({'error': 'Unable to create user.'}), 500
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM `admin-users` ORDER BY username')
            users = [serialize_user(row) for row in cur.fetchall()]
    finally:
        conn.close()
    return jsonify({'users': users, 'current_user_id': g.admin_user['id']})


@bounded_ids(user_id=2147483647)
def admin_user_item(user_id):
    data = json_object(USER_UPDATE_FIELDS, nonempty=True) if request.method == 'PATCH' else None
    current = g.admin_user or {}
    if request.method == 'DELETE' and user_id == current.get('id'):
        return jsonify({'error': 'You cannot delete your own account.'}), 400
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM `admin-users` WHERE id = %s', (user_id,))
            row = cur.fetchone()
            if not row:
                return jsonify({'error': 'User not found.'}), 404
            if request.method == 'DELETE':
                cur.execute('DELETE FROM `admin-users` WHERE id = %s', (user_id,))
                conn.commit()
                return jsonify({'ok': True})
            changed = update_user(
                user_id,
                password=data.get('password'),
                permissions=data.get('permissions'),
                is_active=data.get('is_active'),
                is_admin=data.get('is_admin'),
            )
            if changed and user_id == current.get('id'):
                session.clear()
    except ValueError:
        return jsonify({'error': 'Invalid account details.'}), 400
    finally:
        conn.close()
    return jsonify({'ok': True})


def register_routes(app):
    app.add_url_rule('/admin/api/admin-users', view_func=admin_users_collection, methods=['GET', 'POST'])
    app.add_url_rule('/admin/api/admin-users/<int:user_id>', view_func=admin_user_item, methods=['PATCH', 'DELETE'])
