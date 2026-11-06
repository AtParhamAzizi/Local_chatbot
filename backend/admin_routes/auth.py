"""Administrator auth HTTP routes."""

import os
from pathlib import Path

from flask import g, jsonify, redirect, render_template_string, request, session, url_for

from abuse_controls import limit_requests, retry_message
from admin_csrf import csrf_token, valid_csrf_token
from admin_policy import OPERATION_POLICIES, PUBLIC, allowed_pages, permits, request_operation
from admin_routes.paths import ADMIN_USERS_DIR
from admin_sessions import create_admin_session, load_admin_session, revoke_admin_session
from admin_users import authenticate
from request_validation import form_object, text
from settings import is_development


def require_admin_session():
    g.admin_user = None
    if not is_development() and not request.is_secure:
        return jsonify({'error': 'HTTPS is required for administrator access.'}), 400
    operation = request_operation(request.endpoint, request.method, request.path, request.view_args)
    if operation == 'login.assets':
        return None
    current = load_admin_session(session.get('admin_session'))
    if not current and ('admin_session' in session or 'admin_user' in session):
        session.clear()
    g.admin_user = current
    if not current and OPERATION_POLICIES.get(operation) != PUBLIC:
        if request.path.startswith('/admin/api/') or request.endpoint == 'admin_logout':
            return jsonify({'error': 'Authentication required.'}), 401
        return redirect(url_for('admin_login'))
    if request.routing_exception is not None:
        return None
    if request.method not in {'GET', 'HEAD', 'OPTIONS'} and not valid_csrf_token():
        return jsonify({'error': 'Invalid or missing CSRF token. Reload the page and try again.'}), 403
    if not permits(current, operation):
        return jsonify({'error': 'You do not have access to this operation.'}), 403
    return None


def protect_admin_response(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


def login_throttled(retry_after):
    return render_template_string(
        Path(os.path.join(ADMIN_USERS_DIR, 'login.html')).read_text(encoding='utf-8'),
        error=retry_message(retry_after),
        csrf_token=csrf_token(),
    )


@limit_requests('login', methods=('POST',), on_limit=login_throttled)
def admin_login():
    if request.method == 'POST':
        data = form_object({
            'username': text(64, 1), 'password': text(1024, 1, strip=False), 'csrf_token': text(256, 1),
        }, required=('username', 'password'))
        username = data['username']
        password = data['password']
        user = authenticate(username, password)
        if user:
            token = create_admin_session(user)
            revoke_admin_session(session.get('admin_session'))
            session.clear()
            session.permanent = True
            session['admin_session'] = token
            return redirect(url_for('admin_index'))
        return render_template_string(
            Path(os.path.join(ADMIN_USERS_DIR, 'login.html')).read_text(encoding='utf-8'),
            error='Invalid username or password.',
            csrf_token=csrf_token(),
        ), 401
    if g.admin_user:
        return redirect(url_for('admin_index'))
    return render_template_string(
        Path(os.path.join(ADMIN_USERS_DIR, 'login.html')).read_text(encoding='utf-8'),
        error='',
        csrf_token=csrf_token(),
    )


def admin_logout():
    revoke_admin_session(session.get('admin_session'))
    session.clear()
    return redirect(url_for('admin_login'))


def admin_session():
    return jsonify({'user': g.admin_user, 'csrf_token': csrf_token(), 'allowed_pages': allowed_pages(g.admin_user)})


def register_routes(app):
    app.before_request(require_admin_session)
    app.after_request(protect_admin_response)
    app.add_url_rule('/login', view_func=admin_login, methods=['GET', 'POST'])
    app.add_url_rule('/logout', view_func=admin_logout, methods=['POST'])
    app.add_url_rule('/admin/api/session', view_func=admin_session)
