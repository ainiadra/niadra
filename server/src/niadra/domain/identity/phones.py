"""Phone numbers in E.164, with the mobile prefixes that make one line look like two (5.3).

Brazil added a leading 9 to every mobile number; WhatsApp still reports many accounts by the
legacy 8-digit subscriber number. Mobile subscribers start with 6-9, landlines with 2-5, so
only the former gain the 9: a landline never does. Mexico dropped its mobile `1` in 2019 and
Argentina dials its mobiles with a `9`, and WhatsApp keeps both in its ids (`521...`, `549...`):
the prefix is dropped, since the ten digits after the country code already name one line.

The same rules are the canonical destination of the suppression-list spec (3.1), so a key the
server writes and a key an SDK computes for one number agree.
"""

import re
import unicodedata

from niadra.domain.common.errors import InvalidInput

BRAZIL = "55"
_SEPARATORS = re.compile(r"[\s().\-/]")
_DIGITS = re.compile(r"^\d+$")
_TRUNK_ZERO = re.compile(r"\(0\)")
_TEL = re.compile(r"^tel:", re.IGNORECASE)
AREA_CODES = frozenset(
    str(code)
    for tens, units in (
        (1, "123456789"),
        (2, "12478"),
        (3, "1234578"),
        (4, "123456789"),
        (5, "1345"),
        (6, "123456789"),
        (7, "134579"),
        (8, "123456789"),
        (9, "123456789"),
    )
    for code in (f"{tens}{unit}" for unit in units)
)
"""Brazil's area codes (DDD) in use."""
_MOBILE_PREFIXES = {"52": "1", "54": "9"}
"""Country code -> the digit WhatsApp and old dialing plans put before a ten-digit mobile number."""


def normalize_phone(value: str, default_country: str = BRAZIL) -> str:
    """Returns `+<digits>`; national Brazilian numbers without a country code get `default_country`."""
    raw = _TEL.sub("", clean_text(value))
    international = raw.startswith("+") or raw.startswith("00")
    if international:
        raw = _TRUNK_ZERO.sub("", raw)  # `+44 (0) 20 ...`: the national trunk zero is never dialed
    raw = _SEPARATORS.sub("", raw)
    digits = raw[1:] if raw.startswith("+") else raw[2:] if international else raw
    if not _DIGITS.match(digits):
        raise InvalidInput("not a phone number")
    if not international and default_country == BRAZIL and (national := _brazilian_national(digits)):
        digits = BRAZIL + national
    if not 8 <= len(digits) <= 15 or digits.startswith("0"):
        raise InvalidInput("not a phone number")
    if digits.startswith(BRAZIL):
        digits = BRAZIL + _brazilian_subscriber(digits[len(BRAZIL) :])
    prefix = _MOBILE_PREFIXES.get(digits[:2])
    if prefix is not None and len(digits) == 13 and digits[2] == prefix:
        digits = digits[:2] + digits[3:]
    return "+" + digits


def line_forms(value: str, default_country: str = BRAZIL) -> frozenset[str]:
    """The digits one line is written with: as given, in E.164 (`normalize_phone`) and in every form that
    names the same line, a Brazilian mobile without its ninth digit and a Mexican or Argentine one with its
    mobile prefix. A handle keeps the E.164 form while a person writes any of them; only the digits as given
    when the value is no phone number."""
    digits = "".join(c for c in value if c.isdigit())
    forms = {digits} if digits else set()
    try:
        canonical = normalize_phone(value, default_country)[1:]
    except InvalidInput:
        return frozenset(forms)
    forms.add(canonical)
    national = canonical[len(BRAZIL) :]
    if canonical.startswith(BRAZIL) and len(national) == 11 and national[2] == "9" and national[3] in "6789":
        forms.add(BRAZIL + national[:2] + national[3:])
    prefix = _MOBILE_PREFIXES.get(canonical[:2])
    if prefix is not None and len(canonical) == 12:
        forms.add(canonical[:2] + prefix + canonical[2:])
    return frozenset(forms)


def clean_text(value: str) -> str:
    """Compatibility forms folded (full-width digits) and invisible format characters dropped: WhatsApp
    wraps a copied number in direction marks, and spreadsheets leave zero-width spaces."""
    folded = unicodedata.normalize("NFKC", value)
    return "".join(c for c in folded if unicodedata.category(c) != "Cf").strip()


def _brazilian_national(digits: str) -> str | None:
    """The national number a string of digits dialed in Brazil names: after the trunk `0`, and after
    a carrier code when one was dialed (`0 21 11 98765-4321`)."""
    national = digits.lstrip("0")
    if _is_brazilian_national(national):
        return national
    carrier = digits.startswith("0") and len(national) in (12, 13) and national[0] != "0"
    if carrier and _is_brazilian_national(national[2:]):
        return national[2:]
    return None


def _is_brazilian_national(national: str) -> bool:
    """Ten digits for a landline or a mobile from before the ninth digit, eleven for a mobile, which
    always starts with 9: `14155550123` is a number of another country written without its `+`."""
    if national[:2] not in AREA_CODES:
        return False
    if len(national) == 10:
        return national[2] in "23456789"
    return len(national) == 11 and national[2] == "9"


def _brazilian_subscriber(national: str) -> str:
    if len(national) == 10 and national[:2] in AREA_CODES and national[2] in "6789":
        return national[:2] + "9" + national[2:]  # legacy mobile: add the ninth digit
    return national
