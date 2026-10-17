"""What one request to the in-cell models pod may carry (5.6), and how longer input still gets read whole.

The pod (`niadra_models.settings.Settings`) refuses with 413 a request of more than `MAX_TEXTS` texts or
with a text of more than `MAX_CHARS` characters. Nothing a caller sends is cut to fit: the texts go in
requests of at most `MAX_TEXTS`, and a longer text is read in overlapping windows that end at a line, a
sentence or a word. Two windows in a row share at least `OVERLAP` characters, so any value of up to that
length (an e-mail, a card, a document number) lies whole inside one window and a PII span is never lost
at a cut. The spans of the windows come back on the text's own offsets.

The release of 106d3f6 (28/09/2026): the extractions of a sandbox customer with 417 open items sent more
than 256 texts in one PII call (the last one 424), the pod answered 413 to each attempt, and the last
attempt masked every text whole.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

MAX_TEXTS = 256
"""Texts per request; the pod's `NIADRA_MODELS_MAX_TEXTS` must be at least this."""
MAX_CHARS = 20_000
"""Characters per text; the pod's `NIADRA_MODELS_MAX_CHARS` must be at least this."""
OVERLAP = 500
"""Characters two consecutive windows of a long text share: longer than any PII value the pod finds."""

_LINE = re.compile(r"\n")
_SENTENCE = re.compile(r"[.!?;…]\s")
_SPACE = re.compile(r"\s")


@dataclass(frozen=True, slots=True)
class Window:
    """A part of text number `source`, starting at `start` in it."""

    source: int
    start: int
    text: str


def windows(text: str, size: int = MAX_CHARS, overlap: int = OVERLAP) -> list[tuple[int, int]]:
    """(start, end) of each window of `text`: one when it fits, otherwise windows of at most `size`
    characters that cover every character, each ending at a line break, else a sentence end, else a space
    in the second half of its room, and each starting at least `overlap` characters before the previous
    one ends."""
    if len(text) <= size:
        return [(0, len(text))]
    overlap = min(overlap, size // 8)
    spans: list[tuple[int, int]] = []
    start = 0
    while len(text) - start > size:
        end = _cut(text, start + size // 2, start + size)
        spans.append((start, end))
        start = _begin(text, end - 2 * overlap, end - overlap)
    spans.append((start, len(text)))
    return spans


def _cut(text: str, low: int, high: int) -> int:
    """Where a window that may end anywhere in (low, high] ends: after the last line break, sentence end or
    space in that room, else at `high`."""
    room = text[low:high]
    for boundary in (_LINE, _SENTENCE, _SPACE):
        found = [m.end() for m in boundary.finditer(room)]
        if found:
            return low + found[-1]
    return high


def _begin(text: str, low: int, high: int) -> int:
    """Where the next window starts, in [low, high]: after the last line break or space there, else at
    `high`. Starting no later than `high` keeps the overlap."""
    room = text[low:high]
    for boundary in (_LINE, _SPACE):
        found = [m.end() for m in boundary.finditer(room)]
        if found:
            return low + found[-1]
    return high


def split(texts: Sequence[str], size: int = MAX_CHARS, overlap: int = OVERLAP) -> list[Window]:
    """Every text as the windows the pod reads, in order; a text that fits is one window."""
    return [
        Window(i, start, text[start:end])
        for i, text in enumerate(texts)
        for start, end in windows(text, size, overlap)
    ]


def units(texts: Sequence[str], size: int = MAX_CHARS, overlap: int = OVERLAP) -> int:
    """How many texts the pod reads for these: what a deadline per text counts."""
    return sum(1 if len(t) <= size else len(windows(t, size, overlap)) for t in texts)


def groups[T](items: Sequence[T], size: int = MAX_TEXTS) -> list[Sequence[T]]:
    """`items` in requests of at most `size`."""
    return [items[i : i + size] for i in range(0, len(items), size)] or [items]


def merge_spans(spans: Iterable[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """The spans of one text found across its windows, by start: overlapping spans of one kind joined, so a
    value two windows both found is one span."""
    merged: list[tuple[int, int, str]] = []
    last: dict[str, int] = {}
    for start, end, kind in sorted(set(spans)):
        i = last.get(kind)
        if i is not None and start <= merged[i][1]:
            merged[i] = (merged[i][0], max(merged[i][1], end), kind)
        else:
            last[kind] = len(merged)
            merged.append((start, end, kind))
    return merged
