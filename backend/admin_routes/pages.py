"""Administrator pages HTTP routes."""

import os

from flask import g, redirect, request, send_from_directory, url_for

from admin_routes.paths import ADMIN_DIR, ADMIN_USERS_DIR
from database import logger


@logger.operation
def admin_index():
    if not g.admin_user:
        return redirect(url_for('admin_login'))
    return send_from_directory(os.path.dirname(ADMIN_DIR), 'dashboard.html')


def login_css():
    return send_from_directory(ADMIN_USERS_DIR, 'login.css')


def login_js():
    return send_from_directory(ADMIN_USERS_DIR, 'login.js')


def login_theme():
    return send_from_directory(os.path.dirname(ADMIN_DIR), 'theme.js')


def dashboard_asset():
    return send_from_directory(os.path.dirname(ADMIN_DIR), request.path.lstrip('/'))


def admin_page_asset(filename):
    return send_from_directory(ADMIN_DIR, filename)


def register_routes(app):
    app.add_url_rule('/', view_func=admin_index, methods=['GET'])
    app.add_url_rule('/login.css', view_func=login_css)
    app.add_url_rule('/login.js', view_func=login_js)
    app.add_url_rule('/login-theme.js', view_func=login_theme)
    app.add_url_rule('/admin-csrf.js', view_func=dashboard_asset)
    app.add_url_rule('/theme.js', view_func=dashboard_asset)
    app.add_url_rule('/dashboard.js', view_func=dashboard_asset)
    app.add_url_rule('/dashboard.css', view_func=dashboard_asset)
    app.add_url_rule('/admin/<path:filename>', view_func=admin_page_asset)
