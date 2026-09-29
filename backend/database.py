import pymysql
import os
import hashlib
from flask import request
# from logger import logDrop

Loggerlevel = 'database'
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


def get_conn():
    # logDrop(Loggerlevel, 'get_conn', 'config got loaded succesfuly', 1)
    return pymysql.connect(**DB_CONFIG)


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


def init_logger_table():
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS adminlogger (
                id             INT AUTO_INCREMENT PRIMARY KEY,
                _level          TEXT        NOT NULL,
                _from         TEXT        NOT NULL,
                message        CHAR(64)    NOT NULL,
                warninglevel   INT         NOT NULL,
                created_at     TIMESTAMP   DEFAULT CURRENT_TIMESTAMP
                                           ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_created (created_at)
            ) ENGINE=InnoDB
              DEFAULT CHARSET=utf8mb4
              COLLATE=utf8mb4_unicode_ci
        """)
    conn.close()


def get_client_ip():
    forwarded = request.headers.get('X-Forwarded-For')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.remote_addr



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
            feedback_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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

class logger:
    def logDrop(level, log_from, message, warninglevel ):
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adminlogger "
                "  (_level, _from, message, warninglevel) "
                "VALUES (%s, %s, %s, %s)",
                (level, log_from, message, warninglevel)
            )
        conn.close()


    