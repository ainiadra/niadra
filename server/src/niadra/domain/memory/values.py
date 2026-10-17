"""Settled values: what a session fixes that has no predicate (11, 3.2).

A protocol, ticket, order or record number, a receipt, a postal code, an amount, a date, a code. None of
them is a fact about the customer, so none has a predicate, a validity rule or an exact index; they only
survived inside episode text. A settled value is its own memory item, typed, with when it was said, by
whom, on which channel and in which session, so "the protocol you sent me by e-mail" is answered by rule:
type `protocol`, channel `email`, most recent.

Two producers feed it, merged per session: the extraction's `values` and the deterministic net in this
module, which types a token of three or more digits by the word before it (PT/EN/ES). Neither ever keeps
a document, card or bank account number, nor a password or verification code: the type list has no such
kind, the words that name them veto the token (an "ID" no value word qualifies names a document), and
numbers that pass a document or card check digit are dropped whatever word came before them. The same
words find what a free text must not keep (`withheld_numbers`, for `domain/extract/withhold.py`).
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from niadra.domain.privacy.purposes import Tags
from niadra.domain.records import ObjectKey
from niadra.domain.vocabulary import Speaker


class ValueType(StrEnum):
    PROTOCOL = "protocol"
    TICKET = "ticket"
    ORDER = "order"
    RECORD = "record"
    RECEIPT = "receipt"
    POSTAL_CODE = "postal_code"
    AMOUNT = "amount"
    DATE = "date"
    CODE = "code"


class ValueOrigin(StrEnum):
    EXTRACTION = "extraction"
    """The extraction model listed it; the validation found it, as written, in a turn of the session."""
    RULE = "rule"
    """The deterministic net found it: a gated or failed session still keeps its values."""


@dataclass(frozen=True, slots=True)
class SettledValue:
    """One value one session settled, anchored on a handle like every memory row (L6)."""

    value_id: UUID
    origin_handle_id: UUID
    type: ValueType
    raw: str
    """As written in the turn; sealed at rest. `key` is derived from it."""
    said_at: datetime
    speaker: Speaker
    session_id: UUID
    channel: str
    source_id: UUID
    created_at: datetime
    evidence_event_ids: tuple[UUID, ...] = ()
    conversation_id: str | None = None
    episode_id: UUID | None = None
    object_ref: ObjectKey | None = None
    origin: ValueOrigin = ValueOrigin.EXTRACTION
    purposes: Tags | None = None
    """Its conversation's purposes (13, 6.6); None until it is tagged."""

    @property
    def key(self) -> str:
        return value_key(self.raw)


# Type-independent on purpose: a turn that names a number without saying what it is ("88280, please")
# finds the value by key alone.
_CURRENCY = re.compile(
    r"^(?:r\$|us\$|u\$s|\$|€|£|usd|brl|eur|mxn|ars|clp|cop)\s*"
    r"|\s*(?:reais|real|d[oó]lares|dollars?|euros?|pesos?)$",
    re.IGNORECASE,
)
_LABEL = re.compile(r"^(?:n\.?\s?[º°]|nr\.?|n[uú]m\.?|#)\s*", re.IGNORECASE)
_EDGE = " \t\n.,;:!?/-\"'()[]"


def value_key(raw: str) -> str:
    """The lookup key: `45778-204`, `45778204` and `nº 45778.204` are the same value."""
    text = _LABEL.sub("", _CURRENCY.sub("", raw.strip().strip(_EDGE)).strip())
    return "".join(ch for ch in _fold(text) if ch.isascii() and ch.isalnum())


def digits(text: str) -> int:
    return sum(ch.isdigit() for ch in text)


ORIGIN_PRECEDENCE: dict[Speaker, int] = {
    Speaker.SYSTEM: 3,
    Speaker.HUMAN_AGENT: 2,
    Speaker.AI_AGENT: 1,
    Speaker.CUSTOMER: 0,
}
"""Who wins a tie between two statements of the same thing: the system of record, then a human agent,
then an AI agent, then the customer (11, 3.2)."""


def origin_rank(speaker: Speaker) -> int:
    return ORIGIN_PRECEDENCE[speaker]


def ranked(values: Iterable[SettledValue], type: ValueType | None = None) -> list[SettledValue]:
    """One entry per (type, key), the latest statement of it, newest first; ties go to the origin with
    precedence. The first entry of a type is its current value."""
    latest: dict[tuple[ValueType, str], SettledValue] = {}
    for value in values:
        if type is not None and value.type is not type:
            continue
        slot = (value.type, value.key)
        if slot not in latest or _rank(value) > _rank(latest[slot]):
            latest[slot] = value
    return sorted(latest.values(), key=_rank, reverse=True)


def current(
    values: Iterable[SettledValue], type: ValueType, *, channel: str | None = None
) -> SettledValue | None:
    """The value of `type` that holds now, optionally as said on one channel ("the protocol you sent by
    e-mail"): the most recent statement wins, and between statements of the same moment the system of
    record comes first."""
    found = ranked((v for v in values if channel is None or v.channel == channel), type)
    return found[0] if found else None


def _rank(value: SettledValue) -> tuple[datetime, int, str]:
    return (value.said_at, origin_rank(value.speaker), value.key)


# The deterministic net.

TOKEN = re.compile(r"(?<![\w])(?=(?:[^\s\d]*\d){3})[\w$.,/:-]*\d[\w.,/:-]*")
"""A token with three or more digits: the same pattern the extraction gate counts as substance."""

_WORD = re.compile(r"[a-z$]+")
_CLAUSE_END = re.compile(r"[.!?;](?:\s|$)|\n")
_POSTAL = re.compile(r"^\d{5}-\d{3}$")
_DATE = re.compile(r"^(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?|\d{4}-\d{2}-\d{2})$")
_MOBILE = re.compile(r"^9\d{4}-\d{4}$")
_SPAN = re.compile(r"\S+")
MARKER = re.compile(r"\[(?:retido|withheld|retenido):[^\[\]\n]{1,40}\]")
"""What the ingest boundary left where it held a card, a code, a password or a secret (domain/ingest/masking):
opaque here, so a marker's words never veto a value next to it and its last four digits are never one."""
_TRAILING = ".,;:!?/-)]\"'"

LOOK_BACK = 6
VETO_REACH = 4
LOOK_AHEAD = 2


class _Veto(StrEnum):
    """What a value must never be; the strong ones veto from anywhere near the token."""

    DOCUMENT = "document"
    CARD = "card"
    ACCOUNT = "account"
    SECRET = "secret"  # noqa: S105 (a kind of word, not a credential)
    CONTACT = "contact"


_STRONG = frozenset({_Veto.DOCUMENT, _Veto.CARD, _Veto.ACCOUNT, _Veto.SECRET})

# Folded (lowercase, no accents). A trailing `*` matches the stem.
_LEXICON: dict[ValueType | _Veto, tuple[str, ...]] = {
    ValueType.PROTOCOL: ("protocol*", "atendimento"),
    ValueType.TICKET: (
        "ticket*",
        "chamado*",
        "incidente*",
        "incident*",
        "incidencia*",
        "caso",
        "case",
        "solicitacao",
        "solicitud",
        "reclamacao",
        "reclamo",
        "complaint",
    ),
    ValueType.ORDER: ("pedido*", "order*", "orden*", "encomenda*", "compra*", "purchase*"),
    ValueType.RECORD: (
        "registr*",
        "record*",
        "sinistro*",
        "claim*",
        "ocorrencia*",
        "ocurrencia*",
        "occurrence*",
    ),
    ValueType.RECEIPT: ("comprovante*", "recibo*", "receipt*", "transacao", "transaccion", "transaction*"),
    ValueType.POSTAL_CODE: ("cep", "postal", "zip", "zipcode"),
    ValueType.AMOUNT: (
        "valor",
        "value",
        "amount",
        "monto",
        "importe",
        "total",
        "r$",
        "us$",
        "$",
        "reais",
        "real",
        "dolares",
        "dollars",
        "euros",
        "pesos",
        "preco",
        "price",
        "precio",
        "custo",
        "cost",
        "costo",
        "taxa",
        "fee",
        "tarifa",
        "multa",
        "desconto",
        "discount",
        "descuento",
        "reembolso",
        "refund",
        "estorno",
        "parcela*",
        "installment*",
        "cuota*",
    ),
    ValueType.DATE: (
        "data",
        "date",
        "fecha",
        "dia",
        "day",
        "vencimento",
        "vencimiento",
        "due",
        "prazo",
        "deadline",
    ),
    ValueType.CODE: (
        "codigo",
        "code",
        "cupom",
        "cupon",
        "coupon",
        "voucher",
        "rastreio",
        "rastreamento",
        "tracking",
        "seguimiento",
        "rastreo",
        "agendamento",
        "appointment",
        "cita",
        "reserva",
        "booking",
        "reservation",
        "oferta",
        "offer",
        "promocao",
        "promo",
        "plano",
        "plan",
        "fatura",
        "invoice",
        "factura",
        "boleto",
        "contrato",
        "contract",
        "apolice",
        "policy",
        "poliza",
        "politica",
        "aviso",
        "notice",
    ),
    _Veto.DOCUMENT: (
        "document*",
        "doc",
        "cpf",
        "cnpj",
        "rg",
        "identidade",
        "identity",
        "dni",
        "nie",
        "nif",
        "nipc",
        "ssn",
        "social",
        "passaporte",
        "passport",
        "pasaporte",
        "cedula",
        "rut",
        "curp",
        "rfc",
        "cuit",
        "cuil",
        "cnh",
        "habilitacao",
        "pis",
        "nis",
    ),
    _Veto.CARD: ("cartao", "cartoes", "card", "cards", "tarjeta*"),
    _Veto.ACCOUNT: (
        "conta",
        "contas",
        "account*",
        "acct",
        "cuenta*",
        "agencia",
        "iban",
        "pix",
        "banco",
        "bank",
        "routing",
        "swift",
        "clabe",
        "cbu",
    ),
    _Veto.SECRET: (
        "senha*",
        "password*",
        "contrasena*",
        "pin",
        "token",
        "otp",
        "cvv",
        "cvc",
        "verificacao",
        "verification",
        "verificacion",
        "seguranca",
        "security",
        "seguridad",
        "acesso",
        "access",
        "acceso",
    ),
    _Veto.CONTACT: (
        "telefone",
        "fone",
        "celular",
        "whatsapp",
        "zap",
        "phone",
        "mobile",
        "cell",
        "telefono",
        "movil",
    ),
}

_EXACT: dict[str, ValueType | _Veto] = {}
_STEMS: list[tuple[str, ValueType | _Veto]] = []
for _meaning, _words in _LEXICON.items():
    for _entry in _words:
        if _entry.endswith("*"):
            _STEMS.append((_entry[:-1], _meaning))
        else:
            _EXACT[_entry] = _meaning


@dataclass(frozen=True, slots=True)
class Found:
    type: ValueType
    raw: str
    start: int


def find_values(text: str) -> list[Found]:
    """Every typed value of one turn, in order; a token no word types, or a vetoed one, is left out."""
    folded, markers = _fold_around_markers(text)
    found: list[Found] = []
    for match in TOKEN.finditer(text):
        if any(s <= match.start() < e for s, e in markers):
            continue
        raw = match.group(0).rstrip(_TRAILING)
        kind = classify(folded, match.start(), match.start() + len(raw), raw)
        if kind is not None:
            found.append(Found(kind, raw, match.start()))
    return found


def classify(folded: str, start: int, end: int, raw: str) -> ValueType | None:
    """The type of the token at `folded[start:end]` (offsets into text folded by `fold`), or `None` when
    nothing types it or something vetoes it."""
    vetoed, nearest = _context(folded, start, end, raw)
    if vetoed:
        return None
    if _POSTAL.match(raw):
        return ValueType.POSTAL_CODE
    if _DATE.match(raw):
        return ValueType.DATE
    if "$" in raw:
        return ValueType.AMOUNT
    return nearest


def locate(text: str, raw: str) -> str | None:
    """How `raw` is written in `text`, when it is there and nothing vetoes it there: the check an extracted
    value passes before it is kept, since the model may type anything, or paraphrase a number."""
    key = value_key(raw)
    if digits(key) < 3:
        return None
    folded, markers = _fold_around_markers(text)
    words = list(_SPAN.finditer(text))
    for i, first in enumerate(words):
        for last in reversed(words[i : i + 3]):
            written = text[first.start() : last.end()].rstrip(_TRAILING)
            if value_key(written) != key:
                continue
            if any(first.start() < e and s < first.start() + len(written) for s, e in markers):
                continue
            if not _context(folded, first.start(), first.start() + len(written), written)[0]:
                return written
    return None


def _context(folded: str, start: int, end: int, raw: str) -> tuple[bool, ValueType | None]:
    """(vetoed, the type the nearest word gives): a strong veto reaches four words back, a document six,
    and any other word only decides when it is the nearest one."""
    if never_a_value(raw):
        return True, None
    kinds = _kinds_before(folded, start)
    if any(k in _STRONG for k in kinds[:VETO_REACH]) or _Veto.DOCUMENT in kinds:
        return True, None
    nearest = next((k for k in kinds if k is not None), None)
    if nearest is None:
        after = _WORD.findall(folded[end : _clause_end(folded, end)])[:LOOK_AHEAD]
        nearest = next((k for k in map(_kind, after) if k is not None), None)
    if isinstance(nearest, _Veto):
        return True, None
    return False, nearest


def _kinds_before(folded: str, start: int) -> list[ValueType | _Veto | None]:
    """What each of the six words before `start` in its clause says, nearest first. "ID" names a
    document ("my ID number", "o ID é") unless a value word qualifies it ("order ID", "ID do pedido")."""
    words = _WORD.findall(folded[_clause_start(folded, start) : start])[::-1]
    return [_id_kind(words, i) if w in _ID_WORDS else _kind(w) for i, w in enumerate(words[:LOOK_BACK])]


def _id_kind(words: Sequence[str], i: int) -> _Veto | None:
    """`words` run backwards from the token: `words[i + 1]` comes right before the ID word in the text, and
    `words[i - 1]`, `words[i - 2]` right after it."""
    before = words[i + 1] if i + 1 < len(words) else ""
    after = next((w for w in reversed(words[:i]) if w not in _ID_LINKS), "")
    if _qualifies(before) or _qualifies(after):
        return None
    return _Veto.DOCUMENT


def _qualifies(word: str) -> bool:
    return word in _ID_QUALIFIERS or isinstance(_kind(word), ValueType)


# Words that name an identifier; "identity", "identidade" and "documento" are document words already.
_ID_WORDS = frozenset({"id", "ids", "identificacao", "identification", "identificacion", "identidad"})
# An ID of the business, not of the person: "customer ID", "ID de cliente", "user id".
_ID_QUALIFIERS = frozenset(
    {"customer", "cliente", "client", "user", "usuario", "usuaria", "session", "sessao", "sesion", "member"}
)
_ID_LINKS = frozenset({"de", "do", "da", "dos", "das", "del", "of", "the", "o", "a", "el", "la"})


@dataclass(frozen=True, slots=True)
class Withheld:
    """A number of a text that memory must never keep, and what it is: `document`, `card`, `account` or
    `secret`."""

    start: int
    end: int
    raw: str
    kind: str


_AMOUNT = re.compile(r"^\d{1,3}(?:[.,]\d{3})*[.,]\d{1,2}$")
_TIME = re.compile(r"^\d{1,2}[:h]\d{2}$")


def withheld_numbers(text: str) -> list[Withheld]:
    """The numbers of a free text (a turn, a summary, a promise) that are a document, a card, a bank account
    or a secret, in order. The same words veto them as veto a settled value, with differences that keep
    summaries readable: a card, account or secret word counts only when it is nearer the number than a
    value word ("the bank refunded order 12345" keeps the order), and a card word only before a number
    long enough to be a card, while a document word within six words does unless a value word comes right
    before the number ("my ID number for the invoice is 87798164" is withheld, "meu CPF está no pedido
    12345" is not). An amount, a date, a time and a postal code are never withheld by words; a number that
    passes a document or card check always is."""
    if not any(ch.isdigit() for ch in text):
        return []
    folded, markers = _fold_around_markers(text)
    found: list[Withheld] = []
    for match in TOKEN.finditer(text):
        if any(s <= match.start() < e for s, e in markers):
            continue
        raw = match.group(0).rstrip(_TRAILING)
        kind = _withheld_kind(folded, match.start(), raw)
        if kind is not None:
            found.append(Withheld(match.start(), match.start() + len(raw), raw, kind))
    return found


def _withheld_kind(folded: str, start: int, raw: str) -> str | None:
    number = "".join(ch for ch in raw if ch.isdigit())
    if all(ch.isdigit() or ch in " .-/" for ch in raw):
        if (len(number) == 11 and _cpf(number)) or (len(number) == 14 and _cnpj(number)):
            return _Veto.DOCUMENT.value
        if card_number(number):
            return _Veto.CARD.value
    if "$" in raw or any(p.match(raw) for p in (_AMOUNT, _DATE, _POSTAL, _TIME)):
        return None
    kinds = _kinds_before(folded, start)
    if kinds and isinstance(kinds[0], ValueType):
        return None  # the word right before names it: "o documento do pedido 12345" keeps the order
    if _Veto.DOCUMENT in kinds:
        return _Veto.DOCUMENT.value
    for kind in kinds[:VETO_REACH]:
        if isinstance(kind, ValueType):
            return None
        # A card word before a short number ("the ZIP code for card delivery is 73235", "the card ending
        # 1234") is about the card, not its number: a card has 13 digits or more.
        if kind in _STRONG and (kind is not _Veto.CARD or len(number) >= 13):
            return str(kind)
    return None


def label_word(word: str) -> bool:
    """A word that only names what was withheld: "CPF", "ID", "account", "senha"."""
    return word in _ID_WORDS or _kind(word) in _STRONG


def never_a_value(raw: str) -> bool:
    """A number that passes a document or card check, a mobile number, an e-mail: whatever the words said."""
    if "@" in raw or _MOBILE.match(raw):
        return True
    number = "".join(ch for ch in raw if ch.isdigit())
    only_digits = all(ch.isdigit() or ch in " .-/" for ch in raw)
    if not only_digits:
        return False
    return (
        (len(number) == 11 and _cpf(number)) or (len(number) == 14 and _cnpj(number)) or card_number(number)
    )


def card_number(number: str) -> bool:
    """Digits that pass the card check: 13 to 19 of them, a card network's first digit, Luhn."""
    return (
        13 <= len(number) <= 19
        and number.isascii()
        and number.isdigit()
        and number[0] in "23456"
        and _luhn(number)
    )


def _fold_around_markers(text: str) -> tuple[str, list[tuple[int, int]]]:
    folded = _fold(text)
    markers = [(m.start(), m.end()) for m in MARKER.finditer(text)] if "[" in text else []
    for start, end in markers:
        folded = folded[:start] + " " * (end - start) + folded[end:]
    return folded, markers


def fold(text: str) -> str:
    """Lowercase without accents, one character per character, so offsets still point into `text`."""
    return _fold(text)


def _fold(text: str) -> str:
    out = []
    for ch in text:
        plain = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
        low = plain.casefold()
        out.append(low if len(low) == 1 else ch.lower() if len(ch.lower()) == 1 else "?")
    return "".join(out)


def _kind(word: str) -> ValueType | _Veto | None:
    if word in _EXACT:
        return _EXACT[word]
    return next((kind for stem, kind in _STEMS if word.startswith(stem)), None)


def _clause_start(folded: str, start: int) -> int:
    ends = [m.end() for m in _CLAUSE_END.finditer(folded, 0, start)]
    return ends[-1] if ends else 0


def _clause_end(folded: str, end: int) -> int:
    found = _CLAUSE_END.search(folded, end)
    return found.start() if found else len(folded)


def _mod11(values: Sequence[int], weights: Iterable[int]) -> int:
    rest = sum(v * w for v, w in zip(values, weights, strict=False)) % 11
    return 0 if rest < 2 else 11 - rest


def _cpf(number: str) -> bool:
    d = [int(c) for c in number]
    if len(set(d)) == 1:
        return False
    first = _mod11(d[:9], range(10, 1, -1))
    return d[9:] == [first, _mod11([*d[:9], first], range(11, 1, -1))]


def _cnpj(number: str) -> bool:
    d = [int(c) for c in number]
    if len(set(d)) == 1:
        return False
    weights = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    first = _mod11(d[:12], weights[1:])
    return d[12:] == [first, _mod11([*d[:12], first], weights)]


def _luhn(number: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(number)):
        doubled = int(ch) * 2 if i % 2 else int(ch)
        total += doubled - 9 if doubled > 9 else doubled
    return total % 10 == 0
