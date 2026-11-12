"""Durable shadow vectors and atomic, validated embedding-index publication."""

import uuid
from contextlib import contextmanager

import numpy as np

from embedding_profile import EmbeddingBatch, EmbeddingProfile, checked_batch
from services import adapters


class IndexChanged(RuntimeError):
    """The source corpus or requested migration changed before publication."""


class InvalidIndexVector(ValueError):
    def __init__(self, kind, source_id):
        super().__init__('Stored candidate vector failed validation')
        self.kind, self.source_id = kind, source_id


def source_query(kind, *, missing=False, locking=False):
    if kind == 'qa':
        source, text, condition = 'qa_documents s', 's.question', '1=1'
    elif kind == 'pdf':
        source = 'pdf_chunks s JOIN pdf_documents d ON d.id=s.document_id'
        text, condition = 's.content', "d.status='completed'"
    else:
        raise ValueError('Unknown embedding source')
    return (
        f'SELECT s.id, {text} AS text, SHA2({text}, 256) AS source_hash, v.embedding '
        f'FROM {source} LEFT JOIN embedding_index_vectors v '
        'ON v.index_id=%s AND v.source_kind=%s AND v.source_id=s.id '
        f'AND v.source_hash=SHA2({text}, 256) '
        f'WHERE {condition} AND s.id>%s '
        + ('AND v.source_id IS NULL ' if missing else '')
        + 'ORDER BY s.id LIMIT %s' + (' FOR UPDATE' if locking else '')
    )


class MySQLIndexStore:
    def __init__(self, *, connect=adapters.connect_database):
        self.connect = connect

    @contextmanager
    def transaction(self, *, repeatable=False):
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                if repeatable:
                    cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                connection.begin()
                yield cursor
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def state(self, cursor=None, *, lock=False):
        if cursor is None:
            with self.transaction() as cursor:
                return self.state(cursor)
        cursor.execute('SELECT * FROM embedding_index_state WHERE singleton=1' + (' FOR UPDATE' if lock else ''))
        return cursor.fetchone()

    def get(self, index_id, cursor=None):
        if index_id is None:
            return None
        if cursor is None:
            with self.transaction() as cursor:
                return self.get(index_id, cursor)
        cursor.execute('SELECT * FROM embedding_indexes WHERE index_id=%s', (index_id,))
        return cursor.fetchone()

    def ensure(self, runtime, cursor=None):
        if cursor is None:
            with self.transaction() as cursor:
                return self.ensure(runtime, cursor)
        cursor.execute(
            'INSERT IGNORE INTO embedding_indexes (index_id, profile, model_id, model_path) VALUES (%s,%s,%s,%s)',
            (runtime.profile.identity, runtime.profile.to_json(), runtime.model_id, runtime.path),
        )
        return runtime.profile.identity

    def initialize(self, runtime):
        with self.transaction() as cursor:
            cursor.execute('INSERT IGNORE INTO embedding_index_state (singleton) VALUES (1)')
            state = self.state(cursor, lock=True)
            index_id = self.ensure(runtime, cursor)
            if state['active_id'] is None:
                cursor.execute('UPDATE embedding_index_state SET active_id=%s, configured_model=%s, configured_path=%s '
                               'WHERE singleton=1', (index_id, runtime.model_id, runtime.path))
            # A saved rollback/request survives restart. Otherwise detect changed artifacts,
            # tokenizer, normalization, dimensions or runtime versions on this startup.
            elif state['request_token'] is None and state['active_id'] != index_id:
                self._request(cursor, runtime.model_id, runtime.path, index_id)

    def _request(self, cursor, model_id, path, index_id=None):
        cursor.execute(
            'UPDATE embedding_index_state SET requested_model=%s, requested_path=%s, requested_id=%s, '
            'request_token=%s, error_code=NULL WHERE singleton=1', (model_id, path, index_id, uuid.uuid4().hex),
        )

    def request(self, model_id, path):
        with self.transaction() as cursor:
            self.state(cursor, lock=True)
            self._request(cursor, model_id, path)
            cursor.execute('UPDATE embedding_index_state SET configured_model=%s, configured_path=%s WHERE singleton=1',
                           (model_id, path))

    def rollback(self):
        with self.transaction() as cursor:
            state = self.state(cursor, lock=True)
            previous = self.get(state['previous_id'], cursor)
            if previous is None:
                raise ValueError('No previous embedding index is available')
            self._request(cursor, previous['model_id'], previous['model_path'], previous['index_id'])

    def retry(self):
        with self.transaction() as cursor:
            state = self.state(cursor, lock=True)
            cursor.execute("UPDATE embedding_indexes SET status='building', error_code=NULL WHERE status='failed'")
            if state['request_token']:
                self._request(cursor, state['requested_model'], state['requested_path'], state['requested_id'])

    def bind(self, runtime, token):
        with self.transaction() as cursor:
            state = self.state(cursor, lock=True)
            if state['request_token'] != token:
                raise IndexChanged('Migration request superseded')
            index_id = self.ensure(runtime, cursor)
            cursor.execute('UPDATE embedding_index_state SET requested_id=%s WHERE singleton=1', (index_id,))
        return index_id

    def pending(self, index_id, kind, limit):
        with self.transaction() as cursor:
            cursor.execute(source_query(kind, missing=True), (index_id, kind, 0, limit))
            return cursor.fetchall()

    def save(self, index_id, kind, rows, vectors):
        with self.transaction() as cursor:
            for row, vector in zip(rows, vectors, strict=True):
                cursor.execute(
                    'INSERT INTO embedding_index_vectors (index_id, source_kind, source_id, source_hash, embedding) '
                    'VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE '
                    'source_hash=VALUES(source_hash), embedding=VALUES(embedding)',
                    (index_id, kind, row['id'], row['source_hash'], vector.tobytes()),
                )
            cursor.execute(
                "UPDATE embedding_indexes SET status='building', embedded_count="
                '(SELECT COUNT(*) FROM embedding_index_vectors WHERE index_id=%s), error_code=NULL WHERE index_id=%s',
                (index_id, index_id),
            )

    def fail(self, index_id):
        with self.transaction() as cursor:
            cursor.execute("UPDATE embedding_indexes SET status='failed', error_code='index_build_failed' WHERE index_id=%s",
                           (index_id,))

    def fail_request(self, token):
        with self.transaction() as cursor:
            cursor.execute("UPDATE embedding_index_state SET error_code='index_build_failed' "
                           'WHERE singleton=1 AND request_token=%s', (token,))

    def discard_invalid(self, index_id, error):
        with self.transaction() as cursor:
            cursor.execute('DELETE FROM embedding_index_vectors WHERE index_id=%s AND source_kind=%s AND source_id=%s',
                           (index_id, error.kind, error.source_id))

    def validate_and_activate(self, index_id, *, token=None):
        """No inference here. Lock source ranges, verify every vector, then commit a pointer.

        The state lock also fences ingestion that staged a now-obsolete profile.
        Old generations remain readable by queries already holding their profile.
        """
        with self.transaction(repeatable=True) as cursor:
            state = self.state(cursor, lock=True)
            if token is not None and (state['request_token'] != token or state['requested_id'] != index_id):
                raise IndexChanged('Migration request superseded')
            if token is None and state['active_id'] != index_id:
                raise IndexChanged('Serving index changed')
            # Ordered full range locking includes end gaps: concurrent inserts,
            # replacements, PDF status changes and cascaded deletes cannot slip in.
            for table in ('qa_documents', 'pdf_documents', 'pdf_chunks'):
                cursor.execute(f'SELECT id FROM {table} ORDER BY id FOR UPDATE')
                cursor.fetchall()
            # Create the consistent-read snapshot only after acquiring source
            # locks, so validation cannot see an earlier version of the corpus.
            metadata = self.get(index_id, cursor)
            profile = EmbeddingProfile.from_json(metadata['profile'])
            count = 0
            for kind in ('qa', 'pdf'):
                after = 0
                while True:
                    cursor.execute(source_query(kind), (index_id, kind, after, 256))
                    rows = cursor.fetchall()
                    if not rows:
                        break
                    for row in rows:
                        if row['embedding'] is None:
                            raise IndexChanged('Source changed during migration; catch up before switching')
                        try:
                            vector = np.frombuffer(row['embedding'], dtype=np.float32)
                            checked_batch(EmbeddingBatch(vector.reshape(1, -1), profile), rows=1)
                        except ValueError as error:
                            raise InvalidIndexVector(kind, row['id']) from error
                    count += len(rows)
                    after = rows[-1]['id']
            cursor.execute(
                "UPDATE embedding_indexes SET status='ready', embedded_count=%s, error_code=NULL, "
                'validated_at=UTC_TIMESTAMP(6) WHERE index_id=%s', (count, index_id),
            )
            if token is not None:
                previous = state['active_id'] if state['active_id'] != index_id else state['previous_id']
                cursor.execute(
                    'UPDATE embedding_index_state SET active_id=%s, previous_id=%s, requested_id=NULL, '
                    'requested_model=NULL, requested_path=NULL, request_token=NULL, error_code=NULL WHERE singleton=1',
                    (index_id, previous),
                )

    def publication_guard(self, connection, profile):
        with connection.cursor() as cursor:
            state = self.state(cursor, lock=True)
            if state and state['active_id'] != profile.identity:
                raise IndexChanged('Embedding selection changed; retry this ingestion job')

    def status(self):
        with self.transaction() as cursor:
            state = self.state(cursor)
            cursor.execute('SELECT index_id, model_id, status, embedded_count, error_code, validated_at '
                           'FROM embedding_indexes ORDER BY created_at')
            return {'state': state, 'indexes': cursor.fetchall()}
