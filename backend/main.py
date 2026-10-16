
import threading
from interface import get_embedder, ADMIN_PORT
from admin import admin_run
from user import user_run, UI_PORT
from database import init_db, init_logger_table, logger, init_documents_chunk_table, init_documents_table, init_history_table
# from logger import logDrop
from generator import get_generator

Loggerlevel = 'main'

def main():
    init_db()
    init_logger_table()
    logger.logDrop(Loggerlevel, 'main', 'db initialized successfuly', 1)
    init_documents_table()
    init_documents_chunk_table()
    init_history_table()
    get_embedder()
    get_generator()


    print(f'[admin] http://localhost:{ADMIN_PORT}')
    print(f'[ui]    http://localhost:{UI_PORT}')

    t = threading.Thread(target=user_run, daemon=True)    
    t.start()
    admin_run()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nbye')