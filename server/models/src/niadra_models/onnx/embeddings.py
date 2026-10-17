from collections.abc import Sequence
from typing import Any

import numpy as np
import numpy.typing as npt

from niadra_models.capabilities import Vectors, normalized
from niadra_models.onnx.runtime import Session, Tokenizer, feeds


class OnnxEmbedder:
    """Sentence-transformer embeddings: mean of the token states under the attention mask, L2-normalized.

    Texts run in chunks of similar length, shortest first: on a CPU the padding of a batch to its longest
    text is most of the cost, and a small chunk bounds the memory of the attention scores. Graphs
    exported with pooling already inside return (batch, dim) and are used as they come.
    """

    def __init__(
        self, session: Session, tokenizer: Tokenizer, dim: int, version: str, chunk: int = 16
    ) -> None:
        self._session = session
        self._tokenizer = tokenizer
        self._chunk = chunk
        self.dim = dim
        self.version = f"onnx:{version}"

    def embed(self, texts: Sequence[str]) -> Vectors:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        if not texts:
            return out
        encodings = self._tokenizer.encode_batch(list(texts))
        by_length = sorted(range(len(encodings)), key=lambda i: len(encodings[i].ids))
        for start in range(0, len(by_length), self._chunk):
            rows = by_length[start : start + self._chunk]
            inputs = feeds(self._session, [encodings[i] for i in rows])
            output = self._session.run(None, inputs)[0]
            pooled = output if output.ndim == 2 else _mean_pool(output, inputs["attention_mask"])
            out[rows] = normalized(pooled)
        return out


def _mean_pool(states: npt.NDArray[Any], mask: npt.NDArray[np.int64]) -> npt.NDArray[Any]:
    weights = mask[..., None].astype(states.dtype)
    pooled: npt.NDArray[Any] = (states * weights).sum(axis=1) / np.clip(weights.sum(axis=1), 1e-9, None)
    return pooled
