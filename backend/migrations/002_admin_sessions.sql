ALTER TABLE `admin-users` ADD COLUMN session_version BIGINT UNSIGNED NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS admin_sessions (
    token_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    user_id INT NOT NULL,
    session_version BIGINT UNSIGNED NOT NULL,
    expires_at BIGINT UNSIGNED NOT NULL,
    last_seen_at BIGINT UNSIGNED NOT NULL,
    INDEX idx_admin_session_expiry (expires_at),
    INDEX idx_admin_session_activity (last_seen_at),
    FOREIGN KEY (user_id) REFERENCES `admin-users` (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
