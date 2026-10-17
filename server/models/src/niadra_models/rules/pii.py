"""Deterministic PII detection: the first of the two PII layers, run over every text (06 4.5).

Every identifier with a check digit is validated, so a number that only looks like a CPF, a card or an
IBAN is not redacted. Patterns run over folded text (see `text.fold`), which keeps offsets.
When two rules claim overlapping characters, the rule listed first wins.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from niadra_models.capabilities import PiiDetector, Span
from niadra_models.text import fold

VERSION = "pii-rules@3"

# Brazilian area codes in use (Anatel); a number with another prefix is not a Brazilian phone.
_BR_AREA_CODES = frozenset(
    [
        "11",
        "12",
        "13",
        "14",
        "15",
        "16",
        "17",
        "18",
        "19",
        "21",
        "22",
        "24",
        "27",
        "28",
        "31",
        "32",
        "33",
        "34",
        "35",
        "37",
        "38",
        "41",
        "42",
        "43",
        "44",
        "45",
        "46",
        "47",
        "48",
        "49",
        "51",
        "53",
        "54",
        "55",
        "61",
        "62",
        "63",
        "64",
        "65",
        "66",
        "67",
        "68",
        "69",
        "71",
        "73",
        "74",
        "75",
        "77",
        "79",
        "81",
        "82",
        "83",
        "84",
        "85",
        "86",
        "87",
        "88",
        "89",
        "91",
        "92",
        "93",
        "94",
        "95",
        "96",
        "97",
        "98",
        "99",
    ]
)


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _mod11_digit(values: Sequence[int], weights: Sequence[int]) -> int:
    rest = sum(v * w for v, w in zip(values, weights, strict=True)) % 11
    return 0 if rest < 2 else 11 - rest


def cpf_valid(value: str) -> bool:
    d = [int(c) for c in _digits(value)]
    if len(d) != 11 or len(set(d)) == 1:
        return False
    first = _mod11_digit(d[:9], range(10, 1, -1))
    second = _mod11_digit([*d[:9], first], range(11, 1, -1))
    return d[9:] == [first, second]


def cnpj_valid(value: str) -> bool:
    """Numeric and the alphanumeric CNPJ of 2026: each character counts as its ASCII code minus 48."""
    chars = [c for c in value.upper() if c.isalnum()]
    if len(chars) != 14 or not "".join(chars[12:]).isdigit() or len(set(chars)) == 1:
        return False
    values = [ord(c) - 48 for c in chars]
    weights = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    first = _mod11_digit(values[:12], weights[1:])
    second = _mod11_digit([*values[:12], first], weights)
    return values[12:] == [first, second]


def luhn_valid(value: str) -> bool:
    d = [int(c) for c in _digits(value)]
    if not 13 <= len(d) <= 19 or d[0] not in (2, 3, 4, 5, 6):
        return False
    total = 0
    for i, digit in enumerate(reversed(d)):
        doubled = digit * 2 if i % 2 else digit
        total += doubled - 9 if doubled > 9 else doubled
    return total % 10 == 0


def iban_valid(value: str) -> bool:
    compacted = "".join(value.split()).upper()
    if not 15 <= len(compacted) <= 34:
        return False
    rearranged = compacted[4:] + compacted[:4]
    return int("".join(str(int(c, 36)) for c in rearranged)) % 97 == 1


def br_phone_valid(value: str) -> bool:
    d = _digits(value)
    if len(d) in (12, 13) and d.startswith("55"):
        d = d[2:]
    if d[:2] not in _BR_AREA_CODES:
        return False
    local = d[2:]
    if len(local) == 9:
        return local[0] == "9"
    # Ten bare digits are as often an order or protocol number; a landline needs some formatting.
    return len(local) == 8 and local[0] in "2345" and not value.isdigit()


def nanp_phone_valid(value: str) -> bool:
    d = _digits(value)
    d = d[1:] if len(d) == 11 and d[0] == "1" else d
    return len(d) == 10 and d[0] in "23456789" and d[3] in "23456789"


def e164_valid(value: str) -> bool:
    d = _digits(value)
    if d.startswith("55"):
        return br_phone_valid(d) or _br_legacy_mobile(d[2:])
    return 8 <= len(d) <= 15


def _br_legacy_mobile(national: str) -> bool:
    """A mobile from before the ninth digit, eight digits from 6 to 9 after the area code: written after the
    country code it is a number to call, as the server's phone normalization reads it."""
    return len(national) == 10 and national[:2] in _BR_AREA_CODES and national[2] in "6789"


def ssn_valid(value: str) -> bool:
    """A US Social Security number as the SSA issues them: no area 000, 666 or 9xx, no zero group."""
    d = _digits(value)
    return len(d) == 9 and d[:3] not in ("000", "666") and d[0] != "9" and d[3:5] != "00" and d[5:] != "0000"


def sin_valid(value: str) -> bool:
    """A Canadian Social Insurance Number: nine digits, Luhn, never starting with 0 or 8."""
    d = [int(c) for c in _digits(value)]
    if len(d) != 9 or d[0] in (0, 8):
        return False
    total = sum((x * 2 - 9 if x * 2 > 9 else x * 2) if i % 2 else x for i, x in enumerate(d))
    return total % 10 == 0


_NINO_PREFIXES = frozenset({"BG", "GB", "NK", "KN", "TN", "NT", "ZZ"})


def nino_valid(value: str) -> bool:
    """A UK National Insurance number, written in capitals as HMRC prints it."""
    compacted = "".join(value.split())
    if compacted != compacted.upper() or len(compacted) != 9:
        return False
    first, second = compacted[0], compacted[1]
    return compacted[:2] not in _NINO_PREFIXES and first not in "DFIQUV" and second not in "DFIOQUV"


_DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"


def dni_es_valid(value: str) -> bool:
    """A Spanish DNI or NIE (X, Y or Z first), with its check letter."""
    compacted = "".join(ch for ch in value.upper() if ch.isalnum())
    if len(compacted) != 9:
        return False
    head = compacted[:-1]
    if head[0] in "XYZ":
        head = str("XYZ".index(head[0])) + head[1:]
    return head.isdigit() and compacted[-1] == _DNI_LETTERS[int(head) % 23]


def nif_pt_valid(value: str) -> bool:
    """A Portuguese NIF: nine digits, mod 11."""
    d = [int(c) for c in _digits(value)]
    if len(d) != 9 or d[0] not in (1, 2, 3, 5, 6, 8, 9):
        return False
    rest = sum(x * (9 - i) for i, x in enumerate(d[:8])) % 11
    return d[8] == (0 if rest < 2 else 11 - rest)


def rut_valid(value: str) -> bool:
    """A Chilean RUT or RUN: the body and its mod 11 check digit (K for 10)."""
    compacted = "".join(ch for ch in value.upper() if ch.isalnum())
    body, check = compacted[:-1], compacted[-1:]
    if not body.isdigit() or not 7 <= len(body) <= 8:
        return False
    total = sum(int(x) * (2 + i % 6) for i, x in enumerate(reversed(body)))
    expected = 11 - total % 11
    return check == {11: "0", 10: "K"}.get(expected, str(expected))


_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def aadhaar_valid(value: str) -> bool:
    """An Indian Aadhaar number: twelve digits, never starting with 0 or 1, Verhoeff check."""
    d = _digits(value)
    if len(d) != 12 or d[0] in "01":
        return False
    check = 0
    for i, ch in enumerate(reversed(d)):
        check = _VERHOEFF_D[check][_VERHOEFF_P[i % 8][int(ch)]]
    return check == 0


def _uppercase(value: str) -> bool:
    return value == value.upper()


def _capitalized_street(value: str) -> bool:
    # "20 minutes on the road" is not an address; "221B Baker Street" and "12 Main st" are.
    return all(w[0].isupper() or w[0].isdigit() for w in value.split()[:-1])


def _always(_: str) -> bool:
    return True


# What a dialed number writes before its last digits: a country code after `+` or an area code in parentheses,
# then groups of digits. Any country writes a phone number so, and no country a postal code.
_DIALED = re.compile(r"(?:\+\d+|\(\d+\))(?:[ .-]?\(\d+\)|[ .-]\d+)*[ .-]?$")
_DIALED_REACH = 24
"""How far before a candidate the dialed part is looked for: a country code, an area code and a group."""


@dataclass(frozen=True, slots=True)
class Rule:
    kind: str
    pattern: re.Pattern[str]
    valid: Callable[[str], bool] = _always
    standalone: bool = False
    """Never the end of a dialed number: the digits of `+55 11 91234-567` or `(11) 91234-567` are a phone
    number written in groups of its own, found as one, never as a postal code."""

    def spans(self, original: str, folded: str) -> list[Span]:
        found = []
        group = "v" if "v" in self.pattern.groupindex else 0
        for match in self.pattern.finditer(folded):
            start, end = match.span(group)
            if self.standalone and (dialed := _DIALED.search(folded, max(0, start - _DIALED_REACH), start)):
                found.append(Span(dialed.start(), end, "phone"))
                continue
            if self.valid(original[start:end]):
                found.append(Span(start, end, self.kind))
        return found


_B = r"(?<![\w./+-])"  # no digit, letter or joiner right before: never match inside a longer token
_E = r"(?![\w-])"
_SEP = r"[ .-]?"

_PT_ES_STREET = (
    r"rua|r\.|avenida|av\.?|avda\.?|travessa|tv\.|alameda|al\.|estrada|rodovia|praca|largo|"
    r"calle|paseo|plaza|carrera|camino"
)
_EN_STREET = r"street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct|way|place|pl|terrace"
_COMPLEMENT = r"apto|apartamento|ap|bloco|bl|casa|sala|conjunto|cj|piso|puerta|depto|dpto|apt|suite|unit"

RULES: tuple[Rule, ...] = (
    Rule("email", re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}(?!\w)")),
    Rule(
        "cnpj",
        re.compile(
            _B + r"(?:\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}"
            r"|[0-9a-z]{2}\.[0-9a-z]{3}\.[0-9a-z]{3}/[0-9a-z]{4}-\d{2})" + _E
        ),
        cnpj_valid,
    ),
    Rule("cpf", re.compile(_B + r"\d{3}\.?\d{3}\.?\d{3}[-.]?\d{2}" + _E), cpf_valid),
    # Documents of other countries, each with its check digit or its written form.
    Rule("national_id", re.compile(_B + r"\d{3}-\d{2}-\d{4}" + _E), ssn_valid),
    Rule("national_id", re.compile(r"(?<!\w)[a-z]{2} ?\d{2} ?\d{2} ?\d{2} ?[a-d](?!\w)"), nino_valid),
    Rule("national_id", re.compile(_B + r"[xyz]?\d{7,8}-?[a-z]" + _E), dni_es_valid),
    Rule("national_id", re.compile(r"(?<!\w)[a-z]{4}\d{6}[hm][a-z]{5}[a-z\d]\d(?!\w)"), _uppercase),
    Rule("national_id", re.compile(_B + r"\d{1,2}\.\d{3}\.\d{3}-[\dk]" + _E), rut_valid),
    Rule("national_id", re.compile(_B + r"[2-9]\d{3} \d{4} \d{4}" + _E), aadhaar_valid),
    Rule(
        "national_id",
        re.compile(
            r"(?<!\w)(?:nif|contribuinte|nif/nipc)[:.º°]?\s*(?:n[.º°o]?\s*)?(?P<v>\d{3} ?\d{3} ?\d{3})(?!\w)"
        ),
        nif_pt_valid,
    ),
    # Written next to their name, so a bare number of the same length is never taken for one.
    Rule(
        "national_id",
        re.compile(
            r"(?<!\w)(?:rg|r\.g\.|identidade|dni|cedula|c\.i\.|passaporte|passport|pasaporte)"
            r"(?:\s*(?:n[.º°o]?|no\.|number|numero|#))?[:.]?\s*(?P<v>[a-z]{0,2}\d[\d.]{4,12}(?:-?[\dx])?)(?!\w)"
        ),
    ),
    Rule(
        "bank_account",
        re.compile(r"(?<!\w)(?:agencia|ag\.?)[:.]?\s*(?:n[.º°o]?\s*)?(?P<v>\d{3,5}(?:-[\dx])?)(?!\w)"),
    ),
    Rule(
        "bank_account",
        re.compile(
            r"(?<!\w)(?:conta(?: corrente| poupanca| salario)?|c/c|c\.c\.|cc|account(?: number)?|acct)"
            r"[:.]?\s*(?:n[.º°o]?\s*)?(?P<v>\d{4,12}-?[\dx])(?!\w)"
        ),
    ),
    Rule(
        "bank_account",
        re.compile(
            r"(?<!\w)(?:chave )?pix[:.]?\s*"
            r"(?P<v>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?!\w)"
        ),
    ),
    Rule("card", re.compile(_B + r"(?:\d[ -]?){12,18}\d" + _E), luhn_valid),
    Rule(
        "iban",
        re.compile(r"(?<!\w)[a-z]{2}\d{2}(?: ?[a-z0-9]{4}){2,7}(?: ?[a-z0-9]{1,3})?(?!\w)"),
        iban_valid,
    ),
    Rule(
        "phone",
        re.compile(
            _B
            + r"(?:\+?55"
            + _SEP
            + r")?(?:\(\d{2}\)|\d{2})"
            + _SEP
            + r"(?:9"
            + _SEP
            + r"\d{4}|[2-5]\d{3})"
            + _SEP
            + r"\d{4}"
            + _E
        ),
        br_phone_valid,
    ),
    Rule(
        "phone",
        re.compile(_B + r"(?:\+?1" + _SEP + r")?(?:\([2-9]\d{2}\)|[2-9]\d{2})[ .-]\d{3}[ .-]\d{4}" + _E),
        nanp_phone_valid,
    ),
    Rule("phone", re.compile(_B + r"\+[1-9]\d{0,2}(?:[ .-]?\(?\d{1,4}\)?){2,5}" + _E), e164_valid),
    # After the phones: "+34 612 345 678" is a number to call, not a Canadian SIN.
    Rule("national_id", re.compile(_B + r"\d{3}[ -]\d{3}[ -]\d{3}" + _E), sin_valid),
    # A Brazilian mobile written without the area code keeps its hyphen: 98765-4321.
    Rule("phone", re.compile(_B + r"9\d{4}-\d{4}" + _E)),
    Rule("postal_code", re.compile(_B + r"\d{5}-\d{3}" + _E), standalone=True),
    Rule(
        "postal_code",
        re.compile(
            r"(?<!\w)(?:cep|c\.p\.|cp|codigo postal|zip(?: code)?|postal code)[:.]?\s*"
            r"(?P<v>\d{5}(?:-?\d{3}|-\d{4})?)(?!\w)"
        ),
    ),
    Rule("postal_code", re.compile(r"(?<!\w)(?P<v>[a-z]{1,2}\d[a-z\d]? \d[a-z]{2})(?!\w)"), _uppercase),
    Rule(
        "address",
        re.compile(
            r"(?<!\w)(?:" + _PT_ES_STREET + r")\s+[\w'.-]+(?:\s+[\w'.-]+){0,5}?,?\s*"
            r"(?:n(?:o|°|umero)?\.?\s*)?\d{1,5}[a-z]?"
            r"(?:\s*[,-]?\s*(?:" + _COMPLEMENT + r")\.?\s*\w{1,5})?(?!\w)"
        ),
    ),
    Rule(
        "address",
        re.compile(r"(?<!\w)\d{1,5}[a-z]?(?:\s+[a-z][\w'.-]*){1,4}\s+(?:" + _EN_STREET + r")\.?(?!\w)"),
        _capitalized_street,
    ),
)


def _resolve(candidates: list[Span]) -> list[Span]:
    """Keeps candidates in rule order, dropping any that overlaps one already kept."""
    kept: list[Span] = []
    for span in candidates:
        if all(span.end <= k.start or span.start >= k.end for k in kept):
            kept.append(span)
    return sorted(kept, key=lambda s: s.start)


def find_spans(text: str, rules: Sequence[Rule] = RULES) -> list[Span]:
    folded = fold(text)
    return _resolve([span for rule in rules for span in rule.spans(text, folded)])


class RulePiiDetector:
    version = VERSION

    def detect(self, texts: Sequence[str]) -> list[list[Span]]:
        return [find_spans(text) for text in texts]


class LayeredPiiDetector:
    """Rules over the whole text, then a second detector (the NER) over what the rules left (06 4.5).

    Rule spans are blanked with spaces of the same length before the second pass, so its offsets still
    point into the original text and it never re-labels a document number as something else.
    """

    def __init__(self, second: PiiDetector) -> None:
        self._rules = RulePiiDetector()
        self._second = second
        self.version = f"{VERSION}+{second.version}"

    def detect(self, texts: Sequence[str]) -> list[list[Span]]:
        first = self._rules.detect(texts)
        masked = [_blank(text, spans) for text, spans in zip(texts, first, strict=True)]
        second = self._second.detect(masked)
        return [_resolve([*a, *b]) for a, b in zip(first, second, strict=True)]


def _blank(text: str, spans: Sequence[Span]) -> str:
    chars = list(text)
    for span in spans:
        chars[span.start : span.end] = " " * (span.end - span.start)
    return "".join(chars)
