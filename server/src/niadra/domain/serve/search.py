"""Ranking inside one profile (16.2): BM25 and exact cosine over a few hundred rows, fused by rank.

No approximate index and no index over ciphertext (5.14.1): the rows are decrypted in memory
and scored here.
"""

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from operator import mul

_WORD = re.compile(r"\w+")
RRF_K = 60


def tokenize(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.lower())
    return _WORD.findall("".join(ch for ch in folded if not unicodedata.combining(ch)))


def bm25(query: str, documents: Mapping[str, str], *, k1: float = 1.2, b: float = 0.75) -> dict[str, float]:
    """Scores of the documents that share at least one term with the query."""
    terms = set(tokenize(query))
    if not terms or not documents:
        return {}
    tokens = {doc_id: tokenize(text) for doc_id, text in documents.items()}
    avg_len = sum(len(t) for t in tokens.values()) / len(tokens) or 1.0
    df = Counter(term for words in tokens.values() for term in set(words) & terms)
    n = len(tokens)
    scores: dict[str, float] = {}
    for doc_id, words in tokens.items():
        tf = Counter(w for w in words if w in terms)
        score = 0.0
        for term, freq in tf.items():
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * freq * (k1 + 1) / (freq + k1 * (1 - b + b * len(words) / avg_len))
        if score > 0:
            scores[doc_id] = score
    return scores


def cosine(a: Sequence[float], b: Sequence[float], a_norm: float | None = None) -> float:
    """`a_norm` is `norm(a)`, for a question compared with many rows. The sums run in the same order as
    a plain loop, so the scores, and the ranking, keep their bits."""
    if len(a) != len(b):
        raise ValueError("vectors of different lengths")
    dot = sum(map(mul, a, b))
    product = (norm(a) if a_norm is None else a_norm) * norm(b)
    return dot / product if product else 0.0


def norm(a: Sequence[float]) -> float:
    return math.sqrt(sum(map(mul, a, a)))


def ranking(scores: Mapping[str, float]) -> list[str]:
    """Best first; ties broken by id so the same query returns the same bytes (16.5)."""
    return sorted(scores, key=lambda doc_id: (-scores[doc_id], doc_id))


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], *, k: int = RRF_K) -> dict[str, float]:
    fused: dict[str, float] = {}
    for ranked in rankings:
        for position, doc_id in enumerate(ranked, start=1):
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (k + position)
    return fused
