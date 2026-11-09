CREATE TABLE IF NOT EXISTS ingestion_jobs (
    job_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    kind ENUM('json', 'pdf') NOT NULL,
    filename VARCHAR(255) NOT NULL,
    replace_existing BOOLEAN NOT NULL DEFAULT FALSE,
    file_size INT UNSIGNED NOT NULL,
    payload_sha256 CHAR(64) CHARACTER SET ascii NOT NULL,
    submitted_by INT NULL,
    status ENUM('queued', 'running', 'completed', 'failed', 'canceled') NOT NULL DEFAULT 'queued',
    worker_id CHAR(32) CHARACTER SET ascii NULL,
    result_json JSON NULL,
    error_code VARCHAR(64) CHARACTER SET ascii NULL,
    error_status SMALLINT UNSIGNED NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    started_at DATETIME(6) NULL,
    finished_at DATETIME(6) NULL,
    INDEX ingestion_jobs_queue (status, created_at, job_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ingestion_job_payloads (
    job_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    chunk_index SMALLINT UNSIGNED NOT NULL,
    content MEDIUMBLOB NOT NULL,
    PRIMARY KEY (job_id, chunk_index),
    CONSTRAINT fk_ingestion_payload_job FOREIGN KEY (job_id)
        REFERENCES ingestion_jobs(job_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
