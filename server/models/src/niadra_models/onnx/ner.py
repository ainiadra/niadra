"""Token-classification NER for the PII the rules cannot see: names, places, organizations (06 4.5).

Long texts are split by the tokenizer into overlapping pieces (truncation with stride); offsets from
every piece point into the original text, and duplicates from the overlap are merged.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from niadra_models.capabilities import Span
from niadra_models.onnx.runtime import Encoding, Session, Tokenizer, feeds

# Entity names vary by model; these are the common spellings. Anything else keeps its own lowercase
# name, and `misc` is dropped because it carries no redaction meaning.
_KINDS = {
    "per": "person",
    "person": "person",
    "loc": "location",
    "location": "location",
    "gpe": "location",
    "org": "organization",
    "organization": "organization",
    "misc": None,
}


def entity_kind(entity: str) -> str | None:
    key = entity.lower()
    return _KINDS.get(key, key)


@dataclass(slots=True)
class _Open:
    kind: str
    start: int
    end: int
    scores: list[float] = field(default_factory=list)


class OnnxNer:
    def __init__(
        self,
        session: Session,
        tokenizer: Tokenizer,
        id2label: Mapping[int, str],
        version: str,
        min_score: float,
    ) -> None:
        self._session = session
        self._tokenizer = tokenizer
        self._id2label = dict(id2label)
        self._min_score = min_score
        self.version = f"onnx:{version}"

    def detect(self, texts: Sequence[str]) -> list[list[Span]]:
        if not texts:
            return []
        owners: list[int] = []
        pieces: list[Encoding] = []
        for index, encoding in enumerate(self._tokenizer.encode_batch(list(texts))):
            for piece in (encoding, *encoding.overflowing):
                owners.append(index)
                pieces.append(piece)
        logits = self._session.run(None, feeds(self._session, pieces))[0]
        found: list[list[Span]] = [[] for _ in texts]
        for owner, piece, piece_logits in zip(owners, pieces, logits, strict=True):
            found[owner].extend(self._spans(piece, piece_logits))
        return [_dedupe(spans) for spans in found]

    def _spans(self, piece: Encoding, logits: npt.NDArray[Any]) -> list[Span]:
        probs = _softmax(logits[: len(piece.ids)])
        spans: list[Span] = []
        current: _Open | None = None
        for (start, end), mask, label_id, score in zip(
            piece.offsets, piece.attention_mask, probs.argmax(-1), probs.max(-1), strict=True
        ):
            tag = self._id2label.get(int(label_id), "O")
            special = mask == 0 or start == end
            prefix, _, entity = tag.partition("-") if "-" in tag else ("I", "", tag)
            kind = None if special or tag == "O" else entity_kind(entity)
            # A B- tag on a sub-word that touches the previous token continues the same word.
            continues = (
                current is not None and kind == current.kind and (prefix == "I" or start == current.end)
            )
            if current is not None and not continues:
                self._close(current, spans)
                current = None
            if kind is None:
                continue
            if current is None:
                current = _Open(kind, start, end)
            current.end = end
            current.scores.append(float(score))
        if current is not None:
            self._close(current, spans)
        return spans

    def _close(self, entity: _Open, into: list[Span]) -> None:
        if sum(entity.scores) / len(entity.scores) >= self._min_score:
            into.append(Span(entity.start, entity.end, entity.kind))


def _softmax(logits: npt.NDArray[Any]) -> npt.NDArray[np.float64]:
    shifted = np.exp(logits - logits.max(axis=-1, keepdims=True))
    return np.asarray(shifted / shifted.sum(axis=-1, keepdims=True), dtype=np.float64)


def _dedupe(spans: list[Span]) -> list[Span]:
    """Merges overlapping spans of the same kind (the stride overlap) and keeps the result sorted."""
    merged: list[Span] = []
    for span in sorted(spans, key=lambda s: (s.start, -s.end)):
        last = merged[-1] if merged else None
        if last is not None and span.start < last.end:
            if span.kind == last.kind:
                merged[-1] = Span(last.start, max(last.end, span.end), last.kind)
            continue
        merged.append(span)
    return merged


def id2label(config: Mapping[str, Any]) -> dict[int, str]:
    return {int(k): str(v) for k, v in config.get("id2label", {}).items()}
