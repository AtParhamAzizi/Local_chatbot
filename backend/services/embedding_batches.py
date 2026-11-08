"""Bound model work and stage ordered vectors without retaining an upload matrix."""

from contextlib import contextmanager
from tempfile import TemporaryFile

import numpy as np


@contextmanager
def staged_embeddings(records, *, embed, batch_size, text_for, report):
    """Yield (float32 BLOB iterator, dimension) only after all batches succeed.

    The temporary file is request-local, disk-backed and removed on every exit.
    Neither the next model call nor persistence retains the previous batch array.
    ``report(completed, total)`` runs initially and after each staged batch.
    """
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
        raise ValueError('batch_size must be a positive integer')
    total = len(records)
    if not total:
        raise ValueError('At least one embedding input is required')
    with TemporaryFile(mode='w+b') as staged:
        dimension = None
        report(0, total)
        for start in range(0, total, batch_size):
            batch = records[start:start + batch_size]
            vectors = np.ascontiguousarray(embed([text_for(record) for record in batch]), dtype=np.float32)
            if vectors.ndim != 2 or vectors.shape[0] != len(batch) or vectors.shape[1] < 1:
                raise ValueError('Embedding batch shape does not match its inputs')
            if dimension is None:
                dimension = int(vectors.shape[1])
            elif vectors.shape[1] != dimension:
                raise ValueError('Embedding dimension changed between batches')
            if not np.isfinite(vectors).all():
                raise ValueError('Embedding batch contains non-finite values')
            vectors.tofile(staged)
            del vectors
            report(start + len(batch), total)
        staged.seek(0)

        def blobs():
            for _ in range(total):
                blob = staged.read(dimension * np.dtype(np.float32).itemsize)
                if len(blob) != dimension * np.dtype(np.float32).itemsize:
                    raise OSError('Incomplete staged embedding')
                yield blob

        yield blobs(), dimension
