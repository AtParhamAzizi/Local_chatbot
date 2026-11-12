"""Authorized asynchronous upload submission, polling, cancellation and retry."""

from flask import current_app, g, jsonify, request, url_for

from abuse_controls import limit_requests
from database import logger
from request_validation import MAX_JSON_UPLOAD_BYTES, MAX_PDF_UPLOAD_BYTES, query_schema, read_upload
from services.ingestion import IngestionError
from services.importers import EXTENSIONS, MAX_IMPORT_BYTES, clean_filename


UPLOADS = {'json': ({'.json'}, MAX_JSON_UPLOAD_BYTES), 'pdf': ({'.pdf'}, MAX_PDF_UPLOAD_BYTES),
           'import': (set(EXTENSIONS), MAX_IMPORT_BYTES)}
JOB_ENDPOINTS = {'json': 'admin_qa_job', 'pdf': 'admin_pdf_job', 'import': 'admin_import_job'}


@query_schema({})
@limit_requests('upload')
@logger.operation
def submit_job(kind):
    extensions, maximum = UPLOADS[kind]
    raw, filename, replace = read_upload(extensions, maximum)
    if kind == 'import':
        filename = clean_filename(filename)
    try:
        job = current_app.extensions['ingestion_jobs'].submit(
            kind, raw, filename, replace=replace, submitted_by=g.admin_user['id'],
        )
    except IngestionError as error:
        return jsonify(error.payload), error.status
    endpoint = JOB_ENDPOINTS[kind]
    response = jsonify(job)
    response.status_code = 202
    response.headers['Location'] = url_for(endpoint, job_id=job['job_id'])
    response.headers['Retry-After'] = '1'
    return response


@query_schema({})
@limit_requests('upload')
@logger.operation
def preflight_upload(kind):
    extensions, maximum = UPLOADS[kind]
    raw, filename, replace = read_upload(extensions, maximum)
    return jsonify(current_app.extensions['ingestion_service'].preflight(raw, filename, kind=kind, replace=replace))


@query_schema({})
@logger.operation
def job_status(job_id, kind):
    jobs = current_app.extensions['ingestion_jobs']
    try:
        job = jobs.cancel(job_id, kind) if request.method == 'DELETE' else jobs.get(job_id, kind)
        return jsonify(job)
    except IngestionError as error:
        return jsonify(error.payload), error.status


@query_schema({})
@limit_requests('upload')
@logger.operation
def retry_job(job_id, kind):
    if request.get_data(cache=False):
        return jsonify(error='Retry does not accept a request body.'), 400
    try:
        job = current_app.extensions['ingestion_jobs'].retry(job_id, kind)
        response = jsonify(job)
        response.status_code = 202
        response.headers['Location'] = url_for(JOB_ENDPOINTS[kind], job_id=job_id)
        response.headers['Retry-After'] = '1'
        return response
    except IngestionError as error:
        return jsonify(error.payload), error.status


def register_routes(app):
    for prefix, kind, name in (('/admin/api/upload/jobs', 'json', 'qa'),
                               ('/admin/api/pdf/upload/jobs', 'pdf', 'pdf'),
                               ('/admin/api/import/jobs', 'import', 'import')):
        app.add_url_rule(prefix.removesuffix('/jobs') + '/preflight', endpoint=f'admin_{name}_preflight',
                         view_func=preflight_upload, defaults={'kind': kind}, methods=['POST'])
        app.add_url_rule(prefix, endpoint=f'admin_{name}_submit_job', view_func=submit_job,
                         defaults={'kind': kind}, methods=['POST'])
        app.add_url_rule(prefix + '/<job_id>', endpoint=f'admin_{name}_job', view_func=job_status,
                         defaults={'kind': kind}, methods=['GET', 'DELETE'])
        app.add_url_rule(prefix + '/<job_id>/retry', endpoint=f'admin_{name}_retry_job', view_func=retry_job,
                         defaults={'kind': kind}, methods=['POST'])
