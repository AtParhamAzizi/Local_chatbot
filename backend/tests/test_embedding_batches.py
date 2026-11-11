"""Ordered ingestion batches, pre-write failure boundaries and a 32 MiB fixture budget."""

import hashlib
import importlib
import json
import sys
import tracemalloc
import unittest
import weakref
from contextlib import ExitStack
from unittest.mock import Mock, patch

import numpy as np

from core_support import BACKEND_DIR


class RecordingDatabase:
    """Keep actual persisted BLOBs, without mock call histories retaining model arrays."""

    def __init__(self, existing=None):
        self.existing = existing
        self.writes = []
        self.lastrowid = 17
        self.connections = self.closes = 0

    def connect(self):
        self.connections += 1
        return self

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, parameters):
        if not sql.strip().startswith('SELECT'):
            self.writes.append((sql, parameters))

    def fetchone(self):
        return self.existing

    def fetchall(self):
        return []

    def close(self):
        self.closes += 1

    def commit(self):
        pass

    def begin(self):
        pass

    def rollback(self):
        pass


class EmbeddingBatchTests(unittest.TestCase):
    def setUp(self):
        self.resources = ExitStack()
        self.addCleanup(self.resources.close)
        self.resources.enter_context(patch.object(sys, 'path', [str(BACKEND_DIR), *sys.path]))
        self.resources.enter_context(patch.dict('os.environ', {'APP_ENV': 'test'}, clear=True))
        self.ingestion = importlib.import_module('services.ingestion')
        self.batches = importlib.import_module('services.embedding_batches')
        self.database = RecordingDatabase()
        self.calls, self.events, self.files = [], [], []
        self.audit = Mock()
        real_temporary_file = self.batches.TemporaryFile

        def temporary_file(**options):
            file = real_temporary_file(**options)
            self.files.append(file)
            return file

        self.resources.enter_context(patch.object(self.batches, 'TemporaryFile', temporary_file))

    def embed(self, texts):
        self.calls.append(texts)
        return np.array([[int(text.split()[0]), 0.5] for text in texts], dtype=np.float32)

    def service(self, **options):
        return self.ingestion.IngestionService(**{
            'connect': self.database.connect, 'embed': self.embed,
            'hash_question': lambda text: hashlib.sha256(text.encode()).hexdigest(),
            'hash_document': lambda raw: hashlib.sha256(raw).hexdigest(),
            'audit': self.audit, 'progress': self.events.append, **options,
        })

    def raw_json(self, count):
        return json.dumps([{'question': f'{i} question', 'answer': f'answer {i}'} for i in range(count)]).encode()

    def pdf(self, pages):
        document = Mock(page_count=len(pages))
        document.load_page.side_effect = [Mock(get_text=Mock(return_value=text)) for text in pages]
        return Mock(return_value=document)

    def persisted(self, kind):
        return [parameters for sql, parameters in self.database.writes if f'INSERT INTO {kind}' in sql]

    def assert_progress(self, source, counts, total):
        self.assertEqual(self.events, [
            {'stage': 'embedding', 'source': source, 'completed': count, 'total': total} for count in counts
        ])
        for count in counts:
            self.assertIn(
                f'{source} embedding progress: completed={count}, total={total}',
                [call.args[0] for call in self.audit.call_args_list],
            )

    def test_json_uneven_batches_preserve_order_after_deduplication(self):
        rows = json.loads(self.raw_json(11))
        rows.insert(3, rows[1])
        rows.append(rows[0])
        result = self.service(batch_size=4).ingest_qa(json.dumps(rows).encode())
        self.assertEqual([len(batch) for batch in self.calls], [4, 4, 3])
        stored = self.persisted('qa_documents')
        self.assertEqual((result['received'], result['embedded'], len(stored)), (13, 11, 11))
        for index, row in enumerate(stored):
            self.assertEqual(row[:2], (f'{index} question', f'answer {index}'))
            np.testing.assert_array_equal(np.frombuffer(row[3], dtype=np.float32), [index, 0.5])
        self.assert_progress('json', [0, 4, 8, 11], 11)
        self.assertTrue(all(file.closed for file in self.files))

    def test_pdf_uneven_batches_preserve_chunk_order_and_pages(self):
        service = self.service(
            batch_size=3, open_pdf=self.pdf(['0 first|1 second|2 third|3 fourth', '4 fifth|5 sixth|6 seventh']),
            splitter=lambda text, **options: text.split('|'),
        )
        result = service.ingest_pdf(b'pdf', 'test.pdf')
        self.assertEqual([len(batch) for batch in self.calls], [3, 3, 1])
        stored = self.persisted('pdf_chunks')
        self.assertEqual((result['embedded_chunks'], len(stored)), (7, 7))
        for index, chunk in enumerate(stored):
            page = 1 if index < 4 else 2
            self.assertEqual(chunk[1:4], (index, page, page))
            self.assertEqual(int(chunk[4].split()[0]), index)
            np.testing.assert_array_equal(np.frombuffer(chunk[5], dtype=np.float32), [index, 0.5])
        self.assert_progress('pdf', [0, 3, 6, 7], 7)
        self.assertTrue(all(file.closed for file in self.files))

    def test_environment_configures_actual_adapter_calls_and_rejects_bad_sizes(self):
        self.assertEqual(self.service().batch_size, 8)
        with patch.dict('os.environ', {'EMBEDDING_BATCH_SIZE': '3'}):
            self.service().ingest_qa(self.raw_json(8))
        self.assertEqual([len(batch) for batch in self.calls], [3, 3, 2])
        for value in ('0', '65', 'bad'):
            with self.subTest(value=value), patch.dict('os.environ', {'EMBEDDING_BATCH_SIZE': value}):
                with self.assertRaises(importlib.import_module('settings').ConfigurationError):
                    self.service()
        for value in (True, 0, 65, 1.5):
            with self.subTest(override=value), self.assertRaises(ValueError):
                self.service(batch_size=value)

    def test_single_item_and_maximum_configured_batches_still_preserve_order(self):
        for size, count, expected in ((1, 3, [1, 1, 1]), (64, 129, [64, 64, 1])):
            with self.subTest(size=size):
                self.database = RecordingDatabase()
                self.calls.clear()
                result = self.service(batch_size=size).ingest_qa(self.raw_json(count))
                self.assertEqual([len(batch) for batch in self.calls], expected)
                self.assertEqual(result['embedded'], count)
                stored = self.persisted('qa_documents')
                self.assertEqual(
                    [np.frombuffer(row[3], dtype=np.float32)[0] for row in stored], list(range(count)),
                )

    def test_later_batch_failure_reports_partial_work_without_writing_or_replacing(self):
        for source in ('json', 'pdf'):
            with self.subTest(source=source):
                self.database = RecordingDatabase({'id': 4, 'filename': 'old.pdf', 'status': 'completed'})
                self.events.clear()
                embed = Mock(side_effect=[np.ones((2, 3), dtype=np.float32), RuntimeError('private path')])
                service = self.service(batch_size=2, embed=embed, open_pdf=self.pdf(['0 a', '1 b', '2 c']))
                with self.assertRaises(self.ingestion.IngestionError) as caught:
                    if source == 'json':
                        service.ingest_qa(self.raw_json(3), replace=True)
                    else:
                        service.ingest_pdf(b'pdf', 'new.pdf', replace=True)
                self.assertEqual(caught.exception.status, 500)
                self.assertNotIn('private', str(caught.exception.payload))
                self.assert_progress(source, [0, 2], 3)
                self.assertEqual(self.database.writes, [])
                self.assertEqual(self.database.connections, 0 if source == 'json' else 1)
                self.assertTrue(all(file.closed for file in self.files))

    def test_malformed_later_batch_never_silently_truncates_or_mixes_dimensions(self):
        for bad in (None, np.ones(2), np.ones((1, 3)), np.ones((3, 3)),
                    np.ones((2, 0)), np.ones((2, 4)), np.full((2, 3), np.nan)):
            with self.subTest(shape=np.shape(bad)):
                self.events.clear()
                service = self.service(batch_size=2, embed=Mock(side_effect=[np.ones((2, 3)), bad]))
                with self.assertRaises(self.ingestion.IngestionError):
                    service.ingest_qa(self.raw_json(4))
                self.assertEqual([event['completed'] for event in self.events], [0, 2])
                self.assertEqual(self.database.connections, 0)
                self.assertTrue(all(file.closed for file in self.files))

    def test_previous_array_is_released_before_next_model_call(self):
        previous = []

        def embed(texts):
            self.assertTrue(all(reference() is None for reference in previous))
            activation = np.ones((len(texts), 512, 16), dtype=np.float32)
            previous.append(weakref.ref(activation))
            return activation[:, -1, :]

        self.service(batch_size=2, embed=embed).ingest_qa(self.raw_json(5))
        self.assertTrue(all(reference() is None for reference in previous))

    def test_staging_closes_on_connection_and_persistence_failures(self):
        for failure in ('connect', 'write'):
            with self.subTest(failure=failure):
                if failure == 'connect':
                    connect = Mock(side_effect=RuntimeError('database offline'))
                else:
                    connect = self.database.connect
                with patch.object(self.database, 'execute', side_effect=RuntimeError('write failed')):
                    with self.assertRaises(RuntimeError):
                        self.service(connect=connect).ingest_qa(self.raw_json(2))
                self.assertTrue(all(file.closed for file in self.files))

    def test_unavailable_temporary_storage_precedes_model_and_database_work(self):
        embed = Mock()
        with patch.object(self.batches, 'TemporaryFile', side_effect=OSError('private temporary path')):
            with self.assertRaises(self.ingestion.IngestionError) as caught:
                self.service(embed=embed).ingest_qa(self.raw_json(2))
        self.assertEqual(caught.exception.status, 500)
        self.assertNotIn('private', str(caught.exception.payload))
        embed.assert_not_called()
        self.assertEqual(self.database.connections, 0)

    def test_job_progress_reports_all_stages_and_page_counts(self):
        events = []
        self.service(batch_size=1, open_pdf=self.pdf(['0 first', '1 second'])).ingest_pdf(
            b'pdf', 'test.pdf', progress=events.append,
        )
        stages = [event['stage'] for event in events]
        self.assertEqual(list(dict.fromkeys(stages)), ['extraction', 'chunking', 'embedding', 'publishing'])
        for stage in ('extraction', 'chunking', 'embedding', 'publishing'):
            counts = [event['completed'] for event in events if event['stage'] == stage]
            self.assertEqual((counts[0], counts[-1]), (0, 2))
            self.assertEqual(counts, sorted(counts))
            self.assertTrue(all(event['total'] == 2 for event in events if event['stage'] == stage))

    def test_cancel_during_pdf_extraction_or_chunking_stops_at_next_page_and_closes_pdf(self):
        for stage in ('extraction', 'chunking'):
            with self.subTest(stage=stage):
                stopped = False
                open_pdf = self.pdf(['0 first', '1 second', '2 third'])
                splitter = Mock(side_effect=lambda text, **options: [text])
                embed = Mock(side_effect=AssertionError('Canceled job must not embed'))

                def report(event):
                    nonlocal stopped
                    if event['stage'] == stage and event['completed'] == 1:
                        stopped = True

                def guard():
                    if stopped:
                        raise RuntimeError('Canceled')

                with self.assertRaises(Exception):
                    self.service(open_pdf=open_pdf, splitter=splitter, embed=embed).ingest_pdf(
                        b'pdf', 'cancel.pdf', progress=report, publication_guard=guard,
                    )
                open_pdf.return_value.close.assert_called_once()
                if stage == 'extraction':
                    self.assertEqual(open_pdf.return_value.load_page.call_count, 1)
                else:
                    self.assertEqual(splitter.call_count, 1)
                embed.assert_not_called()

    def test_maximum_upload_fixtures_fit_32_mib_additional_allocation_budget(self):
        budget = 32 * 1024 * 1024
        for source in ('json', 'pdf'):
            with self.subTest(source=source):
                self.database = RecordingDatabase()
                self.events.clear()
                sizes, completed = [], 0

                def embed(texts):
                    nonlocal completed
                    sizes.append(len(texts))
                    activation = np.ones((len(texts), 512, 1024), dtype=np.float32)
                    output = activation[:, -1, :].copy()
                    output[:, 0] = np.arange(completed, completed + len(texts))
                    completed += len(texts)
                    return output

                raw = json.dumps([
                    {'question': f'{index:03d} ' + 'q' * 4092, 'answer': 'a' * 16000} for index in range(256)
                ]).encode()
                service = self.service(embed=embed, open_pdf=self.pdf(['x' * 1500] * 128))
                tracemalloc.start()
                try:
                    result = service.ingest_qa(raw) if source == 'json' else service.ingest_pdf(b'pdf', 'large.pdf')
                    _, peak = tracemalloc.get_traced_memory()
                finally:
                    tracemalloc.stop()
                stored = self.persisted('qa_documents' if source == 'json' else 'pdf_chunks')
                self.assertEqual(sizes, [8] * 32)
                self.assertEqual((completed, len(stored)), (256, 256))
                self.assertEqual(result['embedded' if source == 'json' else 'embedded_chunks'], 256)
                for index, row in enumerate(stored):
                    self.assertEqual(np.frombuffer(row[3 if source == 'json' else 5], dtype=np.float32)[0], index)
                self.assert_progress(source, list(range(0, 257, 8)), 256)
                self.assertGreaterEqual(peak, 16 * 1024 * 1024, 'NumPy allocations must be measured')
                self.assertLess(peak, budget, f'{source} exceeded the declared 32 MiB allocation budget')
                print(f'F025 {source}: 256 vectors, 32 batches, peak tracked allocation {peak / 1024**2:.2f} MiB')


if __name__ == '__main__':
    unittest.main()
