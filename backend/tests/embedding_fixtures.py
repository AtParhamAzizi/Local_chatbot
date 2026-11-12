"""Explicit offline identities for deterministic embedding doubles."""

import numpy as np


def profile(dimension=3, **changes):
    from embedding_profile import EmbeddingProfile
    from persian_text import NORMALIZATION_VERSION
    return EmbeddingProfile.create(**{
        'model_id': 'fixture/embedder', 'revision': 'fixture-weights-v1',
        'tokenizer': {'implementation': 'fixture-v1', 'max_length': 512},
        'pooling': {'method': 'fixture-v1', 'l2_normalize': True},
        'normalization_version': NORMALIZATION_VERSION, 'dimension': dimension,
        'runtime': {'implementation': 'fixture-v1'}, **changes,
    })


def profiled(vectors):
    from embedding_profile import EmbeddingBatch
    if isinstance(vectors, EmbeddingBatch):
        return vectors
    shape = np.shape(vectors)
    dimension = int(shape[1]) if len(shape) == 2 and shape[1] > 0 else 3
    return EmbeddingBatch(vectors, profile(dimension))


def with_profile(embed):
    def batch(texts):
        return profiled(embed(texts))
    return batch
