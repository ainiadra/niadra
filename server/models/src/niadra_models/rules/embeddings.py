"""Dependency-free embeddings by character n-grams.

Feature hashing captures lexical similarity only (typos, inflections, shared names), not meaning.
It keeps search and dedup pre-filters working before a benchmarked ONNX model is configured, and it
is cheap enough to run on every text.
"""

import math
import zlib
from collections import Counter
from collections.abc import Sequence

import numpy as np

from niadra_models.capabilities import Vectors, normalized
from niadra_models.text import compact


class HashingEmbedder:
    """Character 3-5-gram term frequencies, signed-hashed into `dim` buckets and L2-normalized.

    The sign halves the bias of collisions: two grams in one bucket cancel on average instead of adding.
    CRC32 is used because Python's `hash` is salted per process and vectors must be stable across them.
    """

    def __init__(self, dim: int = 384, min_n: int = 3, max_n: int = 5) -> None:
        self.dim = dim
        self._sizes = range(min_n, max_n + 1)
        self.version = f"hash-char{min_n}{max_n}-{dim}@1"

    def embed(self, texts: Sequence[str]) -> Vectors:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            self._fill(out[row], text)
        return normalized(out)

    def _fill(self, row: Vectors, text: str) -> None:
        padded = f" {compact(text)} "
        grams = Counter(padded[i : i + n] for n in self._sizes for i in range(len(padded) - n + 1))
        for gram, count in grams.items():
            h = zlib.crc32(gram.encode())
            row[(h >> 1) % self.dim] += (1.0 + math.log(count)) * (1.0 if h & 1 else -1.0)
