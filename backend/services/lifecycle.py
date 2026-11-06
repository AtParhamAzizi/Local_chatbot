"""Process-shared model lifecycle operations without Flask or route dependencies."""

import importlib
import threading

from services import adapters


MODEL_ACTIONS = {
    'generator_load': 'load_generator',
    'generator_unload': 'unload_generator',
    'embedder_load': 'load_embedder',
    'embedder_unload': 'unload_embedder',
    'voice_load': 'load_voice',
    'voice_unload': 'unload_voice',
}


class ModelLifecycleService:
    """Select, load, unload and monitor shared models without accepting HTTP input."""

    def __init__(self, *, modules=None, release_memory=None, gpu_info=None,
                 audit=adapters.audit, thread_factory=threading.Thread):
        self.modules = {} if modules is None else dict(modules)
        self.release_memory = release_memory
        self.gpu_info = gpu_info
        self.audit = audit
        self.thread_factory = thread_factory
        self.busy = {}
        self.errors = {}
        self.lock = threading.Lock()

    def _module(self, name):
        return self.modules[name] if name in self.modules else importlib.import_module(name)

    def _free_memory(self):
        if self.release_memory is not None:
            self.release_memory()
        else:
            self._module('controller')._free_memory()

    def load_generator(self):
        self._module('generator').get_generator()

    def unload_generator(self):
        generator = self._module('generator')
        with generator._gen_lock:
            generator._gen_model = None
            generator._gen_tokenizer = None
            generator._gen_device = None
        self._free_memory()

    def load_embedder(self):
        self._module('interface').get_embedder()

    def unload_embedder(self):
        embedder = self._module('interface')
        with embedder._lock:
            embedder._model = None
            embedder._tokenizer = None
            embedder._dim = None
        self._free_memory()

    def load_voice(self):
        self._module('voice')._start_model_loading()

    def unload_voice(self):
        voice = self._module('voice')
        with voice._model_lock:
            voice._model_future = None
        self._free_memory()

    def warmup(self):
        self.load_embedder()
        self.load_generator()

    def catalog(self):
        registry = self._module('model_registry')
        return {'active_model': registry.get_active_model_id(), 'models': registry.get_model_catalog()}

    def select_model(self, model_id):
        self._module('model_registry').set_active_model(model_id)
        self._module('interface').set_embedding_model(model_id)
        self._module('generator').set_generator_model(model_id)
        return self._module('model_registry').get_active_model_id()

    def _run_action(self, action):
        target = action.split('_')[0]
        try:
            getattr(self, MODEL_ACTIONS[action])()
            with self.lock:
                self.errors.pop(target, None)
            self.audit(f'Controller action completed: {action}')
        except Exception as error:
            with self.lock:
                self.errors[target] = 'model_action_failed'
            self.audit(f'Controller action {action} failed: {type(error).__name__}', 3)
        finally:
            with self.lock:
                self.busy.pop(target, None)

    def start_action(self, action):
        if action not in MODEL_ACTIONS:
            raise ValueError('Unknown model action')
        target = action.split('_')[0]
        with self.lock:
            if target in self.busy:
                return False
            self.busy[target] = action
        try:
            self.thread_factory(target=self._run_action, args=(action,), daemon=True).start()
        except Exception:
            with self.lock:
                self.busy.pop(target, None)
            raise
        return True

    def status(self):
        generator = self._module('generator')
        embedder = self._module('interface')
        voice = self._module('voice')
        future = voice._model_future
        if voice.IMPORT_ERROR is not None:
            voice_state = 'unavailable'
        elif future is None:
            voice_state = 'unloaded'
        elif not future.done():
            voice_state = 'loading'
        else:
            voice_state = 'error' if future.exception() else 'loaded'
        with self.lock:
            busy, errors = dict(self.busy), dict(self.errors)
        return {
            'models': {
                'generator': {
                    'loaded': generator._gen_model is not None,
                    'device': generator._gen_device,
                    'model_id': generator.MODEL_ID,
                },
                'embedder': {
                    'loaded': embedder._model is not None,
                    'model_id': embedder.MODEL_ID,
                    'dim': embedder._dim,
                },
                'voice': {'state': voice_state, 'model': voice.MODEL_NAME},
            },
            'busy': busy,
            'errors': errors,
            'gpu': self.gpu_info() if self.gpu_info is not None else self._module('controller')._gpu_info(),
        }


model_lifecycle = ModelLifecycleService()
