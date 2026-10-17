"""What is held back at the ingest boundary, before anything is stored (12-AI-MEMORY, WP-A).

A card number, a card's security code, a password and the credentials inside a URL never reach `events`,
the raw payloads, the hot layer or anything built from them: each becomes a marker that says what was
there, like `[retido:cartão ****1234]`. A card keeps its last four digits, so "the card ending 1234" still
reads and still extracts.

Protocol, order and ticket numbers, postal codes, amounts and dates are what memory needs, and no rule
touches them: a card needs digits that pass the card check (`values.card_number`) and either the shape of
a card (groups of four) or a card word nearby, never an order or protocol word right before it (a bare
number with no word of its own takes the word from the turn before, "qual o número do cartão?"); a
security code and a password need their keyword right before the value, and a code is a lone group of
three or four digits, never part of a postal code, a date, a time or an amount.

Pure and idempotent: `mask_text` repeats its pass until nothing changes, and a marker hides its own words,
so masking masked text changes nothing. The rules are written for PT, EN and ES.
"""

import hashlib
import re
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import orjson

from niadra.domain.memory.values import MARKER, card_number, fold


class Held(StrEnum):
    CARD = "card"
    CVV = "cvv"
    PASSWORD = "password"  # noqa: S105 (a type name, not a credential)
    SECRET = "secret"  # noqa: S105


ALL_TYPES: frozenset[Held] = frozenset(Held)
REGULATED_FLOOR: frozenset[Held] = frozenset({Held.CARD, Held.CVV, Held.PASSWORD})
"""What a space on the Regulado plan (a cell dedicated to the tenant) always masks, whatever its settings."""

MASKER_VERSION = 1
"""Bumped when a rule masks more: the stored events of every space are masked again (`mask_events`)."""

_PASSES = 4
_CARD_REACH = 40
_CODE_REACH = 30
_CODE_BEFORE = 12
_BLANK = "\x00"
"""Stands for a marker's characters while the rules look at the text; control characters were removed."""

_LABELS: dict[str, tuple[str, dict[Held, str]]] = {
    "pt": ("retido", {Held.CARD: "cartão", Held.CVV: "cvv", Held.PASSWORD: "senha", Held.SECRET: "segredo"}),
    "en": (
        "withheld",
        {Held.CARD: "card", Held.CVV: "cvv", Held.PASSWORD: "password", Held.SECRET: "secret"},
    ),
    "es": (
        "retenido",
        {Held.CARD: "tarjeta", Held.CVV: "cvv", Held.PASSWORD: "contraseña", Held.SECRET: "secreto"},
    ),
}

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[@-Z\\-_]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_MAYBE = re.compile(r"[0-9]|://|senha|pass|contrase|\bpin\b|clave", re.IGNORECASE)

_RUN = re.compile(r"[0-9]+(?:[ .\-][0-9]+)*")
_GROUP = re.compile(r"[0-9]+")
_CARD_WORD = (
    r"cartao|cartoes|card|cards|tarjeta|tarjetas|visa|mastercard|amex|elo|hipercard|diners|cvv2?|cvc2?"
)
# Words that name a number memory keeps: the nearest of them before a bare number says it is not a card.
_ORDER_WORD = (
    r"pedidos?|protocolos?|orders?|orden(?:es)?|ordem|tickets?|chamados?|rastreio|rastreamento|tracking"
    r"|faturas?|facturas?|invoices?|boletos?|nfe?|apolices?|sinistros?|reservas?|booking|matricula"
)
_LABEL = re.compile(rf"\b(?:(?P<card>{_CARD_WORD})|(?P<order>{_ORDER_WORD}))\b")
_CODE_WORD = re.compile(
    r"\b(?:cvv2?|cvc2?|cvn|cod(?:igo|\.)? (?:de )?seguranca|security code"
    r"|card verification (?:code|value)|codigo de seguridad)\b"
)
_CODE = re.compile(r"(?<![0-9])[0-9]{3,4}(?![0-9])")
_PASSWORD_WORD = re.compile(r"\b(?:senhas?|password|passwd|passcode|contrasena|pin|(?P<colon_only>clave))\b")
_PASSWORD_TAIL = re.compile(
    r"(?:[ \t]+(?!(?:e|and|y|ou|or)\b)[^\s:=]{1,20}){0,3}?[ \t]*"
    r"(?P<sep>[:=]|[ \t](?:é|eh|is|es|era|was|será)[ \t])[ \t]*",
    re.IGNORECASE,
)
_QUOTES = {'"': '"', "'": "'", "“": "”", "‘": "’"}
_TRAILING = ".,;)"
# What follows "senha:" in a sentence about a password, not the password itself.
_NOT_A_PASSWORD = frozenset(
    {
        "não",
        "nao",
        "no",
        "not",
        "sim",
        "yes",
        "si",
        "muito",
        "very",
        "errada",
        "errado",
        "wrong",
        "incorreta",
        "incorrect",
        "invalida",
        "inválida",
        "invalid",
        "expirada",
        "expired",
        "bloqueada",
        "blocked",
        "esqueci",
        "forgot",
        "nova",
        "new",
        "antiga",
        "old",
        "a",
        "o",
        "um",
        "uma",
        "the",
        "la",
        "el",
        "de",
        "do",
        "da",
        "que",
        "igual",
        "same",
        "mesma",
        "mesmo",
    }
)
_URL_CREDENTIALS = re.compile(
    r"\b[a-z][a-z0-9+.\-]{1,20}://(?P<creds>[^\s/@:\[\]]+:[^\s/@\[\]]+)@", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class Hold:
    """One value held back: its type and, for a card, the last four digits the marker shows."""

    kind: Held
    last4: str | None = None
    digits: str | None = field(default=None, repr=False, compare=False)
    """A card's digits, in memory only: the raw payload of the same batch masks the same card by them."""


@dataclass(frozen=True, slots=True)
class Masked:
    text: str
    held: tuple[Hold, ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        return counts(self.held)


def counts(held: Iterable[Hold]) -> dict[str, int]:
    return dict(sorted(Counter(h.kind.value for h in held).items()))


def added(before: Mapping[str, int] | None, held: Iterable[Hold]) -> dict[str, int]:
    """`content_meta.masked` after more was held from the same event."""
    total = Counter(dict(before or {}))
    total.update(h.kind.value for h in held)
    return dict(sorted(total.items()))


def fingerprint(types: Collection[Held]) -> int:
    """The rules and the types a space's stored events were masked with, as a signed 63-bit number."""
    digest = hashlib.sha256(f"{MASKER_VERSION}:{','.join(sorted(types))}".encode()).digest()
    return int.from_bytes(digest[:8], "big") >> 1


def language(locale: str | None) -> str:
    """The markers' language from the space's locale: `pt-BR` gives `pt`; anything unknown gives `en`."""
    code = (locale or "").split("-", 1)[0].split("_", 1)[0].lower()
    return code if code in _LABELS else "en"


def marker(kind: Held, lang: str = "pt", last4: str | None = None) -> str:
    word, labels = _LABELS.get(lang, _LABELS["en"])
    tail = f" ****{last4}" if last4 else ""
    return f"[{word}:{labels[kind]}{tail}]"


def strip_controls(text: str) -> str:
    """Terminal escape sequences, NUL and the other control characters, tab and line breaks kept."""
    return _CONTROL.sub("", _ANSI.sub("", text))


def mask_text(
    text: str,
    lang: str = "pt",
    types: Collection[Held] = ALL_TYPES,
    *,
    context: str | None = None,
    known_cards: Collection[str] = (),
    lines_as_spaces: bool = False,
) -> Masked:
    """`context` is the turn before this one in the conversation: a bare number that passes the card check
    and has no word of its own is a card when that turn speaks of one ("qual o número do cartão?").
    `known_cards` are digits already held as a card elsewhere in the same payload. `lines_as_spaces` is for
    text read from a file or an image: the rules see its line breaks as spaces, so a value split across
    two lines of a scan is held as it was when that text was stored on one line, and the breaks stay."""
    clean = strip_controls(text)
    held: list[Hold] = []
    if not types or not _MAYBE.search(clean):
        return Masked(clean, ())
    context_card = context is not None and speaks_of_card(context)
    for _ in range(_PASSES):
        clean, found = _pass(
            clean, lang, frozenset(types), context_card, frozenset(known_cards), lines_as_spaces
        )
        if not found:
            break
        held += found
    return Masked(clean, tuple(held))


def needs_context(text: str, types: Collection[Held] = ALL_TYPES) -> bool:
    """Whether the turn before would decide something here: a bare number that passes the card check. Rare,
    so the caller looks that turn up only then."""
    if Held.CARD not in types or sum(ch.isdigit() for ch in text) < 13:
        return False
    scan = _scan(strip_controls(text))
    with_context = sum(1 for _ in _cards(scan, True, frozenset()))
    return with_context > sum(1 for _ in _cards(scan, False, frozenset()))


def speaks_of_card(text: str) -> bool:
    return any(m.group("card") for m in _LABEL.finditer(_scan(strip_controls(text))))


def mask_value(
    key: str | None,
    value: Any,
    lang: str = "pt",
    types: Collection[Held] = ALL_TYPES,
    known_cards: Collection[str] = (),
) -> tuple[Any, tuple[Hold, ...]]:
    """A structured value, masked by its text and by the name of the field that holds it: `card_number`
    alone says a bare number is a card, `cvv` that three digits are a code. Keys are never changed;
    numbers are, only when their field names a card, a code or a password."""
    if isinstance(value, Mapping):
        out: dict[Any, Any] = {}
        held: list[Hold] = []
        for k, v in value.items():
            out[k], found = mask_value(str(k), v, lang, types, known_cards)
            held += found
        return out, tuple(held)
    if isinstance(value, list | tuple):
        items: list[Any] = []
        held = []
        for v in value:
            masked, found = mask_value(key, v, lang, types, known_cards)
            items.append(masked)
            held += found
        return items, tuple(held)
    if isinstance(value, bool) or not isinstance(value, str | int):
        return value, ()
    by_name = _by_field_name(key, value, types)
    if by_name is not None:
        return marker(by_name.kind, lang, by_name.last4), (by_name,)
    if isinstance(value, str):
        result = mask_text(value, lang, types, known_cards=known_cards)
        return result.text, result.held
    return value, ()


def mask_fields(
    fields: Mapping[str, Any], lang: str = "pt", types: Collection[Held] = ALL_TYPES
) -> tuple[dict[str, Any], tuple[Hold, ...]]:
    masked, held = mask_value(None, fields, lang, types)
    return dict(masked), held


def mask_payload(
    body: bytes,
    lang: str = "pt",
    types: Collection[Held] = ALL_TYPES,
    known_cards: Collection[str] = (),
) -> tuple[bytes, tuple[Hold, ...]]:
    """A raw payload as stored in object storage: JSON keeps its shape with its string values masked;
    anything else is masked as text. The same bytes come back when nothing was held. `known_cards` are the
    cards its events held, so a bare card number the events masked by their context goes here too."""
    try:
        document = orjson.loads(body)
    except orjson.JSONDecodeError:
        text = body.decode("utf-8", errors="surrogateescape")
        result = mask_text(text, lang, types, known_cards=known_cards)
        if result.text == text:
            return body, ()
        return result.text.encode("utf-8", errors="surrogateescape"), result.held
    masked, held = mask_value(None, document, lang, types, known_cards)
    if not held and masked == document:
        return body, ()
    return orjson.dumps(masked), held


def mask_lines(data: bytes, lang: str = "pt", types: Collection[Held] = ALL_TYPES) -> bytes:
    """A part of a JSONL upload, line by line, so the rows keep their count and their numbers."""
    return b"\n".join(
        mask_payload(line, lang, types)[0] if line.strip() else line for line in data.split(b"\n")
    )


def _pass(
    text: str,
    lang: str,
    types: frozenset[Held],
    context_card: bool,
    known: frozenset[str],
    lines_as_spaces: bool = False,
) -> tuple[str, list[Hold]]:
    # A break and a space are one character each, so what the rules find in `seen` is cut out of `text`.
    seen = text.replace("\n", " ") if lines_as_spaces else text
    scan = _scan(seen)
    taken: list[tuple[int, int, Hold]] = []

    def take(start: int, end: int, hold: Hold) -> None:
        if start < end and _BLANK not in scan[start:end] and all(end <= s or start >= e for s, e, _ in taken):
            taken.append((start, end, hold))

    if Held.SECRET in types:
        for m in _URL_CREDENTIALS.finditer(seen):
            take(m.start("creds"), m.end("creds"), Hold(Held.SECRET))
    if Held.CARD in types:
        for start, end, digits in _cards(scan, context_card, known):
            take(start, end, Hold(Held.CARD, digits[-4:], digits))
    if Held.CVV in types:
        for start, end in _codes(scan):
            take(start, end, Hold(Held.CVV))
    if Held.PASSWORD in types:
        for start, end in _passwords(seen, scan):
            take(start, end, Hold(Held.PASSWORD))
    if not taken:
        return text, []
    taken.sort(key=lambda t: t[0])
    parts: list[str] = []
    at = 0
    for start, end, hold in taken:
        parts += [text[at:start], marker(hold.kind, lang, hold.last4)]
        at = end
    parts.append(text[at:])
    return "".join(parts), [hold for _, _, hold in taken]


def _scan(text: str) -> str:
    """Lowercase without accents, one character per character, with every marker blanked out."""
    folded = text.lower() if text.isascii() else fold(text)
    if "[" not in text:
        return folded
    chars = list(folded)
    for m in MARKER.finditer(text):
        chars[m.start() : m.end()] = _BLANK * (m.end() - m.start())
    return "".join(chars)


def _cards(scan: str, context_card: bool, known: frozenset[str]) -> Iterable[tuple[int, int, str]]:
    for run in _RUN.finditer(scan):
        if _glued(scan, run.start(), run.end()):
            continue
        groups = [(g.start(), g.end()) for g in _GROUP.finditer(scan, run.start(), run.end())]
        i = 0
        while i < len(groups):
            found = _card_at(scan, groups, i, context_card, known)
            if found is None:
                i += 1
                continue
            j, digits = found
            yield groups[i][0], groups[j][1], digits
            i = j + 1


def _card_at(
    scan: str, groups: list[tuple[int, int]], i: int, context_card: bool, known: frozenset[str]
) -> tuple[int, str] | None:
    """The longest card starting at group `i`: (its last group, its digits). A card has at most 19
    digits, so only the groups within that reach are tried, however long the run of numbers."""
    spans: list[tuple[int, str]] = []
    digits = ""
    for j in range(i, len(groups)):
        digits += scan[groups[j][0] : groups[j][1]]
        if len(digits) > 19:
            break
        spans.append((j, digits))
    for j, candidate in reversed(spans):
        if candidate in known:
            return j, candidate
        if not card_number(candidate):
            continue
        if _card_shape([e - s for s, e in groups[i : j + 1]]):
            return j, candidate
        own = _card_context(scan, groups[i][0], groups[j][1])
        if own or (own is None and context_card):
            return j, candidate
    return None


def _card_shape(sizes: list[int]) -> bool:
    if sizes in ([4, 6, 5], [4, 6, 4]):
        return True
    return len(sizes) >= 3 and all(n == 4 for n in sizes[:-1]) and 1 <= sizes[-1] <= 4


def _card_context(scan: str, start: int, end: int) -> bool | None:
    """What the number's own words say: True for a card word nearby, False when the nearest word before it
    names an order or a protocol (or the first after it does), None when no such word is near."""
    before = list(_LABEL.finditer(scan, max(0, start - _CARD_REACH), start))
    if before:
        return before[-1].group("card") is not None
    after = _LABEL.search(scan, end, end + _CARD_REACH)
    return None if after is None else after.group("card") is not None


def _codes(scan: str) -> Iterable[tuple[int, int]]:
    for word in _CODE_WORD.finditer(scan):
        window_end = min(len(scan), word.end() + _CODE_REACH)
        for code in _CODE.finditer(scan, word.end(), window_end):
            between = scan[word.end() : code.start()]
            label = _LABEL.search(between)
            if _BLANK in between or (label is not None and label.group("order")):
                break
            if _standalone(scan, code.start(), code.end()):
                yield code.start(), code.end()
                break
        else:
            yield from _code_before(scan, word.start())


def _code_before(scan: str, word_start: int) -> Iterable[tuple[int, int]]:
    """ "123 é o cvv": a code just before its keyword, with no other number in between."""
    window = scan[max(0, word_start - _CODE_BEFORE) : word_start]
    codes = list(_CODE.finditer(scan, max(0, word_start - _CODE_BEFORE), word_start))
    if not codes or _BLANK in window:
        return
    code = codes[-1]
    between = scan[code.end() : word_start]
    if not any(ch.isdigit() for ch in between) and _standalone(scan, code.start(), code.end()):
        yield code.start(), code.end()


def _standalone(scan: str, start: int, end: int) -> bool:
    """A group of digits of its own: not part of a postal code, a date, a time, an amount or a longer id."""
    prev = scan[start - 1] if start else " "
    before_prev = scan[start - 2] if start >= 2 else " "
    after = scan[end] if end < len(scan) else " "
    after_next = scan[end + 1] if end + 1 < len(scan) else " "
    if prev.isalnum() or after.isalnum() or after == "%":
        return False
    if prev in ".,-/:" and before_prev.isdigit():
        return False
    if after in ".,-/:" and after_next.isdigit():
        return False
    return not (prev == "$" or (prev == " " and before_prev == "$"))


def _passwords(text: str, scan: str) -> Iterable[tuple[int, int]]:
    for word in _PASSWORD_WORD.finditer(scan):
        tail = _PASSWORD_TAIL.match(text, word.end())
        if tail is None or _BLANK in scan[word.end() : tail.end()]:
            continue
        if word.group("colon_only") and tail.group("sep") not in (":", "="):
            continue
        span = _password_value(text, tail.end())
        if span is None:
            continue
        start, end = span
        value = scan[start:end]
        if _BLANK in value or value.strip(_TRAILING + "!?") in _NOT_A_PASSWORD or _LABEL.fullmatch(value):
            continue
        yield start, end


def _password_value(text: str, at: int) -> tuple[int, int] | None:
    if at >= len(text) or text[at].isspace():
        return None
    close = _QUOTES.get(text[at])
    if close is not None:
        end = text.find(close, at + 1, at + 66)
        return (at + 1, end) if end > at + 1 else None
    end = at
    while end < len(text) and not text[end].isspace():
        end += 1
    while end - at > 1 and text[end - 1] in _TRAILING:
        end -= 1
    return at, end


def _glued(scan: str, start: int, end: int) -> bool:
    """Digits glued to letters are part of an identifier, like `AB4111111111111111`."""
    return (start > 0 and scan[start - 1].isalpha()) or (end < len(scan) and scan[end].isalpha())


def _by_field_name(key: str | None, value: str | int, types: Collection[Held]) -> Hold | None:
    if not key:
        return None
    words = set(re.split(r"[^a-z0-9]+", fold(re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key))))
    raw = str(value).strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    only_digits = bool(raw) and all(ch.isdigit() or ch in " .-" for ch in raw)
    if raw.startswith("[") and MARKER.fullmatch(raw):
        return None
    card_words = {"card", "cartao", "tarjeta", "pan", "cc"}
    code_words = {"cvv", "cvc", "cvv2", "cvc2", "cvn"}
    if Held.CARD in types and words & card_words and only_digits and card_number(digits):
        return Hold(Held.CARD, digits[-4:], digits)
    is_code = bool(words & code_words) or {"security", "code"} <= words
    if Held.CVV in types and is_code and only_digits and 3 <= len(digits) <= 4:
        return Hold(Held.CVV)
    if (
        Held.PASSWORD in types
        and words & {"password", "passwd", "passcode", "senha", "contrasena", "pin"}
        and raw
    ):
        return Hold(Held.PASSWORD)
    if Held.SECRET in types and (words & {"secret", "apikey"} or {"api", "key"} <= words) and raw:
        return Hold(Held.SECRET)
    return None
