"""Controller routes and model actions (see controller.py for state helpers).

Hooks and routes are registered explicitly on factory-created applications.
Only the process-shared model and controller singletons are read or reset.
"""

import json

from flask import Response, current_app, g, jsonify, request

from admin_policy import CONTROLLER_ACTION_OPERATIONS, permits
from controller import _set_state, _state
from services.lifecycle import MODEL_ACTIONS
from database import logger
from request_validation import json_object, text

TOGGLES = {
    'voice_enable': ('voice_enabled', True),
    'voice_disable': ('voice_enabled', False),
    'chat_enable': ('chat_enabled', True),
    'chat_disable': ('chat_enabled', False),
}


def _start_model_action(action):
    return current_app.extensions['model_lifecycle'].start_action(action)


def get_status():
    return {'features': dict(_state), **current_app.extensions['model_lifecycle'].status()}


def _public_features():
    return {'voice': _state['voice_enabled'], 'chat': _state['chat_enabled']}


FEATURES_JS = """(function () {
  var initial = window.__CTL_FEATURES__ || { voice: true, chat: true };
  var loadedWithVoiceOff = !initial.voice;
  var style = document.createElement('style');
  style.textContent =
    'html.ctl-voice-off #voiceButton, html.ctl-voice-off #voiceStatus,' +
    'html.ctl-voice-off #voiceReplies, html.ctl-voice-off label:has(#voiceReplies)' +
    '{display:none !important}' +
    '#ctlBanner{position:fixed;top:0;left:0;right:0;z-index:99999;padding:10px 16px;' +
    'text-align:center;background:#b45309;color:#fff;font:14px sans-serif}';
  document.head.appendChild(style);

  function apply(features) {
    document.documentElement.classList.toggle('ctl-voice-off', !features.voice);
    var banner = document.getElementById('ctlBanner');
    if (!features.chat) {
      if (!banner && document.body) {
        banner = document.createElement('div');
        banner.id = 'ctlBanner';
        banner.textContent = 'سرویس چت موقتاً غیرفعال است.';
        document.body.appendChild(banner);
      }
    } else if (banner) {
      banner.remove();
    }
    if (features.voice && loadedWithVoiceOff) location.reload();
  }

  apply(initial);
  document.addEventListener('DOMContentLoaded', function () { apply(initial); });
  setInterval(function () {
    fetch('/controller/features', { cache: 'no-store' })
      .then(function (response) { return response.json(); })
      .then(apply)
      .catch(function () {});
  }, 5000);
})();
"""


def register(ui_app, admin_app):
    register_ui(ui_app)
    register_admin(admin_app)


def register_ui(ui_app):
    if ui_app.extensions.get('controller'):
        return
    ui_app.extensions['controller'] = True

    @ui_app.before_request
    def controller_gate():
        path = request.path
        if path.startswith('/voice') and not _state['voice_enabled']:
            return jsonify(status='disabled', error='Voice chat is disabled by the administrator.'), 403
        if path == '/chat' and not _state['chat_enabled']:
            return jsonify(error='سرویس چت موقتاً غیرفعال است.'), 503
        return None

    @ui_app.after_request
    def controller_inject(response):
        if request.path != '/' or response.status_code != 200 or response.mimetype != 'text/html':
            return response
        response.direct_passthrough = False
        html = response.get_data(as_text=True)
        snippet = (
            '<script>window.__CTL_FEATURES__=' + json.dumps(_public_features()) + ';</script>'
            '<script src="/controller/features.js"></script>'
        )
        html = html.replace('</head>', snippet + '</head>', 1) if '</head>' in html else snippet + html
        response.set_data(html)
        for header in ('ETag', 'Last-Modified', 'Content-MD5'):
            response.headers.pop(header, None)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @ui_app.get('/controller/features')
    def controller_features():
        response = jsonify(_public_features())
        response.headers['Cache-Control'] = 'no-store'
        return response

    @ui_app.get('/controller/features.js')
    def controller_features_js():
        response = Response(FEATURES_JS, mimetype='application/javascript')
        response.headers['Cache-Control'] = 'no-store'
        return response


def register_admin(admin_app):
    if admin_app.extensions.get('controller'):
        return
    admin_app.extensions['controller'] = True

    @admin_app.get('/controller')
    def controller_page():
        from admin_routes.paths import ADMIN_DIR
        from flask import send_from_directory
        return send_from_directory(ADMIN_DIR, 'controller.html')

    @admin_app.get('/admin/api/controller/status')
    @logger.operation
    def controller_status():
        return jsonify(get_status())

    @admin_app.post('/admin/api/controller/action')
    @logger.operation
    def controller_action():
        data = json_object({'action': text(64, 1)}, required=('action',))
        action = data['action']
        operation = CONTROLLER_ACTION_OPERATIONS.get(action) if isinstance(action, str) else None
        if not permits(g.admin_user, operation):
            return jsonify(error='Controller action is not permitted.'), 403
        if action in TOGGLES:
            key, value = TOGGLES[action]
            _set_state(**{key: value})
            logger.logDrop(f'Controller action completed: {action}')
            return jsonify(get_status())
        if action in MODEL_ACTIONS:
            if not _start_model_action(action):
                return jsonify(error='Another action is already running for this model.'), 409
            return jsonify(get_status()), 202
        return jsonify(error='Unknown action'), 400
