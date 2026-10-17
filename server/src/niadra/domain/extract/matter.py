"""The matter of a conversation (17, 6.2): a short English key the customer's conversations about the same
problem share, whatever words each one used, and a label in the customer's language.

The model writes the key; this module only keeps it well formed and steers it to a key the customer
already has: `failed_delivery` and `delivery_failed`, or `internet_drops` and `internet_drop`, are one
matter. A key or label holding a digit, a bracket or an address sign is dropped: neither ever carries an
identifier, a placeholder or a withheld value.
"""

import re
from collections.abc import Sequence

from niadra.domain.serve.lexicon import fold_words, stem

MAX_KEY_WORDS = 4
MAX_KEY_CHARS = 48
MAX_LABEL_WORDS = 6
MAX_LABEL_CHARS = 48
_FILLER = frozenset({"a", "an", "the", "of", "and", "issue", "issues", "problem", "problems"})
"""Words that name no matter: `delivery_issue` and `delivery` meet, `delivery_failed` stays apart."""
_GENERIC = frozenset(
    {
        "service",
        "services",
        "product",
        "products",
        "general",
        "support",
        "customer",
        "other",
        "misc",
        "generic",
    }
)
"""A key built on one of these names no thing (`service_delayed` holds a late tow and a late repair): it is
dropped, and the count reads those conversations by their words instead."""
_UNSAFE = re.compile(r"[\d\[\]@<>{}#/\\]")


def matter_key(raw: object) -> str | None:
    """`Delivery failed`, `delivery-failed` and `delivery_failed` are `delivery_failed`; None for anything
    that is not a short key of letters."""
    if not isinstance(raw, str) or _UNSAFE.search(raw):
        return None
    words = fold_words(raw.replace("_", " ").replace("-", " "))
    if not words or len(words) > MAX_KEY_WORDS or _GENERIC & set(words):
        return None
    key = "_".join(words)
    return key if len(key) <= MAX_KEY_CHARS else None


def matter_label(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    label = " ".join(raw.split())
    if not label or _UNSAFE.search(label) or len(label) > MAX_LABEL_CHARS:
        return None
    return label if len(label.split()) <= MAX_LABEL_WORDS else None


def signature(key: str) -> frozenset[str]:
    """The stems of a key's words without filler, in any order: what two spellings of one matter share."""
    words = [w for w in key.split("_") if w not in _FILLER]
    return frozenset(stem(w) for w in words) or frozenset(stem(w) for w in key.split("_"))


def settle(
    key: str | None, label: str | None, recent: Sequence[tuple[str, str]]
) -> tuple[str | None, str | None]:
    """The key and label to store: a recent key with the same words wins, and keeps its label when the
    model gave none."""
    if key is None:
        return None, None
    mine = signature(key)
    for known, known_label in recent:
        if known == key or signature(known) == mine:
            return known, label or known_label or None
    return key, label
