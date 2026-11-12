"""Shared request schemas and bounded parsing, without model or database work."""

import json
import re
from functools import wraps
from tempfile import SpooledTemporaryFile

from flask import Request, g, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge, UnsupportedMediaType


MAX_BODY_BYTES = 64 * 1024
MAX_JSON_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_PDF_UPLOAD_BYTES = 50 * 1024 * 1024
MULTIPART_OVERHEAD_BYTES = 64 * 1024
MAX_MESSAGE_CHARS = 4096
MAX_ANSWER_CHARS = 16000
MAX_IMPORT_ROWS = 256
MAX_PDF_PAGES = 200
MAX_PDF_TEXT_CHARS = 500000
MAX_PDF_CHUNKS = 256
MAX_PAGE = 10000
MAX_BIGINT = 9223372036854775807
UPLOAD_LIMITS = {
    '/admin/api/import/jobs': MAX_JSON_UPLOAD_BYTES,
    '/admin/api/import/preflight': MAX_JSON_UPLOAD_BYTES,
    '/admin/api/upload': MAX_JSON_UPLOAD_BYTES,
    '/admin/api/upload/jobs': MAX_JSON_UPLOAD_BYTES,
    '/admin/api/upload/preflight': MAX_JSON_UPLOAD_BYTES,
    '/admin/api/pdf/upload': MAX_PDF_UPLOAD_BYTES,
    '/admin/api/pdf/upload/jobs': MAX_PDF_UPLOAD_BYTES,
    '/admin/api/pdf/upload/preflight': MAX_PDF_UPLOAD_BYTES,
}


class InvalidRequest(BadRequest):
    pass


class BoundedUpload(SpooledTemporaryFile):
    def __init__(self, limit):
        super().__init__(max_size=512 * 1024, mode='w+b')
        self.limit = limit
        self.received = 0

    def write(self, data):
        if self.received + len(data) > self.limit:
            raise RequestEntityTooLarge()
        self.received += len(data)
        return super().write(data)


class BoundedRequest(Request):
    max_form_memory_size = 128 * 1024
    max_form_parts = 8

    @property
    def max_content_length(self):
        upload_limit = UPLOAD_LIMITS.get(self.path)
        return upload_limit + MULTIPART_OVERHEAD_BYTES if upload_limit else MAX_BODY_BYTES

    def _get_file_stream(self, total_content_length, content_type, filename=None, content_length=None):
        upload = BoundedUpload(UPLOAD_LIMITS.get(self.path, MAX_BODY_BYTES))
        self.__dict__.setdefault('_bounded_uploads', []).append(upload)
        return upload

    def close(self):
        try:
            super().close()
        finally:
            for upload in self.__dict__.get('_bounded_uploads', ()):
                upload.close()


def configure_request_limits(app):
    app.request_class = BoundedRequest
    app.config['MAX_CONTENT_LENGTH'] = MAX_BODY_BYTES

    @app.before_request
    def enforce_request_size():
        if request.content_length is not None and request.content_length > request.max_content_length:
            raise RequestEntityTooLarge()
        if len(request.query_string) > 8192:
            raise RequestEntityTooLarge()


def text(maximum, minimum=0, *, strip=True):
    def validate(value, field):
        if not isinstance(value, str):
            raise InvalidRequest(f'{field} must be a string.')
        if len(value) > maximum:
            raise InvalidRequest(f'{field} must contain at most {maximum} characters.')
        try:
            value.encode('utf-8')
        except UnicodeEncodeError:
            raise InvalidRequest(f'{field} must be valid Unicode.') from None
        value = value.strip() if strip else value
        if len(value) < minimum:
            raise InvalidRequest(f'{field} must contain at least {minimum} characters.')
        return value
    return validate


def integer(minimum, maximum):
    def validate(value, field):
        if type(value) is not int or not minimum <= value <= maximum:
            raise InvalidRequest(f'{field} must be an integer between {minimum} and {maximum}.')
        return value
    return validate


def query_integer(minimum, maximum):
    def validate(value, field):
        if not re.fullmatch(r'[0-9]{1,19}', value):
            raise InvalidRequest(f'{field} must be an integer between {minimum} and {maximum}.')
        return integer(minimum, maximum)(int(value), field)
    return validate


def boolean(value, field):
    if type(value) is not bool:
        raise InvalidRequest(f'{field} must be a boolean.')
    return value


def choice(values):
    def validate(value, field):
        if not isinstance(value, str) or value not in values:
            raise InvalidRequest(f'{field} has an invalid value.')
        return value
    return validate


def permissions(value, field):
    if value == '*':
        return value
    if not isinstance(value, list) or len(value) > 32:
        raise InvalidRequest(f'{field} must be an array of at most 32 permission names.')
    return [text(64, 1)(item, field) for item in value]


def validate_object(payload, fields, required=(), *, nonempty=False):
    if not isinstance(payload, dict):
        raise InvalidRequest('A JSON object is required.')
    if payload.keys() - fields.keys():
        raise InvalidRequest('Unexpected request fields.')
    if set(required) - payload.keys():
        raise InvalidRequest('Required request fields are missing.')
    if nonempty and not payload:
        raise InvalidRequest('At least one field is required.')
    return {field: fields[field](value, field) for field, value in payload.items()}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidRequest('Duplicate JSON fields are not allowed.')
        result[key] = value
    return result


def _reject_constant(value):
    raise InvalidRequest('Non-finite JSON numbers are not allowed.')


def parse_json(raw):
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise InvalidRequest('Malformed UTF-8 JSON.') from None


def json_object(fields, required=(), *, nonempty=False):
    if not request.is_json:
        raise UnsupportedMediaType()
    raw = request.get_data()
    if request.content_length is None and len(raw) >= request.max_content_length:
        raise RequestEntityTooLarge()
    return validate_object(parse_json(raw), fields, required, nonempty=nonempty)


def query_schema(fields, required=()):
    def decorate(function):
        @wraps(function)
        def validated(*args, **kwargs):
            if any(len(values) != 1 for field, values in request.args.lists()):
                raise InvalidRequest('Duplicate query parameters are not allowed.')
            g.validated_query = validate_object(request.args.to_dict(), fields, required)
            return function(*args, **kwargs)
        return validated
    return decorate


def bounded_ids(**fields):
    def decorate(function):
        @wraps(function)
        def validated(*args, **kwargs):
            for field, maximum in fields.items():
                integer(1, maximum)(kwargs[field], field)
            return function(*args, **kwargs)
        return validated
    return decorate


def form_object(fields, required=()):
    if request.mimetype not in {'multipart/form-data', 'application/x-www-form-urlencoded'}:
        raise UnsupportedMediaType()
    if request.files or any(len(values) != 1 for field, values in request.form.lists()):
        raise InvalidRequest('Unexpected files or duplicate form fields.')
    return validate_object(request.form.to_dict(), fields, required)


def read_upload(extensions, limit):
    if request.mimetype != 'multipart/form-data':
        raise UnsupportedMediaType()
    if set(request.files) != {'file'} or len(request.files.getlist('file')) != 1:
        raise InvalidRequest('Exactly one file field is required.')
    if request.form.keys() - {'replace', 'csrf_token'} or any(
        len(values) != 1 for field, values in request.form.lists()
    ):
        raise InvalidRequest('Unexpected or duplicate upload fields.')
    replace_value = request.form.get('replace', 'false')
    if replace_value not in {'true', 'false', '1', '0', 'yes', 'no', 'on', 'off'}:
        raise InvalidRequest('replace must be a boolean form value.')
    upload = request.files['file']
    filename = text(255, 1)(upload.filename, 'filename')
    if '.' not in filename or '.' + filename.rsplit('.', 1)[1].lower() not in extensions:
        raise InvalidRequest('Unsupported file extension.')
    raw = upload.read(limit + 1)
    if len(raw) > limit:
        raise RequestEntityTooLarge()
    if not raw:
        raise InvalidRequest('The uploaded file is empty.')
    return raw, filename, replace_value in {'true', '1', 'yes', 'on'}


CHAT_FIELDS = {'message': text(MAX_MESSAGE_CHARS, 1), 'user_id': text(64)}
FEEDBACK_FIELDS = {'message_id': integer(1, MAX_BIGINT), 'feedback': choice(('like', 'dislike'))}
USER_FIELDS = {
    'username': text(64, 3), 'password': text(1024, 8, strip=False),
    'permissions': permissions, 'is_admin': boolean,
}
USER_UPDATE_FIELDS = {key: value for key, value in USER_FIELDS.items() if key != 'username'}
USER_UPDATE_FIELDS['is_active'] = boolean
PAGE_FIELDS = {'page': query_integer(1, MAX_PAGE), 'per_page': query_integer(1, 100), 'q': text(256)}
CONVERSATION_FIELDS = {'user_id': text(64, 1), 'ip': text(45)}
