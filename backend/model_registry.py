import json
import os
import tempfile
from database import logger
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SELECTION_FILE = os.path.join(BASE_DIR, 'model_selection.json')
DEFAULT_MODEL_ID = 'qwen3.5-2b'
QWEN_EMBEDDING_PATH = os.getenv(
    'QWEN_EMBEDDING_PATH',
    os.path.join(BASE_DIR, 'model', 'Qwen3-Embedding-0.6B'),
)

MODEL_DEFINITIONS = {
    DEFAULT_MODEL_ID: {
        'label': 'Qwen3.5-2B',
        'embedding_path': QWEN_EMBEDDING_PATH,
        'generator_path': os.getenv(
            'QWEN35_GENERATOR_PATH',
            os.getenv('GEN_MODEL', os.path.join(BASE_DIR, 'modelgen', 'qwen3.5-2B')),
        ),
    },
    'qwen3-4b': {
        'label': 'Qwen3-4B',
        'embedding_path': QWEN_EMBEDDING_PATH,
        'generator_path': os.getenv('QWEN_GENERATOR_PATH', os.getenv('GEN_MODEL', os.path.join(BASE_DIR, 'modelgen'))),
    },
    'gemma-4-e2b-it': {
        'label': 'Gemma 4 E2B IT',
        'embedding_path': os.getenv(
            'GEMMA_EMBEDDING_PATH',
            os.path.join(BASE_DIR, 'model', 'gemma-4-E2B-it'),
        ),
        'generator_path': os.getenv(
            'GEMMA_GENERATOR_PATH',
            os.path.join(BASE_DIR, 'modelgen', 'gemma-4-E2B-it'),
        ),
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
    requested = _read_saved_id() or os.getenv('GEN_MODEL_ID')
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
