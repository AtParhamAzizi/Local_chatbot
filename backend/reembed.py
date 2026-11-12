"""Inspect, retry or request rollback of the serving process's index migration."""

import argparse
import json
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('status', 'retry', 'rollback'))
    options = parser.parse_args(argv)
    try:
        from bootstrap import initialize_database
        from services.index_store import MySQLIndexStore
        initialize_database()
        store = MySQLIndexStore()
        if options.action != 'status':
            if store.state() is None:
                raise ValueError('Start the serving process once to initialize index management')
            getattr(store, options.action)()
        print(json.dumps(store.status(), default=str, ensure_ascii=True))
        return 0
    except Exception as error:
        print(f'Index operation failed ({type(error).__name__}). See docs/embedding-index-migration.md.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
