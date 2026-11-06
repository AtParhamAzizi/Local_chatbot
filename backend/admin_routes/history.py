"""Administrator history HTTP routes."""

from flask import jsonify, request

from database import logger
from request_validation import CONVERSATION_FIELDS, MAX_BIGINT, query_integer, query_schema, text
from services.adapters import connect_database as get_conn
from settings import safe_error_message


@query_schema({'limit': query_integer(1, 1000), 'q': text(256)})
@logger.operation
def admin_chat_sessions():
    try:
        # -----------------------------
        # Parameters
        # -----------------------------
        try:
            limit = int(request.args.get('limit', 300))
        except (TypeError, ValueError):
            return jsonify({
                'error': 'پارامتر limit نامعتبر است'
            }), 400
        limit = max(1, min(1000, limit))
        search_query = (request.args.get('q') or '').strip()
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                # -----------------------------------------
                # Search sessions
                # -----------------------------------------
                if search_query:
                    like = f'%{search_query}%'
                    sql = """
                        SELECT
                            user_id,
                            ip_address,
                            COUNT(*) AS message_count,
                            COALESCE(
                                SUM(
                                    CASE
                                        WHEN feedback = 'like'
                                        THEN 1
                                        ELSE 0
                                    END
                                ),
                                0
                            ) AS likes,
                            COALESCE(
                                SUM(
                                    CASE
                                        WHEN feedback = 'dislike'
                                        THEN 1
                                        ELSE 0
                                    END
                                ),
                                0
                            ) AS dislikes,
                            MIN(created_at) AS started_at,
                            MAX(created_at) AS last_activity
                        FROM chat_history
                        WHERE
                            user_id LIKE %s
                            OR ip_address LIKE %s
                            OR user_message LIKE %s
                            OR bot_response LIKE %s
                        GROUP BY
                            user_id,
                            ip_address
                        ORDER BY
                            last_activity DESC
                        LIMIT %s
                    """
                    cur.execute(
                        sql,
                        (
                            like,
                            like,
                            like,
                            like,
                            limit
                        )
                    )
                # -----------------------------------------
                # Get all sessions
                # -----------------------------------------
                else:
                    sql = """
                        SELECT
                            user_id,
                            ip_address,
                            COUNT(*) AS message_count,
                            COALESCE(
                                SUM(
                                    CASE
                                        WHEN feedback = 'like'
                                        THEN 1
                                        ELSE 0
                                    END
                                ),
                                0
                            ) AS likes,
                            COALESCE(
                                SUM(
                                    CASE
                                        WHEN feedback = 'dislike'
                                        THEN 1
                                        ELSE 0
                                    END
                                ),
                                0
                            ) AS dislikes,
                            MIN(created_at) AS started_at,
                            MAX(created_at) AS last_activity
                        FROM chat_history
                        GROUP BY
                            user_id,
                            ip_address
                        ORDER BY
                            last_activity DESC
                        LIMIT %s
                    """
                    cur.execute(sql, (limit,))
                sessions = cur.fetchall()
                # -----------------------------------------
                # Add first message preview
                # -----------------------------------------
                for session in sessions:
                    cur.execute(
                        """
                        SELECT user_message
                        FROM chat_history
                        WHERE
                            user_id = %s
                            AND ip_address = %s
                        ORDER BY
                            created_at ASC,
                            id ASC
                        LIMIT 1
                        """,
                        (
                            session['user_id'],
                            session['ip_address']
                        )
                    )
                    first_message = cur.fetchone()
                    session['first_message'] = (
                        first_message['user_message']
                        if first_message
                        else None
                    )
                    # -------------------------------------
                    # Normalize numbers
                    # -------------------------------------
                    session['message_count'] = int(
                        session.get('message_count') or 0
                    )
                    session['likes'] = int(
                        session.get('likes') or 0
                    )
                    session['dislikes'] = int(
                        session.get('dislikes') or 0
                    )
                    # -------------------------------------
                    # Serialize dates
                    # -------------------------------------
                    if session.get('started_at'):
                        session['started_at'] = (
                            session['started_at']
                            .isoformat(sep=' ')
                        )
                    if session.get('last_activity'):
                        session['last_activity'] = (
                            session['last_activity']
                            .isoformat(sep=' ')
                        )
        finally:
            conn.close()
        return jsonify({
            'sessions': sessions,
            'count': len(sessions)
        }), 200
    except Exception as error:
        logger.logDrop(f'Unable to list chat sessions: {safe_error_message(error)}', logger.FAILED)
        return jsonify({
            'error': 'خطا در دریافت گفتگوها',
            'details': 'Request failed.'
        }), 500


@query_schema(CONVERSATION_FIELDS, required=('user_id',))
@logger.operation
def admin_chat_messages():
    """
    Get messages belonging to a session.
    JS calls:
    GET /admin/api/chat/messages
        ?user_id=...
        &ip=...
    """
    try:
        user_id = (
            request.args.get('user_id') or ''
        ).strip()
        ip = (
            request.args.get('ip') or ''
        ).strip()
        # -----------------------------------------
        # Validate user_id
        # -----------------------------------------
        if not user_id:
            return jsonify({
                'error': 'پارامتر user_id الزامی است'
            }), 400
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                # -------------------------------------
                # Specific user + IP
                # -------------------------------------
                if ip:
                    cur.execute(
                        """
                        SELECT
                            id,
                            user_id,
                            ip_address,
                            user_message,
                            bot_response,
                            feedback,
                            feedback_at,
                            created_at
                        FROM chat_history
                        WHERE
                            user_id = %s
                            AND ip_address = %s
                        ORDER BY
                            created_at ASC,
                            id ASC
                        """,
                        (
                            user_id,
                            ip
                        )
                    )
                # -------------------------------------
                # User only
                # -------------------------------------
                else:
                    cur.execute(
                        """
                        SELECT
                            id,
                            user_id,
                            ip_address,
                            user_message,
                            bot_response,
                            feedback,
                            feedback_at,
                            created_at
                        FROM chat_history
                        WHERE
                            user_id = %s
                        ORDER BY
                            created_at ASC,
                            id ASC
                        """,
                        (user_id,)
                    )
                messages = cur.fetchall()
        finally:
            conn.close()
        # -----------------------------------------
        # Convert database values to JSON values
        # -----------------------------------------
        for message in messages:
            message['id'] = int(
                message.get('id') or 0
            )
            if message.get('created_at'):
                message['created_at'] = (
                    message['created_at']
                    .isoformat(sep=' ')
                )
            if message.get('feedback_at'):
                message['feedback_at'] = (
                    message['feedback_at']
                    .isoformat(sep=' ')
                )
        return jsonify({
            'messages': messages,
            'count': len(messages)
        }), 200
    except Exception as error:
        logger.logDrop(f'Unable to list chat messages: {safe_error_message(error)}', logger.FAILED)
        return jsonify({
            'error': 'خطا در دریافت پیام‌ها',
            'details': 'Request failed.'
        }), 500


@logger.operation
def admin_chat_stats():
    """
    Get global chat statistics.
    JS calls:
    GET /admin/api/chat/stats
    """
    try:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        COUNT(*) AS total_messages,
                        COUNT(
                            DISTINCT user_id,
                            ip_address
                        ) AS total_sessions,
                        COUNT(
                            DISTINCT ip_address
                        ) AS total_ips,
                        COALESCE(
                            SUM(
                                CASE
                                    WHEN feedback = 'like'
                                    THEN 1
                                    ELSE 0
                                END
                            ),
                            0
                        ) AS likes,
                        COALESCE(
                            SUM(
                                CASE
                                    WHEN feedback = 'dislike'
                                    THEN 1
                                    ELSE 0
                                END
                            ),
                            0
                        ) AS dislikes
                    FROM chat_history
                    """
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            return jsonify({
                'total_messages': 0,
                'total_sessions': 0,
                'total_ips': 0,
                'likes': 0,
                'dislikes': 0
            }), 200
        return jsonify({
            'total_messages': int(
                row.get('total_messages') or 0
            ),
            'total_sessions': int(
                row.get('total_sessions') or 0
            ),
            'total_ips': int(
                row.get('total_ips') or 0
            ),
            'likes': int(
                row.get('likes') or 0
            ),
            'dislikes': int(
                row.get('dislikes') or 0
            )
        }), 200
    except Exception as error:
        logger.logDrop(f'Unable to read chat statistics: {safe_error_message(error)}', logger.FAILED)
        return jsonify({
            'error': 'خطا در دریافت آمار',
            'details': 'Request failed.'
        }), 500


@query_schema(CONVERSATION_FIELDS, required=('user_id',))
@logger.operation
def admin_chat_delete_session():
    try:
        user_id = (
            request.args.get('user_id') or ''
        ).strip()
        ip = (
            request.args.get('ip') or ''
        ).strip()
        if not user_id:
            return jsonify({
                'error': 'پارامتر user_id الزامی است'
            }), 400
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                if ip:
                    cur.execute(
                        """
                        DELETE FROM chat_history
                        WHERE
                            user_id = %s
                            AND ip_address = %s
                        """,
                        (
                            user_id,
                            ip
                        )
                    )
                else:
                    cur.execute(
                        """
                        DELETE FROM chat_history
                        WHERE user_id = %s
                        """,
                        (user_id,)
                    )
                deleted = cur.rowcount
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        if deleted <= 0:
            return jsonify({
                'error': 'گفتگویی یافت نشد'
            }), 404
        return jsonify({
            'ok': True,
            'deleted': deleted
        }), 200
    except Exception as error:
        logger.logDrop(f'Unable to delete a chat session: {safe_error_message(error)}', logger.FAILED)
        return jsonify({
            'error': 'خطا در حذف گفتگو',
            'details': 'Request failed.'
        }), 500


@query_schema({'id': query_integer(1, MAX_BIGINT)}, required=('id',))
@logger.operation
def admin_chat_delete_message():
    try:
        raw_id = request.args.get('id', '0')
        try:
            log_id = int(raw_id)
        except (TypeError, ValueError):
            return jsonify({
                'error': 'پارامتر id نامعتبر است'
            }), 400
        if log_id <= 0:
            return jsonify({
                'error': 'پارامتر id الزامی است'
            }), 400
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM chat_history
                    WHERE id = %s
                    """,
                    (log_id,)
                )
                deleted = cur.rowcount
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        if deleted <= 0:
            return jsonify({
                'error': 'پیام یافت نشد'
            }), 404
        return jsonify({
            'ok': True,
            'deleted': log_id
        }), 200
    except Exception as error:
        logger.logDrop(f'Unable to delete a chat message: {safe_error_message(error)}', logger.FAILED)
        return jsonify({
            'error': 'خطا در حذف پیام',
            'details': 'Request failed.'
        }), 500


def register_routes(app):
    app.add_url_rule('/admin/api/chat/sessions', view_func=admin_chat_sessions, methods=['GET'])
    app.add_url_rule('/admin/api/chat/messages', view_func=admin_chat_messages, methods=['GET'])
    app.add_url_rule('/admin/api/chat/stats', view_func=admin_chat_stats, methods=['GET'])
    app.add_url_rule('/admin/api/chat/sessions', view_func=admin_chat_delete_session, methods=['DELETE'])
    app.add_url_rule('/admin/api/chat/logs', view_func=admin_chat_delete_message, methods=['DELETE'])
