"""Bounded privacy cleanup for live application tables, without loading models."""

import argparse
import atexit
import json
import logging
import threading
from datetime import datetime, timedelta, timezone

from settings import ConfigurationError, retention_settings, validate_settings


LOCK_NAME = "SHA2(CONCAT(DATABASE(), ':privacy-retention'), 256)"
OPERATIONS = (
    ('deleted_chats', 'chat_days', 'chat_history', '', 'DELETE FROM chat_history'),
    ('cleared_ips', 'ip_days', 'chat_history', "ip_address <> '' AND ",
     "UPDATE chat_history SET ip_address = ''"),
    ('cleared_feedback', 'feedback_days', 'chat_history',
     '(feedback IS NOT NULL OR feedback_at IS NOT NULL) AND ',
     'UPDATE chat_history SET feedback = NULL, feedback_at = NULL'),
    ('deleted_logs', 'log_days', 'adminlogger', '', 'DELETE FROM adminlogger'),
)


def emit_completion(record):
    message = json.dumps(record, sort_keys=True)
    logging.getLogger('chatbot.retention').warning(message)
    if record['dry_run']:
        return
    try:
        from database import logger
        level = logger.FAILED if record['status'] == 'failed' else logger.INFORMATION
        logger.logDrop(message, level)
    except Exception:
        logging.getLogger('chatbot.retention').error('retention_record_database_unavailable')


def run_retention(*, policy=None, dry_run=False, connect=None, emit=emit_completion):
    policy = policy or retention_settings()
    record = {
        'event': 'privacy_retention', 'status': 'completed', 'dry_run': dry_run,
        'finished_at': None, 'counts': {operation[0]: 0 for operation in OPERATIONS},
        'batch_limit_reached': False,
    }
    connection = None
    locked = False
    try:
        if not policy.delete_enabled and not dry_run:
            record['status'] = 'skipped_policy'
        else:
            if connect is None:
                from database import get_conn
                connect = get_conn
            connection = connect()
            with connection.cursor() as cursor:
                cursor.execute(f'SELECT GET_LOCK({LOCK_NAME}, 0) AS acquired')
                locked = cursor.fetchone()['acquired'] == 1
                if not locked:
                    record['status'] = 'skipped_locked'
                else:
                    cursor.execute("SET time_zone = '+00:00'")
                    cursor.execute('SELECT UTC_TIMESTAMP() AS cutoff_time')
                    now = cursor.fetchone()['cutoff_time']
                    for counter, setting, table, condition, mutation in OPERATIONS:
                        cutoff = now - timedelta(days=getattr(policy, setting))
                        predicate = condition + '(created_at IS NULL OR created_at <= %s)'
                        parameters = (cutoff,)
                        if getattr(policy, setting) == 0:
                            predicate = condition + '1 = 1'
                            parameters = ()
                        if dry_run:
                            cursor.execute(f'SELECT COUNT(*) AS total FROM {table} WHERE {predicate}', parameters)
                            record['counts'][counter] = int(cursor.fetchone()['total'])
                            continue
                        for _ in range(policy.max_batches):
                            connection.begin()
                            cursor.execute(f'{mutation} WHERE {predicate} ORDER BY created_at, id LIMIT %s',
                                           (*parameters, policy.batch_size))
                            affected = cursor.rowcount
                            connection.commit()
                            record['counts'][counter] += affected
                            if affected < policy.batch_size:
                                break
                        else:
                            record['batch_limit_reached'] = True
                    if record['batch_limit_reached']:
                        record['status'] = 'partial'
    except Exception:
        record['status'] = 'failed'
        record['code'] = 'retention_failed'
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass
    finally:
        if connection is not None:
            try:
                if locked:
                    with connection.cursor() as cursor:
                        cursor.execute(f'SELECT RELEASE_LOCK({LOCK_NAME})')
            except Exception:
                record['status'] = 'failed'
                record['code'] = 'retention_failed'
            finally:
                try:
                    connection.close()
                except Exception:
                    record['status'] = 'failed'
                    record['code'] = 'retention_failed'
        record['finished_at'] = datetime.now(timezone.utc).isoformat()
        emit(record)
    return record


class RetentionWorker:
    def __init__(self, policy):
        self.policy = policy
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, name='privacy-retention', daemon=True)

    def _run(self):
        while not self.stopped.is_set():
            run_retention(policy=self.policy)
            if self.stopped.wait(self.policy.interval_seconds):
                break

    def stop(self):
        self.stopped.set()


def start_retention_worker(*apps):
    existing = next((app.extensions.get('retention_worker') for app in apps
                     if app.extensions.get('retention_worker') is not None), None)
    if existing is not None:
        return existing
    worker = RetentionWorker(retention_settings())
    for app in apps:
        app.extensions['retention_worker'] = worker
    atexit.register(worker.stop)
    worker.thread.start()
    return worker


def main(argv=None):
    parser = argparse.ArgumentParser(description='Privacy retention cleanup; defaults to a read-only preview.')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true', help='Apply the configured deletion policy.')
    mode.add_argument('--dry-run', action='store_true', help='Count eligible records without deleting them (default).')
    arguments = parser.parse_args(argv)
    try:
        validate_settings()
        record = run_retention(dry_run=not arguments.apply)
    except ConfigurationError:
        print(json.dumps({'event': 'privacy_retention', 'status': 'failed', 'code': 'invalid_configuration'}))
        return 1
    print(json.dumps(record, sort_keys=True))
    return 1 if record['status'] in {'failed', 'partial', 'skipped_locked'} else 0


if __name__ == '__main__':
    raise SystemExit(main())
