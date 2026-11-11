import os
import hashlib
import inspect
import logging
import threading
from functools import wraps
from flask import request
import pymysql.cursors
from settings import database_config, redact_secrets, retention_settings, safe_error_message
from public_errors import public_error
from persian_text import normalize_persian


DB_CONFIG = {
    **database_config(),
    'charset':  'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor,
    'autocommit': True,
}


class logger:
    INFORMATION = 1
    WARNING = 2
    FAILED = 3
    _UNSET = object()
    _state = threading.local()

    @staticmethod
    def logDrop(level=None, log_from=_UNSET, message=_UNSET, warninglevel=1):
        if message is logger._UNSET:
            message = level
            if log_from is not logger._UNSET:
                warninglevel = log_from

        frame = inspect.currentframe()
        try:
            caller = frame.f_back
            filename = os.path.basename(caller.f_code.co_filename)
            function_name = caller.f_code.co_name
        finally:
            del frame
            del caller

        return logger._write(filename, function_name, message, warninglevel)

    @staticmethod
    def _write(filename, function_name, message, warninglevel):
        if type(warninglevel) is not int or warninglevel not in (1, 2, 3):
            raise ValueError('warninglevel must be 1 (information), 2 (warning), or 3 (failed)')
        if retention_settings().log_days == 0:
            return False
        if getattr(logger._state, 'writing', False):
            return False

        message = redact_secrets(message)
        logger._state.writing = True
        connection = None
        try:
            connection = get_conn()
            with connection.cursor() as cursor:
                cursor.execute(
                    'INSERT INTO adminlogger (_level, _from, message, warninglevel) '
                    'VALUES (%s, %s, %s, %s)',
                    (filename, function_name, str(message), warninglevel),
                )
            connection.commit()
            return True
        except Exception as error:
            logging.getLogger('chatbot.audit').error(
                'Database logging unavailable (%s); [%s:%s] [%s] %s',
                type(error).__name__, filename, function_name, warninglevel,
                message,
            )
            return False
        finally:
            try:
                if connection is not None:
                    connection.close()
            except Exception:
                logging.getLogger('chatbot.audit').error('Unable to close the log connection')
            finally:
                logger._state.writing = False

    @staticmethod
    def operation(function):
        original = inspect.unwrap(function)
        filename = os.path.basename(original.__code__.co_filename)
        function_name = original.__name__

        @wraps(function)
        def wrapped(*args, **kwargs):
            try:
                result = function(*args, **kwargs)
            except Exception as error:
                logger._write(
                    filename, function_name,
                    f'{function_name} failed: {safe_error_message(error)}',
                    logger.FAILED,
                )
                raise

            response = result[0] if isinstance(result, tuple) and result else result
            status = getattr(response, 'status_code', None)
            if isinstance(result, tuple) and len(result) > 1 and isinstance(result[1], int):
                status = result[1]
            warninglevel = logger.INFORMATION
            message = f'{function_name} completed'
            if isinstance(status, int):
                if status >= 500:
                    warninglevel = logger.FAILED
                elif status >= 400:
                    warninglevel = logger.WARNING
                message = f'{function_name} returned HTTP {status}'
                if status >= 400 and hasattr(response, 'get_json'):
                    payload = response.get_json(silent=True)
                    if isinstance(payload, dict):
                        code = public_error(status, payload.get('code'))['code']
                        message = f'{message}: {code}'
            logger._write(filename, function_name, message, warninglevel)
            return result

        return wrapped


def get_conn():
    try:
        return pymysql.connect(**DB_CONFIG)
    except Exception as error:
        logger.logDrop(
            f'Database connection failed: {safe_error_message(error)}',
            logger.FAILED,
        )
        raise


def get_client_ip():
    return request.remote_addr


def hash_question(q: str) -> str:
    return hashlib.sha256(normalize_persian(q).encode('utf-8')).hexdigest()


#Hash of documents
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

