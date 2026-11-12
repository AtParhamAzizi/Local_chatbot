"""Immutable embedding identities and batches; unknown identities fail closed."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


@dataclass(frozen=True)
class EmbeddingProfile:
    model_id: str
    revision: str
    tokenizer_config: str
    pooling_config: str
    normalization_version: str
    dimension: int
    runtime: str

    def __post_init__(self):
        for value in (self.model_id, self.revision, self.normalization_version):
            if not isinstance(value, str) or not value.strip():
                raise ValueError('Embedding identity fields must be nonempty strings')
        if type(self.dimension) is not int or self.dimension < 1:
            raise ValueError('Embedding dimension must be a positive integer')
        for name in ('tokenizer_config', 'pooling_config', 'runtime'):
            value = json.loads(getattr(self, name))
            if not isinstance(value, dict) or not value:
                raise ValueError('Embedding configuration must be a nonempty object')
            object.__setattr__(self, name, _json(value))

    @classmethod
    def create(cls, *, model_id, revision, tokenizer, pooling, normalization_version, dimension, runtime):
        return cls(model_id, revision, _json(tokenizer), _json(pooling), normalization_version,
                   dimension, _json(runtime))

    def to_json(self):
        return _json({
            'version': 1, 'model_id': self.model_id, 'revision': self.revision,
            'tokenizer': json.loads(self.tokenizer_config), 'pooling': json.loads(self.pooling_config),
            'normalization_version': self.normalization_version, 'dimension': self.dimension,
            'runtime': json.loads(self.runtime), 'storage_dtype': 'float32',
        })

    @property
    def identity(self):
        return hashlib.sha256(self.to_json().encode('utf-8')).hexdigest()

    @classmethod
    def from_json(cls, raw):
        value = json.loads(raw)
        fields = {'version', 'model_id', 'revision', 'tokenizer', 'pooling', 'normalization_version',
                  'dimension', 'runtime', 'storage_dtype'}
        if not isinstance(value, dict) or set(value) != fields or type(value['version']) is not int or value['version'] != 1:
            raise ValueError('Unknown embedding profile format')
        if value['storage_dtype'] != 'float32':
            raise ValueError('Unsupported embedding storage type')
        return cls.create(model_id=value['model_id'], revision=value['revision'], tokenizer=value['tokenizer'],
                          pooling=value['pooling'], normalization_version=value['normalization_version'],
                          dimension=value['dimension'], runtime=value['runtime'])


def compatible_profile(raw, expected):
    """NULL, malformed and future/unknown profiles are never assumed compatible."""
    if not isinstance(expected, EmbeddingProfile):
        raise ValueError('A query embedding profile is required')
    try:
        return EmbeddingProfile.from_json(raw) == expected
    except (TypeError, ValueError, UnicodeError):
        return False


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: object
    profile: EmbeddingProfile


def checked_batch(batch, *, rows):
    """Validate before staging/querying; plain arrays have no provable identity."""
    if not isinstance(batch, EmbeddingBatch) or not isinstance(batch.profile, EmbeddingProfile):
        raise ValueError('Embedding adapter must return a profiled batch')
    vectors = np.ascontiguousarray(batch.vectors, dtype=np.float32)
    if vectors.ndim != 2 or vectors.shape != (rows, batch.profile.dimension):
        raise ValueError('Embedding batch shape disagrees with its inputs/profile')
    if not np.isfinite(vectors).all() or np.any(np.linalg.norm(vectors, axis=1) == 0):
        raise ValueError('Embedding batch contains non-finite or zero vectors')
    return vectors, batch.profile


def _artifact_files(root):
    suffixes = {'.json', '.safetensors', '.bin', '.pt', '.pth', '.model', '.txt', '.tiktoken',
                '.py', '.vocab', '.merges', '.jinja', '.jinja2'}
    return sorted((path for path in root.rglob('*') if path.is_file() and path.suffix in suffixes
                   and not any(part.startswith('.') or part == '__pycache__' for part in path.relative_to(root).parts)),
                  key=lambda path: path.relative_to(root).as_posix())


def local_artifact_revision(directory):
    """Content identity of local weights, configs, tokenizers and custom loader code.

    No network/repository label or absolute installation path substitutes for bytes.
    Artifacts must remain immutable while loaded; reload after replacing them.
    """
    root = Path(directory)
    files = _artifact_files(root)
    if not (root / 'config.json').is_file() or not any(path.suffix in {'.safetensors', '.bin', '.pt', '.pth'} for path in files):
        raise ValueError('Embedding identity requires local configuration and weights')
    digest = hashlib.sha256()
    manifest = []
    for path in files:
        before = path.stat()
        with path.open('rb') as stream:
            content = hashlib.file_digest(stream, 'sha256').digest()
        after = path.stat()
        signature = (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != signature:
            raise ValueError('Embedding artifact changed while computing its identity')
        digest.update(path.relative_to(root).as_posix().encode('utf-8') + b'\0' + content)
        manifest.append((path, signature))
    manifest = root, tuple(manifest)
    require_unchanged_artifacts(manifest)
    return 'sha256:' + digest.hexdigest(), manifest


def require_unchanged_artifacts(manifest):
    root, entries = manifest
    if _artifact_files(root) != [path for path, _ in entries]:
        raise ValueError('Embedding artifact files changed during loading')
    for path, expected in entries:
        current = path.stat()
        if (current.st_size, current.st_mtime_ns, current.st_ctime_ns) != expected:
            raise ValueError('Embedding artifacts changed during loading; retry with immutable files')
