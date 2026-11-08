from __future__ import annotations

import re
from dataclasses import dataclass


URL_RE = re.compile(r"https?://[^\s،؛!?؟]+", re.IGNORECASE)
TOKEN_RE = re.compile(
    r"https?://[^\s،؛!?؟]+|[\u0600-\u06ff]+|[A-Za-z]+(?:['’-][A-Za-z]+)*|\d+|[^\w\s]|\s+",
    re.IGNORECASE,
)
PERSIAN_RE = re.compile(r"[\u0600-\u06ff]")
LATIN_RE = re.compile(r"[A-Za-z]")


@dataclass(frozen=True)
class TextSegment:
    language: str
    text: str


def _word_language(token: str, previous: str | None) -> str:
    if URL_RE.fullmatch(token):
        return "english"
    if PERSIAN_RE.search(token):
        return "persian"
    if LATIN_RE.search(token):
        return "english"
    if token.isdigit():
        return previous or "english"
    return previous or "english"


def split_text(text: str) -> list[TextSegment]:
    """Split mixed text while keeping spaces and punctuation with nearby speech."""
    segments: list[TextSegment] = []
    current_language: str | None = None
    current_parts: list[str] = []

    def flush() -> None:
        nonlocal current_parts, current_language
        value = "".join(current_parts).strip()
        if value:
            segments.append(TextSegment(current_language or "english", value))
        current_parts = []

    for token in TOKEN_RE.findall(text):
        if token.isspace() or not token:
            if current_parts:
                current_parts.append(token)
            continue

        language = _word_language(token, current_language)
        if current_language is None:
            current_language = language
        elif language != current_language and (PERSIAN_RE.search(token) or LATIN_RE.search(token) or URL_RE.fullmatch(token)):
            flush()
            current_language = language
        current_parts.append(token)

    flush()
    return segments

