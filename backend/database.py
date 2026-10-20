import pymysql
import os
import hashlib
import inspect
import logging
import threading
from functools import wraps
from flask import request
import pymysql
import pymysql.cursors


DB_CONFIG = {
    'host':     os.getenv('DB_HOST', 'localhost'),
    'port':     int(os.getenv('DB_PORT', 3306)),
    'user':     os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASS', '159632@qw'),
    'database': os.getenv('DB_NAME', 'chatbot_db'),
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
        if getattr(logger._state, 'writing', False):
            return False

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
                type(error).__name__, filename, function_name, warninglevel, message,
            )
            return False
        finally:
            try:
                if connection is not None:
                    connection.close()
            except Exception:
                logging.getLogger('chatbot.audit').exception('Unable to close the log connection')
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
                    f'{function_name} failed: {type(error).__name__}: {error}',
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
                        detail = payload.get('error') or payload.get('message')
                        if detail:
                            message = f'{message}: {detail}'
            logger._write(filename, function_name, message, warninglevel)
            return result

        return wrapped


def get_conn():
    try:
        return pymysql.connect(**DB_CONFIG)
    except Exception as error:
        logger.logDrop(f'Database connection failed: {type(error).__name__}', logger.FAILED)
        raise


@logger.operation
def init_db():
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS qa_documents (
                id             INT AUTO_INCREMENT PRIMARY KEY,
                question       TEXT        NOT NULL,
                answer         TEXT        NOT NULL,
                question_hash  CHAR(64)    NOT NULL UNIQUE,
                embedding      LONGBLOB    NULL,
                embedding_dim  INT         NULL,
                created_at     TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
                updated_at     TIMESTAMP   DEFAULT CURRENT_TIMESTAMP
                                           ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_created (created_at)
            ) ENGINE=InnoDB
              DEFAULT CHARSET=utf8mb4
              COLLATE=utf8mb4_unicode_ci
        """)
    conn.close()


@logger.operation
def init_logger_table():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS adminlogger (
                id             INT AUTO_INCREMENT PRIMARY KEY,
                _level          TEXT        NOT NULL,
                _from         TEXT        NOT NULL,
                message        TEXT        NOT NULL,
                warninglevel   INT         NOT NULL,
                created_at     TIMESTAMP   DEFAULT CURRENT_TIMESTAMP
                                           ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_created (created_at)
            ) ENGINE=InnoDB
              DEFAULT CHARSET=utf8mb4
              COLLATE=utf8mb4_unicode_ci
            """)
            cur.execute("SHOW COLUMNS FROM adminlogger LIKE 'message'")
            column = cur.fetchone()
            if column and column['Type'].lower().startswith(('char(', 'varchar(')):
                cur.execute('ALTER TABLE adminlogger MODIFY COLUMN message TEXT NOT NULL')
    finally:
        conn.close()


def get_client_ip():
    forwarded = request.headers.get('X-Forwarded-For')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.remote_addr



@logger.operation
def init_documents_table():
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pdf_documents (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            filename VARCHAR(255) NOT NULL,
            document_hash CHAR(64) NOT NULL UNIQUE,
            file_size BIGINT NOT NULL,
            page_count INT NOT NULL DEFAULT 0,
            chunk_count INT NOT NULL DEFAULT 0,
            embedded_chunk_count INT NOT NULL DEFAULT 0,
            embedding_dim INT NULL,
            status ENUM('processing', 'completed', 'failed') NOT NULL DEFAULT 'processing',
            error_message TEXT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                ON UPDATE CURRENT_TIMESTAMP
        );
        """)
    conn.close()


@logger.operation
def init_documents_chunk_table():
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pdf_chunks (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            document_id BIGINT NOT NULL,
            chunk_index INT NOT NULL,
            page_start INT NOT NULL,
            page_end INT NOT NULL,
            content LONGTEXT NOT NULL,
            embedding LONGBLOB NULL,
            embedding_dim INT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            UNIQUE KEY uq_pdf_chunk (document_id, chunk_index),

            CONSTRAINT fk_pdf_chunks_document
                FOREIGN KEY (document_id)
                REFERENCES pdf_documents(id)
                ON DELETE CASCADE
        );
        """)
    conn.close()




@logger.operation
def init_history_table():
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_history (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            user_id VARCHAR(64) NOT NULL,
            ip_address VARCHAR(45) NOT NULL,
            user_message TEXT NOT NULL,
            bot_response TEXT NOT NULL,
            feedback ENUM('like', 'dislike') DEFAULT NULL,
            feedback_at TIMESTAMP NULL DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_user_id (user_id),
            INDEX idx_ip (ip_address),
            INDEX idx_created_at (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """)
    conn.close()


def hash_question(q: str) -> str:
    return hashlib.sha256(q.strip().encode('utf-8')).hexdigest()


#Hash of documents
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

