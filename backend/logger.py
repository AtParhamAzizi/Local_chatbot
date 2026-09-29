# from database import get_conn

# def init_logger_table():
#     conn = get_conn()
#     with conn.cursor() as cur:
#         cur.execute("""
#             CREATE TABLE IF NOT EXISTS adminlogger (
#                 id             INT AUTO_INCREMENT PRIMARY KEY,
#                 level          TEXT        NOT NULL,
#                 from           TEXT        NOT NULL,
#                 message        CHAR(64)    NOT NULL UNIQUE,
#                 warninglevel   INT         NOT NULL,
#                 created_at     TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
#                                            ON UPDATE CURRENT_TIMESTAMP,
#                 INDEX idx_created (created_at)
#             ) ENGINE=InnoDB
#               DEFAULT CHARSET=utf8mb4
#               COLLATE=utf8mb4_unicode_ci
#         """)
#     conn.close()


# def logDrop(level, log_from, message, warninglevel ):
#     conn = get_conn()
#     with conn.cursor() as cur:
#         cur.execute(
#             "INSERT INTO adminlogger "
#             "  (level, from, message, warninglevel) "
#             "VALUES (%s, %s, %s, %s)",
#             (level, log_from, message, warninglevel)
#         )
#     conn.close()