"""Offline request-schema and resource-limit regressions; no real models or DB."""

import importlib
import io
import json
import unittest
from unittest.mock import MagicMock, Mock, patch

from werkzeug.datastructures import MultiDict
from werkzeug.test import EnvironBuilder

from test_abuse_controls import AbuseEndpointFixture


class UnreadableInput:
    def read(self, *args):
        raise AssertionError('Oversized declared bodies must not be read')

    def readinto(self, *args):
        raise AssertionError('Oversized declared bodies must not be read')


class RequestValidationTests(AbuseEndpointFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.validation = importlib.import_module('request_validation')
        self.headers = self.authenticated_headers()
        self.no_work = Mock(side_effect=AssertionError('Invalid requests must not perform work'))
        for module, names in (
            (self.admin, ('get_conn', 'authenticate', 'create_user', 'update_user', 'set_active_model')),
            (self.user, ('get_conn', '_retrieve_top')),
        ):
            for name in names:
                self.resources.enter_context(patch.object(module, name, self.no_work))

    def tearDown(self):
        super().tearDown()
        self.no_work.assert_not_called()

    def assert_error(self, response, status=400):
        self.assertEqual(response.status_code, status, response.get_data(as_text=True))
        self.assertTrue(response.is_json)
        self.assertIsInstance(response.json['error'], str)
        self.assertEqual(response.json['code'], {
            400: 'invalid_request', 413: 'payload_too_large', 415: 'unsupported_media_type',
        }[status])

    def post_json(self, path, payload):
        self.now += 1000
        return self.admin_open(path, 'POST', data=json.dumps(payload),
                               content_type='application/json', headers=self.headers)

    def upload(self, raw, filename='records.json', *, fields=None, path='/admin/api/upload'):
        self.now += 1000
        data = MultiDict(fields or {})
        data.add('file', (io.BytesIO(raw), filename))
        return self.admin_open(path, 'POST', data=data, headers=self.headers)

    def pdf(self, document):
        self.resources.enter_context(patch.object(self.admin.fitz, 'open', return_value=document))
        return self.upload(b'%PDF-test', 'document.pdf', path='/admin/api/pdf/upload')

    def test_chat_rejects_wrong_objects_fields_and_types_before_retrieval(self):
        payloads = [None, [], 'hello', 1, True, {}, {'message': None}, {'message': []},
                    {'message': {}}, {'message': 1}, {'message': True}, {'message': '   '},
                    {'message': 'a' * 4097}, {'message': '\ud800'},
                    {'message': 'hello', 'user_id': 12}, {'message': 'hello', 'user_id': 'u' * 65},
                    {'message': 'hello', 'unexpected': True}]
        for payload in payloads:
            with self.subTest(payload=repr(payload)[:100]):
                self.now += 1000
                self.assert_error(self.ui_client.post('/chat', data=json.dumps(payload),
                                                      content_type='application/json'))

    def test_json_syntax_duplicates_encoding_constants_and_depth(self):
        bodies = [b'', b'{', b'{"message":"hi",}', b'{"message":"\xff"}',
                  b'{"message":"one","message":"two"}', b'{"message":NaN}',
                  b'{"message":Infinity}', b'[' * 2000 + b']' * 2000]
        for body in bodies:
            with self.subTest(body=body[:80]):
                self.now += 1000
                self.assert_error(self.ui_client.post('/chat', data=body, content_type='application/json'))

    def test_json_endpoints_require_json_media_type(self):
        self.assert_error(self.ui_client.post('/chat', data='{"message":"hello"}'), 415)
        self.assert_error(self.post_json('/admin/api/models', {'model_id': []}))
        self.assert_error(self.admin_open('/admin/api/models', 'POST', data='{}', headers=self.headers), 415)
        self.assert_error(self.upload(b'{}', fields={'replace': 'perhaps'}))
        self.assert_error(self.admin_open('/admin/api/upload', 'POST', json=[], headers=self.headers), 415)

    def test_feedback_requires_typed_bounded_ids_and_enum(self):
        payloads = [[], None, {}, {'message_id': 1}, {'message_id': True, 'feedback': 'like'},
                    {'message_id': '1', 'feedback': 'like'}, {'message_id': 1.0, 'feedback': 'like'},
                    {'message_id': 0, 'feedback': 'like'}, {'message_id': -1, 'feedback': 'like'},
                    {'message_id': 2 ** 63, 'feedback': 'like'}, {'message_id': 1, 'feedback': []},
                    {'message_id': 1, 'feedback': 'other'},
                    {'message_id': 1, 'feedback': 'like', 'owner_hash': 'x'}]
        for payload in payloads:
            with self.subTest(payload=payload):
                self.assert_error(self.ui_client.post('/feedback', data=json.dumps(payload),
                                                      content_type='application/json'))

    def test_admin_user_create_schema_precedes_password_hashing(self):
        valid = {'username': 'operator', 'password': 'long-password'}
        payloads = [[], None, {}, {**valid, 'username': 123}, {**valid, 'username': 'a' * 65},
                    {**valid, 'password': 'short'}, {**valid, 'password': 'a' * 1025},
                    {**valid, 'is_admin': 'false'}, {**valid, 'is_admin': 1},
                    {**valid, 'permissions': {'controller': True}}, {**valid, 'permissions': [1]},
                    {**valid, 'permissions': ['x'] * 33}, {**valid, 'permissions': ['x' * 65]},
                    {**valid, 'extra': 'x'}]
        for payload in payloads:
            with self.subTest(payload=repr(payload)[:100]):
                self.assert_error(self.post_json('/admin/api/admin-users', payload))

    def test_admin_user_patch_schema_precedes_lookup(self):
        for payload in ([], {}, {'password': None}, {'password': ''}, {'username': 'rename'},
                        {'is_active': 0}, {'is_admin': 'true'}, {'permissions': None}):
            with self.subTest(payload=payload):
                self.assert_error(self.admin_open('/admin/api/admin-users/2', 'PATCH',
                                                  json=payload, headers=self.headers))

    def test_model_selection_requires_a_short_string(self):
        for payload in ([], {}, {'model_id': True}, {'model_id': {}}, {'model_id': ''},
                        {'model_id': 'a' * 65}, {'model_id': 'model', 'load': True}):
            with self.subTest(payload=payload):
                self.assert_error(self.post_json('/admin/api/models', payload))

    def test_login_rejects_long_duplicate_and_unexpected_fields_before_authentication(self):
        self.current_user = None
        proof = self.login_proof()
        valid = {'username': 'operator', 'password': 'password', 'csrf_token': proof}
        payloads = [{**valid, 'username': 'a' * 65}, {**valid, 'password': 'a' * 1025},
                    {**valid, 'username': ''}, {**valid, 'extra': 'x'},
                    MultiDict([*valid.items(), ('username', 'another')])]
        for payload in payloads:
            with self.subTest(payload=repr(payload)[:100]):
                self.now += 1000
                self.assert_error(self.admin_open('/login', 'POST', data=payload))

    def test_pagination_rejects_out_of_range_duplicate_and_unknown_parameters(self):
        queries = ('page=0', 'page=-1', 'page=10001', 'page=1.0', 'page=abc', 'page=' + '9' * 100,
                   'page=1&page=2', 'per_page=0', 'per_page=101', 'unexpected=1', 'q=' + 'a' * 257)
        for path in ('/admin/api/documents', '/admin/api/pdf/documents'):
            for query in queries:
                with self.subTest(path=path, query=query[:80]):
                    self.assert_error(self.admin_open(path + '?' + query))
        for query in ('limit=0', 'limit=1001', 'limit=bad', 'limit=1&limit=2', 'q=' + 'a' * 257):
            self.assert_error(self.admin_open('/admin/api/chat/sessions?' + query))

    def test_conversation_and_resource_identifiers_are_bounded(self):
        for query in ('', 'user_id=', 'user_id=' + 'u' * 65, 'user_id=x&ip=' + 'a' * 46,
                      'user_id=x&user_id=y', 'user_id=x&extra=1'):
            self.assert_error(self.admin_open('/admin/api/chat/messages?' + query))
            self.assert_error(self.admin_open('/admin/api/chat/sessions?' + query, 'DELETE', headers=self.headers))
        for query in ('', 'id=0', 'id=-1', 'id=' + str(2 ** 63), 'id=1&id=2'):
            self.assert_error(self.admin_open('/admin/api/chat/logs?' + query, 'DELETE', headers=self.headers))
        for path in ('/admin/api/documents/', '/admin/api/pdf/documents/', '/admin/api/admin-users/'):
            for identifier in ('0', '2147483648'):
                self.assert_error(self.admin_open(path + identifier, 'DELETE', headers=self.headers))

    def test_declared_oversized_bodies_are_rejected_without_reading_or_authentication(self):
        cases = ((self.ui_client, '/chat', 'http://localhost:5000', 65537),
                 (self.admin_client, '/admin/api/models', 'http://localhost:8000', 65537),
                 (self.admin_client, '/login', 'http://localhost:8000', 65537),
                 (self.admin_client, '/admin/api/upload', 'http://localhost:8000', 20 * 1024 ** 2 + 65537),
                 (self.admin_client, '/admin/api/pdf/upload', 'http://localhost:8000', 50 * 1024 ** 2 + 65537))
        with patch.object(self.admin, 'load_admin_session', self.no_work):
            for client, path, base_url, size in cases:
                with self.subTest(path=path):
                    self.assert_error(client.post(path, base_url=base_url, environ_overrides={
                        'CONTENT_LENGTH': str(size), 'wsgi.input': UnreadableInput(),
                    }), 413)

    def test_streamed_json_without_content_length_is_still_bounded(self):
        raw = b'{"message":"' + b'a' * 70000 + b'"}'
        self.assert_error(self.ui_client.post('/chat', content_type='application/json', environ_overrides={
            'CONTENT_LENGTH': None, 'wsgi.input': io.BytesIO(raw), 'wsgi.input_terminated': True,
        }), 413)

    def test_query_string_size_is_bounded_before_authentication(self):
        with patch.object(self.admin, 'load_admin_session', self.no_work):
            self.assert_error(self.admin_open('/admin/api/documents?q=' + 'a' * 8193), 413)

    def test_multipart_file_limit_is_enforced_during_parsing(self):
        streams = []
        upload_class = self.validation.BoundedUpload

        def tracked_upload(limit):
            upload = upload_class(limit)
            streams.append(upload)
            return upload

        for path, filename in (('/admin/api/upload', 'records.json'), ('/admin/api/pdf/upload', 'document.pdf')):
            with self.subTest(path=path), patch.dict(self.validation.UPLOAD_LIMITS, {path: 2048}), \
                    patch.object(self.validation, 'BoundedUpload', side_effect=tracked_upload):
                self.assert_error(self.upload(b'a' * 2049, filename, path=path), 413)
        self.assertEqual(len(streams), 2)
        for upload in streams:
            self.assertTrue(upload.closed)

    def test_streamed_multipart_and_form_csrf_cannot_bypass_file_limits(self):
        path = '/admin/api/upload'
        builder = EnvironBuilder(path=path, base_url='http://localhost:8000', method='POST', data={
            'file': (io.BytesIO(b'a' * 2049), 'records.json'), 'csrf_token': self.headers['X-CSRF-Token'],
        })
        self.addCleanup(builder.close)
        environ = builder.get_environ()
        with patch.dict(self.validation.UPLOAD_LIMITS, {path: 2048}):
            self.assert_error(self.admin_client.post(path, base_url='http://localhost:8000',
                                                   environ_overrides={
                'CONTENT_TYPE': environ['CONTENT_TYPE'], 'CONTENT_LENGTH': None,
                'wsgi.input': environ['wsgi.input'], 'wsgi.input_terminated': True,
            }), 413)

    def test_upload_structure_extensions_empty_files_and_form_fields(self):
        for raw, filename, fields in ((b'', 'records.json', {}), (b'[]', 'records.txt', {}),
                                      (b'[]', 'a' * 256 + '.json', {}), (b'[]', 'records.json', {'extra': 'x'}),
                                      (b'[]', 'records.json', [('replace', 'true'), ('replace', 'false')])):
            with self.subTest(filename=filename[:80], fields=fields):
                self.assert_error(self.upload(raw, filename, fields=fields))
        for names in (('file', 'file'), ('file', 'other'), ('other',)):
            self.now += 1000
            data = MultiDict([(name, (io.BytesIO(b'[]'), 'records.json')) for name in names])
            self.assert_error(self.admin_open('/admin/api/upload', 'POST', data=data, headers=self.headers))

    def test_multipart_part_and_form_memory_limits(self):
        self.assert_error(self.upload(b'[]', fields={f'field-{index}': 'x' for index in range(8)}), 413)
        self.assert_error(self.upload(b'[]', fields={'replace': 'x' * (128 * 1024 + 1)}), 413)

    def test_json_upload_validates_every_row_before_embedding_or_writing(self):
        valid = {'question': 'Question?', 'answer': 'Answer.'}
        invalid_rows = [None, 'bad', [], {}, {'question': True, 'answer': 'Answer'},
                        {'question': 'q', 'answer': []}, {'question': 'q', 'answer': ' '},
                        {'question': 'q' * 4097, 'answer': 'a'}, {'question': 'q', 'answer': 'a' * 16001},
                        {**valid, 'extra': 'x'}]
        for invalid in invalid_rows:
            with self.subTest(row=repr(invalid)[:80]):
                self.assert_error(self.upload(json.dumps([valid, invalid]).encode()))
        for payload in ([], {}, None, 'text'):
            self.assert_error(self.upload(json.dumps(payload).encode()))
        self.assert_error(self.upload(b'[{"question":"q","question":"duplicate","answer":"a"}]'))
        self.assert_error(self.upload(b'\xff'))
        self.assert_error(self.upload(json.dumps([valid] * 257).encode()), 413)

    def test_pdf_page_quota_precedes_extraction_and_models(self):
        document = Mock(page_count=201)
        self.assert_error(self.pdf(document), 413)
        document.load_page.assert_not_called()
        document.close.assert_called_once()

    def test_pdf_text_quota_precedes_database_and_models(self):
        document = Mock(page_count=2)
        document.load_page.return_value.get_text.return_value = 'x' * 250001
        self.assert_error(self.pdf(document), 413)
        document.close.assert_called_once()

    def test_pdf_chunk_quota_precedes_database_and_models(self):
        document = Mock(page_count=1)
        document.load_page.return_value.get_text.return_value = 'readable text'
        with patch.object(self.admin, 'split_text', return_value=['chunk'] * 257):
            self.assert_error(self.pdf(document), 413)
        document.close.assert_called_once()

    def test_unreadable_and_empty_pdfs_return_client_errors(self):
        with patch.object(self.admin.fitz, 'open', side_effect=ValueError('bad PDF')):
            self.assert_error(self.upload(b'not a PDF', 'bad.pdf', path='/admin/api/pdf/upload'))
        for count, extracted in ((0, ''), (1, '   '), (1, ValueError('encrypted PDF'))):
            with self.subTest(count=count, extracted=repr(extracted)):
                document = Mock(page_count=count)
                if isinstance(extracted, Exception):
                    document.load_page.side_effect = extracted
                else:
                    document.load_page.return_value.get_text.return_value = extracted
                self.assert_error(self.pdf(document))
                document.close.assert_called_once()

    def test_valid_json_upload_preserves_deduplication_and_embedding(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None
        cursor.lastrowid = 7
        vectors = self.admin.np.array([[0.1, 0.2]], dtype=self.admin.np.float32)
        with patch.object(self.admin, 'get_conn', return_value=connection), \
                patch.object(self.admin, 'embed_texts', return_value=vectors) as embed:
            response = self.upload(json.dumps([{'question': '  question  ', 'answer': 'answer'}] * 2).encode())
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['inserted'], 1)
        self.assertEqual(response.json['received'], 2)
        self.assertEqual(response.json['errors'], [])
        embed.assert_called_once_with(['question'])
        connection.close.assert_called_once()

    def test_valid_pdf_upload_still_extracts_embeds_and_commits(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None
        cursor.lastrowid = 8
        vectors = self.admin.np.array([[0.1, 0.2]], dtype=self.admin.np.float32)
        document = Mock(page_count=1)
        document.load_page.return_value.get_text.return_value = 'A short PDF paragraph.'
        with patch.object(self.admin, 'get_conn', return_value=connection), \
                patch.object(self.admin, 'embed_texts', return_value=vectors) as embed:
            response = self.pdf(document)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['document_id'], 8)
        self.assertEqual(response.json['chunk_count'], 1)
        embed.assert_called_once_with(['A short PDF paragraph.'])
        connection.commit.assert_called_once()
        document.close.assert_called_once()

    def test_valid_schema_boundaries_keep_types_and_password_whitespace(self):
        with self.admin_app.test_request_context('/admin/api/admin-users', method='POST', json={
            'username': 'u' * 64, 'password': ' ' + 'p' * 1022 + ' ', 'permissions': '*', 'is_admin': False,
        }):
            payload = self.validation.json_object(self.validation.USER_FIELDS, required=('username', 'password'))
        self.assertEqual(len(payload['password']), 1024)
        self.assertTrue(payload['password'].startswith(' '))
        self.assertIs(payload['is_admin'], False)
        self.assertEqual(self.validation.text(4096, 1)('m' * 4096, 'message'), 'm' * 4096)
        self.assertEqual(self.validation.integer(1, 2 ** 63 - 1)(2 ** 63 - 1, 'id'), 2 ** 63 - 1)
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {'c': 0}
        cursor.fetchall.return_value = []
        with patch.object(self.admin, 'get_conn', return_value=connection):
            response = self.admin_open('/admin/api/documents?page=10000&per_page=100')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(cursor.execute.call_args.args[1], (100, 999900))


if __name__ == '__main__':
    unittest.main()
