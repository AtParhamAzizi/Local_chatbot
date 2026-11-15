"""Explicit operator-only download of a pinned optional reranker; never serving code."""

import argparse
import hashlib
import json
import os
import time
from pathlib import Path


def download_weights(output):
    """Bounded HTTP ranges for connections that stall on a large streaming GET."""
    import httpx2 as httpx
    from concurrent.futures import ThreadPoolExecutor
    from huggingface_hub import HfApi

    metadata = next(item for item in HfApi().model_info(REPOSITORY, revision=REVISION, files_metadata=True).siblings
                    if item.rfilename == 'model.safetensors')
    total, digest = metadata.lfs.size, metadata.lfs.sha256
    target = output / 'model.safetensors'
    partial = output / 'model.safetensors.partial'
    if target.is_file() and target.stat().st_size == total:
        partial = target
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > total:
        raise RuntimeError('Invalid partial snapshot size')
    def fetch(start):
        end = min(total - 1, start + 4 * 1024**2 - 1)
        url = f'https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/model.safetensors?start={start}'
        for attempt in range(4):
            try:
                response = httpx.get(url, headers={'Range': f'bytes={start}-{end}'},
                                     timeout=httpx.Timeout(60, connect=20), follow_redirects=True)
                response.raise_for_status()
                break
            except httpx.HTTPError:
                if attempt == 3:
                    raise
                time.sleep(attempt + 1)
        if (response.status_code != 206 or response.headers.get('Content-Range') != f'bytes {start}-{end}/{total}'
                or len(response.content) != end - start + 1):
            raise RuntimeError('Invalid ranged model response')
        return response.content

    with partial.open('ab') as stream, ThreadPoolExecutor(max_workers=8) as pool:
        while offset < total:
            starts = list(range(offset, min(total, offset + 32 * 1024**2), 4 * 1024**2))
            # At most eight chunks reside in memory; only complete contiguous batches are saved.
            pieces = list(pool.map(fetch, starts))
            for piece in pieces:
                stream.write(piece)
                offset += len(piece)
            stream.flush()
            print(f'Weights: {offset}/{total} bytes', flush=True)
    checksum = hashlib.sha256()
    with partial.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024**2), b''):
            checksum.update(block)
    if checksum.hexdigest() != digest:
        raise RuntimeError('Pinned model checksum mismatch')
    if partial != target:
        partial.replace(target)


REPOSITORY = 'Qwen/Qwen3-Reranker-0.6B'
REVISION = 'e61197ed45024b0ed8a2d74b80b4d909f1255473'
FILES = ('config.json', 'model.safetensors', 'tokenizer.json', 'tokenizer_config.json',
         'merges.txt', 'vocab.json', 'generation_config.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / 'model/Qwen3-Reranker-0.6B')
    parser.add_argument('--chunked', action='store_true', help='Resume weights using checksum-verified HTTP ranges')
    options = parser.parse_args()
    os.environ.setdefault('HF_HUB_DISABLE_XET', '1')
    from huggingface_hub import snapshot_download

    directory = snapshot_download(REPOSITORY, revision=REVISION, local_dir=str(options.output),
                                  allow_patterns=[name for name in FILES if not options.chunked or name != 'model.safetensors'],
                                  max_workers=2)
    if options.chunked:
        download_weights(Path(directory))
    missing = [name for name in FILES if not (Path(directory) / name).is_file()]
    if missing:
        raise RuntimeError('Incomplete reranker snapshot')
    (Path(directory) / 'download.json').write_text(
        json.dumps({'repository': REPOSITORY, 'revision': REVISION}, indent=2) + '\n', encoding='utf-8')
    print(f'Prepared {REPOSITORY} at pinned revision {REVISION}', flush=True)


if __name__ == '__main__':
    main()
