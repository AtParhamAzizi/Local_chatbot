from __future__ import annotations

import re
from dataclasses import dataclass


URL_RE = re.compile(r"https?://[^\s،؛؟<>\"“”]+", re.IGNORECASE)
TOKEN_RE = re.compile(
    URL_RE.pattern
    + r"|[\u0600-\u06ff\u200c]+|[^\W\d_\u0600-\u06ff]+(?:['’-][^\W\d_\u0600-\u06ff]+)*|\d+|_+|[^\w\s]|\s+",
    re.IGNORECASE,
)
PERSIAN_RE = re.compile(r"[\u0600-\u06ff]")


@dataclass(frozen=True)
class TextSegment:
    language: str
    text: str


def _word_language(token: str, previous: str | None) -> str | None:
    if URL_RE.fullmatch(token):
        return "english"
    if any(character.isalnum() and PERSIAN_RE.fullmatch(character) for character in token):
        return "persian"
    if any(character.isalpha() for character in token):
        return "english"
    return previous


def split_text(text: str) -> list[TextSegment]:
    """Split mixed text while keeping spaces and punctuation with nearby speech."""
    segments: list[TextSegment] = []
    current_language: str | None = None
    current_parts: list[str] = []

    def flush() -> None:
        nonlocal current_parts, current_language
        value = "".join(current_parts).strip()
        if any(character.isalnum() for character in value):
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
        elif language != current_language:
            flush()
            current_language = language
        current_parts.append(token)

    flush()
    return segments

