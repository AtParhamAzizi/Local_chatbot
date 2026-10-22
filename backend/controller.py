"""Runtime controller for the admin panel.

Adds a small control page (backend/admin/controller.*) that can:
  * enable/disable the voice chat button on the user frontend
  * enable/disable chat requests
  * load/unload the generator and embedding models
  * load/unload the Whisper speech model

It only reads and reassigns module-level singletons in generator.py,
interface.py and voice.py; those modules are not modified.
"""

import gc
import json
import os
import tempfile
import threading

import torch

import generator
import interface
import voice
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
        logger.logDrop(f'Unable to read controller state: {error}', logger.WARNING)


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
    gc.collect()
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _gpu_info():
    try:
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


_load_state()
