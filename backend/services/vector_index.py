"""Disposable exact vector indexes over immutable, MySQL-derived snapshots."""

import threading
from collections import OrderedDict
from typing import Protocol

import numpy as np


AUTO_INDEX_ROWS = 1000
MAX_INDEX_ROWS = 50000
MAX_CACHE_BYTES = 512 * 1024 * 1024
INDEX_VERSION = 'exact-flat-f32-v1'


def _snapshot(records):
    records = tuple(sorted((record if type(record) is tuple and (record[1] is None or type(record[1]) is bytes)
                            else (record[0], bytes(record[1]) if record[1] is not None else None)
                            for record in records), key=lambda item: item[0]))
    if len({identifier for identifier, _ in records}) != len(records):
        raise ValueError('Duplicate vector source identity')
    return records


def cosine_score(query, blob, dimension):
    if not blob or len(blob) % np.dtype(np.float32).itemsize:
        return None
    vector = np.frombuffer(blob, dtype=np.float32)
    if not dimension or vector.size != int(dimension) or vector.size != query.size:
        return None
    query_norm = float(np.linalg.norm(query))
    vector_norm = float(np.linalg.norm(vector))
    if (not np.isfinite(vector).all() or not np.isfinite(query).all()
            or not np.isfinite(query_norm + vector_norm) or query_norm == 0 or vector_norm == 0):
        return None
    return float(np.dot(query, vector) / (query_norm * vector_norm))


class RetrievalIndex(Protocol):
    """Full semantic evidence is required before common-corpus lexical fusion."""

    def scores(self, query) -> dict:
        ...


class ScalarIndex:
    backend = 'scalar'

    def __init__(self, records, dimension):
        self.records = _snapshot(records)
        self.dimension = dimension

    def scores(self, query):
        scores = {}
        for identifier, blob in self.records:
            score = cosine_score(query, blob, self.dimension)
            if score is not None:
                scores[identifier] = score
        return scores


class FlatIndex:
    """Exact exhaustive inner products, with decoding/validation cached once."""

    backend = 'flat'

    def __init__(self, records, dimension):
        self.records = _snapshot(records)
        self.dimension = dimension
        identifiers, norms = [], []
        matrix = np.empty((len(self.records), dimension), dtype=np.float32)
        for identifier, blob in self.records:
            if not blob or len(blob) != dimension * np.dtype(np.float32).itemsize:
                continue
            vector = np.frombuffer(blob, dtype=np.float32)
            norm = float(np.linalg.norm(vector))
            if not np.isfinite(vector).all() or not np.isfinite(norm) or norm == 0:
                continue
            matrix[len(identifiers)] = vector
            identifiers.append(identifier)
            norms.append(norm)
        self.identifiers = tuple(identifiers)
        self.matrix = matrix[:len(identifiers)]
        self.norms = np.asarray(norms, dtype=np.float64)
        self.matrix.flags.writeable = False
        self.norms.flags.writeable = False
        self.resident_bytes = matrix.nbytes + self.norms.nbytes + sum(len(blob or b'') for _, blob in self.records)

    def scores(self, query):
        if query.ndim != 1 or query.size != self.dimension:
            raise ValueError('Query vector disagrees with vector index')
        query_norm = float(np.linalg.norm(query))
        if not np.isfinite(query).all() or not np.isfinite(query_norm) or query_norm == 0:
            return {}
        products = np.einsum('ij,j->i', self.matrix, query, optimize=False)
        scores = products / (self.norms * query_norm)
        return dict(zip(self.identifiers, map(float, scores)))


class VectorIndexCache:
    def __init__(self, *, capacity=2, max_bytes=MAX_CACHE_BYTES):
        if type(capacity) is not int or capacity < 1 or type(max_bytes) is not int or max_bytes < 1:
            raise ValueError('Vector cache limits must be positive integers')
        self.capacity = capacity
        self.max_bytes = max_bytes
        self.entries = OrderedDict()
        self.lock = threading.Lock()
        self.build_lock = threading.Lock()

    def _get(self, namespace, records, dimension):
        with self.lock:
            index = self.entries.get(namespace)
            if index is not None and index.dimension == dimension and index.records == records:
                self.entries.move_to_end(namespace)
                return index
        return None

    def get(self, records, *, dimension, namespace, mode='auto') -> RetrievalIndex:
        if mode not in {'auto', 'scalar', 'flat'}:
            raise ValueError('Unknown vector search backend')
        records = tuple(sorted(records, key=lambda item: item[0]))
        if (mode == 'scalar' or (mode == 'auto' and len(records) < AUTO_INDEX_ROWS)
                or len(records) > MAX_INDEX_ROWS):
            return ScalarIndex(records, dimension)
        result = self._get(namespace, records, dimension)
        if result is not None:
            return result
        with self.build_lock:
            result = self._get(namespace, records, dimension)
            if result is not None:
                return result
            records = _snapshot(records)
            estimated_bytes = len(records) * (dimension * 4 + 8) + sum(len(blob or b'') for _, blob in records)
            if estimated_bytes > self.max_bytes:
                return ScalarIndex(records, dimension)
            try:
                result = FlatIndex(records, dimension)
            except MemoryError:
                return ScalarIndex(records, dimension)
            with self.lock:
                self.entries.pop(namespace, None)
                while self.entries and (len(self.entries) >= self.capacity
                                        or sum(index.resident_bytes for index in self.entries.values())
                                        + result.resident_bytes > self.max_bytes):
                    self.entries.popitem(last=False)
                self.entries[namespace] = result
            return result

    def clear(self):
        with self.build_lock, self.lock:
            self.entries.clear()


VECTOR_INDEX_CACHE = VectorIndexCache()


def semantic_scores(query, rows, *, profile, collection, cache=VECTOR_INDEX_CACHE):
    from settings import setting_value

    index = cache.get(((row['id'], row['embedding']) for row in rows), dimension=profile.dimension,
                      namespace=(INDEX_VERSION, collection, profile.identity),
                      mode=setting_value('VECTOR_SEARCH_BACKEND'))
    return index.scores(query)
