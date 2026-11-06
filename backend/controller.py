"""Persisted feature toggles and memory/GPU helpers for the runtime controller.

HTTP hooks live in controller_api; model operations live in services.lifecycle.
"""

import gc
import json
import os
import tempfile
import threading

from database import logger

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, 'controller_state.json')

DEFAULT_STATE = {'voice_enabled': True, 'chat_enabled': True}

_state = dict(DEFAULT_STATE)
_state_lock = threading.Lock()
_action_lock = threading.Lock()


def _load_state():
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as handle:
            saved = json.load(handle)
        for key in DEFAULT_STATE:
            if isinstance(saved.get(key), bool):
                _state[key] = saved[key]
    except FileNotFoundError:
        pass
    except Exception as error:
        logger.logDrop(f'Unable to read controller state: {type(error).__name__}', logger.WARNING)


def _save_state():
    descriptor, temp_path = tempfile.mkstemp(dir=BASE_DIR, suffix='.tmp')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(_state, handle)
        os.replace(temp_path, STATE_FILE)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise


def _set_state(**changes):
    with _state_lock:
        _state.update(changes)
        _save_state()


def _free_memory():
    import torch

    gc.collect()
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _gpu_info():
    try:
        import torch

        if not torch.cuda.is_available():
            return {'available': False}
        free, total = torch.cuda.mem_get_info()
        return {
            'available': True,
            'name': torch.cuda.get_device_name(0),
            'free_mb': round(free / 1024 / 1024),
            'total_mb': round(total / 1024 / 1024),
        }
    except Exception:
        return {'available': False}
