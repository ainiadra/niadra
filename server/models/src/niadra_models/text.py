"""Text normalization shared by every rule-based model.

Folding keeps offsets: `fold` maps one input character to exactly one output character, so a match
found in the folded text points at the same span in the original. Rules that return spans rely on it.
"""

import unicodedata
from functools import cache

_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"), " ")


@cache
def _fold_char(ch: str) -> str:
    base = unicodedata.normalize("NFKD", ch)[:1] or ch
    low = base.lower()
    return low if len(low) == 1 else base


def fold(text: str) -> str:
    """Lowercase, strip accents and blank out zero-width characters, one character in, one out."""
    return "".join(_fold_char(ch) for ch in text.translate(_INVISIBLE))


def compact(text: str) -> str:
    """Folded text with runs of whitespace collapsed; for scoring, where offsets do not matter."""
    return " ".join(fold(unicodedata.normalize("NFKC", text)).split())
