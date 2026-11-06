"""Administrator model selection HTTP routes."""

from flask import current_app, jsonify

from database import logger
from request_validation import json_object, text


@logger.operation
def admin_models():
    return jsonify(current_app.extensions['model_lifecycle'].catalog())


@logger.operation
def admin_set_model():
    data = json_object({'model_id': text(64, 1)}, required=('model_id',))
    try:
        active_model = current_app.extensions['model_lifecycle'].select_model(data['model_id'])
        return jsonify({
            'ok': True,
            'active_model': active_model,
            'message': 'Model selection updated. New requests will use the selected model.',
        })
    except FileNotFoundError:
        return jsonify({'error': 'Selected model files are unavailable.'}), 409
    except ValueError:
        return jsonify({'error': 'Invalid model selection.'}), 400
    except Exception:
        return jsonify({'error': 'Unable to change model.'}), 500


def register_routes(app):
    app.add_url_rule('/admin/api/models', view_func=admin_models, methods=['GET'])
    app.add_url_rule('/admin/api/models', view_func=admin_set_model, methods=['POST'])
