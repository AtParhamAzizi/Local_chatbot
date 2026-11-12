"""Equivalent writing must share keys/model inputs while original evidence survives."""

import hashlib
import importlib
import importlib.util
import json
import sqlite3
import sys
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import numpy as np
from werkzeug.exceptions import BadRequest

from core_support import BACKEND_DIR, OfflineCoreFixture, module_double
from ocr_fixture import PdfDatabase
from embedding_fixtures import with_profile


VARIANTS = (
    ('کیف علی 123', ('کیف علی ۱۲۳', 'كيف علي ١٢٣', 'کيف على 123', 'ﻛﻴﻒ ﻋﻠﻲ １２３')),
    ('می رود', ('مي\u200cرود', 'می رود', 'می \u200c رود', 'می\u200bرود', 'می\t\nرود')),
    ('ثبت نام کاربری', ('ثبت\u200cنام كاربري', 'ثبت نام کاربری', 'ثبت\u00a0نام\u202fکاربری')),
    ('کتاب', ('کِـتاب', 'كِتاب', 'کتاب', '\u200fکتاب\ufeff')),
    ('أ', ('أ', 'ا\u200d\u0654', 'ا\u0654')),
)


class PersianTextTests(OfflineCoreFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.module = importlib.import_module('persian_text')

    def test_authored_variants_share_normalized_hash_and_lexical_forms(self):
        for expected, variants in VARIANTS:
            expected_hash = hashlib.sha256(expected.encode()).hexdigest()
            for original in variants:
                with self.subTest(original=original):
                    self.assertEqual(self.module.normalize_persian(original), expected)
                    self.assertEqual(self.database.hash_question(original), expected_hash)
                    self.assertEqual(self.scoring._terms(original), self.scoring._terms(expected))

    def test_policy_is_idempotent_including_format_characters_between_combining_sequences(self):
        alphabet = ('ا', 'ی', '\u0654', '\u064b', '\u200d', '\u200c', '\u200b', '\u00a0', 'ﻲ', '۱', 'A')
        for first in alphabet:
            for second in alphabet:
                text = ' ' + first + second + '\u200d\u0654\t'
                once = self.module.normalize_persian(text)
                self.assertEqual(self.module.normalize_persian(once), once, repr(text))

    def test_distinct_words_numbers_punctuation_case_and_hamza_remain_distinct(self):
        for first, second in (('می رود', 'میرود'), ('۱۲۳', '۱۲۴'), ('آب', 'اب'), ('سؤال', 'سوال'),
                              ('ABC', 'abc'), ('ثبت؟', 'ثبت!'), ('a-b', 'ab')):
            with self.subTest(first=first, second=second):
                self.assertNotEqual(self.database.hash_question(first), self.database.hash_question(second))
        self.assertEqual(self.module.normalize_persian('ویرایش کد ABC-۱۲/۳؟'), 'ویرایش کد ABC-12/3؟')

    def test_lexical_punctuation_is_a_boundary_and_casefold_remains_available(self):
        self.assertEqual(self.scoring._terms('سلام،کتاب؛علي؟ ۱۲۳ STRASSE Straße'),
                         {'سلام', 'کتاب', 'علی', '123', 'strasse'})
        self.assertEqual(self.scoring._lexical_overlap('ثبت\u200cنام ۱۲۳', 'ثبت نام ١٢٣'), 1)

    def test_empty_format_only_queries_do_no_model_or_database_work(self):
        retrieval = importlib.import_module('services.retrieval')
        embed, qa, pdf = Mock(), Mock(), Mock()
        service = retrieval.RetrievalService(embed=with_profile(embed), search_qa=qa, search_pdf=pdf)
        for value in ('', '\u200c\u200d\ufeff', '\u200e\u200f\u0640\u064b'):
            self.assertEqual(service.retrieve(value), [])
        for adapter in (embed, qa, pdf):
            adapter.assert_not_called()

    def test_public_embedding_entrypoint_normalizes_before_the_real_tokenizer_boundary(self):
        registry = module_double('model_registry', get_active_model_id=lambda: 'fixture',
                                 get_model_definition=lambda identifier: {'embedding_path': 'unused'})
        torch = module_double('torch', no_grad=lambda: lambda function: function)
        transformers = module_double('transformers', AutoTokenizer=Mock(), AutoModel=Mock())
        with patch.dict(sys.modules, {'torch': torch, 'transformers': transformers, 'model_registry': registry}):
            spec = importlib.util.spec_from_file_location('normalization_embedding_fixture', BACKEND_DIR / 'interface.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        tokenizer = Mock(side_effect=RuntimeError('Stop before model work'))
        module.get_embedder = lambda: (tokenizer, None)
        originals = ['ثبت\u200cنام كاربري ١٢٣']
        with self.assertRaisesRegex(RuntimeError, 'Stop before model work'):
            module.embed_texts(originals)
        self.assertEqual(tokenizer.call_args.args[0], ['ثبت نام کاربری 123'])
        self.assertEqual(originals, ['ثبت\u200cنام كاربري ١٢٣'])


class KnowledgeDatabase(PdfDatabase):
    def __init__(self, filename):
        super().__init__(filename)
        with closing(sqlite3.connect(self.filename)) as connection:
            connection.execute('''CREATE TABLE qa_documents (
                id INTEGER PRIMARY KEY, question TEXT, answer TEXT, question_hash TEXT UNIQUE,
                embedding BLOB, embedding_dim INTEGER, embedding_profile TEXT)''')

    def rows(self, table='qa_documents'):
        assert table in {'qa_documents', 'pdf_chunks', 'pdf_documents'}
        with closing(sqlite3.connect(self.filename)) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY id')]

    def legacy(self, question, answer='Original answer'):
        old_hash = hashlib.sha256(question.strip().encode()).hexdigest()
        with closing(sqlite3.connect(self.filename)) as connection:
            connection.execute('INSERT INTO qa_documents (question, answer, question_hash) VALUES (?, ?, ?)',
                               (question, answer, old_hash))
            connection.commit()


class NormalizationPipelineTests(OfflineCoreFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.module = importlib.import_module('persian_text')
        ingestion = importlib.import_module('services.ingestion')
        directory = self.resources.enter_context(TemporaryDirectory())
        self.knowledge = KnowledgeDatabase(Path(directory) / 'knowledge.sqlite')
        self.embed = Mock(side_effect=self.vectors)
        self.open_pdf = Mock()
        self.service = ingestion.IngestionService(
            connect=self.knowledge.connect, embed=with_profile(self.embed), open_pdf=self.open_pdf,
            hash_question=self.database.hash_question, hash_document=self.database.sha256_bytes,
            audit=Mock(), ocr=SimpleNamespace(enabled=False), batch_size=2)

    @staticmethod
    def vectors(texts):
        # Deliberately uses the exact adapter inputs, without normalization here.
        vectors = np.stack([np.frombuffer(hashlib.sha256(value.encode()).digest(), dtype=np.uint8) for value in texts])
        vectors = vectors.astype(np.float32) - 128
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)

    def raw(self, *questions, answer='پاسخ اصلی: كد ۱۲۳؛ می\u200cرود'):
        return json.dumps([{'question': question, 'answer': answer} for question in questions], ensure_ascii=False).encode()

    def test_preflight_and_publication_deduplicate_variants_keep_first_original_and_preview(self):
        first, second = 'ثبت\u200cنام كاربري ١٢٣؟', 'ثبت نام کاربری ۱۲۳؟'
        raw = self.raw(first, second)
        preview = self.service.preflight(raw, 'questions.json', kind='json')
        self.assertTrue(preview['can_publish'])
        self.assertEqual(preview['counts']['duplicates_in_file'], 1)
        self.assertEqual(preview['counts']['expected_chunks'], 1)
        self.assertEqual(preview['samples'][0]['question'], first)
        self.assertEqual(preview['cost']['input_characters'], len('ثبت نام کاربری 123؟'))
        self.assertEqual(self.knowledge.rows(), [])
        self.embed.assert_not_called()
        result = self.service.ingest_qa(raw)
        self.assertEqual((result['inserted'], result['received']), (1, 2))
        self.embed.assert_called_once_with(['ثبت نام کاربری 123؟'])
        stored = self.knowledge.rows()
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]['question'], first)
        self.assertEqual(stored[0]['answer'], 'پاسخ اصلی: كد ۱۲۳؛ می\u200cرود')
        preview = self.service.preflight(self.raw(second), 'again.json', kind='json')
        self.assertEqual((preview['counts']['duplicates_existing'], preview['counts']['inserts']), (1, 0))
        self.assertEqual(self.service.ingest_qa(self.raw(second))['skipped'], 1)
        self.assertEqual(self.knowledge.rows(), stored)

    def test_legacy_lookup_is_read_only_skip_preserves_every_byte_and_replace_keeps_question_key_id(self):
        first, variant = 'ثبت\u200cنام كاربري ١٢٣؟', 'ثبت نام کاربری ۱۲۳؟'
        self.knowledge.legacy(first)
        original = self.knowledge.rows()[0]
        self.assertNotEqual(original['question_hash'], self.database.hash_question(variant))
        preview = self.service.preflight(self.raw(variant), 'legacy.json', kind='json')
        self.assertEqual((preview['counts']['duplicates_existing'], preview['counts']['inserts']), (1, 0))
        self.assertEqual(self.knowledge.rows(), [original])
        self.embed.assert_not_called()
        self.assertEqual(self.service.ingest_qa(self.raw(variant))['skipped'], 1)
        self.assertEqual(self.knowledge.rows(), [original])
        answer = 'پاسخ جایگزین ۱۲۳'
        self.assertEqual(self.service.ingest_qa(self.raw(variant, answer=answer), replace=True)['updated'], 1)
        updated = self.knowledge.rows()[0]
        for field in ('id', 'question', 'question_hash'):
            self.assertEqual(updated[field], original[field])
        self.assertEqual(updated['answer'], answer)
        np.testing.assert_array_equal(np.frombuffer(updated['embedding'], dtype=np.float32),
                                      self.vectors(['ثبت نام کاربری 123؟'])[0])

    def test_multiple_legacy_aliases_are_retained_and_count_as_one_existing_equivalence_class(self):
        self.knowledge.legacy('ثبت\u200cنام كاربري ١٢٣؟', 'Older answer')
        self.knowledge.legacy('ثبت  نام کاربری ۱۲۳؟', 'Newer answer')
        before = self.knowledge.rows()
        preview = self.service.preflight(self.raw('ثبت نام کاربری 123؟'), 'legacy.json', kind='json', replace=True)
        self.assertEqual((preview['counts']['duplicates_existing'], preview['counts']['updates']), (1, 1))
        result = self.service.ingest_qa(self.raw('ثبت نام کاربری 123؟', answer='Explicit replacement'), replace=True)
        self.assertEqual((result['updated'], result['inserted']), (1, 0))
        after = self.knowledge.rows()
        self.assertEqual(after[0], before[0])
        self.assertEqual(after[1]['question'], before[1]['question'])
        self.assertEqual(after[1]['answer'], 'Explicit replacement')

    def test_indexed_canonical_match_takes_precedence_over_newer_legacy_alias(self):
        original = 'ثبت\u200cنام كاربري ١٢٣؟'
        self.service.ingest_qa(self.raw(original))
        self.knowledge.legacy('ثبت  نام کاربری ۱۲۳؟', 'Unchanged legacy alias')
        before = self.knowledge.rows()
        self.assertEqual(self.service.ingest_qa(self.raw('ثبت نام کاربری 123؟', answer='Updated canonical'),
                                              replace=True)['updated'], 1)
        after = self.knowledge.rows()
        self.assertEqual(after[0]['id'], before[0]['id'])
        self.assertEqual(after[0]['question'], original)
        self.assertEqual(after[0]['answer'], 'Updated canonical')
        self.assertEqual(after[1], before[1])

    def test_legacy_scan_batches_support_cancellation_and_walk_toward_older_ids(self):
        # Seed enough unrelated rows to require a second bounded read.
        with closing(sqlite3.connect(self.knowledge.filename)) as connection:
            connection.executemany('INSERT INTO qa_documents (question, answer, question_hash) VALUES (?, ?, ?)',
                                   [(f'old {i}', 'answer', str(i)) for i in range(270)])
            connection.commit()
        connection = self.knowledge.connect()
        try:
            with connection.cursor() as cursor:
                guard = Mock(side_effect=[None, RuntimeError('Canceled')])
                with self.assertRaisesRegex(RuntimeError, 'Canceled'):
                    self.service._legacy_qa_matches(cursor, {self.database.hash_question('missing')}, guard)
                self.assertEqual(guard.call_count, 2)
                found = self.service._legacy_qa_matches(cursor, {self.database.hash_question('old 0')})
                self.assertEqual(found, {self.database.hash_question('old 0'): {'id': 1}})
        finally:
            connection.close()

    def test_format_only_questions_are_rejected_before_embedding_or_database_access(self):
        self.service.connect = Mock(side_effect=AssertionError('No database access'))
        raw = self.raw('valid', '\u200c\u200d\ufeff')
        preview = self.service.preflight(raw, 'invalid.json', kind='json')
        self.assertFalse(preview['valid'])
        self.assertEqual((preview['issues'][0]['record'], preview['issues'][0]['field']), (2, 'question'))
        with self.assertRaises(BadRequest):
            self.service.ingest_qa(raw)
        self.embed.assert_not_called()
        self.service.connect.assert_not_called()

    def test_equivalent_queries_return_same_qa_pdf_scores_and_originals_with_page_provenance(self):
        first = 'ثبت\u200cنام كاربري ١٢٣؟'
        self.service.ingest_qa(self.raw(first, 'ثبت نام کاربری 999؟'))
        document = Mock(page_count=3)
        document.load_page.side_effect = [Mock(get_text=Mock(return_value=value)) for value in ('', '', first)]
        self.open_pdf.return_value = document
        self.service.ingest_pdf(b'original pdf bytes', 'راهنمای ۱۲۳.pdf')
        stored_pdf = self.knowledge.rows('pdf_chunks')[0]
        self.assertEqual((stored_pdf['content'], stored_pdf['page_start'], stored_pdf['page_end']), (first, 3, 3))
        self.assertEqual(self.knowledge.rows('pdf_documents')[0]['document_hash'], hashlib.sha256(b'original pdf bytes').hexdigest())
        retrieval = importlib.import_module('services.retrieval').RetrievalService(
            embed=with_profile(self.embed), search_qa=self.scoring._search_qa, search_pdf=self.scoring._search_pdf)
        self.embed.reset_mock()
        variants = (first, 'ثبت نام کاربری ۱۲۳؟', 'ثبت\u200bنام\tكاربري 123؟')
        with patch.object(self.scoring, 'get_conn', self.knowledge.connect):
            hits = [retrieval.retrieve(query) for query in variants]
        self.assertEqual(hits[1:], [hits[0], hits[0]])
        self.assertEqual(self.embed.call_args_list, [unittest.mock.call(['ثبت نام کاربری 123؟'])] * 3)
        self.assertEqual(hits[0][0]['question'], first)
        pdf = next(hit for hit in hits[0] if hit['source_type'] == 'pdf')
        self.assertEqual((pdf['content'], pdf['filename'], pdf['page_start'], pdf['page_end']),
                         (first, 'راهنمای ۱۲۳.pdf', 3, 3))
        self.assertAlmostEqual(pdf['score'], 1, places=6)

    def test_http_chat_preserves_original_query_history_and_generation_context(self):
        query = 'ثبت\u200cنام كاربري ١٢٣؟'
        self.service.ingest_qa(self.raw(query))
        retrieval = importlib.import_module('services.retrieval').RetrievalService(
            embed=with_profile(self.embed), search_qa=self.scoring._search_qa, search_pdf=lambda *args, **kwargs: [])
        user = importlib.import_module('user')
        application = importlib.import_module('application')
        app = application.create_ui_app(retrieval_service=retrieval)
        history = MagicMock()
        history.cursor.return_value.__enter__.return_value.lastrowid = 17
        generated = Mock(return_value='پاسخ اصلی ۱۲۳')
        with patch.object(self.scoring, 'get_conn', self.knowledge.connect), \
                patch.object(user, 'get_conn', return_value=history), patch.object(user, 'generate_reply', generated):
            response = app.test_client().post('/chat', json={'message': query})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(generated.call_args.args[0], query)
        self.assertEqual(generated.call_args.args[1][0]['question'], query)
        parameters = history.cursor.return_value.__enter__.return_value.execute.call_args.args[1]
        self.assertEqual(parameters[3], query)
        self.assertEqual(parameters[4], 'پاسخ اصلی ۱۲۳')


if __name__ == '__main__':
    unittest.main()
