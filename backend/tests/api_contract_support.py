"""Offline assertions for the JSON Schema subset in the checked-in API contract."""

import json
import math
import re
from pathlib import Path


CONTRACT_PATH = Path(__file__).resolve().parents[2] / 'docs' / 'api' / 'openapi.json'
KEYWORDS = frozenset({
    '$ref', 'type', 'properties', 'required', 'additionalProperties', 'items', 'anyOf',
    'const', 'enum', 'minimum', 'maximum', 'minLength', 'maxLength', 'minItems',
    'maxItems', 'minProperties', 'pattern', 'description', 'format', 'default', 'deprecated',
})


class ApiContract:
    def __init__(self):
        self.document = json.loads(CONTRACT_PATH.read_text(encoding='utf-8'))

    def resolve(self, schema):
        if '$ref' not in schema:
            return schema
        reference = schema['$ref']
        if not reference.startswith('#/components/'):
            raise AssertionError('Only local component references are supported')
        target = self.document
        for part in reference[2:].split('/'):
            target = target[part]
        return target

    def operations(self):
        for path, methods in self.document['paths'].items():
            for method, operation in methods.items():
                yield path, method.upper(), operation

    def assert_schema(self, value, schema, location='$'):
        schema = self.resolve(schema)
        unknown = set(schema) - KEYWORDS - {key for key in schema if key.startswith('x-')}
        assert not unknown, f'Unsupported schema keywords: {unknown}'
        if 'anyOf' in schema:
            for option in schema['anyOf']:
                try:
                    self.assert_schema(value, option, location)
                    break
                except AssertionError:
                    pass
            else:
                raise AssertionError(f'{location}: no documented variant matched {value!r}')
        if 'type' in schema:
            matches = {
                'object': isinstance(value, dict), 'array': isinstance(value, list),
                'string': isinstance(value, str), 'integer': type(value) is int,
                'number': type(value) in (int, float) and math.isfinite(value),
                'boolean': type(value) is bool, 'null': value is None,
            }
            assert schema['type'] in matches, f'Unsupported type: {schema["type"]}'
            assert matches[schema['type']], f'{location}: expected {schema["type"]}, got {value!r}'
        if 'const' in schema:
            assert type(value) is type(schema['const']) and value == schema['const'], f'{location}: wrong constant'
        if 'enum' in schema:
            assert any(type(value) is type(item) and value == item for item in schema['enum']), f'{location}: wrong enum'
        for keyword, valid in (
            ('minimum', lambda bound: value >= bound), ('maximum', lambda bound: value <= bound),
            ('minLength', lambda bound: len(value) >= bound), ('maxLength', lambda bound: len(value) <= bound),
            ('minItems', lambda bound: len(value) >= bound), ('maxItems', lambda bound: len(value) <= bound),
            ('minProperties', lambda bound: len(value) >= bound),
        ):
            if keyword in schema:
                assert valid(schema[keyword]), f'{location}: violates {keyword}'
        if 'pattern' in schema:
            assert re.search(schema['pattern'], value), f'{location}: wrong string pattern'
        if isinstance(value, dict):
            assert set(schema.get('required', ())) <= value.keys(), f'{location}: missing required properties'
            properties = schema.get('properties', {})
            for key, item in value.items():
                item_schema = properties.get(key, schema.get('additionalProperties', True))
                assert item_schema is not False, f'{location}: undocumented property {key}'
                if isinstance(item_schema, dict):
                    self.assert_schema(item, item_schema, f'{location}.{key}')
        if isinstance(value, list) and 'items' in schema:
            for index, item in enumerate(value):
                self.assert_schema(item, schema['items'], f'{location}[{index}]')

    def assert_response(self, response, path, method='GET'):
        operation = self.document['paths'][path][method.lower()]
        responses = operation['responses']
        assert str(response.status_code) in responses or response.status_code >= 400, 'Undocumented success status'
        schema = self.resolve(responses.get(str(response.status_code), responses.get('default', {})))
        assert schema, f'{path}: undocumented HTTP status {response.status_code}'
        assert response.headers['API-Version'] == self.document['x-api-major']
        content = schema.get('content', {})
        if method == 'HEAD':
            assert response.data == b''
        elif content:
            assert response.mimetype in content, f'{path}: unexpected media type {response.mimetype}'
            if response.is_json:
                self.assert_schema(response.json, content[response.mimetype]['schema'])
        if response.status_code >= 400:
            assert response.headers['Cache-Control'] == 'no-store'
            if response.is_json:
                assert response.headers['X-Error-Code'] == response.json['code']
                code = response.json['code']
                error_codes = self.document['x-error-codes']
                if response.status_code in error_codes.values():
                    assert response.status_code == error_codes.get(code)
                else:
                    assert code == ('internal_error' if response.status_code >= 500 else 'request_failed')
        return response.json if response.is_json else None
