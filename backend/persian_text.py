"""Versioned comparison copies for Persian knowledge, never display/storage text.

This policy is deliberately not a stemmer or an identity/security normalizer.
In particular, a missing word boundary cannot be inferred safely.
"""

import re
import unicodedata


NORMALIZATION_VERSION = 'fa-v1'
_SPACE = re.compile(r'\s+')
_WORDS = re.compile(r'\w{2,}')
_TRANSLATION = str.maketrans({
    '\u064a': '\u06cc', '\u0649': '\u06cc', '\u0643': '\u06a9',
    **{chr(0x660 + number): str(number) for number in range(10)},
    **{chr(0x6f0 + number): str(number) for number in range(10)},
    '\u200c': ' ', '\u200b': ' ',  # half-space / zero-width word boundary
    **{char: None for char in '\u0640\u00ad\u200d\u2060\ufeff\u061c\u200e\u200f'
       '\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069'},
    # Optional Arabic vowel marks/tanwin/shadda/sukun, not hamza or alef letters.
    **{chr(code): None for code in range(0x64b, 0x653)},
})


def normalize_persian(value: str) -> str:
    """Return an idempotent search/hash/model copy; preserve punctuation/case.

    Compatibility normalization also decodes Persian PDF presentation forms.
    ZWNJ and ordinary whitespace share a boundary; joined words stay distinct.
    """
    value = unicodedata.normalize('NFKC', value).translate(_TRANSLATION)
    # Removing an intervening format character can expose a combining sequence.
    value = unicodedata.normalize('NFKC', value)
    return _SPACE.sub(' ', value).strip()


def persian_terms(value):
    """Lexical terms share normalization, plus the existing Unicode case fold."""
    return set(_WORDS.findall(normalize_persian(value or '').casefold()))
