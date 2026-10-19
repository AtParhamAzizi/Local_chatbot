

import os
import hashlib
import threading

import numpy as np
import torch
import pymysql
from transformers import AutoTokenizer, AutoModel
from model_registry import get_active_model_id, get_model_definition


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MODEL_ID   = get_active_model_id()
MODEL_PATH = get_model_definition(MODEL_ID)['embedding_path']
ADMIN_DIR  = os.path.join(BASE_DIR, 'admin')     # admin panel assets

DB_CONFIG = {
    'host':     os.getenv('DB_HOST', 'localhost'),
    'port':     int(os.getenv('DB_PORT', 3306)),
    'user':     os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASS', 'your_password'),
    'database': os.getenv('DB_NAME', 'chatbot'),
    'charset':  'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor,
    'autocommit': True,
}

ADMIN_PORT = 8000
UI_PORT    = 5000

MAX_UPLOAD_BYTES = 20 * 1024 * 1024        # 20 MB
ALLOWED_EXT      = {'.json'}

MIN_SCORE = 0.35                            # cosine-similarity cutoff
MAX_SEQ_LEN = 512                           # embedding input length


_tokenizer = None
_model     = None
_dim       = None
_lock      = threading.Lock()

def set_embedding_model(model_id):
    global MODEL_ID, MODEL_PATH, _tokenizer, _model, _dim
    definition = get_model_definition(model_id)
    with _lock:
        MODEL_ID = model_id
        MODEL_PATH = definition['embedding_path']
        _tokenizer = None
        _model = None
        _dim = None
    return MODEL_PATH


def _sync_model_selection():
    active_id = get_active_model_id()
    if active_id != MODEL_ID:
        set_embedding_model(active_id)

def _model_hidden_size(model):
    config = model.config
    candidates = [config, getattr(config, 'text_config', None)]
    for candidate in candidates:
        if candidate is None:
            continue
        hidden_size = getattr(candidate, 'hidden_size', None)
        if hidden_size is not None:
            return int(hidden_size)
    raise AttributeError(
        f'Unable to determine embedding dimension for {type(config).__name__}'
    )

def _load_model():
    global _tokenizer, _model, _dim
    _sync_model_selection()
    if _model is not None:
        return
    with _lock:
        if _model is not None:
            return
        print(f'[model] loading from {MODEL_PATH} ...')
        _tokenizer = AutoTokenizer.from_pretrained(
            MODEL_PATH, trust_remote_code=True, padding_side='left', local_files_only=True
        )
        _model = AutoModel.from_pretrained(
            MODEL_PATH, trust_remote_code=True, dtype=torch.float32, local_files_only=True
        ).to('cpu').eval()
        _dim = _model_hidden_size(_model)
        print(f'[model] ready  |  dim = {_dim}')


def get_embedder():
    _load_model()
    return _tokenizer, _model


def embedding_dim():
    try:
        _load_model()
        return _dim
    except Exception as e:
        print('[model] not available:', e)
        return None


@torch.no_grad()
def embed_texts(texts):

    tokenizer, model = get_embedder()
    batch = tokenizer(
        texts,
        padding=True,
        truncation=True,
        max_length=MAX_SEQ_LEN,
        return_tensors='pt',
    )
    out = model(**batch)
    last_hidden = out.last_hidden_state               # (N, T, D)
    mask        = batch['attention_mask']             # (N, T)

    left_padding = bool((mask[:, -1].sum() == mask.shape[0]).item())
    if left_padding:
        pooled = last_hidden[:, -1]
    else:
        seq_lens = mask.sum(dim=1) - 1
        bs       = last_hidden.shape[0]
        pooled   = last_hidden[
            torch.arange(bs, device=last_hidden.device), seq_lens
        ]

    pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
    return pooled.cpu().numpy().astype(np.float32)

