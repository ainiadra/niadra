"""Masking of production samples before they reach the assistant's LLM (22.4).

Masking is pseudonymization, not anonymization: it keeps the shape a configuration needs (keys,
types, repeated codes such as event types) and replaces every value that could identify someone.
Every value it replaces is also returned, so the caller can check that none comes back in a diff.
"""

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

_PATTERNS = (
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("document", re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b|\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")),
    ("phone", re.compile(r"\+?\d[\d\s().-]{7,}\d")),
    ("url", re.compile(r"https?://\S+")),
    ("number", re.compile(r"\b\d{4,}\b")),
)
# Mid-sentence capitalized words are most often names; sentence starts are left alone.
_NAME = re.compile(r"(?<=[a-zà-ú0-9,;:] )[A-ZÀ-Ú][a-zà-ú]+(?: [A-ZÀ-Ú][a-zà-ú]+)*")
# Codes are lowercase or all caps (`order.created`, `PAID`); Title Case words are more often names.
_CODE = re.compile(r"^(?:[a-z][a-z0-9_.:-]{0,39}|[A-Z][A-Z0-9_.:-]{0,39})$")
_ISO_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}([t ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(z|[+-]\d{2}:?\d{2})?)?$", re.I)
# Ids and timestamps look like phone numbers to the patterns above; the leak guard ignores them.
_NOT_PERSONAL = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
    r"|\b\d{4}-\d{2}-\d{2}(?:[t ][\d:.]+(?:z|[+-]\d{2}:?\d{2})?)?",
    re.I,
)
KEEP_CODES_SEEN_IN = 3
"""A code-like string is kept only when at least this many samples share it at the same path."""


@dataclass
class Masked:
    samples: list[Any]
    removed: set[str] = field(default_factory=set)


def mask_text(text: str, removed: set[str] | None = None) -> str:
    def replace(kind: str) -> Any:
        def sub(match: re.Match[str]) -> str:
            if removed is not None:
                removed.add(match.group(0))
            return f"<{kind}>"

        return sub

    for kind, pattern in _PATTERNS:
        text = pattern.sub(replace(kind), text)
    return _NAME.sub(replace("name"), text)


def mask_texts(texts: Iterable[str]) -> Masked:
    removed: set[str] = set()
    return Masked([mask_text(t, removed) for t in texts], removed)


def mask_payloads(payloads: Sequence[Mapping[str, Any]]) -> Masked:
    """Keys and structure stay; leaves become `<string:...>`, `<number>` or `<datetime>`, codes stay."""
    shared = _shared_codes(payloads)
    removed: set[str] = set()
    return Masked([_mask_node(p, "", shared, removed) for p in payloads], removed)


DISCRIMINATORS = frozenset(
    {"type", "event", "event_type", "eventtype", "kind", "action", "topic", "status", "object", "resource"}
)
"""Keys whose code-like value says what the event is. In a single example there is no repetition to tell a
code from a value, so only these keep theirs: a mapping must be able to match `type == 'invoice.paid'`."""


def mask_example(payload: Mapping[str, Any]) -> Masked:
    """One example event a person typed: masked like the samples, keeping only the codes of the
    keys that name the event's kind."""
    shared = {
        (path, value)
        for path, value in _leaves(payload, "")
        if path.rsplit(".", 1)[-1].lower() in DISCRIMINATORS and _CODE.match(value)
    }
    removed: set[str] = set()
    return Masked([_mask_node(payload, "", shared, removed)], removed)


def contains_removed(document: str, removed: Iterable[str], min_length: int = 4) -> bool:
    """True when a masked value came back; codes are skipped, since a diff may name an event type."""
    lowered = document.lower()
    return any(
        len(value) >= min_length and not _CODE.match(value) and value.lower() in lowered for value in removed
    )


def is_code(value: str) -> bool:
    return bool(_CODE.match(value))


def looks_personal(document: str) -> bool:
    document = _NOT_PERSONAL.sub(" ", document)
    return any(
        pattern.search(document) for kind, pattern in _PATTERNS if kind in {"email", "document", "phone"}
    )


def _mask_node(node: Any, path: str, shared: set[tuple[str, str]], removed: set[str]) -> Any:
    if isinstance(node, Mapping):
        masked = {}
        for index, (key, value) in enumerate(node.items()):
            name = str(key) if _CODE.match(str(key)) else f"<key{index}>"
            if name != str(key):
                removed.add(str(key))
            masked[name] = _mask_node(value, f"{path}.{name}", shared, removed)
        return masked
    if isinstance(node, list):
        return [_mask_node(item, f"{path}[]", shared, removed) for item in node[:3]]
    if isinstance(node, bool) or node is None:
        return node
    if isinstance(node, int | float):
        removed.add(str(node))
        return "<number>"
    text = str(node)
    if (path, text) in shared:
        return text
    removed.add(text)
    return "<datetime>" if _ISO_TIME.match(text) else f"<string:{_shape(text)}>"


def _shape(text: str) -> str:
    if looks_personal(text):
        return "personal"
    if text.isdigit():
        return "digits"
    return "text" if " " in text else "token"


def _shared_codes(payloads: Sequence[Mapping[str, Any]]) -> set[tuple[str, str]]:
    counts: Counter[tuple[str, str]] = Counter()
    for payload in payloads:
        counts.update(set(_leaves(payload, "")))
    return {leaf for leaf, n in counts.items() if n >= KEEP_CODES_SEEN_IN and _CODE.match(leaf[1])}


def _leaves(node: Any, path: str) -> Iterable[tuple[str, str]]:
    if isinstance(node, Mapping):
        for key, value in node.items():
            name = str(key) if _CODE.match(str(key)) else None
            if name is not None:
                yield from _leaves(value, f"{path}.{name}")
    elif isinstance(node, list):
        for item in node:
            yield from _leaves(item, f"{path}[]")
    elif isinstance(node, str):
        yield (path, node)
