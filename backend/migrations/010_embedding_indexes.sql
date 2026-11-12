CREATE TABLE IF NOT EXISTS embedding_indexes (
    index_id CHAR(64) CHARACTER SET ascii PRIMARY KEY,
    profile JSON NOT NULL,
    model_id VARCHAR(64) NOT NULL,
    model_path TEXT NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'building',
    embedded_count BIGINT NOT NULL DEFAULT 0,
    error_code VARCHAR(64) NULL,
    validated_at TIMESTAMP(6) NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS embedding_index_vectors (
    index_id CHAR(64) CHARACTER SET ascii NOT NULL,
    source_kind VARCHAR(3) CHARACTER SET ascii NOT NULL,
    source_id BIGINT NOT NULL,
    source_hash CHAR(64) CHARACTER SET ascii NOT NULL,
    embedding LONGBLOB NOT NULL,
    PRIMARY KEY (index_id, source_kind, source_id),
    FOREIGN KEY (index_id) REFERENCES embedding_indexes(index_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS embedding_index_state (
    singleton INT PRIMARY KEY,
    active_id CHAR(64) CHARACTER SET ascii NULL,
    previous_id CHAR(64) CHARACTER SET ascii NULL,
    requested_id CHAR(64) CHARACTER SET ascii NULL,
    requested_model VARCHAR(64) NULL,
    requested_path TEXT NULL,
    request_token CHAR(32) CHARACTER SET ascii NULL,
    configured_model VARCHAR(64) NULL,
    configured_path TEXT NULL,
    error_code VARCHAR(64) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
