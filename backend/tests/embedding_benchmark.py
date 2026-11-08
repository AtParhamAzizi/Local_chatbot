"""Optional real CPU embedder/PyMuPDF acceptance run; no database or downloads.

Run with the application interpreter, outside the lightweight offline suite.
The declared budget covers whole-process peak resident memory, including weights.
"""

import argparse
import ctypes
import json
import os
import socket
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch


def peak_resident_mib():
    if os.name == 'nt':
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    'peak_working_set', 'working_set', 'peak_paged', 'paged',
                    'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile',
                )
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return counters.peak_working_set / 1024**2
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024**2 if sys.platform == 'darwin' else 1024)


def reject_external_work(*args, **kwargs):
    raise AssertionError('This benchmark must not access a database or the network')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--memory-budget-mib', type=int, default=6144)
    parser.add_argument('--batch-size', type=int, default=8)
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--long-json-count', type=int, default=8,
                        help='Number of 512-token JSON questions; use 256 for a sustained full-length stress run')
    options = parser.parse_args()
    if options.memory_budget_mib < 1 or options.threads < 1 or not 1 <= options.batch_size <= 64:
        parser.error('Use a positive memory budget/thread count and batch size 1-64')
    if not 1 <= options.long_json_count <= 256:
        parser.error('--long-json-count must be 1-256')
    os.environ.update(APP_ENV='test', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    import fitz
    import numpy as np
    import pymysql
    import torch

    from database import logger
    from interface import embed_texts
    from services.ingestion import IngestionService
    from test_embedding_batches import RecordingDatabase

    torch.set_num_threads(options.threads)
    reports = []
    with ExitStack() as guards:
        guards.enter_context(patch.object(pymysql, 'connect', reject_external_work))
        guards.enter_context(patch.object(socket.socket, 'connect', reject_external_work))
        guards.enter_context(patch.object(logger, '_write', return_value=True))
        for source in ('json', 'pdf'):
            database = RecordingDatabase()
            calls, progress = [], []

            def embed(texts):
                calls.append(len(texts))
                return embed_texts(texts)

            def report(event):
                progress.append(event)
                if event['completed'] % 64 == 0:
                    print(f"{source}: embedded {event['completed']}/{event['total']}", flush=True)

            service = IngestionService(
                connect=database.connect, embed=embed, batch_size=options.batch_size,
                audit=lambda *args, **kwargs: None, progress=report,
            )
            started = time.monotonic()
            if source == 'json':
                records = [{'question': f'{index:03d} ' + (
                    ('support ' * 600)[:4090] if index < options.long_json_count else 'How can I contact support?'
                ), 'answer': 'answer'}
                           for index in range(256)]
                result = service.ingest_qa(json.dumps(records).encode())
                stored = [row for sql, row in database.writes if 'INSERT INTO qa_documents' in sql]
                texts = [row[0] for row in stored]
                blobs = [row[3] for row in stored]
                assert result['embedded'] == 256
                assert texts == [row['question'] for row in records]
            else:
                with fitz.open() as document:
                    for _ in range(128):
                        page = document.new_page()
                        assert page.insert_textbox(
                            fitz.Rect(30, 30, 560, 810), 'documentation ' * 115, fontsize=8,
                        ) >= 0
                    raw = document.tobytes()
                result = service.ingest_pdf(raw, 'microbatch-benchmark.pdf')
                stored = [row for sql, row in database.writes if 'INSERT INTO pdf_chunks' in sql]
                texts = [row[4] for row in stored]
                blobs = [row[5] for row in stored]
                assert result['embedded_chunks'] == 256
                assert [row[1:4] for row in stored] == [(index, index // 2 + 1, index // 2 + 1) for index in range(256)]
            assert len(blobs) == sum(calls) == progress[-1]['completed'] == progress[-1]['total'] == 256
            assert all(0 < count <= options.batch_size for count in calls)
            assert len(calls) == (256 + options.batch_size - 1) // options.batch_size
            for index in (0, min(options.batch_size, 255), 255):
                expected = embed_texts([texts[index]])[0]
                np.testing.assert_allclose(np.frombuffer(blobs[index], dtype=np.float32), expected, atol=2e-5, rtol=2e-4)
            peak = peak_resident_mib()
            assert peak <= options.memory_budget_mib, f'Peak {peak:.1f} MiB exceeded declared budget'
            reports.append({
                'source': source, 'vectors': len(blobs), 'batches': len(calls),
                'dimension': len(blobs[0]) // 4, 'seconds': round(time.monotonic() - started, 2),
                'peak_resident_mib': round(peak, 2), 'budget_mib': options.memory_budget_mib,
            })
    print(json.dumps({
        'batch_size': options.batch_size, 'threads': options.threads,
        'long_json_count': options.long_json_count, 'fixtures': reports,
    }, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
