"""Value normalization for deterministic matching (19.2): dates, amounts and ids compare by meaning."""

import re
import unicodedata

_DATE_BR = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_DATE_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
# An amount needs a currency sign or decimal cents, so a bare order number never reads as money.
_AMOUNT = re.compile(
    r"^(?:(?:r\$|us\$|\$|€)\s*(\d{1,3}(?:\.\d{3})*|\d+)(?:,(\d{1,2}))?|(\d{1,3}(?:\.\d{3})*|\d+),(\d{2}))$"
)
_ID = re.compile(r"^(?:#|n[º°o]\.?\s*)?(\d[\w-]*|[a-z]{1,4}-?\d[\w-]*)$")
_SPACES = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s.,$/#º°€-]")


def normalize_value(value: str) -> str:
    """Lowercase, accent-free, with Brazilian dates as ISO, amounts as `1234.50` and ids without prefixes."""
    text = _strip_accents(value).lower()
    text = _SPACES.sub(" ", _PUNCT.sub("", text)).strip(" .")
    if match := _DATE_BR.match(text):
        day, month, year = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    if match := _DATE_ISO.match(text):
        return "-".join(match.groups())
    if match := _AMOUNT.match(text):
        whole = match.group(1) or match.group(3)
        cents = match.group(2) or match.group(4) or "0"
        return f"{int(whole.replace('.', ''))}.{cents.ljust(2, '0')}"
    if match := _ID.match(text):
        return match.group(1)
    return text


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
