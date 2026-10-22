
import threading
from interface import get_embedder, ADMIN_PORT
from admin import admin_run
from user import user_run, UI_PORT
from database import init_db, init_admin_users_table, init_logger_table, logger, init_documents_chunk_table, init_documents_table, init_history_table
# from logger import logDrop
from generator import get_generator
import controller_api  # registers the admin controller page and routes

@logger.operation
def main():
    init_logger_table()
    logger.logDrop('Starting local chatbot services')
    init_db()
    init_admin_users_table()
    init_documents_table()
    init_documents_chunk_table()
    init_history_table()
    get_embedder()
    get_generator()

    logger.logDrop('Database and local models initialized')

    print(f'[admin] http://localhost:{ADMIN_PORT}')
    print(f'[ui]    http://localhost:{UI_PORT}')

    t = threading.Thread(target=user_run, daemon=True)    
    t.start()
    logger.logDrop(f'User service listening on port {UI_PORT}; admin service on port {ADMIN_PORT}')
    admin_run()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.logDrop('Application stopped by user')
        print('\nbye')
