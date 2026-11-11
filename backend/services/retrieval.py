"""Embedding/search orchestration, independent of HTTP and application startup."""

import numpy as np

from services import adapters
from persian_text import normalize_persian


class RetrievalService:
    def __init__(self, *, embed=adapters.embed_texts, search_qa=adapters.search_qa,
                 search_pdf=adapters.search_pdf, min_score=0.35):
        self.embed = embed
        self.search_qa = search_qa
        self.search_pdf = search_pdf
        self.min_score = min_score

    def retrieve(self, query, limit=6):
        query = normalize_persian(query)
        if not query:
            return []
        vector = np.asarray(self.embed([query])[0], dtype=np.float32)
        qa_results = self.search_qa(vector, k=5)
        pdf_results = self.search_pdf(vector, k=8, query=query)
        combined = [hit for hit in qa_results + pdf_results if hit['score'] >= self.min_score]
        combined.sort(key=lambda hit: hit['score'], reverse=True)
        return combined[:limit]
