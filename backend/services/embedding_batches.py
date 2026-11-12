"""Bound model work and stage ordered vectors without retaining an upload matrix."""

from contextlib import contextmanager
from tempfile import TemporaryFile

import numpy as np
from embedding_profile import checked_batch
from persian_text import NORMALIZATION_VERSION


@contextmanager
def staged_embeddings(records, *, embed, batch_size, text_for, report):
    """Yield (float32 BLOB iterator, profile) only after all batches succeed.

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
        profile = None
        report(0, total)
        for start in range(0, total, batch_size):
            batch = records[start:start + batch_size]
            vectors, current = checked_batch(embed([text_for(record) for record in batch]), rows=len(batch))
            if current.normalization_version != NORMALIZATION_VERSION:
                raise ValueError('Embedding profile disagrees with ingestion normalization')
            if profile is None:
                profile = current
            elif current != profile:
                raise ValueError('Embedding profile changed between batches')
            vectors.tofile(staged)
            del vectors
            report(start + len(batch), total)
        staged.seek(0)

        def blobs():
            for _ in range(total):
                blob = staged.read(profile.dimension * np.dtype(np.float32).itemsize)
                if len(blob) != profile.dimension * np.dtype(np.float32).itemsize:
                    raise OSError('Incomplete staged embedding')
                yield blob

        yield blobs(), profile
