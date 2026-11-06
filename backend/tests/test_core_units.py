"""Small deterministic regressions against real core functions, not model weights."""

import hashlib
import importlib
import unittest
from unittest.mock import MagicMock, Mock, patch

import numpy as np

from core_support import OfflineCoreFixture


class HashingTests(OfflineCoreFixture, unittest.TestCase):
    def test_question_hash_has_a_known_sha256_vector(self):
        self.assertEqual(self.database.hash_question('abc'),
                         'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')

    def test_question_hash_trims_edges_but_preserves_internal_text(self):
        question = '\u0633\u0644\u0627\u0645 \u062f\u0646\u06cc\u0627'
        expected = hashlib.sha256(question.encode('utf-8')).hexdigest()
        self.assertEqual(self.database.hash_question('\n\t ' + question + ' \r\n'), expected)
        self.assertNotEqual(self.database.hash_question(question.replace(' ', '  ')), expected)
        self.assertNotEqual(self.database.hash_question('ABC'), self.database.hash_question('abc'))

    def test_document_hash_preserves_every_byte(self):
        self.assertEqual(self.database.sha256_bytes(b''),
                         'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')
        self.assertEqual(self.database.sha256_bytes(b'abc'), self.database.hash_question('abc'))
        self.assertNotEqual(self.database.sha256_bytes(b'abc\n'), self.database.sha256_bytes(b'abc'))
        self.assertEqual(self.database.sha256_bytes(b'\x00\xff'),
                         hashlib.sha256(b'\x00\xff').hexdigest())

    def test_password_hash_round_trip_and_salt_format(self):
        salt = bytes(range(16))
        password = ' \u0631\u0645\u0632 \u0639\u0628\u0648\u0631 '
        with patch.object(self.accounts.secrets, 'token_bytes', return_value=salt) as random_bytes:
            encoded = self.accounts._hash_password(password)
        random_bytes.assert_called_once_with(16)
        expected = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 240000).hex()
        self.assertEqual(encoded, f'pbkdf2_sha256$240000${salt.hex()}${expected}')
        self.assertTrue(self.accounts.verify_password(password, encoded))
        self.assertFalse(self.accounts.verify_password(password.strip(), encoded))
        self.assertFalse(self.accounts.verify_password('wrong', encoded))

    def test_password_hash_uses_fresh_salts(self):
        with patch.object(self.accounts.secrets, 'token_bytes', side_effect=[b'a' * 16, b'b' * 16]):
            first = self.accounts._hash_password('password')
            second = self.accounts._hash_password('password')
        self.assertNotEqual(first, second)
        self.assertTrue(self.accounts.verify_password('password', first))
        self.assertTrue(self.accounts.verify_password('password', second))

    def test_malformed_password_encodings_fail_closed(self):
        for encoded in ('', 'sha256$1$00$00', 'pbkdf2_sha256$bad$00$00',
                        'pbkdf2_sha256$0$00$00', 'pbkdf2_sha256$1$zz$00'):
            with self.subTest(encoded=encoded):
                self.assertFalse(self.accounts.verify_password('password', encoded))


class PersianNormalizationTests(OfflineCoreFixture, unittest.TestCase):
    def test_arabic_yeh_and_kaf_match_persian_variants(self):
        self.assertEqual(self.scoring._terms('\u0643\u062a\u0627\u0628 \u0639\u0644\u064a'),
                         {'\u06a9\u062a\u0627\u0628', '\u0639\u0644\u06cc'})
        self.assertEqual(self.scoring._lexical_overlap('\u0643\u062a\u0627\u0628 \u0639\u0644\u064a',
                                                       '\u06a9\u062a\u0627\u0628 \u0639\u0644\u06cc'), 1.0)

    def test_mixed_script_casefold_whitespace_and_duplicate_terms(self):
        self.assertEqual(self.scoring._terms('\u0633\u0644\u0627\u0645\tStra\u00dfe STRASSE\n\u0633\u0644\u0627\u0645'),
                         {'\u0633\u0644\u0627\u0645', 'strasse'})
        self.assertEqual(self.scoring._terms('a I 1 ab 12'), {'ab', '12'})

    def test_empty_inputs_have_no_terms_or_overlap(self):
        for value in (None, '', ' \t\n', '. !'):
            with self.subTest(value=value):
                self.assertEqual(self.scoring._terms(value), set())
                self.assertEqual(self.scoring._lexical_overlap(value, 'hello'), 0.0)
                self.assertEqual(self.scoring._lexical_overlap('hello', value), 0.0)

    def test_normalized_terms_are_idempotent(self):
        terms = self.scoring._terms('\u0643\u062a\u0627\u0628 \u0639\u0644\u064a HELLO hello')
        self.assertEqual(self.scoring._terms(' '.join(sorted(terms))), terms)


class ChunkingTests(OfflineCoreFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.split_text = importlib.import_module('services.ingestion').split_text

    def test_empty_and_short_text(self):
        for value in ('', '  \t\n', '\u00ad'):
            with self.subTest(value=value):
                self.assertEqual(self.split_text(value), [])
        self.assertEqual(self.split_text(' \u0633\u0644\u0627\u0645 \n'), ['\u0633\u0644\u0627\u0645'])

    def test_pdf_layout_noise_is_removed(self):
        self.assertEqual(self.split_text('  soft\u00adhyphen\t  exam-\nple\n\n\nnext  '),
                         ['softhyphen example\n\nnext'])

    def test_exact_size_and_unbroken_text_overlap(self):
        self.assertEqual(self.split_text('abcdefgh', chunk_size=8, overlap=2), ['abcdefgh'])
        self.assertEqual(self.split_text('abcdefghijklmnopqrst', chunk_size=8, overlap=2),
                         ['abcdefgh', 'ghijklmn', 'mnopqrst'])

    def test_zero_overlap_preserves_all_unbroken_text(self):
        text = '\u06a9\u062a\u0627\u0628' * 25
        chunks = self.split_text(text, chunk_size=13, overlap=0)
        self.assertEqual(''.join(chunks), text)
        self.assertTrue(all(0 < len(chunk) <= 13 for chunk in chunks))
        self.assertEqual(chunks, self.split_text(text, chunk_size=13, overlap=0))

    def test_natural_boundaries_are_preferred_in_second_half(self):
        for separator in ('\n\n', '\n', '. ', '\u061f ', '! '):
            with self.subTest(separator=separator):
                chunks = self.split_text('abcdefgh' + separator + 'ijklmnopqrst', 12, 0)
                self.assertEqual(chunks[0], ('abcdefgh' + separator).strip())
                self.assertEqual(''.join(chunks[1:]), 'ijklmnopqrst')
                self.assertTrue(all(len(chunk) <= 12 for chunk in chunks))

    def test_overlap_must_be_smaller_than_chunk_size(self):
        for overlap in (8, 9):
            with self.subTest(overlap=overlap), self.assertRaises(ValueError):
                self.split_text('some text', chunk_size=8, overlap=overlap)


class ScoringTests(OfflineCoreFixture, unittest.TestCase):
    def test_cosine_known_vectors_and_scale_invariance(self):
        query = np.array([3, 4], dtype=np.float32)
        for vector, expected in (([6, 8], 1.0), ([-4, 3], 0.0), ([-6, -8], -1.0), ([3, 0], 0.6)):
            with self.subTest(vector=vector):
                blob = np.array(vector, dtype=np.float32).tobytes()
                self.assertAlmostEqual(self.scoring._cosine_score(query, blob, 2), expected, places=6)

    def test_absent_zero_and_incompatible_embeddings_are_skipped(self):
        vector = np.array([1, 0], dtype=np.float32)
        for query, blob, dimension in (
            (vector, None, 2), (vector, b'', 2), (vector, vector.tobytes(), 3),
            (vector, np.ones(3, dtype=np.float32).tobytes(), 3),
            (vector, np.zeros(2, dtype=np.float32).tobytes(), 2),
            (np.zeros(2, dtype=np.float32), vector.tobytes(), 2),
        ):
            with self.subTest(dimension=dimension, blob=blob):
                self.assertIsNone(self.scoring._cosine_score(query, blob, dimension))
        self.assertEqual(self.scoring._cosine_score(vector, vector.tobytes(), None), 1.0)

    def test_lexical_overlap_counts_unique_query_terms_not_document_length(self):
        self.assertEqual(self.scoring._lexical_overlap('alpha alpha beta', 'alpha other terms'), 0.5)
        self.assertEqual(self.scoring._lexical_overlap('alpha', 'alpha beta gamma'), 1.0)
        self.assertEqual(self.scoring._lexical_overlap('alpha', 'unrelated'), 0.0)

    def connection(self, rows):
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.fetchall.return_value = rows
        return connection

    def test_qa_scores_sort_limit_skip_bad_dimensions_and_close_connection(self):
        query = np.array([1, 0], dtype=np.float32)
        rows = [dict(id=identifier, question='question', answer='answer',
                     embedding=np.array(vector, dtype=np.float32).tobytes(), embedding_dim=len(vector))
                for identifier, vector in ((1, [0, 1]), (2, [1, 0]), (3, [1, 0, 0]))]
        connection = self.connection(rows)
        with patch.object(self.scoring, 'get_conn', return_value=connection):
            hits = self.scoring._search_qa(query, k=1)
        self.assertEqual([hit['id'] for hit in hits], [2])
        self.assertEqual(hits[0]['source_type'], 'qa')
        self.assertEqual(hits[0]['score'], 1.0)
        connection.close.assert_called_once_with()

    def test_pdf_weighting_ranking_and_source_metadata(self):
        query = np.array([1, 0], dtype=np.float32)
        rows = [dict(id=identifier, document_id=7, filename='guide.pdf', chunk_index=identifier,
                     page_start=2, page_end=2, content=content,
                     embedding=query.tobytes(), embedding_dim=2)
                for identifier, content in ((1, 'unrelated'), (2, 'alpha beta'))]
        connection = self.connection(rows)
        with patch.object(self.scoring, 'get_conn', return_value=connection):
            hits = self.scoring._search_pdf(query, k=2, query='alpha beta')
        self.assertEqual([hit['id'] for hit in hits], [2, 1])
        self.assertAlmostEqual(hits[0]['score'], 1.0)
        self.assertAlmostEqual(hits[1]['score'], 0.82)
        self.assertEqual(hits[0]['semantic_score'], 1.0)
        self.assertEqual((hits[0]['document_id'], hits[0]['page_start'], hits[0]['chunk_index']), (7, 2, 2))
        cursor = connection.cursor.return_value.__enter__.return_value
        self.assertIn("d.status = 'completed'", cursor.execute.call_args.args[0])
        connection.close.assert_called_once_with()

    def test_search_closes_connection_on_query_failure(self):
        for search in (self.scoring._search_qa, self.scoring._search_pdf):
            connection = self.connection([])
            connection.cursor.return_value.__enter__.return_value.execute.side_effect = RuntimeError('query failed')
            with self.subTest(search=search.__name__), patch.object(self.scoring, 'get_conn', return_value=connection):
                with self.assertRaisesRegex(RuntimeError, 'query failed'):
                    search(np.ones(2, dtype=np.float32))
                connection.close.assert_called_once_with()

    def test_retrieval_uses_embedding_double_threshold_merge_and_top_k(self):
        retrieval = importlib.import_module('services.retrieval')
        embedder = Mock(return_value=[[1.0, 0.0]])
        qa = [{'id': 1, 'score': 0.35}, {'id': 2, 'score': 0.349}, {'id': 3, 'score': 0.9}]
        pdf = [{'id': 4, 'score': 0.7}, {'id': 5, 'score': 0.1}]
        search_qa = Mock(return_value=qa)
        search_pdf = Mock(return_value=pdf)
        service = retrieval.RetrievalService(embed=embedder, search_qa=search_qa, search_pdf=search_pdf)
        self.assertEqual([hit['id'] for hit in service.retrieve('question', limit=2)], [3, 4])
        self.assertEqual([hit['id'] for hit in service.retrieve('question', limit=6)], [3, 4, 1])
        self.assertEqual(embedder.call_args.args, (['question'],))
        np.testing.assert_array_equal(search_qa.call_args.args[0], [1, 0])
        self.assertEqual(search_qa.call_args.args[0].dtype, np.float32)
        self.assertEqual(search_qa.call_args.kwargs, {'k': 5})
        self.assertEqual(search_pdf.call_args.kwargs, {'k': 8, 'query': 'question'})


class PermissionUnitTests(OfflineCoreFixture, unittest.TestCase):
    def test_flags_require_actual_booleans_not_truthy_values(self):
        for flag in (1, 'true', [], None):
            with self.subTest(flag=flag):
                user = {'is_active': flag, 'is_admin': True, 'permissions': ['*']}
                self.assertFalse(self.policy.permits(user, 'models.list'))
                user.update(is_active=True, is_admin=flag)
                self.assertFalse(self.policy.permits(user, 'users.list'))

    def test_allowed_pages_exposes_only_granted_views(self):
        user = {'is_active': True, 'is_admin': False, 'permissions': ['pdf_upload']}
        self.assertEqual(self.policy.allowed_pages(user), ['admin/admin_pdf.html'])
        self.assertEqual(self.policy.allowed_pages(None), [])
        user['permissions'] = '*'
        self.assertNotIn('admin/admin-user-control.html', self.policy.allowed_pages(user))
        user['is_admin'] = True
        self.assertIn('admin/admin-user-control.html', self.policy.allowed_pages(user))

    def test_stored_permission_lists_are_deduplicated_and_sorted(self):
        for value in (['models', 'pdf_upload', 'models', ''], '["pdf_upload", "models", "models", ""]'):
            with self.subTest(value=value):
                self.assertEqual(self.accounts._permissions(value), ['models', 'pdf_upload'])
        self.assertEqual(self.accounts._permissions('*'), '*')
        self.assertEqual(self.accounts._permissions('invalid json'), [])


class ParsingUnitTests(OfflineCoreFixture, unittest.TestCase):
    def test_utf8_objects_and_arrays_parse_without_mutating_text(self):
        payload = '{"question":" \u0633\u0644\u0627\u0645 ","rows":[1,true,null]}'
        self.assertEqual(self.parsing.parse_json(payload.encode('utf-8')),
                         {'question': ' \u0633\u0644\u0627\u0645 ', 'rows': [1, True, None]})

    def test_malformed_encoding_duplicates_and_constants(self):
        for raw in (b'', b'{', b'{} trailing', b'"\xff"', b'{"nested":{"a":1,"a":2}}',
                    b'NaN', b'Infinity', b'-Infinity'):
            with self.subTest(raw=raw[:60]), self.assertRaises(self.parsing.InvalidRequest):
                self.parsing.parse_json(raw)

    def test_decoder_recursion_failures_become_client_errors(self):
        with patch.object(self.parsing.json, 'loads', side_effect=RecursionError):
            with self.assertRaises(self.parsing.InvalidRequest):
                self.parsing.parse_json(b'[]')

    def test_exact_object_schema_and_required_fields(self):
        fields = {'name': self.parsing.text(4, 1), 'count': self.parsing.integer(1, 3)}
        self.assertEqual(self.parsing.validate_object({'name': ' hi ', 'count': 3}, fields, ('name',)),
                         {'name': 'hi', 'count': 3})
        for payload in ([], None, {}, {'name': 'ok', 'extra': 1}, {'name': 'ok', 'count': True}):
            with self.subTest(payload=payload), self.assertRaises(self.parsing.InvalidRequest):
                self.parsing.validate_object(payload, fields, ('name',))
        with self.assertRaises(self.parsing.InvalidRequest):
            self.parsing.validate_object({}, fields, nonempty=True)

    def test_text_limits_unicode_and_password_whitespace(self):
        validate = self.parsing.text(4, 1)
        self.assertEqual(validate('abcd', 'text'), 'abcd')
        self.assertEqual(self.parsing.text(8, 1, strip=False)(' pass ', 'password'), ' pass ')
        for value in ('', '   ', 'abcde', '\ud800', 1, True, None):
            with self.subTest(value=repr(value)), self.assertRaises(self.parsing.InvalidRequest):
                validate(value, 'text')

    def test_integers_booleans_and_query_numbers_are_strict(self):
        validate = self.parsing.integer(1, 3)
        for value in (1, 3):
            self.assertEqual(validate(value, 'count'), value)
        for value in (0, 4, True, 1.0, '1', None):
            with self.subTest(value=value), self.assertRaises(self.parsing.InvalidRequest):
                validate(value, 'count')
        self.assertEqual(self.parsing.query_integer(1, 3)('03', 'page'), 3)
        for value in ('+1', '-1', '1.0', ' 1', '\u06f1', '1' * 20):
            with self.subTest(value=value), self.assertRaises(self.parsing.InvalidRequest):
                self.parsing.query_integer(1, 3)(value, 'page')
        for value in (True, False):
            self.assertIs(self.parsing.boolean(value, 'flag'), value)
        for value in (0, 1, 'true', None):
            with self.subTest(value=value), self.assertRaises(self.parsing.InvalidRequest):
                self.parsing.boolean(value, 'flag')

    def test_permission_parser_limits_and_legacy_wildcard(self):
        self.assertEqual(self.parsing.permissions('*', 'permissions'), '*')
        self.assertEqual(self.parsing.permissions([' models '], 'permissions'), ['models'])
        self.assertEqual(len(self.parsing.permissions(['a' * 64] * 32, 'permissions')), 32)
        for value in ('models', {}, None, [''], ['a' * 65], ['models'] * 33):
            with self.subTest(value=value), self.assertRaises(self.parsing.InvalidRequest):
                self.parsing.permissions(value, 'permissions')
