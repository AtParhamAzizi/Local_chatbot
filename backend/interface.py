

import os
import threading
from dataclasses import dataclass, field
from importlib.metadata import version
from database import logger

import numpy as np
import torch
from settings import ADMIN_PORT, UI_PORT
from request_validation import MAX_JSON_UPLOAD_BYTES as MAX_UPLOAD_BYTES
from transformers import AutoTokenizer, AutoModel
from model_registry import get_active_model_id, get_model_definition
from persian_text import NORMALIZATION_VERSION, normalize_persian
from embedding_profile import EmbeddingBatch, EmbeddingProfile, local_artifact_revision, require_unchanged_artifacts


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MODEL_ID   = get_active_model_id()
MODEL_PATH = get_model_definition(MODEL_ID)['embedding_path']
ADMIN_DIR  = os.path.join(BASE_DIR, 'admin')     # admin panel assets

ALLOWED_EXT      = {'.json'}

MIN_SCORE = 0.35                            # cosine-similarity cutoff
MAX_SEQ_LEN = 512                           # embedding input length


_tokenizer = None
_model     = None
_dim       = None
_profile   = None
_lock      = threading.RLock()

@logger.operation
def set_embedding_model(model_id):
    global MODEL_ID, MODEL_PATH, _tokenizer, _model, _dim, _profile
    definition = get_model_definition(model_id)
    with _lock:
        MODEL_ID = model_id
        MODEL_PATH = definition['embedding_path']
        _tokenizer = None
        _model = None
        _dim = None
        _profile = None
    logger.logDrop(f'Embedding selection changed to {model_id}')
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

@logger.operation
def _load_model():
    global _tokenizer, _model, _dim, _profile
    _sync_model_selection()
    if _model is not None:
        return
    with _lock:
        if _model is not None:
            return
        runtime = load_embedding_runtime(MODEL_ID, MODEL_PATH)
        _tokenizer, _model, _dim, _profile = runtime.tokenizer, runtime.model, runtime.profile.dimension, runtime.profile
        print(f'[model] ready  |  dim = {_dim}')
        logger.logDrop(f'Embedding model ready with dimension {_dim}')


@dataclass
class EmbeddingRuntime:
    model_id: str
    path: str
    tokenizer: object
    model: object
    profile: EmbeddingProfile
    lock: object = field(default_factory=threading.RLock)

    @torch.no_grad()
    def embed(self, texts):
        with self.lock:
            return EmbeddingBatch(_embed(texts, self.tokenizer, self.model), self.profile)


def load_embedding_runtime(model_id, path=None):
    """Load a candidate independently, without unloading the serving model."""
    path = os.path.abspath(path or get_model_definition(model_id)['embedding_path'])
    revision, manifest = local_artifact_revision(path)
    tokenizer = AutoTokenizer.from_pretrained(
        path, trust_remote_code=True, padding_side='left', local_files_only=True
    )
    model = AutoModel.from_pretrained(
        path, trust_remote_code=True, dtype=torch.float32, local_files_only=True
    ).to('cpu').eval()
    dimension = _model_hidden_size(model)
    require_unchanged_artifacts(manifest)
    profile = EmbeddingProfile.create(
        model_id=get_model_definition(model_id)['embedding_model_id'], revision=revision,
        tokenizer={'class': type(tokenizer).__name__, 'padding_side': tokenizer.padding_side,
                   'padding': True, 'truncation': True, 'max_length': MAX_SEQ_LEN,
                   'add_special_tokens': True, 'artifact_revision': revision},
        pooling={'method': 'last-non-padding-token-v1', 'l2_normalize': True},
        normalization_version=NORMALIZATION_VERSION, dimension=dimension,
        runtime={'implementation': 'hf-auto-float32-cpu-v1',
                 **{name: version(name) for name in ('torch', 'transformers', 'tokenizers')}},
    )
    return EmbeddingRuntime(model_id, path, tokenizer, model, profile)


def get_embedder():
    _load_model()
    return _tokenizer, _model


def embedding_dim():
    try:
        _load_model()
        return _dim
    except Exception as e:
        logger.logDrop(f'Embedding model unavailable: {type(e).__name__}', logger.FAILED)
        return None


@torch.no_grad()
@logger.operation
def embed_texts(texts):
    """Array-only convenience API; indexed work must use embed_batch."""
    return embed_batch(texts).vectors


@torch.no_grad()
@logger.operation
def embed_batch(texts):
    # Selection/unload cannot relabel an in-flight batch. The immutable profile
    # travels with these vectors even if another model is selected afterwards.
    with _lock:
        tokenizer, model = get_embedder()
        vectors = _embed(texts, tokenizer, model)
        if not isinstance(_profile, EmbeddingProfile):
            raise ValueError('Loaded embedder has no compatibility profile')
        return EmbeddingBatch(vectors, _profile)


def _embed(texts, tokenizer, model):
    batch = tokenizer(
        [normalize_persian(value) for value in texts],
        padding=True,
        truncation=True,
        max_length=MAX_SEQ_LEN,
        add_special_tokens=True,
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

