"""Embedding/search orchestration, independent of HTTP and application startup."""

from services import adapters
from persian_text import NORMALIZATION_VERSION, normalize_persian
from embedding_profile import checked_batch


class RetrievalService:
    def __init__(self, *, embed=adapters.embed_batch, search_qa=adapters.search_qa,
                 search_pdf=adapters.search_pdf, min_score=0.35):
        self.embed = embed
        self.search_qa = search_qa
        self.search_pdf = search_pdf
        self.min_score = min_score

    def retrieve(self, query, limit=6):
        query = normalize_persian(query)
        if not query:
            return []
        vectors, profile = checked_batch(self.embed([query]), rows=1)
        if profile.normalization_version != NORMALIZATION_VERSION:
            raise ValueError('Embedding profile disagrees with retrieval normalization')
        vector = vectors[0]
        qa_results = self.search_qa(vector, k=5, profile=profile)
        pdf_results = self.search_pdf(vector, k=8, query=query, profile=profile)
        combined = [hit for hit in qa_results + pdf_results if hit['score'] >= self.min_score]
        combined.sort(key=lambda hit: hit['score'], reverse=True)
        return combined[:limit]
