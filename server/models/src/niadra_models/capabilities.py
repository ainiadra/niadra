"""One interface per capability; the rule backends and the ONNX backends both implement them.

Every call is synchronous and CPU-bound on purpose: the batcher runs it in the thread pool, so an
implementation never has to think about the event loop. Every model names its `version`, which goes
into each response so a stored decision or vector can be traced to what produced it (5.6).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

Vectors = npt.NDArray[np.float32]


def normalized(rows: npt.NDArray[Any]) -> Vectors:
    """Rows scaled to unit length as float32; all-zero rows (empty text) stay zero."""
    rows = rows.astype(np.float32, copy=False)
    norms = np.linalg.norm(rows, axis=1, keepdims=True)
    out: Vectors = np.divide(rows, norms, out=np.zeros_like(rows), where=norms > 0)
    return out


@dataclass(frozen=True, slots=True)
class Span:
    start: int
    end: int
    kind: str


class Embedder(Protocol):
    version: str
    dim: int

    def embed(self, texts: Sequence[str]) -> Vectors:
        """One L2-normalized row per text, so a dot product is the cosine."""
        ...


class PiiDetector(Protocol):
    version: str

    def detect(self, texts: Sequence[str]) -> list[list[Span]]:
        """Non-overlapping spans per text, sorted by start, as offsets into the original string."""
        ...
