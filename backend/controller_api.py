"""Controller routes and model actions (see controller.py for state helpers).

Importing this module registers its hooks on the existing user and admin
Flask apps. generator.py, interface.py, voice.py, user.py and admin.py are
not modified; only their module-level singletons are read or reset.
"""

import json
import threading

from flask import Response, jsonify, request

import generator
import interface
import voice
from admin import admin_app
from controller import _free_memory, _gpu_info, _set_state, _state
from database import logger
from user import ui_app

_busy = {}
_errors = {}
_busy_lock = threading.Lock()


def _voice_state():
    if voice.IMPORT_ERROR is not None:
        return 'unavailable'
    future = voice._model_future
    if future is None:
        return 'unloaded'
    if not future.done():
        return 'loading'
    return 'error' if future.exception() else 'loaded'


def _generator_load():
    generator.get_generator()


def _generator_unload():
    with generator._gen_lock:
        generator._gen_model = None
        generator._gen_tokenizer = None
        generator._gen_device = None
    _free_memory()


def _embedder_load():
    interface.get_embedder()


def _embedder_unload():
    with interface._lock:
        interface._model = None
        interface._tokenizer = None
        interface._dim = None
    _free_memory()


def _voice_load():
    voice._start_model_loading()


def _voice_unload():
    with voice._model_lock:
        voice._model_future = None
    _free_memory()


MODEL_ACTIONS = {
    'generator_load': _generator_load,
    'generator_unload': _generator_unload,
    'embedder_load': _embedder_load,
    'embedder_unload': _embedder_unload,
    'voice_load': _voice_load,
    'voice_unload': _voice_unload,
}

TOGGLES = {
    'voice_enable': ('voice_enabled', True),
    'voice_disable': ('voice_enabled', False),
    'chat_enable': ('chat_enabled', True),
    'chat_disable': ('chat_enabled', False),
}


def _run_model_action(action):
    target = action.split('_')[0]
    try:
        MODEL_ACTIONS[action]()
        _errors.pop(target, None)
        logger.logDrop(f'Controller action completed: {action}')
    except Exception as error:
        _errors[target] = str(error)
        logger.logDrop(f'Controller action {action} failed: {error}', logger.FAILED)
    finally:
        with _busy_lock:
            _busy.pop(target, None)


def _start_model_action(action):
    target = action.split('_')[0]
    with _busy_lock:
        if target in _busy:
            return False
        _busy[target] = action
    threading.Thread(target=_run_model_action, args=(action,), daemon=True).start()
    return True


def get_status():
    with _busy_lock:
        busy = dict(_busy)
    return {
        'features': dict(_state),
        'models': {
            'generator': {
                'loaded': generator._gen_model is not None,
                'device': generator._gen_device,
                'model_id': generator.MODEL_ID,
            },
            'embedder': {
                'loaded': interface._model is not None,
                'model_id': interface.MODEL_ID,
                'dim': interface._dim,
            },
            'voice': {'state': _voice_state(), 'model': voice.MODEL_NAME},
        },
        'busy': busy,
        'errors': dict(_errors),
        'gpu': _gpu_info(),
    }


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


def register():
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

    @admin_app.get('/controller')
    def controller_page():
        from interface import ADMIN_DIR
        from flask import send_from_directory
        return send_from_directory(ADMIN_DIR, 'controller.html')

    @admin_app.get('/admin/api/controller/status')
    @logger.operation
    def controller_status():
        return jsonify(get_status())

    @admin_app.post('/admin/api/controller/action')
    @logger.operation
    def controller_action():
        data = request.get_json(silent=True) or {}
        action = data.get('action')
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


register()
