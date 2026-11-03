"""Explicit operation policies for every administrator entry point."""

PUBLIC = 'public'
AUTHENTICATED = 'authenticated'
ADMINISTRATOR = 'administrator'

OPERATION_POLICIES = {
    'login.view': PUBLIC,
    'login.submit': PUBLIC,
    'login.assets': PUBLIC,
    'dashboard.view': AUTHENTICATED,
    'dashboard.assets': AUTHENTICATED,
    'session.read': AUTHENTICATED,
    'session.logout': AUTHENTICATED,
    'users.view': ADMINISTRATOR,
    'users.list': ADMINISTRATOR,
    'users.create': ADMINISTRATOR,
    'users.update': ADMINISTRATOR,
    'users.delete': ADMINISTRATOR,
    'qa.view': 'json_upload',
    'qa.stats': 'json_upload',
    'qa.list': 'json_upload',
    'qa.upload': 'json_upload',
    'qa.delete': 'json_upload',
    'pdf.view': 'pdf_upload',
    'pdf.stats': 'pdf_upload',
    'pdf.list': 'pdf_upload',
    'pdf.upload': 'pdf_upload',
    'pdf.delete': 'pdf_upload',
    'models.view': 'models',
    'models.list': 'models',
    'models.select': 'models',
    'chats.view': 'chats',
    'chats.sessions': 'chats',
    'chats.messages': 'chats',
    'chats.stats': 'chats',
    'chats.delete_session': 'chats',
    'chats.delete_message': 'chats',
    'logs.view': 'system_reports',
    'logs.list': 'system_reports',
    'controller.view': 'controller',
    'controller.status': 'controller',
    'controller.dispatch': 'controller',
    'controller.voice_enable': 'controller',
    'controller.voice_disable': 'controller',
    'controller.chat_enable': 'controller',
    'controller.chat_disable': 'controller',
    'controller.generator_load': 'controller',
    'controller.generator_unload': 'controller',
    'controller.embedder_load': 'controller',
    'controller.embedder_unload': 'controller',
    'controller.voice_load': 'controller',
    'controller.voice_unload': 'controller',
}


def _read(operation):
    return dict.fromkeys(('GET', 'HEAD', 'OPTIONS'), operation)


def _mutation(operation, method='POST'):
    return dict.fromkeys((method, 'OPTIONS'), operation)


ENDPOINT_OPERATIONS = {
    'admin_login': {**_read('login.view'), 'POST': 'login.submit'},
    'login_css': _read('login.assets'),
    'login_js': _read('login.assets'),
    'admin_index': _read('dashboard.view'),
    'dashboard_asset': _read('dashboard.assets'),
    'admin_session': _read('session.read'),
    'admin_logout': _mutation('session.logout'),
    'admin_users_collection': {**_read('users.list'), 'POST': 'users.create'},
    'admin_user_item': {**_mutation('users.update', 'PATCH'), 'DELETE': 'users.delete'},
    'admin_stats': _read('qa.stats'),
    'admin_documents': _read('qa.list'),
    'admin_upload': _mutation('qa.upload'),
    'admin_delete': _mutation('qa.delete', 'DELETE'),
    'admin_pdf_stats': _read('pdf.stats'),
    'admin_pdf_documents': _read('pdf.list'),
    'admin_pdf_upload': _mutation('pdf.upload'),
    'admin_pdf_delete': _mutation('pdf.delete', 'DELETE'),
    'admin_models': _read('models.list'),
    'admin_set_model': _mutation('models.select'),
    'admin_chat_sessions': _read('chats.sessions'),
    'admin_chat_messages': _read('chats.messages'),
    'admin_chat_stats': _read('chats.stats'),
    'admin_chat_delete_session': _mutation('chats.delete_session', 'DELETE'),
    'admin_chat_delete_message': _mutation('chats.delete_message', 'DELETE'),
    'get_admin_logs': _read('logs.list'),
    'controller_page': _read('controller.view'),
    'controller_status': _read('controller.status'),
    'controller_action': _mutation('controller.dispatch'),
}

PAGE_OPERATIONS = {
    'admin.html': 'qa.view',
    'admin_pdf.html': 'pdf.view',
    'adminmodel.html': 'models.view',
    'adminchat.html': 'chats.view',
    'adminlogger.html': 'logs.view',
    'controller.html': 'controller.view',
    'admin-user-control.html': 'users.view',
}

ASSET_OPERATIONS = {
    filename.removesuffix('.html') + extension: operation
    for filename, operation in PAGE_OPERATIONS.items()
    for extension in ('.html', '.css', '.js')
}

DASHBOARD_ASSETS = frozenset({'/dashboard.css', '/dashboard.js', '/theme.js', '/admin-csrf.js'})

CONTROLLER_ACTION_OPERATIONS = {
    'voice_enable': 'controller.voice_enable',
    'voice_disable': 'controller.voice_disable',
    'chat_enable': 'controller.chat_enable',
    'chat_disable': 'controller.chat_disable',
    'generator_load': 'controller.generator_load',
    'generator_unload': 'controller.generator_unload',
    'embedder_load': 'controller.embedder_load',
    'embedder_unload': 'controller.embedder_unload',
    'voice_load': 'controller.voice_load',
    'voice_unload': 'controller.voice_unload',
}


def request_operation(endpoint, method, path, view_args):
    if endpoint == 'admin_page_asset':
        if method not in {'GET', 'HEAD', 'OPTIONS'}:
            return None
        return ASSET_OPERATIONS.get((view_args or {}).get('filename'))
    if endpoint == 'dashboard_asset' and path not in DASHBOARD_ASSETS:
        return None
    return ENDPOINT_OPERATIONS.get(endpoint, {}).get(method)


def permits(user, operation):
    policy = OPERATION_POLICIES.get(operation)
    if policy is None:
        return False
    if policy == PUBLIC:
        return True
    if not user or user.get('is_active') is not True:
        return False
    if policy == AUTHENTICATED or user.get('is_admin') is True:
        return True
    if policy == ADMINISTRATOR:
        return False
    permissions = user.get('permissions')
    if permissions == '*':
        return True
    return isinstance(permissions, (list, tuple, set, frozenset)) and (
        policy in permissions or '*' in permissions
    )


def allowed_pages(user):
    return [f'admin/{filename}' for filename, operation in PAGE_OPERATIONS.items() if permits(user, operation)]
