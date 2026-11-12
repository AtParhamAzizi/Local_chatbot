"""Lazy production adapters; importing a service never starts external work."""


def connect_database():
    from database import get_conn
    return get_conn()


def embed_texts(texts):
    from interface import embed_texts as embed
    return embed(texts)


def embed_batch(texts):
    from interface import embed_batch as embed
    return embed(texts)


def embedding_dim():
    from interface import embedding_dim as dimension
    return dimension()


def search_qa(vector, **options):
    from searchForAnswer import _search_qa
    return _search_qa(vector, **options)


def search_pdf(vector, **options):
    from searchForAnswer import _search_pdf
    return _search_pdf(vector, **options)


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
