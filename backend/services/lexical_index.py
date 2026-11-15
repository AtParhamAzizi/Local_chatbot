"""Immutable Persian inverted indexes and a bounded cache of compatible snapshots."""

import math
import re
import threading
from collections import Counter, OrderedDict, defaultdict
from types import MappingProxyType

from persian_text import normalize_persian


TOKENIZER_VERSION = 'fa-lexical-v1'
_WORDS = re.compile(r'\w{2,}', re.UNICODE)
STOP_WORDS = frozenset(('از', 'به', 'برای', 'در', 'با', 'را', 'چه', 'چند', 'چطور', 'چگونه',
                        'آیا', 'این', 'آن', 'است', 'می', 'شود', 'تا', 'یا', 'هم'))


def tokenize(value, mode='words'):
    words = _WORDS.findall(normalize_persian(value).casefold())
    if mode == 'words':
        return words
    if mode == 'filtered-words':
        return [word for word in words if word not in STOP_WORDS]
    if mode == 'character-3':
        return [word[index:index + 3] for word in words for index in range(len(word) - 2)]
    raise ValueError('Unknown Persian lexical tokenizer')


class LexicalIndex:
    def __init__(self, documents, *, mode='words'):
        lengths, postings = {}, defaultdict(dict)
        for identifier, text in documents:
            if identifier in lengths:
                raise ValueError('Duplicate lexical source identifier')
            frequencies = Counter(tokenize(text, mode))
            lengths[identifier] = sum(frequencies.values())
            for term, frequency in frequencies.items():
                postings[term][identifier] = frequency
        self.mode = mode
        self.lengths = MappingProxyType(lengths)
        self.postings = MappingProxyType({term: MappingProxyType(values) for term, values in postings.items()})
        self.average_length = sum(lengths.values()) / len(lengths) if lengths else 0

    def scores(self, query):
        scores = defaultdict(float)
        total = len(self.lengths)
        if not total or not self.average_length:
            return {}
        for term in sorted(set(tokenize(query, self.mode))):
            matches = self.postings.get(term, {})
            weight = math.log1p((total - len(matches) + 0.5) / (len(matches) + 0.5))
            for identifier, frequency in matches.items():
                denominator = frequency + 1.2 * (0.25 + 0.75 * self.lengths[identifier] / self.average_length)
                scores[identifier] += weight * frequency * 2.2 / denominator
        return dict(scores)


class LexicalIndexCache:
    def __init__(self, capacity=2):
        if type(capacity) is not int or capacity < 1:
            raise ValueError('Lexical cache capacity must be positive')
        self.capacity = capacity
        self.entries = OrderedDict()
        self.lock = threading.Lock()
        self.build_lock = threading.Lock()

    def _get(self, key):
        with self.lock:
            result = self.entries.get(key)
            if result is not None:
                self.entries.move_to_end(key)
            return result

    def get(self, documents, *, namespace, mode='words'):
        documents = tuple(documents)
        key = (TOKENIZER_VERSION, mode, namespace, documents)
        result = self._get(key)
        if result is not None:
            return result
        with self.build_lock:
            result = self._get(key)
            if result is None:
                result = LexicalIndex(documents, mode=mode)
                with self.lock:
                    self.entries[key] = result
                    while len(self.entries) > self.capacity:
                        self.entries.popitem(last=False)
            return result

    def clear(self):
        with self.lock:
            self.entries.clear()


INDEX_CACHE = LexicalIndexCache()


def hybrid_score(semantic_score, lexical_score, *, weight, scale):
    if not 0 <= weight <= 1 or scale <= 0:
        raise ValueError('Invalid hybrid score configuration')
    lexical = lexical_score / (lexical_score + scale) if lexical_score > 0 else 0.0
    return (1 - weight) * semantic_score + weight * lexical
