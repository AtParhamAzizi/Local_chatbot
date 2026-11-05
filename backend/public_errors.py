"""Allowlisted public failures, independent of exceptions and infrastructure."""


ERRORS = {
    'invalid_request': (400, 'The request is invalid.'),
    'authentication_required': (401, 'Authentication is required.'),
    'forbidden': (403, 'This operation is not permitted.'),
    'not_found': (404, 'The requested resource was not found.'),
    'method_not_allowed': (405, 'This method is not permitted.'),
    'conflict': (409, 'The request conflicts with the current state.'),
    'payload_too_large': (413, 'The request exceeds the supported limits.'),
    'unsupported_media_type': (415, 'The request content type is not supported.'),
    'rate_limited': (429, 'Too many requests. Please try again later.'),
    'internal_error': (500, 'Request failed. Please try again.'),
    'service_unavailable': (503, 'The service is temporarily unavailable. Please try again.'),
    'voice_loading': (503, 'The voice model is loading. Please try again shortly.'),
    'voice_unavailable': (503, 'Voice transcription is temporarily unavailable.'),
    'voice_model_failed': (503, 'The voice model could not be loaded.'),
    'transcription_failed': (500, 'Transcription failed. Please try again.'),
}
STATUS_CODES = {
    400: 'invalid_request', 401: 'authentication_required', 403: 'forbidden',
    404: 'not_found', 405: 'method_not_allowed', 409: 'conflict',
    413: 'payload_too_large', 415: 'unsupported_media_type', 429: 'rate_limited',
    500: 'internal_error', 503: 'service_unavailable',
}


def public_error(status, code=None):
    if not isinstance(code, str) or code not in ERRORS or ERRORS[code][0] != status:
        code = STATUS_CODES.get(status, 'internal_error' if status >= 500 else 'request_failed')
    message = ERRORS[code][1] if code in ERRORS else 'The request could not be completed.'
    return {'code': code, 'error': message}
