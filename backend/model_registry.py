import json
import os
import tempfile
from database import logger
from settings import MODEL_IDS, setting_value
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SELECTION_FILE = os.path.join(BASE_DIR, 'model_selection.json')
DEFAULT_MODEL_ID = MODEL_IDS[0]
QWEN_EMBEDDING_PATH = setting_value('QWEN_EMBEDDING_PATH')

MODEL_DEFINITIONS = {
    DEFAULT_MODEL_ID: {
        'label': 'Qwen3.5-2B',
        'embedding_path': QWEN_EMBEDDING_PATH,
        'embedding_model_id': 'Qwen/Qwen3-Embedding-0.6B',
        'generator_path': setting_value('QWEN35_GENERATOR_PATH'),
    },
    MODEL_IDS[1]: {
        'label': 'Qwen3-4B',
        'embedding_path': QWEN_EMBEDDING_PATH,
        'embedding_model_id': 'Qwen/Qwen3-Embedding-0.6B',
        'generator_path': setting_value('QWEN_GENERATOR_PATH'),
    },
}


def _has_config(path):
    return os.path.isfile(os.path.join(path, 'config.json'))


def _read_saved_id():
    try:
        with open(SELECTION_FILE, 'r', encoding='utf-8') as handle:
            value = json.load(handle).get('model_id')
        if value in MODEL_DEFINITIONS:
            return value
    except (OSError, ValueError, TypeError):
        pass
    return None


@logger.operation
def _write_saved_id(model_id):
    directory = os.path.dirname(SELECTION_FILE)
    fd, temp_path = tempfile.mkstemp(prefix='.model-selection-', dir=directory, text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump({'model_id': model_id}, handle, indent=2)
            handle.write('\n')
        os.replace(temp_path, SELECTION_FILE)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def model_available(model_id):
    definition = MODEL_DEFINITIONS[model_id]
    return (
        _has_config(definition['embedding_path'])
        and _has_config(definition['generator_path'])
    )


def get_active_model_id():
    requested = _read_saved_id() or setting_value('GEN_MODEL_ID')
    if requested in MODEL_DEFINITIONS and model_available(requested):
        return requested

    for model_id in MODEL_DEFINITIONS:
        if model_available(model_id):
            return model_id

    return requested if requested in MODEL_DEFINITIONS else DEFAULT_MODEL_ID


def get_model_definition(model_id=None):
    model_id = model_id or get_active_model_id()
    if model_id not in MODEL_DEFINITIONS:
        raise ValueError(f'Unknown model: {model_id}')
    return MODEL_DEFINITIONS[model_id]


def get_model_catalog():
    active_id = get_active_model_id()
    catalog = []
    for model_id, definition in MODEL_DEFINITIONS.items():
        catalog.append({
            'id': model_id,
            'label': definition['label'],
            'available': model_available(model_id),
            'active': model_id == active_id,
            'embedding_path': definition['embedding_path'],
            'generator_path': definition['generator_path'],
        })
    return catalog


@logger.operation
def set_active_model(model_id):
    if model_id not in MODEL_DEFINITIONS:
        raise ValueError(f'Unknown model: {model_id}')
    if not model_available(model_id):
        definition = MODEL_DEFINITIONS[model_id]
        missing = [
            path for path in (definition['embedding_path'], definition['generator_path'])
            if not _has_config(path)
        ]
        raise FileNotFoundError(
            f"{definition['label']} is unavailable. Missing config.json in: {', '.join(missing)}"
        )
    _write_saved_id(model_id)
    logger.logDrop(f'Active model selection saved: {model_id}')
    return model_id
