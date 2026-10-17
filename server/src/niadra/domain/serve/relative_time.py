"""Time words in a history search (10, D.1): "semana passada", "last month", "hace dos semanas", read
without an LLM, in Portuguese, English and Spanish.

The window is computed in the space's time zone from a reference moment the caller passes: the last event
of the conversation in progress when there is one, so a replay reads the same words the same way. An
expression the reader does not know returns None, and the search says it ignored it; it never guesses.
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from fnmatch import fnmatchcase
from functools import lru_cache
from uuid import UUID
from zoneinfo import ZoneInfo

from niadra.domain.records import BusinessObject, EventRecord
from niadra.domain.vocabulary import EventKind


@dataclass(frozen=True, slots=True)
class Window:
    since: datetime
    until: datetime
    """Exclusive: the first instant after the window."""


_NUMBERS = {
    # en
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "couple": 2, "few": 3,
    # pt
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7,
    "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "alguns": 3, "algumas": 3,
    # es
    "un": 1, "uno": 1, "una": 1, "dos": 2, "cuatro": 4, "siete": 7, "ocho": 8, "nueve": 9,
    "diez": 10, "once": 11, "doce": 12, "unos": 3, "unas": 3,
}  # fmt: skip

_UNITS = {
    "day": "day", "days": "day", "dia": "day", "dias": "day",
    "week": "week", "weeks": "week", "semana": "week", "semanas": "week",
    "month": "month", "months": "month", "mes": "month", "meses": "month",
    "year": "year", "years": "year", "ano": "year", "anos": "year",
}  # fmt: skip

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
    "oct": 10, "nov": 11, "dec": 12,
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7,
    "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
    "fev": 2, "abr": 4, "mai": 5, "set": 9, "out": 10, "dez": 12,
    "enero": 1, "febrero": 2, "marzo": 3, "mayo": 5, "junio": 6, "julio": 7,
    "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    "ene": 1, "dic": 12,
}  # fmt: skip

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
    "segunda": 0, "segunda-feira": 0, "terca": 1, "terca-feira": 1, "quarta": 2, "quarta-feira": 2,
    "quinta": 3, "quinta-feira": 3, "sexta": 4, "sexta-feira": 4, "sabado": 5, "domingo": 6,
    "lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4,
}  # fmt: skip

_FIXED = {
    "today": ("day", 0), "hoje": ("day", 0), "hoy": ("day", 0),
    "yesterday": ("day", 1), "ontem": ("day", 1), "ayer": ("day", 1),
    "day before yesterday": ("day", 2), "the day before yesterday": ("day", 2),
    "anteontem": ("day", 2), "antes de ontem": ("day", 2), "anteayer": ("day", 2), "antier": ("day", 2),
    "this week": ("week", 0), "esta semana": ("week", 0), "nesta semana": ("week", 0),
    "last week": ("week", 1), "past week": ("week", 1), "the previous week": ("week", 1),
    "semana passada": ("week", 1), "na semana passada": ("week", 1), "semana anterior": ("week", 1),
    "a semana passada": ("week", 1), "la semana pasada": ("week", 1), "semana pasada": ("week", 1),
    "la semana anterior": ("week", 1),
    "this month": ("month", 0), "este mes": ("month", 0), "neste mes": ("month", 0),
    "last month": ("month", 1), "past month": ("month", 1), "mes passado": ("month", 1),
    "no mes passado": ("month", 1), "mes anterior": ("month", 1), "el mes pasado": ("month", 1),
    "mes pasado": ("month", 1), "el mes anterior": ("month", 1),
    "this year": ("year", 0), "este ano": ("year", 0), "neste ano": ("year", 0),
    "last year": ("year", 1), "ano passado": ("year", 1), "no ano passado": ("year", 1),
    "ano pasado": ("year", 1), "el ano pasado": ("year", 1),
}  # fmt: skip

_RECENT = frozenset(
    {"recently", "lately", "recentemente", "ultimamente", "recientemente", "estos dias", "these days"}
)

_NUMBER = r"(\d{1,3}|[a-z]+)"
_UNIT = r"(days?|weeks?|months?|years?|dias?|semanas?|mes|meses|anos?)"
_AGO = [
    re.compile(rf"^{_NUMBER} {_UNIT} ago$"),
    re.compile(rf"^(?:ha|faz|fazem) {_NUMBER} {_UNIT}(?: atras)?$"),
    re.compile(rf"^{_NUMBER} {_UNIT} atras$"),
    re.compile(rf"^hace {_NUMBER} {_UNIT}$"),
]
_LAST_N = [
    re.compile(rf"^(?:in )?(?:the )?(?:last|past) {_NUMBER} {_UNIT}$"),
    re.compile(rf"^(?:n?os |n?as )?ultim[oa]s {_NUMBER} {_UNIT}$"),
    re.compile(rf"^(?:en )?(?:los |las )?ultim[oa]s {_NUMBER} {_UNIT}$"),
]
_LAST_FEW = re.compile(r"^(?:the )?(?:last|past) few days$|^(?:n?os |los )?ultimos dias$")
_MONTH = re.compile(
    r"^(?:in |em |en |no mes de |em mes de |el mes de |during )?([a-z]+)(?: (?:de |of )?(\d{4}))?$"
)
_WEEKDAY = [
    re.compile(r"^(?:last |on |this past |na |no |el |la |o |a )?([a-z-]+)$"),
    re.compile(r"^(?:na |no |el |o |a )?([a-z-]+) (?:passad[oa]|pasad[oa]|anterior)$"),
    re.compile(r"^(?:el |la )?([a-z-]+) pasado$"),
]
_YEAR = re.compile(r"^(?:in |em |en |no ano de |el ano |the year )?(\d{4})$")


_ACCENTS = re.compile(r"[\u0300-\u036f]")
_NOT_WORD = re.compile(r"[^a-z0-9\s-]")


def _fold(text: str) -> str:
    # The temporal channel folds each candidate text on every read: regular expressions, no loop per char.
    lowered = text.lower()
    if not lowered.isascii():
        lowered = _ACCENTS.sub("", unicodedata.normalize("NFKD", lowered))
    return " ".join(_NOT_WORD.sub(" ", lowered).split())


def _count(word: str) -> int | None:
    if word.isdigit():
        value = int(word)
        return value if 0 < value <= 366 else None
    return _NUMBERS.get(word)


def _day_start(d: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, time.min, tzinfo=tz)


def _month_start(year: int, month: int) -> date:
    """The first day of `month`, which may run past either end of `year`."""
    return date(year + (month - 1) // 12, (month - 1) % 12 + 1, 1)


def _period(unit: str, back: int, today: date, tz: ZoneInfo) -> Window:
    """The calendar period `back` units before the one holding `today`: weeks start on Monday."""
    if unit == "day":
        start = today - timedelta(days=back)
        return Window(_day_start(start, tz), _day_start(start + timedelta(days=1), tz))
    if unit == "week":
        monday = today - timedelta(days=today.weekday()) - timedelta(weeks=back)
        return Window(_day_start(monday, tz), _day_start(monday + timedelta(weeks=1), tz))
    if unit == "month":
        start = _month_start(today.year, today.month - back)
        end = _month_start(start.year, start.month + 1)
        return Window(_day_start(start, tz), _day_start(end, tz))
    start = date(today.year - back, 1, 1)
    return Window(_day_start(start, tz), _day_start(date(start.year + 1, 1, 1), tz))


def _span(unit: str, n: int, reference: datetime, today: date, tz: ZoneInfo) -> Window:
    """The last `n` units up to the reference moment, counted by calendar day."""
    if unit == "day":
        start = today - timedelta(days=n - 1)
    elif unit == "week":
        start = today - timedelta(weeks=n) + timedelta(days=1)
    elif unit == "month":
        start = _month_start(today.year, today.month - n).replace(day=min(today.day, 28))
    else:
        start = date(today.year - n, today.month, min(today.day, 28))
    return Window(_day_start(start, tz), reference + timedelta(microseconds=1))


def read_when(expression: str, reference: datetime, timezone: str) -> Window | None:
    """The window `expression` names, or None when it is not one this reader knows."""
    tz = ZoneInfo(timezone)
    local = reference.astimezone(tz)
    today = local.date()
    text = _fold(expression)
    if not text:
        return None
    if text in _FIXED:
        unit, back = _FIXED[text]
        return _period(unit, back, today, tz)
    if text in _RECENT or _LAST_FEW.match(text):
        return _span("day", 7, reference, today, tz)
    for pattern in _AGO:
        if m := pattern.match(text):
            n, named = _count(m.group(1)), _UNITS.get(m.group(2))
            if n is None or named is None:
                return None
            return _period(named, n, today, tz)
    for pattern in _LAST_N:
        if m := pattern.match(text):
            n, named = _count(m.group(1)), _UNITS.get(m.group(2))
            if n is None or named is None:
                return None
            return _span(named, n, reference, today, tz)
    if m := _YEAR.match(text):
        year = int(m.group(1))
        if not 1900 < year <= today.year:
            return None
        return Window(_day_start(date(year, 1, 1), tz), _day_start(date(year + 1, 1, 1), tz))
    if (m := _MONTH.match(text)) and m.group(1) in _MONTHS:
        month = _MONTHS[m.group(1)]
        # Without a year, the most recent such month: in September, "March" is this year's, "November"
        # last year's.
        latest = today.year if month <= today.month else today.year - 1
        year = int(m.group(2)) if m.group(2) else latest
        start = date(year, month, 1)
        if start > today:
            return None
        end = _month_start(year, month + 1)
        return Window(_day_start(start, tz), _day_start(end, tz))
    for pattern in _WEEKDAY:
        if (m := pattern.match(text)) and m.group(1) in _WEEKDAYS:
            # The most recent such day before today: "on Monday" said on a Monday means last week's.
            back = (today.weekday() - _WEEKDAYS[m.group(1)]) % 7 or 7
            day = today - timedelta(days=back)
            return Window(_day_start(day, tz), _day_start(day + timedelta(days=1), tz))
    return None


# Time words inside a whole question (10, D.1 and the temporal slot channel). `read_when` takes one
# expression; a question carries other words around it, and some of them look like time words and are not:
# "hoje" is "currently" in "o CEP que vocês têm hoje", "segunda" is "second" in "segunda via", "out" and
# "set" are English words, a four-digit order number reads like a year.

_TODAY = frozenset({"today", "hoje", "hoy"})
_AMBIGUOUS_WORDS = frozenset(
    {"may", "mar", "set", "out", "dez", "dec", "jan", "feb", "fev", "apr", "abr", "jun", "jul", "aug",
     "sep", "sept", "oct", "nov", "mai", "ene", "dic"}
)  # fmt: skip
_ORDINAL_WEEKDAYS = frozenset({"segunda", "terca", "quarta", "quinta", "sexta"})
_PAST_WORDS = frozenset({"passada", "passado", "anterior", "pasada", "pasado"})
_TIME_TOKENS = (
    (
        frozenset(_UNITS)
        | frozenset(m for m in _MONTHS if len(m) > 3)
        | frozenset(_WEEKDAYS)
        | frozenset(w for phrase in (*_FIXED, *_RECENT) for w in phrase.split() if len(w) > 3)
        | frozenset({"ago", "atras", "hace", "recently", "lately"})
    )
    - _TODAY
    - {"este", "esta", "this", "last", "past", "passado", "passada", "pasado", "pasada", "anterior"}
)
_MAX_WHEN_WORDS = 6


def _when_allowed(tokens: list[str]) -> bool:
    phrase = " ".join(tokens)
    if phrase in _TODAY:
        return False
    if len(tokens) == 1 and (tokens[0] in _AMBIGUOUS_WORDS or tokens[0].isdigit()):
        return False  # a lone month abbreviation or a bare number
    # "a segunda via", "a quinta parcela": a Portuguese weekday without "-feira" is read only as "passada".
    return not (_ORDINAL_WEEKDAYS & set(tokens)) or bool(_PAST_WORDS & set(tokens))


def find_when(question: str, reference: datetime, timezone: str) -> Window | None:
    """The first calendar window a question names, reading its longest time expression, or None."""
    tokens = _fold(question).split()
    for start in range(len(tokens)):
        for size in range(min(_MAX_WHEN_WORDS, len(tokens) - start), 0, -1):
            chunk = tokens[start : start + size]
            if not any(t in _TIME_TOKENS for t in chunk) or not _when_allowed(chunk):
                continue
            if (window := read_when(" ".join(chunk), reference, timezone)) is not None:
                return window
    return None


# Anchors: "depois da vistoria", "before the delivery", "desde el cambio de tarjeta" (11, 3.3). The
# words name a kind of event from the customer's systems through a lexicon per canonical event type; the
# customer's own event of that type gives the date. No model reads the question.


class Relation(StrEnum):
    AFTER = "after"
    BEFORE = "before"
    SINCE = "since"


_CUES = {
    "after": Relation.AFTER, "depois": Relation.AFTER, "apos": Relation.AFTER, "despues": Relation.AFTER,
    "tras": Relation.AFTER,
    "before": Relation.BEFORE, "antes": Relation.BEFORE,
    "since": Relation.SINCE, "desde": Relation.SINCE,
}  # fmt: skip
_CUE_PAIRS: dict[tuple[str, ...], Relation] = {("prior", "to"): Relation.BEFORE}
# Between the cue and the event: prepositions, articles, possessives, demonstratives, and the ordinal that
# picks which of several events of the type ("the first delivery").
_LINKS = frozenset(
    {"de", "da", "do", "das", "dos", "del", "desta", "deste", "dessa", "desse", "que", "of",
     "a", "o", "as", "os", "um", "uma", "the", "an", "el", "la", "los", "las", "un", "una",
     "meu", "minha", "meus", "minhas", "seu", "sua", "nosso", "nossa", "my", "our", "your", "mi", "mis",
     "su", "sus", "nuestro", "nuestra", "esta", "este", "essa", "esse", "this", "that", "esa", "ese",
     "i", "we", "eu", "yo"}
)  # fmt: skip
_FIRST = frozenset({"first", "primeira", "primeiro", "primera", "primer"})
_LATEST = frozenset({"last", "latest", "recent", "ultima", "ultimo", "most", "mais", "recente", "reciente"})
_MAX_LINKS = 4
_MAX_OFFSET = 3
"""How far into the words after the links the event's name may start: "after the new card was issued"."""
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_CLAUSE = re.compile(r"[,.;:!?¿¡()\n]")


def _stem(token: str) -> str:
    return token[:-1] if len(token) > 3 and token.endswith("s") else token


def _words(text: str) -> list[str]:
    # "technician's visit" folds to "technician s visit"; the lone "s" is the possessive, not a word.
    return [w for w in _fold(text).replace("-", " ").split() if w != "s"]


def _anchor_tokens(text: str) -> list[str]:
    return [_stem(t) for t in _words(text)]


@dataclass(frozen=True, slots=True)
class AnchorEntry:
    """One kind of event the customer can name: the canonical types it covers and the words for it."""

    name: str
    canonical_types: tuple[str, ...]
    terms: tuple[str, ...]

    def covers(self, canonical_type: str) -> bool:
        return any(fnmatchcase(canonical_type, pattern) for pattern in self.canonical_types)


@dataclass(frozen=True, slots=True)
class AnchorLexicon:
    """The entries with their terms folded once: build it when the configuration changes, not per read."""

    entries: tuple[AnchorEntry, ...] = ()
    _index: tuple[tuple[tuple[str, ...], int, str], ...] = ()
    _terms: tuple[tuple[str, ...], ...] = ()
    """Per entry, its folded terms padded with spaces, for a substring test over a padded text."""

    @classmethod
    def build(cls, entries: Sequence[AnchorEntry]) -> "AnchorLexicon":
        """Built once per distinct list of entries: every read of a space rebuilds the same list."""
        return _build_lexicon(tuple(entries))

    def match(self, words: Sequence[str]) -> tuple[tuple[int, ...], str] | None:
        """The entries (by position) whose longest term starts earliest in `words`, within the first few.
        Several entries may share a term: a mapping rule that names its own event "vistoria" as well."""
        tokens = [_stem(w) for w in words[: _MAX_OFFSET + 8]]
        for offset in range(min(_MAX_OFFSET + 1, len(tokens))):
            found: list[int] = []
            size, first_term = 0, ""
            for term_tokens, position, term in self._index:
                if size and len(term_tokens) < size:
                    break
                if tuple(tokens[offset : offset + len(term_tokens)]) == term_tokens:
                    size, first_term = size or len(term_tokens), first_term or term
                    found.append(position)
            if found:
                return tuple(dict.fromkeys(found)), first_term
        return None

    def mentions(self, positions: Iterable[int], text: str) -> bool:
        """Whether `text` names the entries' event with any of their terms."""
        padded = f" {' '.join(_anchor_tokens(text))} "
        return any(term in padded for position in positions for term in self._terms[position])


@lru_cache(maxsize=256)
def _build_lexicon(entries: tuple[AnchorEntry, ...]) -> AnchorLexicon:
    index = {
        (tuple(tokens), position, term): None
        for position, entry in enumerate(entries)
        for term in entry.terms
        if (tokens := _anchor_tokens(term))
    }
    # Longest term first, so "visita do tecnico" wins over "visita"; then the order of the entries.
    ordered = tuple(sorted(index, key=lambda item: (-len(item[0]), item[1], item[2])))
    terms = tuple(
        tuple(dict.fromkeys(f" {' '.join(tokens)} " for tokens, position, _ in ordered if position == i))
        for i in range(len(entries))
    )
    return AnchorLexicon(entries, ordered, terms)


@dataclass(frozen=True, slots=True)
class AnchorPhrase:
    """What the question said, before any event resolves it."""

    relation: Relation
    entries: tuple[AnchorEntry, ...]
    positions: tuple[int, ...]
    """The entries' positions in the lexicon."""
    term: str
    first: bool = False
    """"The first delivery": the earliest event of the type instead of the most recent."""

    @property
    def name(self) -> str:
        return self.entries[0].name

    def covers(self, canonical_type: str) -> bool:
        return any(entry.covers(canonical_type) for entry in self.entries)


def read_anchor(question: str, lexicon: AnchorLexicon) -> AnchorPhrase | None:
    """The first "after/before/since <event>" in the question whose event the lexicon names, or None."""
    if not lexicon.entries:
        return None
    # Within one clause: "antes de tudo, a entrega atrasou" names no time before the delivery.
    for clause in _CLAUSE.split(question):
        if (phrase := _anchor_in(_words(clause), lexicon)) is not None:
            return phrase
    return None


def _anchor_in(tokens: list[str], lexicon: AnchorLexicon) -> AnchorPhrase | None:
    for i, token in enumerate(tokens):
        relation, skip = _CUES.get(token), 1
        if relation is None and (pair := _CUE_PAIRS.get((token, *tokens[i + 1 : i + 2]))) is not None:
            relation, skip = pair, 2
        if relation is None:
            continue
        rest = tokens[i + skip :]
        first = False
        links = 0
        while rest and links < _MAX_LINKS and (rest[0] in _LINKS or rest[0] in _FIRST or rest[0] in _LATEST):
            first = first or rest[0] in _FIRST
            rest, links = rest[1:], links + 1
        if (found := lexicon.match(rest)) is not None:
            positions, term = found
            return AnchorPhrase(
                relation, tuple(lexicon.entries[p] for p in positions), positions, term, first
            )
    return None


@dataclass(frozen=True, slots=True)
class SystemMark:
    """An event from the customer's system of record, as an anchor needs it: its type and when."""

    canonical_type: str
    at: datetime
    object_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ResolvedAnchor:
    phrase: AnchorPhrase
    mark: SystemMark
    window: Window


def resolve_anchor(
    phrase: AnchorPhrase, marks: Iterable[SystemMark], reference: datetime
) -> ResolvedAnchor | None:
    """The window the phrase names, from the customer's event of its type; None when there is none.

    "After" and "since" start just after the event and end at the reference moment; "before" ends just
    before it. An event later than the reference (a replay reading an older turn) never anchors."""
    candidates = [m for m in marks if m.at <= reference and phrase.covers(m.canonical_type)]
    if not candidates:
        return None
    pick = min if phrase.first else max
    mark = pick(candidates, key=lambda m: (m.at, m.canonical_type, str(m.object_id)))
    tick = timedelta(microseconds=1)
    if phrase.relation is Relation.BEFORE:
        window = Window(_EPOCH, mark.at)
    else:
        window = Window(mark.at + tick, reference + tick)
    return ResolvedAnchor(phrase, mark, window)


def marks_from_objects(objects: Iterable[BusinessObject]) -> tuple[SystemMark, ...]:
    """The last system event of each of the customer's business objects, from the memory snapshot."""
    return tuple(
        SystemMark(str(event_type), obj.as_of, obj.object_id)
        for obj in objects
        if (event_type := obj.state.get("last_event_type"))
    )


def marks_from_events(events: Iterable[EventRecord]) -> tuple[SystemMark, ...]:
    """System events read from the log (a history page, the hot layer), when the caller has them."""
    return tuple(
        SystemMark(e.canonical_type, e.occurred_at)
        for e in events
        if e.kind is EventKind.SYSTEM_EVENT and e.canonical_type
    )
