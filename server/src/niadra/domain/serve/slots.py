"""Per-turn slots (11, 3.3): what the customer just said picks, from the memory the pinned skeleton left
out, the few lines worth this turn, by rules and indexes, without a model.

Channels rank the pool on their own: `exact` (the identifiers the turn names, the values of the type it
asks for), `lexical` (BM25 over the lines) and `temporal` (the windows its time words and anchors resolve
to, the channels it names). Other packages plug in through `Channel`: the settled values (a channel that
brings its own items), the semantic channel (run by the app within a deadline, `skipped` past it) and
the anchors of system events (windows handed to `TemporalChannel`). Reciprocal rank fusion merges the
rankings, and a knapsack in rank order fills the view's small budget with what the body does not already
say. Derived lines answer what no single item does: how many times, by the topic asked; that memory has
no record of what was asked, or that the conversation the turn is about has none; that a value of the kind
asked is withheld until the customer is verified, which is never said as "no record".

A value line says what it is about, the matter of the conversation it was settled in (`value_subject`),
and the conversation the turn is about (`asked_conversation`) puts its own values first: a record number of
another matter never answers alone.

Every slot carries why it was chosen (`SlotWhy`): its position in each channel that ranked it, what each
added to the fused score, and for a derived line the rule that wrote it. The receipt keeps that always,
without the lines; the response shows it only when the caller asks (`explain`).

Same turn, same pool, same bytes: ties break by id, and nothing reads a clock.
"""

import math
import re
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Protocol
from uuid import UUID

from niadra.domain.compile.model import EpisodeRef, Section
from niadra.domain.compile.templates import Formatter
from niadra.domain.compile.tokens import estimate_tokens
from niadra.domain.extract.matter import signature
from niadra.domain.memory.values import SettledValue, ValueType
from niadra.domain.memory.values import current as current_value
from niadra.domain.memory.values import ranked as values_ranked
from niadra.domain.serve.atoms import atoms_of, best_atom
from niadra.domain.serve.guards import CONTROL_ARM, GUARD_KIND, GUARDS_ARM, Guard
from niadra.domain.serve.lexicon import (
    CATEGORY_WEIGHT,
    CONCEPT_WEIGHT,
    WORD_KEY,
    WORD_WEIGHT,
    Lexicon,
    Unit,
    fold_words,
    stem,
)
from niadra.domain.serve.lexicon import EMPTY as EMPTY_LEXICON
from niadra.domain.serve.recurrence import RECURRENCE_INTENT, RECURRENCE_WINDOW_DAYS
from niadra.domain.serve.relative_time import Window
from niadra.domain.serve.search import RRF_K, ranking, tokenize
from niadra.domain.serve.turn import (
    TEXT_CACHE,
    VALUE_TYPES,
    QuestionKind,
    TurnAnalysis,
    identifiers_in,
    typed_values,
)

DEFAULT_WEIGHTS: Mapping[str, float] = {
    "exact": 2.0,
    "values": 2.0,
    "lexical": 1.0,
    "temporal": 1.0,
    "semantic": 1.0,
}
"""A channel's weight in the fusion. An exact match outranks a word in common; per-space weights learned
from the measurement replace these later (11, 3.4)."""
PER_CHANNEL = 5
"""The lexical and temporal channels hand over at most this many items: past them, a word in common or a
date in the window says little."""
MAX_SLOTS = {"voice": 2, "chat": 4}
"""Lines per turn at most; other views take `DEFAULT_MAX_SLOTS`."""
DEFAULT_MAX_SLOTS = 4
QUESTION_MAX_SLOTS = {"voice": 3}
"""Lines per turn at most when the turn asks for a value, a count or a day (`asks_value`): on voice they are
atoms, a third of a whole line each (17, 6.5)."""
WINDOWED = frozenset({"episode", "value", "object", "action"})
"""What an anchored window filters; a fact or an open item holds whatever the window."""
EXCERPT_RANKS = 3
"""Only the best fused matches are cut to fit; below them a whole line or nothing."""
COUNT_DATES = 5
"""A count line lists the most recent dates, so the agent never counts again."""
DERIVED_SECTION = {
    GUARD_KIND: Section.META,
    "count": Section.HISTORY,
    "no_record": Section.META,
    "withheld": Section.META,
}


@dataclass(frozen=True, slots=True)
class SlotItem:
    """One line a slot can carry. `text` is the line without its label, what the channels read; `line` is
    what the agent reads. An item of the pinned body is in the pool too, `in_body`: channels see it, so
    "no record" is never said of what the body holds, and the knapsack skips it."""

    item_id: str
    kind: str
    text: str
    line: str
    tokens: int
    at: datetime | None = None
    channel: str | None = None
    category: str | None = None
    value_type: str | None = None
    """Set by a channel whose items are typed values (the settled values): the type it holds."""
    in_body: bool = False
    object_id: UUID | None = None
    source: str | None = None
    """For a settled value, the conversation it was settled in (`episode:<id>`)."""
    headline: bool = False
    """The pinned body carries this item shorter (a voice headline, a compacted line): its date, channel
    and matter are already before the agent, so a slot of it says only what the body left out."""


@dataclass(frozen=True, slots=True)
class UnseenValues:
    """Values of what the turn asks for that this reader does not see: `withheld`, those in a conversation
    the policy keeps until the customer is verified; `hidden`, those kept for anything else (another
    purpose, a link nobody confirmed, past the reserve). Types and digits only, never a value. Either makes
    "no record" untrue; only the first is said, as withheld until the verification."""

    withheld: tuple[tuple[str, str], ...] = ()
    """(type, digits) of each value withheld until the verification."""
    hidden: tuple[tuple[str, str], ...] = ()
    """(type, digits) of each value kept from this reader for anything else."""

    def hides(self, turn: TurnAnalysis) -> bool:
        """Whether a value asked for exists that this reader does not see, whatever the reason."""
        return _unseen_kind(turn, (*self.withheld, *self.hidden)) is not None


NOTHING_UNSEEN = UnseenValues()


def _unseen_kind(turn: TurnAnalysis, unseen: Sequence[tuple[str, str]]) -> str | None:
    kinds = {kind for kind, _ in unseen}
    if asked := [t for t in turn.asked_types if t in kinds]:
        return asked[0]
    named = set(turn.identifiers)
    return next((kind for kind, number in unseen if number in named), None)


def unseen_values(
    values: Sequence[SettledValue], pool: Sequence[SlotItem], withheld_episodes: Collection[str]
) -> UnseenValues:
    """What of `values` this reader does not see, split by why (`UnseenValues`). `withheld_episodes` are the
    conversations the pack kept for the reader's level (`episode:<id>`)."""
    visible = {i.item_id for i in pool if i.kind == "episode"}
    withheld: dict[tuple[str, str], None] = {}
    hidden: dict[tuple[str, str], None] = {}
    for value in values:
        episode = f"episode:{value.episode_id}" if value.episode_id is not None else None
        if episode is not None and episode in visible:
            continue
        found = (value.type.value, digits_of(value.raw))
        (withheld if episode is not None and episode in withheld_episodes else hidden)[found] = None
    return UnseenValues(tuple(sorted(withheld)), tuple(sorted(k for k in hidden if k not in withheld)))


@dataclass(frozen=True, slots=True)
class ChannelHits:
    channel: str
    ranked: tuple[str, ...] = ()
    """Item ids, best first."""
    items: tuple[SlotItem, ...] = ()
    """Items the channel brings that the pool does not hold (settled values)."""
    skipped: bool = False
    """The channel did not answer within its deadline; the slots went out without it."""


class Channel(Protocol):
    @property
    def name(self) -> str: ...

    def rank(self, turn: TurnAnalysis, pool: Sequence[SlotItem]) -> ChannelHits: ...


def _types(item: SlotItem) -> set[str]:
    return {item.value_type} if item.value_type else set(typed_values(item.text))


def _newest_first(items: Sequence[SlotItem]) -> list[SlotItem]:
    return sorted(items, key=lambda i: (-(i.at.timestamp() if i.at else 0.0), i.item_id))


class ExactChannel:
    """The identifiers the turn names, and the values of the types it asks for: an item holding the
    number, then an item holding a value of that type, the one from a channel the turn names first, the
    most recent first. A type the pinned body already holds a value of is answered there: an older value
    of it comes only by its number, so "the CEP you have now" does not bring back the old one."""

    name = "exact"

    def rank(self, turn: TurnAnalysis, pool: Sequence[SlotItem]) -> ChannelHits:
        if not (turn.identifiers or turn.asked_types):
            return ChannelHits(self.name)
        wanted_ids, asked = set(turn.identifiers), set(turn.asked_types)
        types = {i.item_id: _types(i) for i in pool}
        answered = {t for i in pool if i.in_body for t in types[i.item_id]}
        scored: dict[str, int] = {}
        for item in pool:
            score = 0
            if wanted_ids & set(identifiers_in(item.text)):
                score += 4
            if asked & (types[item.item_id] - (set() if item.in_body else answered)):
                score += 3
            if score and turn.channels and item.channel in turn.channels:
                score += 1
            if score:
                scored[item.item_id] = score
        ordered = _newest_first([i for i in pool if i.item_id in scored])
        ranked = sorted(ordered, key=lambda i: -scored[i.item_id])
        return ChannelHits(self.name, tuple(i.item_id for i in ranked))


@dataclass(frozen=True, slots=True)
class LexicalChannel:
    """BM25 of what the turn says over the lines (16.2), read through the space's lexicon: each unit of
    the turn (a content word, or a phrase of the lexicon) scores a line by its best key there, the word's
    stem first, then a concept it shares ("etiqueta" for "rotulo"), then the conversation's category. A
    line's keys are kept by its text."""

    lexicon: Lexicon = EMPTY_LEXICON
    name: str = "lexical"

    def rank(self, turn: TurnAnalysis, pool: Sequence[SlotItem]) -> ChannelHits:
        if not turn.topic:
            return ChannelHits(self.name)
        units = [u.weights() for u in turn_units(self.lexicon, turn)]
        if not units:
            return ChannelHits(self.name)
        keys = {i.item_id: line_keys(self.lexicon, i) for i in pool}
        lengths = {i.item_id: _length(i.text) for i in pool}
        avg = sum(lengths.values()) / len(lengths) if lengths else 1.0
        wanted = {k for unit in units for k in unit}
        df = Counter(k for found in keys.values() for k in found & wanted)
        n = len(keys)
        scores: dict[str, float] = {}
        for item_id, found in keys.items():
            norm = (_K1 + 1) / (1 + _K1 * (1 - _B + _B * lengths[item_id] / (avg or 1.0)))
            # The category alone never ranks a line: it only adds to one that says something asked.
            if any(k in found for unit in units for k, w in unit.items() if w > CATEGORY_WEIGHT):
                best = (
                    max((w * _idf(n, df[k]) for k, w in u.items() if k in found), default=0.0) for u in units
                )
                scores[item_id] = sum(best) * norm
        if not scores:
            return ChannelHits(self.name)
        # Far below the best line (a line the body may hold), a word in common is noise, not an answer.
        floor = LEXICAL_FLOOR * max(scores.values())
        kept = {i: v for i, v in scores.items() if v >= floor}
        return ChannelHits(self.name, tuple(ranking(kept)[:PER_CHANNEL]))


def _idf(n: int, df: int) -> float:
    return math.log(1 + (n - df + 0.5) / (df + 0.5))


def turn_units(lexicon: Lexicon, turn: TurnAnalysis) -> tuple[Unit, ...]:
    """The turn as the lexicon reads it; its content words alone when it was analyzed without its text."""
    return lexicon.units(turn.folded or " ".join(turn.topic), turn.topic)


def line_keys(lexicon: Lexicon, item: SlotItem) -> frozenset[str]:
    return lexicon.keys(item.text, item.category if item.kind == "episode" else None)


LEXICAL_FLOOR = 0.5
"""The lexical channel hands over the lines that score at least this share of the best one."""
_K1, _B = 1.2, 0.75
"""The history search's BM25 parameters (`search.bm25`)."""


@lru_cache(maxsize=TEXT_CACHE)
def _length(text: str) -> int:
    return len(fold_words(text))


@dataclass(frozen=True, slots=True)
class TemporalChannel:
    """When and where: first what the question's time words and anchors ranked (`leading`, from
    `domain.serve.temporal`), then what happened inside `windows`, the most recent first, then the latest
    item of each channel the turn names ("what I wrote on WhatsApp")."""

    windows: Sequence[Window] = ()
    leading: Sequence[str] = ()
    name: str = "temporal"

    def rank(self, turn: TurnAnalysis, pool: Sequence[SlotItem]) -> ChannelHits:
        dated = [i for i in pool if i.at is not None]
        inside = _newest_first(
            [i for i in dated if any(w.since <= i.at < w.until for w in self.windows if i.at is not None)]
        )
        named = [
            latest[0]
            for channel in turn.channels
            if (latest := _newest_first([i for i in dated if i.channel == channel]))
        ]
        present = {i.item_id for i in pool}
        leading = [i for i in self.leading if i in present]
        ranked = list(dict.fromkeys([*leading, *(i.item_id for i in [*inside, *named])]))
        return ChannelHits(self.name, tuple(ranked[:PER_CHANNEL]))


VALUES_CHANNEL = "values"
_VALUE_TYPES = frozenset(t.value for t in ValueType)


def value_hits(
    values: Sequence[SettledValue],
    turn: TurnAnalysis,
    pool: Sequence[SlotItem],
    *,
    fmt: Formatter,
    voice: bool,
    lexicon: Lexicon = EMPTY_LEXICON,
) -> ChannelHits:
    """The settled values channel (11, 3.2): the values whose number the turn names, then the value
    that holds now for each type it asks for (from the channel it names, when it names one), then the
    older ones; those of the conversation the turn is about (`asked_conversation`) before any other. A
    value is seen only when the conversation it was said in is in the pool, so it passes the same policy
    as that conversation; a value the body already prints is marked as the body's.

    Each line says what the value is about (`value_subject`), and of a kind a person holds one at a time
    (a postal code), which one holds now and which one it replaced."""
    episodes = {i.item_id: i for i in pool if i.kind == "episode"}
    shown = [v for v in values if v.episode_id is not None and f"episode:{v.episode_id}" in episodes]
    if not shown:
        return ChannelHits(VALUES_CHANNEL)
    body = " ".join(i.text for i in pool if i.in_body)
    body_ids = set(identifiers_in(body))
    wanted = set(turn.identifiers)
    order: list[SettledValue] = [v for v in values_ranked(shown) if digits_of(v.raw) in wanted]
    for name in turn.asked_types:
        if name not in _VALUE_TYPES:
            continue  # asked for, but not a kind of value memory settles (an address, a document)
        typed = [v for v in shown if v.type.value == name]
        for channel in (*turn.channels, None):
            if (now := current_value(typed, ValueType(name), channel=channel)) is not None:
                order.append(now)
                break
        order += values_ranked(typed)
    if (matter := asked_conversation(turn, lexicon, pool)) is not None:
        # The conversation the turn is about answers first; another matter's number never leads.
        order = sorted(order, key=lambda v: f"episode:{v.episode_id}" != matter.item_id)
    labels = _current_labels(shown, turn, fmt)
    items: dict[str, SlotItem] = {}
    for value in order:
        item_id = f"value:{value.value_id}"
        if item_id in items:
            continue
        source = f"episode:{value.episode_id}"
        about = value_subject(episodes[source].text, value.raw)
        term = labels.get(value.value_id, fmt.term(value.type.value))
        parts = [f"{term} {value.raw}", *([about] if about else []), value.channel, fmt.date(value.said_at)]
        text = " · ".join(parts)
        line = fmt.line(Section.META, text, voice=voice).replace(
            fmt.section(Section.META), fmt.word("value"), 1
        )
        items[item_id] = SlotItem(
            item_id,
            "value",
            text,
            line,
            estimate_tokens(line + "\n"),
            at=value.said_at,
            channel=value.channel,
            value_type=value.type.value,
            in_body=digits_of(value.raw) in body_ids,
            source=source,
        )
    return ChannelHits(VALUES_CHANNEL, tuple(items), tuple(items.values()))


ONE_AT_A_TIME = frozenset({ValueType.POSTAL_CODE.value})
"""Kinds of value a person holds one of at a time: of two said on different days, the latest holds now and
the line says so, by the precedence contradictions follow."""
SUBJECT_MIN_WORDS = 3
"""A value line says its conversation's matter when the matter has at least this many words."""


def value_subject(text: str, raw: str = "") -> str | None:
    """What a value is about: the matter of the conversation it was settled in, as its atoms say it
    (`atoms.atoms_of`: the summary's first sentence without its reporting clause, cut at its first clause).
    None when the line gives no atom, when the matter is shorter than `SUBJECT_MIN_WORDS` ("Mudei") or when
    it only states the value `raw` again: either says nothing the value does not."""
    found = atoms_of(text)
    matter = found[0].matter if found else ""
    if len(matter.split()) < SUBJECT_MIN_WORDS or (raw and digits_of(raw) in digits_of(matter)):
        return None
    return matter


def _current_labels(values: Sequence[SettledValue], turn: TurnAnalysis, fmt: Formatter) -> dict[UUID, str]:
    """For each kind asked that a person holds one of at a time, and of which two or more were said: the
    current one labeled as such, the others as replaced."""
    labels: dict[UUID, str] = {}
    for name in turn.asked_types:
        if name not in ONE_AT_A_TIME:
            continue
        typed = values_ranked(values, ValueType(name))
        if len(typed) < 2:
            continue
        term = fmt.term(name)
        labels[typed[0].value_id] = fmt.word("value_current", term=term)
        for older in typed[1:]:
            labels[older.value_id] = fmt.word("value_previous", term=term)
    return labels


MATTER_MIN_UNITS = 2
"""The conversation a value question is about answers at least this many of the words it says besides the
kind of value asked (fewer when it says fewer)."""
_MATTER_WORDS = """
    numero numeros number numbers numer passaram passou passar deram deu gave give given reply replied
    resposta respostas responderam respondeu respuesta mandei mandou enviei envie sent send emailed
    escrevi escreveu wrote write escribi dias days dia day ontem yesterday voces you
"""
_MATTER_GENERIC = frozenset(WORD_KEY + stem(w) for w in fold_words(_MATTER_WORDS))
_TYPE_STEMS = tuple(s for stems in VALUE_TYPES.values() for s in stems)


def _matter_units(lexicon: Lexicon, turn: TurnAnalysis) -> list[Unit]:
    """What a value question says besides the kind of value it asks for and how it was sent."""
    return [
        u
        for u in turn_units(lexicon, turn)
        if u.word is None
        or not (u.word in _MATTER_GENERIC or u.word[len(WORD_KEY) :].startswith(_TYPE_STEMS))
    ]


def asked_conversation(turn: TurnAnalysis, lexicon: Lexicon, pool: Sequence[SlotItem]) -> SlotItem | None:
    """The conversation a turn that asks for a value is about ("the e-mail about the tow truck that did not
    come"): the visible conversation that answers the most of what the turn says besides the kind of value,
    the newest on a tie. On a channel the turn names, one word of the matter is enough: the channel already
    says which conversation it was ("emailed you about my claim because the tow truck did not show up",
    whose summary says only "reboque"). Elsewhere, or when no conversation on the named channel answers (a
    turn saying "the app blocked my transfer" names no channel it wrote on), at least `MATTER_MIN_UNITS`.
    None when the turn asks for no value or no conversation stands out."""
    if not turn.asked_types:
        return None
    units = _matter_units(lexicon, turn)
    if not units:
        return None
    if turn.channels and (named := _best_matter(units, lexicon, pool, turn.channels, 1)) is not None:
        return named
    return _best_matter(units, lexicon, pool, (), min(MATTER_MIN_UNITS, len(units)))


def _best_matter(
    units: Sequence[Unit], lexicon: Lexicon, pool: Sequence[SlotItem], channels: Sequence[str], need: int
) -> SlotItem | None:
    best: tuple[int, float, str] | None = None
    found: SlotItem | None = None
    for item in pool:
        if item.kind != "episode" or (channels and item.channel not in channels):
            continue
        keys = lexicon.keys(item.text)
        matched = sum(u.matched_by(keys) for u in units)
        if matched < need:
            continue
        rank = (matched, item.at.timestamp() if item.at else 0.0, item.item_id)
        if best is None or rank > best:
            best, found = rank, item
    return found


def _answered(units: Sequence[Unit], keys: frozenset[str]) -> float:
    """How much of the turn a line answers: a word it says weighs more than a synonym it says."""

    def weight(u: Unit) -> float:
        if u.word is not None and u.word in keys:
            return WORD_WEIGHT
        return CONCEPT_WEIGHT if u.matched_by(keys) else 0.0

    return sum(weight(u) for u in units)


NAMED_NUMBERS = 3
"""A negative about one conversation lists at most this many of the numbers it names."""


def off_matter(item: SlotItem, matter: SlotItem, lexicon: Lexicon, turn: TurnAnalysis) -> bool:
    """Whether a line is of another matter than the conversation the turn is about: a value settled in
    another conversation, or another conversation that answers less than half of what the asked one
    answers of the turn besides the kind of value (a word it says counts more than a synonym). The local
    A/B of 27/09 lost four answers to "registro 90561" of an unrelated conversation next to the e-mail the
    customer asked about (pt-152, pt-156, pt-162), and then two more to a conversation that shared only
    a synonym or two with it ("a internet está caindo" beside "a fibra fica sem sinal", pt-151, pt-161)."""
    if item.item_id == matter.item_id:
        return False
    if item.kind == "value":
        return item.source != matter.item_id
    if item.kind != "episode":
        return False
    units = _matter_units(lexicon, turn)
    mine = _answered(units, lexicon.keys(item.text))
    return mine == 0 or 2 * mine < _answered(units, lexicon.keys(matter.text))


GENERIC_VALUE_TYPES = frozenset({"record"})
"""Kinds a question uses for any number ("the number of that record"): any number of the conversation
answers them."""


def matter_negatives(
    turn: TurnAnalysis,
    matter: SlotItem,
    items: Mapping[str, SlotItem],
) -> list[str]:
    """The kinds of value asked that the conversation the turn is about does not hold: none of its settled
    values is of the kind, and its text names none. Every value of a visible conversation is visible, so
    saying so is true."""
    types = typed_values(matter.text)
    numbers = identifiers_in(matter.text)
    held = {i.value_type for i in items.values() if i.kind == "value" and i.source == matter.item_id}
    return [
        name
        for name in turn.asked_types
        if name not in held
        and name not in types
        and not (name in GENERIC_VALUE_TYPES and numbers)
        and name in _VALUE_TYPES
    ]


def digits_of(raw: str) -> str:
    return "".join(ch for ch in raw if ch.isdigit())


def standard_channels(
    windows: Sequence[Window] = (), leading: Sequence[str] = (), lexicon: Lexicon = EMPTY_LEXICON
) -> list[Channel]:
    return [ExactChannel(), LexicalChannel(lexicon), TemporalChannel(windows, leading)]


def run_channels(
    turn: TurnAnalysis, pool: Sequence[SlotItem], channels: Sequence[Channel]
) -> list[ChannelHits]:
    return [channel.rank(turn, pool) for channel in channels]


@dataclass(frozen=True, slots=True)
class ChannelRank:
    """One channel's part in an item's fused score."""

    channel: str
    position: int
    """1-based, in that channel's own ranking."""
    weight: float
    contribution: float
    """`weight / (RRF_K + position)`: what the channel added to the fused score."""


@dataclass(frozen=True, slots=True)
class Fused:
    item_id: str
    score: float
    channels: tuple[str, ...]
    ranks: tuple[ChannelRank, ...] = ()
    """Per channel that ranked the item, in the order they were fused; the contributions sum to `score`."""


def fuse(hits: Sequence[ChannelHits], weights: Mapping[str, float] | None = None) -> list[Fused]:
    """Weighted reciprocal rank fusion; ties by id. A channel weighing 0 adds nothing and names nothing."""
    weights = DEFAULT_WEIGHTS if weights is None else weights
    scores: dict[str, float] = {}
    found: dict[str, list[ChannelRank]] = {}
    for channel in hits:
        weight = weights.get(channel.channel, 1.0)
        if weight <= 0:
            continue
        for position, item_id in enumerate(channel.ranked, start=1):
            contribution = weight / (RRF_K + position)
            scores[item_id] = scores.get(item_id, 0.0) + contribution
            found.setdefault(item_id, []).append(ChannelRank(channel.channel, position, weight, contribution))
    return [
        Fused(item_id, scores[item_id], tuple(r.channel for r in found[item_id]), tuple(found[item_id]))
        for item_id in sorted(scores, key=lambda i: (-scores[i], i))
    ]


@dataclass(frozen=True, slots=True)
class SlotWhy:
    """Why an item took a slot: never its text or a value, only ranks and numbers."""

    ranks: tuple[ChannelRank, ...]
    score: float
    """The fused score: the sum of the ranks' contributions."""
    weights_version: int | None = None
    """The space's learned weights that fused the turn; None for the defaults."""
    excerpt: bool = False
    """The line was cut to the sentences that answer the turn."""
    atom: bool = False
    """The conversation went as its atom that answers the turn: its matter and the value (17, 6.3)."""

    def to_json(self) -> dict[str, Any]:
        return {
            "score": _round(self.score),
            "channels": {
                r.channel: {
                    "position": r.position,
                    "weight": _round(r.weight),
                    "contribution": _round(r.contribution),
                }
                for r in self.ranks
            },
            **({"excerpt": True} if self.excerpt else {}),
            **({"atom": True} if self.atom else {}),
        }


def _round(value: float) -> float:
    return round(value, 6)


@dataclass(frozen=True, slots=True)
class Slot:
    item: SlotItem
    channels: tuple[str, ...]
    position: int
    """1-based, in the fused ranking."""
    why: SlotWhy | None = None


@dataclass(frozen=True, slots=True)
class Derived:
    kind: str
    """`guard`, `count`, `no_record` or `withheld`."""
    line: str
    tokens: int
    rule: str = ""
    """The rule that wrote the line: `guard_<value type>`, `count_complaints`, `count_conversations`,
    `no_record` or `withheld_may_hold`."""
    basis: tuple[tuple[str, Any], ...] = ()
    """What the rule counted or found missing, as ids, kinds and numbers; never a value the turn named."""

    def why(self) -> dict[str, Any]:
        basis = {k: list(v) if isinstance(v, tuple) else v for k, v in self.basis}
        return {"kind": self.kind, "rule": self.rule, **basis}


@dataclass(frozen=True, slots=True)
class SlotPlan:
    slots: tuple[Slot, ...] = ()
    derived: tuple[Derived, ...] = ()
    budget: int = 0
    hits: tuple[ChannelHits, ...] = field(default_factory=tuple)
    weights_version: int | None = None
    guards: tuple[Guard, ...] = ()
    """The guards this turn wrote (13, 6.1), shown or, for the control group, only recorded."""
    guard_arm: str = GUARDS_ARM
    budget_rule: str | None = None
    """What set `budget` (`slot_budget_rule`): `question` when the turn asked for a value, a count or a
    day, `turn` otherwise; None when the caller gave a budget of its own."""

    @property
    def tokens(self) -> int:
        return sum(d.tokens for d in self.derived) + sum(s.item.tokens for s in self.slots)

    @property
    def item_ids(self) -> tuple[str, ...]:
        return tuple(s.item.item_id for s in self.slots)

    def render(self, fmt: Formatter) -> str | None:
        """The block for the end of the prompt, after `live`, before `delta`; None when there is nothing."""
        lines = [d.line for d in self.derived] + [s.item.line for s in self.slots]
        if not lines:
            return None
        tag = fmt.word("turn_tag")
        return "\n".join([f"<{tag}>", *lines, f"</{tag}>"])

    def manifest(self) -> dict[str, Any]:
        """Ids, channels, positions and costs; never the lines."""
        return {
            "budget": self.budget,
            **({"budget_rule": self.budget_rule} if self.budget_rule else {}),
            "tokens": self.tokens,
            "items": [
                {
                    "id": s.item.item_id,
                    "kind": s.item.kind,
                    "channels": list(s.channels),
                    "position": s.position,
                    "tokens": s.item.tokens,
                    **({"value_type": s.item.value_type} if s.item.value_type else {}),
                }
                for s in self.slots
            ],
            "derived": [d.kind for d in self.derived],
            "channels": {
                h.channel: {"hits": len(h.ranked), **({"skipped": True} if h.skipped else {})}
                for h in self.hits
            },
            **(
                {
                    "guards": [
                        {"id": g.item_id, "value_type": g.type.value, "arm": self.guard_arm}
                        for g in self.guards
                    ]
                }
                if self.guards
                else {}
            ),
        }

    def why(self) -> dict[str, Any]:
        """Per slot, why it was chosen, and the rule of each derived line: ids, positions and numbers,
        never a line, a value or the question. The receipt keeps it on every read with slots."""
        return {
            **({"weights_version": self.weights_version} if self.weights_version is not None else {}),
            "items": [
                {"id": s.item.item_id, **(s.why.to_json() if s.why is not None else {})} for s in self.slots
            ],
            "derived": [d.why() for d in self.derived],
        }


def plan_slots(
    turn: TurnAnalysis,
    pool: Sequence[SlotItem],
    hits: Sequence[ChannelHits],
    *,
    fmt: Formatter,
    voice: bool,
    budget: int,
    max_items: int,
    episodes: Sequence[EpisodeRef] = (),
    now: datetime,
    withheld: int = 0,
    unproven: bool = False,
    withheld_said: bool = False,
    outside: frozenset[str] = frozenset(),
    weights: Mapping[str, float] | None = None,
    weights_version: int | None = None,
    guards: Sequence[Guard] = (),
    guard_arm: str = GUARDS_ARM,
    lexicon: Lexicon = EMPTY_LEXICON,
    budget_rule: str | None = None,
    unseen: UnseenValues = NOTHING_UNSEEN,
) -> SlotPlan:
    """The slots of one turn: derived lines first, then the fused items in rank order while they fit.
    `withheld` and `unproven` say whether items the policy kept from this reader may hold the answer;
    `withheld_said`, that the pinned body already says so. `outside` are the items an anchored window
    excludes ("after the inspection"): conversations, values, objects and actions among them never take
    a slot; facts and open items hold whatever the window.

    A conversation goes as its atom (17, 6.3), its matter and the value that answers the turn, when the
    view is voice and the turn asks for a value, a count or a day, or when its whole line does not fit the
    room left; otherwise whole, and only one of the best matches is cut to the sentences that answer.

    `guards` (13, 6.1) come first and are the knapsack's floor: their tokens are taken before anything
    else, and the value a guard states takes no slot of its own. For the control group (`guard_arm`)
    they are only recorded: the lines are the bytes the turn would have had without them.

    The negatives are honest: when a value of the kind asked exists that this reader does
    not see (`unseen`), "no record" is never said; when a verification would release it, the line says so
    and tells the agent to ask for it. When the turn is about one conversation (`asked_conversation`) that
    holds no value of the kind asked, the line says that of that conversation."""
    if budget <= 0 or turn.empty:
        return SlotPlan(
            budget=max(budget, 0), hits=tuple(hits), weights_version=weights_version, budget_rule=budget_rule
        )
    everything = {i.item_id: i for i in pool}
    for channel in hits:
        for item in channel.items:
            everything.setdefault(item.item_id, item)
    held_now = _held_now(turn, everything)
    fused = [
        f
        for f in fuse(hits, weights)
        if not _superseded(everything.get(f.item_id), turn, pool, lexicon)
        and not _replaced(everything.get(f.item_id), turn, held_now)
    ]
    derived: list[Derived] = []

    def add(kind: str, text: str, rule: str, basis: tuple[tuple[str, Any], ...]) -> None:
        line = fmt.line(DERIVED_SECTION[kind], text, voice=voice)
        derived.append(Derived(kind, line, estimate_tokens(line + "\n"), rule, basis))

    shown_guards = tuple(guards) if guard_arm != CONTROL_ARM else ()
    for guard in shown_guards:
        derived.append(
            Derived(
                GUARD_KIND,
                guard.line,
                guard.tokens,
                guard.rule,
                (("basis", guard.item_id), ("value_type", guard.type.value), ("origin", guard.origin.value)),
            )
        )
    guarded = {g.item_id for g in shown_guards}
    covered: frozenset[str] = frozenset()
    if turn.question is QuestionKind.COUNT:
        if topical := topic_count(turn, lexicon, everything, episodes, fmt, now, numbered=not voice):
            text, covered, rule, basis = topical
            add("count", text, rule, basis)
        elif counted := count_line(fused, everything, episodes, fmt, now):
            text, covered = counted
            add("count", text, *_count_rule(fused, everything, episodes, covered))
    asked = (
        ("asked_types", tuple(turn.asked_types[:2])),
        ("identifiers", min(len(turn.identifiers), 2)),
    )
    if (held := _held_kind(turn, unseen, everything)) is not None:
        add("withheld", fmt.word("withheld_record", what=fmt.term(held)), "withheld_record", asked)
    matter = asked_conversation(turn, lexicon, pool)
    negatives = matter_negatives(turn, matter, everything) if matter is not None else []
    if matter is not None and negatives:
        what = " / ".join(fmt.term(t) for t in negatives[:2])
        where = fmt.word("in_conversation", channel=matter.channel or "", date=_day_of(matter, fmt))
        about = value_subject(matter.text)
        # Without the word "record": a question saying "the number of that record" read "no record of the
        # protocol" as "no number at all" (the local A/B of 27/09, pt-147). The numbers it names follow.
        where += f" ({about})" if about else ""
        text = fmt.word("none_given_in", what=what, where=where)
        # Every number it names, even one the matter above says: without "numbers in it: 25964" agents
        # answered "I do not have the record's number" beside "about shipment 25964" (the A/B of 28/09).
        if numbers := identifiers_in(matter.text.partition(" · ")[2])[:NAMED_NUMBERS]:
            text += "; " + fmt.word("numbers_named", numbers=", ".join(numbers))
        add("no_record", text, "no_record_matter", (*asked, ("basis", matter.item_id)))
    if (
        held is None
        and not negatives
        and not unseen.hides(turn)
        and (missing := _missing(turn, fused, everything, hits, fmt))
    ):
        if not (withheld and unproven):
            add("no_record", fmt.word("no_record", what=missing), "no_record", asked)
        elif not withheld_said:
            add(
                "withheld",
                fmt.word("withheld_may_hold", count=withheld),
                "withheld_may_hold",
                (*asked, ("withheld", withheld)),
            )
    room = budget - sum(d.tokens for d in derived)
    slots: list[Slot] = []
    # A number a derived line already says ("no protocol in that conversation; numbers in it: 36866")
    # takes no value line of its own.
    shown: set[str] = {n for d in derived for n in identifiers_in(d.line)}
    # On voice, the conversations a chosen value of the kind asked was settled in: the value's line answers
    # with its matter, channel and day, and an atom of the same conversation would say it again (tpl-12).
    answered: set[str] = set()
    # Numbers the body or a chosen item says; a derived line's numbers are not among them, since the
    # conversation it describes still says what the numbers are (the A/B of 28/09, pt-160).
    told = {n for i in pool if i.in_body for n in identifiers_in(i.text)}
    for position, entry in enumerate(fused, start=1):
        candidate = everything.get(entry.item_id)
        if candidate is None or candidate.in_body or candidate.item_id in covered or len(slots) >= max_items:
            continue  # the body says it, or the count line already dates it
        if candidate.item_id in guarded:
            continue  # the guard line states it
        if candidate.item_id in outside and candidate.kind in WINDOWED:
            continue
        if matter is not None and off_matter(candidate, matter, lexicon, turn):
            continue  # the turn is about one conversation: another matter's number never answers it
        if (
            candidate.kind == "value"
            and set(identifiers_in(candidate.text.partition(" · ")[0])) <= shown
            and candidate.value_type not in negatives
        ):
            # A line already chosen says this number (the value's own, not its matter's); a value of a kind
            # a derived line says is missing always goes, since it says otherwise.
            continue
        if voice and candidate.kind == "episode" and candidate.item_id in answered:
            continue
        candidate, cut, atom = shortest_answer(
            candidate, turn, room, voice=voice, top=position <= EXCERPT_RANKS
        )
        if atom and (ids := set(identifiers_in(candidate.text))) and ids <= told:
            continue  # an atom whose numbers the body or a chosen item already says adds nothing (tpl-12)
        if candidate.tokens <= room:
            why = SlotWhy(entry.ranks, entry.score, weights_version, cut, atom)
            slots.append(Slot(candidate, entry.channels, position, why))
            room -= candidate.tokens
            shown |= set(identifiers_in(candidate.text))
            told |= set(identifiers_in(candidate.text))
            if candidate.kind == "value" and candidate.source and candidate.value_type in turn.asked_types:
                answered.add(candidate.source)
    # A value line whose number a conversation chosen after it says whole goes (tpl-12): the conversation
    # says it with what was asked and done.
    whole = [s for s in slots if s.item.kind == "episode" and not (s.why and (s.why.excerpt or s.why.atom))]
    said = {n for s in whole for n in identifiers_in(s.item.text)}
    slots = [
        s
        for s in slots
        if not (s.item.kind == "value" and set(identifiers_in(s.item.text.partition(" · ")[0])) <= said)
    ]
    return SlotPlan(
        tuple(slots),
        tuple(derived),
        budget,
        tuple(hits),
        weights_version,
        tuple(guards),
        guard_arm,
        budget_rule,
    )


def asks_value(turn: TurnAnalysis) -> bool:
    """The turn asks for a value (a number it names, a kind of value), a count or a day: the question sets
    the slots' budget of a voice turn (17, 6.5), by rule, so the same turn always gets the same bytes."""
    return bool(turn.identifiers or turn.asked_types) or turn.question in (
        QuestionKind.COUNT,
        QuestionKind.WHEN,
    )


def slot_budget_rule(turn: TurnAnalysis) -> str:
    return "question" if asks_value(turn) else "turn"


def max_slots(view: str, turn: TurnAnalysis) -> int:
    """Lines a turn's slots hold at most: the view's, or `QUESTION_MAX_SLOTS` when the turn asks a value."""
    if asks_value(turn) and view in QUESTION_MAX_SLOTS:
        return QUESTION_MAX_SLOTS[view]
    return MAX_SLOTS.get(view, DEFAULT_MAX_SLOTS)


def shortest_answer(
    item: SlotItem, turn: TurnAnalysis, room: int, *, voice: bool, top: bool
) -> tuple[SlotItem, bool, bool]:
    """The line a chosen item takes, with whether it was cut and whether it is an atom. A conversation
    takes its atom that answers the turn (17, 6.3) on a voice turn that asks for a value, a count or a day,
    and on any turn when its whole line does not fit `room`; one of the `top` matches that still does not
    fit is cut to its sentences that answer. Anything else goes whole, or not at all."""
    wanted = item.kind == "episode" and (
        item.tokens > room or (voice and (asks_value(turn) or item.headline))
    )
    if wanted and (atom := best_atom(item.text, turn)) is not None:
        # Of a conversation the body pins as its headline, the atom's value alone, dated as the headline is.
        said = f"{atom.head} · {atom.value}" if item.headline and atom.value else atom.text
        line = item.line.replace(item.text, said)
        if voice and not line.endswith((".", "!", "?")):
            line += "."  # voice reads sentences (7.2)
        shorter = replace(item, text=said, line=line, tokens=estimate_tokens(line + "\n"))
        if shorter.tokens <= room:
            return shorter, False, True
    if item.tokens > room and top and (cut := excerpt(item, turn, room)) is not None:
        return cut, True, False
    return item, False, False


_SENTENCE = re.compile(r"(?<=[.!?;])\s+|\s+/\s+")


def excerpt(item: SlotItem, turn: TurnAnalysis, room: int) -> SlotItem | None:
    """The item cut to the sentences that answer the turn, when they fit `room`: its short fields (date,
    channel, outcome) stay, and of its long text only the sentences holding a number the turn names, a
    value of a type it asks for, a date or its words, marked with an ellipsis. None when nothing answers or
    nothing fits: the whole line stays in memory, a history search away."""
    fields = item.text.split(" · ")
    longest = max(range(len(fields)), key=lambda i: (len(fields[i]), -i))
    sentences = [s for s in _SENTENCE.split(fields[longest]) if s]
    if len(sentences) < 2:
        return None
    wanted_ids, asked, topic = set(turn.identifiers), set(turn.asked_types), set(turn.topic)

    def score(sentence: str) -> int:
        found = set(identifiers_in(sentence))
        points = 4 * bool(found & wanted_ids) + 3 * bool(asked & set(typed_values(sentence)))
        number = bool(re.search(r"\d", sentence))
        points += number * (3 if turn.question is QuestionKind.WHEN else 1) + len(
            topic & set(tokenize(sentence))
        )
        return points

    ranked = sorted(range(len(sentences)), key=lambda i: (-score(sentences[i]), i))
    keep: list[int] = []
    for i in ranked:
        if score(sentences[i]) < 2:
            break
        trial = sorted([*keep, i])
        if not _cut_fits(item, fields, longest, sentences, trial, room):
            continue
        keep = trial
    if not keep:
        return None
    if 0 not in keep and _cut_fits(item, fields, longest, sentences, [0, *keep], room):
        keep = [0, *keep]  # the first sentence says what the matter was (17, 6.3)
    text = _with_field(fields, longest, _joined(sentences, keep))
    line = item.line.replace(item.text, text)
    return replace(item, text=text, line=line, tokens=estimate_tokens(line + "\n"))


def _joined(sentences: Sequence[str], keep: Sequence[int]) -> str:
    """The kept sentences, each gap marked with an ellipsis."""
    inner = " ... ".join(sentences[j] for j in keep)
    return ("" if keep[0] == 0 else "... ") + inner + ("" if keep[-1] == len(sentences) - 1 else " ...")


def _cut_fits(
    item: SlotItem,
    fields: Sequence[str],
    index: int,
    sentences: Sequence[str],
    keep: Sequence[int],
    room: int,
) -> bool:
    line = item.line.replace(item.text, _with_field(fields, index, _joined(sentences, keep)))
    return estimate_tokens(line + "\n") <= room


def _held_kind(turn: TurnAnalysis, unseen: UnseenValues, items: Mapping[str, SlotItem]) -> str | None:
    """The kind of value asked for that nothing this reader sees holds and a verification would release: by
    the kind asked, or by a number the turn names."""
    kinds = {kind for kind, _ in unseen.withheld}
    for name in turn.asked_types:
        if name in kinds and not any(
            i.value_type == name or name in typed_values(i.text) for i in items.values()
        ):
            return name
    seen = {n for i in items.values() for n in identifiers_in(i.text)}
    return next(
        (kind for kind, number in unseen.withheld if number in turn.identifiers and number not in seen), None
    )


def _day_of(item: SlotItem, fmt: Formatter) -> str:
    return fmt.date(item.at) if item.at is not None else ""


def _with_field(fields: Sequence[str], index: int, value: str) -> str:
    return " · ".join([*fields[:index], value, *fields[index + 1 :]])


def _superseded(
    item: SlotItem | None, turn: TurnAnalysis, pool: Sequence[SlotItem], lexicon: Lexicon = EMPTY_LEXICON
) -> bool:
    """An older line holding a value of a type the turn asks for, which the pinned body already answers
    with its own value, and which says nothing more of what the turn is about: "the ZIP code you have
    now" gets the body's, never the one it replaced. A line naming a number the turn names stays.

    What the turn is about is read through the lexicon, as the lexical channel reads it: "my card was
    declined at the grocery store" is what "cartão recusado no supermercado" says, so a protocol of that
    conversation is never taken for a replaced value of the body's other protocol. The local A/B of 27/09
    lost three long-history answers that way (en-141, en-143, en-145): read word by word, a question in
    English shared no word with a line in Portuguese, and every line with a protocol was dropped."""
    if item is None or item.in_body or not turn.asked_types:
        return False
    asked = set(turn.asked_types)
    mine = asked & _types(item)
    if not mine or set(turn.identifiers) & set(identifiers_in(item.text)):
        return False
    answering = [i for i in pool if i.in_body and mine & _types(i)]
    if not answering:
        return False
    units = _matter_units(lexicon, turn)
    keys = lexicon.keys(item.text)
    said = [lexicon.keys(i.text) for i in answering]
    return not any(u.matched_by(keys) and not any(u.matched_by(k) for k in said) for u in units)


_EARLIER = re.compile(
    r"\b(?:antig[oa]s?|antigu[oa]s?|anterior(?:es)?|viej[oa]s?|old|older|previous|former|prior|before|antes)\b"
)
"""A turn that asks for the value a person held before ("my old ZIP code") keeps every one of them."""


def _held_now(
    turn: TurnAnalysis, items: Mapping[str, SlotItem]
) -> dict[str, tuple[datetime, frozenset[str]]]:
    """For each kind asked that a person holds one of at a time, when the latest one was said and its
    digits: a settled value, or a line naming the kind beside it. Empty when the turn asks for an earlier
    one."""
    if _EARLIER.search(turn.folded):
        return {}
    held: dict[str, tuple[datetime, frozenset[str]]] = {}
    for name in turn.asked_types:
        if name not in ONE_AT_A_TIME:
            continue
        dated = [
            (i.at, digits)
            for i in items.values()
            if i.at is not None
            for digits in (typed_values(i.text).get(name) or ())
        ]
        if dated:
            latest = max(at for at, _ in dated)
            held[name] = (latest, frozenset(d for at, d in dated if at == latest))
    return held


def _replaced(
    item: SlotItem | None, turn: TurnAnalysis, held: Mapping[str, tuple[datetime, frozenset[str]]]
) -> bool:
    """A line stating only a value the person no longer holds, said before the one that holds now: "the
    ZIP code you have today" never gets the one it replaced beside it, which an agent read as the answer
    (pt-138, the local A/B of 27/09). A line naming a number the turn names stays."""
    if item is None or item.at is None or not held:
        return False
    if set(turn.identifiers) & set(identifiers_in(item.text)):
        return False
    typed = typed_values(item.text)
    for name, (latest, digits) in held.items():
        said = set(typed.get(name) or ())
        if said and not said & digits and item.at < latest:
            return True
    return False


_COUNT_WORDS = """
    reclamar reclamei reclamou reclamamos reclamacao reclamacoes queixa queixas queixei queixar complain
    complained complaining complaint complaints reclame reclamo reclamado reclamaciones queja quejas quejado
    ligacao ligacoes call calls llamada llamadas atendimento contato contatos contact contacts contacto
    vez vezes time times veces again antes before already ainda agora now
"""
_COUNT_GENERIC = frozenset(WORD_KEY + stem(w) for w in fold_words(_COUNT_WORDS))
"""What any count question says ("quantas vezes eu já reclamei ... antes desta ligação"): not its topic."""
TOPIC_MIN_UNITS = 2
"""A conversation is about the matter asked when it answers this many of the question's units (fewer
when the best one answers fewer): "item faltando na entrega" is "faltou um item na minha entrega" and
"a entrega veio incompleta", never "recebi um item com defeito"."""


def topic_count(
    turn: TurnAnalysis,
    lexicon: Lexicon,
    items: Mapping[str, SlotItem],
    episodes: Sequence[EpisodeRef],
    fmt: Formatter,
    now: datetime,
    *,
    numbered: bool = True,
) -> tuple[str, frozenset[str], str, tuple[tuple[str, Any], ...]] | None:
    """How many times, by the matter asked, read through the lexicon (11, 3.3): the conversations in the
    window that answer the most units of the question the best one answers, down to `TOPIC_MIN_UNITS`,
    whatever category the extraction filed them under. None when no conversation answers a unit: the
    count by category runs instead."""
    units = [u for u in turn_units(lexicon, turn) if u.word not in _COUNT_GENERIC]
    if not units:
        return None
    if by_matter := _matter_count(units, lexicon, items, episodes, fmt, now, numbered=numbered):
        return by_matter
    since = now - timedelta(days=RECURRENCE_WINDOW_DAYS)
    matched: dict[str, int] = {}
    for episode in episodes:
        item = items.get(episode.item_id)
        if item is None or not (since <= episode.started_at <= now):
            continue
        keys = lexicon.keys(item.text)
        matched[episode.item_id] = sum(u.matched_by(keys) for u in units)
    best = max(matched.values(), default=0)
    if best == 0:
        return None
    need = min(TOPIC_MIN_UNITS, best)
    counted = sorted(
        (e for e in episodes if matched.get(e.item_id, 0) >= need),
        key=lambda e: (e.started_at, e.item_id),
        reverse=True,
    )
    dates = [fmt.day(e.started_at) for e in counted]
    shown = ", ".join(dates[:COUNT_DATES]) + (", ..." if len(dates) > COUNT_DATES else "")
    complaints = all(e.intent == RECURRENCE_INTENT for e in counted)
    rule = "count_topic_complaints" if complaints else "count_topic"
    text = fmt.word(rule, days=RECURRENCE_WINDOW_DAYS, count=len(counted), dates=shown)
    basis = (
        ("units", len(units)),
        ("matched", need),
        ("counted", len(counted)),
        ("window_days", RECURRENCE_WINDOW_DAYS),
    )
    return text, frozenset(e.item_id for e in counted), rule, basis


_REFERENCE_TYPES = ("protocol", "ticket", "claim", "order", "record", "receipt", "invoice", "tracking")
_AMOUNT = re.compile(r"\d+[.,]\d{2}(?!\d)")


def _matter_count(
    units: Sequence[Unit],
    lexicon: Lexicon,
    items: Mapping[str, SlotItem],
    episodes: Sequence[EpisodeRef],
    fmt: Formatter,
    now: datetime,
    *,
    numbered: bool = True,
) -> tuple[str, frozenset[str], str, tuple[tuple[str, Any], ...]] | None:
    """The count by the matter the extraction named (17, 6.2): the question is read up to one matter, by
    its units against each matter's key and label first and its conversations' words next, and every
    conversation of that matter in the window is counted, whatever words it used and whether the body holds
    it. Keys spelled two ways (`delivery_failed`, `failed_delivery`) are one matter. The line names the
    matter and lists each occurrence (day, and on a text view the number it was registered under), so the
    agent neither counts nor guesses which matter "this" is; a voice agent says days, not record numbers
    (tpl-12). Conversations extracted before matters existed join
    when their words answer the question as `topic_count` reads them. None when no matter answers."""
    since = now - timedelta(days=RECURRENCE_WINDOW_DAYS)
    window = [e for e in episodes if since <= e.started_at <= now]
    groups: dict[frozenset[str], list[EpisodeRef]] = {}
    for episode in window:
        if episode.matter:
            groups.setdefault(signature(episode.matter), []).append(episode)
    if not groups:
        return None

    def answered(text: str) -> int:
        keys = lexicon.keys(text)
        return sum(u.matched_by(keys) for u in units)

    def score(group: Sequence[EpisodeRef]) -> tuple[int, int]:
        named = max(answered(f"{(e.matter or '').replace('_', ' ')} {e.matter_label or ''}") for e in group)
        said = max((answered(items[e.item_id].text) for e in group if e.item_id in items), default=0)
        return named, said

    scores = {key: score(group) for key, group in groups.items()}
    best = max(
        groups,
        key=lambda k: (scores[k], len(groups[k]), max(e.started_at for e in groups[k]), sorted(k)),
    )
    named, said = scores[best]
    need = min(TOPIC_MIN_UNITS, len(units))
    if max(named, said) < need and not (named and said):
        return None
    # Keys the model spelled apart for one problem (`internet_down`, `internet_outage`) answer the question
    # through their labels as well as each other: every key as good as the best by its name is that matter.
    chosen = [k for k in groups if scores[k][0] == named] if named >= need else [best]
    unkeyed = [
        e for e in window if not e.matter and e.item_id in items and answered(items[e.item_id].text) >= need
    ]
    mine = [e for k in chosen for e in groups[k]]
    counted = sorted([*mine, *unkeyed], key=lambda e: (e.started_at, e.item_id), reverse=True)
    newest = next(e for e in counted if e.matter and signature(e.matter) == best)
    label = newest.matter_label or (newest.matter or "").replace("_", " ")
    occurrences = [_occurrence(e, items, fmt) if numbered else fmt.day(e.started_at) for e in counted]
    shown = ", ".join(occurrences[:COUNT_DATES]) + (", ..." if len(occurrences) > COUNT_DATES else "")
    complaints = all(e.intent == RECURRENCE_INTENT for e in counted)
    rule = "count_matter_complaints" if complaints else "count_matter"
    text = fmt.word(rule, matter=label, days=RECURRENCE_WINDOW_DAYS, count=len(counted), dates=shown)
    basis = (
        ("units", len(units)),
        ("matched", max(named, said)),
        ("matters", len(chosen)),
        ("counted", len(counted)),
        ("window_days", RECURRENCE_WINDOW_DAYS),
    )
    return text, frozenset(e.item_id for e in counted), rule, basis


def _occurrence(episode: EpisodeRef, items: Mapping[str, SlotItem], fmt: Formatter) -> str:
    """ "07/09 16189": the day and the first number the conversation was registered under (a protocol, a
    ticket, an order), never an amount. The channel stays out: a count line pays for every occurrence."""
    head = fmt.day(episode.started_at)
    item = items.get(episode.item_id)
    if item is None:
        return head
    typed = typed_values(item.text)
    references = {v for t in _REFERENCE_TYPES for v in typed.get(t, ())}
    amounts = {re.sub(r"\D", "", a) for a in _AMOUNT.findall(item.text)}
    ref = next((v for v in identifiers_in(item.text) if v in references and v not in amounts), None)
    return f"{head} {ref}" if ref else head


def count_line(
    fused: Sequence[Fused],
    items: Mapping[str, SlotItem],
    episodes: Sequence[EpisodeRef],
    fmt: Formatter,
    now: datetime,
) -> tuple[str, frozenset[str]] | None:
    """How many times, by the topic asked (11, 3.3): the category of the best-ranked complaint the channels
    found for this turn, counted over every episode the reader may see, with the dates, and the episodes
    counted. The pack's history line counted the last conversation's category, whatever the question."""
    by_id = {e.item_id: e for e in episodes}
    since = now - timedelta(days=RECURRENCE_WINDOW_DAYS)
    ranked = [by_id[f.item_id] for f in fused if f.item_id in by_id and f.item_id in items]
    complaints = [e for e in ranked if e.intent == RECURRENCE_INTENT]
    if not ranked:
        return None
    category = (complaints or ranked)[0].category
    same = [e for e in episodes if e.category == category and since <= e.started_at <= now]
    counted = [e for e in same if e.intent == RECURRENCE_INTENT] if complaints else same
    if not counted:
        return None
    dates = [
        fmt.day(e.started_at) for e in sorted(counted, key=lambda e: (e.started_at, e.item_id), reverse=True)
    ]
    shown = ", ".join(dates[:COUNT_DATES]) + (", ..." if len(dates) > COUNT_DATES else "")
    key = "count_complaints" if complaints else "count_conversations"
    text = fmt.word(
        key, category=fmt.term(category), days=RECURRENCE_WINDOW_DAYS, count=len(counted), dates=shown
    )
    return text, frozenset(e.item_id for e in counted)


def _count_rule(
    fused: Sequence[Fused],
    items: Mapping[str, SlotItem],
    episodes: Sequence[EpisodeRef],
    counted: frozenset[str],
) -> tuple[str, tuple[tuple[str, Any], ...]]:
    """The rule behind a count line, as `count_line` applied it: which ranked conversation chose the
    category, and how many were counted over which window."""
    by_id = {e.item_id: e for e in episodes}
    ranked = [by_id[f.item_id] for f in fused if f.item_id in by_id and f.item_id in items]
    complaints = [e for e in ranked if e.intent == RECURRENCE_INTENT]
    chosen = (complaints or ranked)[0]
    rule = "count_complaints" if complaints else "count_conversations"
    return rule, (
        ("basis", chosen.item_id),
        ("category", chosen.category),
        ("counted", len(counted)),
        ("window_days", RECURRENCE_WINDOW_DAYS),
    )


def _missing(
    turn: TurnAnalysis,
    fused: Sequence[Fused],
    items: Mapping[str, SlotItem],
    hits: Sequence[ChannelHits],
    fmt: Formatter,
) -> str | None:
    """What the turn asked for that memory has no line for, as the words of the no-record line; None when
    something answers. Said only when no exact match exists and, for a kind of value asked, nothing the
    lexical channel found holds a value: a line that may answer in other words is never contradicted. A
    number the turn names that no line holds is never answered in other words."""
    if not (turn.asked_types or turn.identifiers):
        return None
    exact = {h.channel for h in hits if h.channel in ("exact", "values") and h.ranked}
    if exact:
        return None
    if turn.identifiers:
        return ", ".join(turn.identifiers[:2])
    lexical = next((h for h in hits if h.channel == "lexical"), None)
    if lexical and any(identifiers_in(items[i].text) for i in lexical.ranked if i in items):
        return None
    if any(f.item_id in items and "semantic" in f.channels for f in fused):
        return None
    return " / ".join(fmt.term(t) for t in turn.asked_types[:2])
