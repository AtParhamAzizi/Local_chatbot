"""Lazy production adapters; importing a service never starts external work."""


def connect_database():
    from database import get_conn
    return get_conn()


def embed_texts(texts):
    from interface import embed_texts as embed
    return embed(texts)


def embed_batch(texts):
    from services.index_migration import current_migration_service
    service = current_migration_service()
    if service is not None:
        return service.embed(texts)
    from interface import embed_batch as embed
    return embed(texts)


def embedding_dim():
    from services.index_migration import current_migration_service
    service = current_migration_service()
    if service is not None:
        return service.runtime(service.store.state()['active_id']).profile.dimension
    from interface import embedding_dim as dimension
    return dimension()


def search_qa(vector, **options):
    from searchForAnswer import _search_qa
    from services.index_migration import current_migration_service
    if current_migration_service() is not None:
        options['index_id'] = options['profile'].identity
    return _search_qa(vector, **options)


def search_pdf(vector, **options):
    from searchForAnswer import _search_pdf
    from services.index_migration import current_migration_service
    if current_migration_service() is not None:
        options['index_id'] = options['profile'].identity
    return _search_pdf(vector, **options)


def guard_embedding_publication(connection, profile):
    from services.index_migration import current_migration_service
    service = current_migration_service()
    if service is not None:
        service.store.publication_guard(connection, profile)


def open_pdf(**options):
    import fitz
    return fitz.open(**options)


def hash_question(question):
    from database import hash_question as question_hash
    return question_hash(question)


def hash_document(raw):
    from database import sha256_bytes
    return sha256_bytes(raw)


def generate_reply(question, hits):
    from generator import generate_reply as generate
    return generate(question, hits)


def audit(message, warninglevel=1):
    from database import logger
    return logger.logDrop(message, warninglevel)
