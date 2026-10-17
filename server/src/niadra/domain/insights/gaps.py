"""Knowledge gaps and standing answers of a space (13, 6.5): what customers ask that memory has no record
of, what agents have to ask customers for, which source could record it, and the questions memory answers
every time by the same rule.

All by rule, without a model, from what the product already keeps: the `slots` receipts of each read with
a customer's turn (the value types the turn asked for and the derived line the rules wrote: `no_record`,
`withheld` or none) and the extraction's `agent_questions` of each measured conversation. The unit is a
subject (a value type or a predicate) in a conversation category. Only counts leave the cell: turns, and
customers through a HyperLogLog sketch of salted hashes, so a month counts each customer once and no id,
value or text is stored or returned.
"""

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class GapKind(StrEnum):
    NO_RECORD = "no_record"
    """A customer's turn asked for a value type and memory wrote `no_record`."""
    AGENT_QUESTION = "agent_question"
    """An agent asked a returning customer for a predicate memory had no row of."""
    ANSWERED = "answered"
    """A customer's turn asked for a value type and the rules answered it; the recurring questions."""
    OBJECT_UNRESOLVED = "object_unresolved"
    """A conversation named an object of a type by its natural key, and the customer is tied to no object
    with it: what was said stayed on the person. The subject is the type."""


IDENTIFIER = "identifier"
"""The subject of a `no_record` line for an identifier the turn named without saying its type."""
PRECISION = 12
REGISTERS = 1 << PRECISION
DIGEST_SIZE = 8
EXACT_LIMIT = 256
"""Below this many distinct customers, a sketch keeps an exact set of their salted digests instead of
trusting the HyperLogLog estimate: at these small cardinalities a single register collision (about a 1.6%
chance per pair at 2^12 registers) can move the reported count by one, which a knowledge-gaps report of a
handful of customers cannot absorb. Past the limit, the exact set is dropped and the registers alone carry
the count, where the standard error is small relative to the cardinality."""
_EXACT_MARKER = b"E"


class Sketch:
    """A HyperLogLog of 2^12 one-byte registers (standard error about 1.6%), with linear counting for the
    cardinalities up to ten thousand most gaps have, and an exact set of salted digests for cardinalities up
    to `EXACT_LIMIT` so a small gap's count is never off by a collision. Mergeable: the union of two days is
    the maximum per register, and the union of two exact sets while both stay within the limit."""

    def __init__(self, registers: bytes | None = None, exact: frozenset[bytes] | None = None) -> None:
        if registers is None:
            self.registers = bytearray(REGISTERS)
            self.exact: frozenset[bytes] | None = frozenset() if exact is None else exact
            return
        head, trailer = registers[:REGISTERS], registers[REGISTERS:]
        self.registers = bytearray(head)
        if len(self.registers) != REGISTERS:
            raise ValueError(f"a sketch has {REGISTERS} registers")
        if exact is not None:
            self.exact = exact
        elif trailer[:1] == _EXACT_MARKER:
            count = int.from_bytes(trailer[1:3], "big")
            body = trailer[3 : 3 + count * DIGEST_SIZE]
            self.exact = frozenset(body[i : i + DIGEST_SIZE] for i in range(0, len(body), DIGEST_SIZE))
        else:
            self.exact = None

    def add(self, salt: bytes, member: UUID) -> None:
        digest = hashlib.blake2b(member.bytes, digest_size=DIGEST_SIZE, key=salt[:64]).digest()
        value = int.from_bytes(digest, "big")
        index = value >> (64 - PRECISION)
        rest = (value << PRECISION) & ((1 << 64) - 1)
        rank = min(64 - PRECISION, (64 - rest.bit_length()) if rest else 64 - PRECISION) + 1
        if rank > self.registers[index]:
            self.registers[index] = rank
        if self.exact is not None:
            grown = self.exact | {digest}
            self.exact = grown if len(grown) <= EXACT_LIMIT else None

    def merge(self, other: "Sketch") -> "Sketch":
        registers = bytes(max(a, b) for a, b in zip(self.registers, other.registers, strict=True))
        exact = None
        if self.exact is not None and other.exact is not None:
            union = self.exact | other.exact
            exact = union if len(union) <= EXACT_LIMIT else None
        return Sketch(registers, exact)

    def count(self) -> int:
        if self.exact is not None:
            return len(self.exact)
        zeros = self.registers.count(0)
        if zeros == REGISTERS:
            return 0
        if zeros:
            linear = REGISTERS * math.log(REGISTERS / zeros)
            if linear <= 2.5 * REGISTERS:
                return round(linear)
        alpha = 0.7213 / (1 + 1.079 / REGISTERS)
        return round(alpha * REGISTERS * REGISTERS / sum(2.0**-r for r in self.registers))

    def to_bytes(self) -> bytes:
        body = bytes(self.registers)
        if self.exact is None:
            return body
        encoded = b"".join(sorted(self.exact))
        return body + _EXACT_MARKER + len(self.exact).to_bytes(2, "big") + encoded


@dataclass(frozen=True, slots=True)
class GapKey:
    kind: GapKind
    subject: str
    topic: str = ""
    """The conversation's normalized category; empty when the read had no measured conversation."""


@dataclass
class GapDay:
    """One day's counters of one key: turns, and the customers as a sketch."""

    day: date
    key: GapKey
    turns: int = 0
    customers: Sketch = field(default_factory=Sketch)


def slot_signals(detail: Mapping[str, Any] | None) -> list[tuple[GapKind, str]]:
    """What one `slots` receipt says about a gap, from the ids and kinds it keeps: a `no_record` line per
    asked type (or `identifier` when the turn named only a number), `answered` per asked type when the rules
    wrote neither `no_record` nor `withheld`. A `withheld` line is a verification matter, not a gap."""
    if not detail:
        return []
    why = _json(detail.get("why"))
    analysis = _json(detail.get("analysis"))
    derived = [d for d in why.get("derived") or [] if isinstance(d, Mapping)]
    kinds = {str(d.get("kind")) for d in derived}
    if "withheld" in kinds:
        return []
    missing = next((d for d in derived if d.get("kind") == "no_record"), None)
    if missing is not None:
        asked = [str(t) for t in missing.get("asked_types") or []]
        return [(GapKind.NO_RECORD, t) for t in asked] or [(GapKind.NO_RECORD, IDENTIFIER)]
    return [(GapKind.ANSWERED, str(t)) for t in analysis.get("asked_types") or []]


@dataclass(frozen=True, slots=True)
class AskedQuestion:
    """An agent's question of an extraction: the predicate it asked the customer for, and when."""

    predicate: str
    at: datetime


def question_gaps(
    questions: Iterable[AskedQuestion], known: Mapping[str, datetime], *, returning: bool
) -> list[str]:
    """The predicates an agent had to ask a returning customer for that memory held no row of before the
    question. `known` is, per predicate, when memory first had a row of it. A first contact has no memory
    to miss, so it counts nothing."""
    if not returning:
        return []
    found = []
    for q in questions:
        first = known.get(q.predicate)
        if first is None or first >= q.at:
            found.append(q.predicate)
    return sorted(set(found))


@dataclass(frozen=True, slots=True)
class SourceHint:
    source_id: str
    name: str
    via: str
    """`mapping`: the source's webhook mapping carries the type; `schema`: extraction captures the
    predicate when a conversation states it."""


@dataclass(frozen=True, slots=True)
class Gap:
    key: str
    subject: str
    topic: str
    kind: GapKind
    customers: int
    turns: int
    could_record: tuple[SourceHint, ...]
    """No source: nothing sends it to memory. A source: it maps the type, yet these customers had none."""
    answered_share: float | None = None
    """For a value type, how many of the turns that asked for it memory answered, when it answered some."""


@dataclass(frozen=True, slots=True)
class Recurring:
    key: str
    subject: str
    topic: str
    customers: int
    turns: int
    answered_share: float


@dataclass(frozen=True, slots=True)
class Proposal:
    """A standing answer for the team to approve, as the extraction schema is approved."""

    key: str
    kind: str
    """`not_recorded`: a subject no source records, whose absence memory must never present as the
    customer's; `settled`: a question the rules answer nearly every time, worth a line of its own."""
    subject: str
    topic: str
    text: str


@dataclass(frozen=True, slots=True)
class GapsReport:
    since: date
    until: date
    gaps: tuple[Gap, ...]
    recurring: tuple[Recurring, ...]
    proposals: tuple[Proposal, ...]
    withheld_groups: int
    """Groups left out because fewer customers than the minimum group size were in them."""
    reviews: Mapping[str, tuple[str, str, datetime, str | None]] = field(default_factory=dict)
    """Per gap or proposal key, the team's decision, who took it, when and the note."""


GAPS_KEEP_DAYS = 400
"""Days of gap rows kept: a year and a month, enough to compare a month with the same one a year before."""
SETTLED_SHARE = 0.9
TOP_RECURRING = 10


def report(
    rows: Sequence[GapDay],
    *,
    since: date,
    until: date,
    min_customers: int,
    hints: Mapping[str, Sequence[SourceHint]],
    language: str,
) -> GapsReport:
    """The window's gaps, the recurring questions and the proposals; groups with fewer customers than
    `min_customers` are counted as withheld, never shown."""
    merged: dict[GapKey, tuple[int, Sketch]] = {}
    for row in rows:
        turns, sketch = merged.get(row.key, (0, Sketch()))
        merged[row.key] = (turns + row.turns, sketch.merge(row.customers))
    answered = {(k.subject, k.topic): v for k, v in merged.items() if k.kind is GapKind.ANSWERED}
    missing = {(k.subject, k.topic): v for k, v in merged.items() if k.kind is GapKind.NO_RECORD}
    gaps: list[Gap] = []
    recurring: list[Recurring] = []
    withheld = 0
    for key, (turns, sketch) in sorted(
        merged.items(), key=lambda kv: (kv[0].kind, kv[0].subject, kv[0].topic)
    ):
        customers = sketch.count()
        if customers < min_customers:
            withheld += 1
            continue
        pair = (key.subject, key.topic)
        if key.kind is GapKind.ANSWERED:
            lost = missing.get(pair, (0, Sketch()))[0]
            recurring.append(
                Recurring(
                    gap_key(key), key.subject, key.topic, customers, turns, round(turns / (turns + lost), 3)
                )
            )
            continue
        share = None
        if key.kind is GapKind.NO_RECORD and pair in answered:
            hit = answered[pair][0]
            share = round(hit / (hit + turns), 3)
        gaps.append(
            Gap(
                gap_key(key),
                key.subject,
                key.topic,
                key.kind,
                customers,
                turns,
                tuple(hints.get(key.subject, ())),
                share,
            )
        )
    gaps.sort(key=lambda g: (-g.customers, -g.turns, g.key))
    recurring.sort(key=lambda r: (-r.customers, -r.turns, r.key))
    recurring = recurring[:TOP_RECURRING]
    return GapsReport(
        since, until, tuple(gaps), tuple(recurring), tuple(proposals(gaps, recurring, language)), withheld
    )


def proposals(gaps: Sequence[Gap], recurring: Sequence[Recurring], language: str) -> list[Proposal]:
    words = _WORDS.get(language[:2], _WORDS["en"])
    found: list[Proposal] = []
    for gap in gaps:
        if gap.kind is GapKind.OBJECT_UNRESOLVED:
            continue  # an object the company's system did not send: nothing for memory to propose
        if gap.could_record or gap.subject == IDENTIFIER or gap.answered_share:
            continue
        text = words["not_recorded"].format(what=_label(gap.subject))
        found.append(
            Proposal(
                f"proposal:not_recorded:{gap.subject}:{gap.topic}",
                "not_recorded",
                gap.subject,
                gap.topic,
                text,
            )
        )
    for question in recurring:
        if question.answered_share < SETTLED_SHARE:
            continue
        text = words["settled"].format(what=_label(question.subject), topic=_label(question.topic) or "-")
        found.append(
            Proposal(
                f"proposal:settled:{question.subject}:{question.topic}",
                "settled",
                question.subject,
                question.topic,
                text,
            )
        )
    return found


def gap_key(key: GapKey) -> str:
    """A review's key: the kind, subject and category, never customer data."""
    return f"{key.kind.value}:{key.subject}:{key.topic}"


@dataclass(frozen=True, slots=True)
class MappedType:
    """What one mapping rule of a source says it brings: a canonical type, object types and field names."""

    source_id: str
    source_name: str
    words: tuple[str, ...]


def source_hints(
    subjects: Iterable[str],
    mapped: Sequence[MappedType],
    predicates: Mapping[str, Sequence[tuple[str, str]]],
    stems: Mapping[str, Sequence[str]],
) -> dict[str, list[SourceHint]]:
    """Per subject, the sources that could record it: a mapping whose canonical type, object type or field
    names start with a stem of the value type, or a predicate the schema declares (for every source whose
    conversations extraction reads, `predicates[name]`)."""
    found: dict[str, list[SourceHint]] = {}
    for subject in sorted(set(subjects)):
        wanted = tuple(stems.get(subject, ())) or (subject,)
        hints = {
            (m.source_id, m.source_name, "mapping")
            for m in mapped
            if any(_fold(word).startswith(stem) or stem in _fold(word) for word in m.words for stem in wanted)
        }
        hints |= {(sid, name, "schema") for sid, name in predicates.get(subject, ())}
        found[subject] = [SourceHint(*h) for h in sorted(hints)]
    return found


def _fold(text: str) -> str:
    return text.lower().replace("_", " ").replace(".", " ")


def _label(subject: str) -> str:
    return subject.replace("_", " ")


def _json(raw: Any) -> Mapping[str, Any]:
    if isinstance(raw, Mapping):
        return raw
    if isinstance(raw, str | bytes):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, Mapping) else {}
    return {}


_WORDS: dict[str, dict[str, str]] = {
    "pt": {
        "not_recorded": (
            "Nenhum sistema envia {what} para a memória: a falta de registro não quer dizer que o cliente "
            "não tenha {what}; consulte o sistema de origem antes de responder."
        ),
        "settled": (
            "Pergunta recorrente sobre {what} em {topic}: a memória responde pela regra; "
            "mantenha a linha pronta."
        ),
    },
    "en": {
        "not_recorded": (
            "No system sends {what} to memory: no record does not mean the customer has no {what}; check the "
            "system of record before answering."
        ),
        "settled": (
            "Recurring question about {what} in {topic}: memory answers it by rule; keep the line ready."
        ),
    },
    "es": {
        "not_recorded": (
            "Ningún sistema envía {what} a la memoria: la falta de registro no significa que el cliente no "
            "tenga {what}; consulte el sistema de origen antes de responder."
        ),
        "settled": (
            "Pregunta recurrente sobre {what} en {topic}: la memoria la responde por regla; "
            "mantenga la línea lista."
        ),
    },
}
