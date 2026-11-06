"""Administrator logs HTTP routes."""

from flask import jsonify

from database import logger
from services.adapters import connect_database as get_conn
from settings import safe_error_message


@logger.operation
def get_admin_logs():
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    id,
                    _level,
                    _from,
                    message,
                    warninglevel,
                    created_at
                FROM adminlogger
                ORDER BY created_at DESC, id DESC
            """)
            rows = cur.fetchall()
        return jsonify({
            "success": True,
            "logs": rows
        }), 200
    except Exception as error:
        logger.logDrop(f'Unable to read application logs: {safe_error_message(error)}', logger.FAILED)
        return jsonify({
            "success": False,
            "message": "Unable to read application logs.",
            "logs": []
        }), 500
    finally:
        if conn:
            conn.close()


def register_routes(app):
    app.add_url_rule('/admin/api/logs', view_func=get_admin_logs, methods=['GET'])
