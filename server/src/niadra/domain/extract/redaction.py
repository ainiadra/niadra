"""PII redaction before any third-party LLM (5.7, 5.14).

Spans come from the decision models. Each distinct value gets a stable placeholder such as `[email_1]`,
so the model can still say "the customer changed the e-mail to [email_2]"; the placeholders
are put back after validation, inside the cell. Policy may redact more, never less.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

_PLACEHOLDER = re.compile(r"\[([a-z_]+)_(\d+)\]")
REDACTED = "[redacted]"
WITHHELD_KINDS: dict[str, str] = {
    "cpf": "document",
    "cnpj": "document",
    "national_id": "document",
    "document": "document",
    "passport": "document",
    "card": "card",
    "bank_account": "account",
    "iban": "account",
}
"""PII kinds memory never keeps, whatever the model writes, and what each is withheld as."""


class Span(Protocol):
    """What the PII model returns for one detection (`PiiSpan` of the models port)."""

    @property
    def start(self) -> int: ...

    @property
    def end(self) -> int: ...

    @property
    def kind(self) -> str: ...


@dataclass(frozen=True, slots=True)
class _Cut:
    start: int
    end: int
    kind: str


class Redactor:
    def __init__(self) -> None:
        self._by_value: dict[tuple[str, str], str] = {}
        self._by_placeholder: dict[str, str] = {}
        self._counts: dict[str, int] = {}

    def redact(self, text: str, spans: Sequence[Span]) -> str:
        out: list[str] = []
        cursor = 0
        for span in _merged(spans, len(text)):
            out.append(text[cursor : span.start])
            out.append(self._placeholder(span.kind, text[span.start : span.end]))
            cursor = span.end
        out.append(text[cursor:])
        return "".join(out)

    def restore(self, text: str, withhold: Callable[[str], str] | None = None) -> str:
        """Puts the redacted values back. With `withhold`, a document, card or bank account placeholder
        comes back as the marker `withhold(kind)` returns instead: the model may write "[cpf_1]" in a
        summary, and memory must never keep what it stands for."""

        def put_back(m: re.Match[str]) -> str:
            if withhold is not None and m.group(1) in WITHHELD_KINDS:
                return withhold(WITHHELD_KINDS[m.group(1)])
            return self._by_placeholder.get(m.group(0), m.group(0))

        return _PLACEHOLDER.sub(put_back, text)

    def redacted(self) -> set[str]:
        """Every value the PII spans took out of the prompt, whatever its kind."""
        return {value for (_, value) in self._by_value}

    def withheld(self) -> dict[str, str]:
        """The values redacted as a document, a card or a bank account, with what each is."""
        return {value: WITHHELD_KINDS[kind] for (kind, value) in self._by_value if kind in WITHHELD_KINDS}

    def _placeholder(self, kind: str, value: str) -> str:
        kind = re.sub(r"[^a-z_]", "_", kind.lower()) or "pii"
        key = (kind, value)
        if key not in self._by_value:
            self._counts[kind] = self._counts.get(kind, 0) + 1
            placeholder = f"[{kind}_{self._counts[kind]}]"
            self._by_value[key] = placeholder
            self._by_placeholder[placeholder] = value
        return self._by_value[key]


def _merged(spans: Sequence[Span], length: int) -> list[_Cut]:
    """Sorted, clipped and non-overlapping: overlapping detections widen, never shrink, the redaction."""
    merged: list[_Cut] = []
    for span in sorted(spans, key=lambda s: (s.start, -s.end)):
        start, end = max(0, span.start), min(length, span.end)
        if start >= end:
            continue
        if merged and start < merged[-1].end:
            last = merged[-1]
            merged[-1] = _Cut(last.start, max(last.end, end), last.kind)
        else:
            merged.append(_Cut(start, end, span.kind))
    return merged
