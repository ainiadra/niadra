"""Which per-turn slots the agent used, and so which retrieval channel pays its way (11, 3.4).

A read with a question leaves a `slots` receipt beside the pack's: the items the
turn's slots carried, each with the channels that ranked it (exact, values, lexical, temporal,
semantic, linked) and its position in the fused ranking, and which channels ran or were skipped; and
why each was chosen (its position in each channel, each channel's share of the fused score). The agent's
answer to that turn is judged by the rules of 19.2, applied to the slotted items: a statement about the
same predicate or object, a value it repeats, or an action on the object is use; asking for it again is
repetition; saying the opposite is contradiction. Each channel that brought an item gets the credit.

A read may also write guard lines (13, 6.1): a settled value the agent must not state otherwise. The
answer goes against a guard when the SDK said so on the agent's turn, or when it states another value
of the guarded type and never the guarded one (`domain.serve.guards.violates`). A violation in the
guards arm is a contradiction of the value, marked `guard`; the control arm's guards were never shown,
and their would-be violations are counted apart, which is what the guards are measured against.

A turn the intent gate served without slots (13, 6.4: thanks, a confirmation, a request to repeat, a
greeting) is measured apart: whether the agent then asked the customer for something memory holds, before
its next read. It is counted under `GATED`, never under a retrieval channel, so it calibrates the gate and
moves no weight.

Only ids, kinds, flags and counters: the question stays a keyed hash in the receipt, and no line of
memory enters these rows.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any
from uuid import UUID

from niadra.domain.compile.model import public_item_id
from niadra.domain.extract.result import QuestionType
from niadra.domain.measure.model import (
    AgentAnswer,
    AgentClaim,
    AgentQuestion,
    ContextUseRow,
    DeliveryOutcome,
    ItemOutcome,
    ManifestItem,
    SessionAction,
)
from niadra.domain.measure.normalize import normalize_value
from niadra.domain.measure.signals import MeasureRules, SessionEvidence, pertinent
from niadra.domain.memory.values import ValueType, value_key
from niadra.domain.serve.guards import GUARDS_ARM, VALUE_ARM, short_id, violates
from niadra.domain.serve.turn import identifiers_in

VALUE_KIND = "value"
GATED = "gate:none"
"""The counters of the turns the intent gate served without slots: `turns`, and `repeated` for those the
agent followed by asking the customer for something memory holds (13, 6.4). Not a retrieval channel."""
GATED_INTENT = "none"
READ_SLACK_S = 1.0
"""A read's pack and slots receipts are drafted a moment apart: a slot belongs to the pack read within
this many seconds before it."""


@dataclass(frozen=True, slots=True)
class SlotEntry:
    """One slotted item as the receipt names it: internal id (`episode:<uuid>`), never its line."""

    item_id: str
    kind: str
    channels: tuple[str, ...]
    position: int
    tokens: int = 0
    why: Mapping[str, Any] | None = None
    """What the receipt's `why` says of this slot, the linked match by its public id; None when it says
    nothing of it."""
    value_type: str | None = None
    """A settled value's type, as the read named it."""

    @property
    def public_id(self) -> str:
        """The id manifests and claims use: `<uuid>`, as for every pack item (5.13)."""
        return public_item_id(self.item_id)


@dataclass(frozen=True, slots=True)
class GuardEntry:
    """A guard a read wrote (13, 6.1): the value it states, its type, and the customer's arm."""

    item_id: str
    value_type: str
    arm: str

    @property
    def public_id(self) -> str:
        return public_item_id(self.item_id)

    @property
    def short_id(self) -> str:
        return short_id(self.public_id)


@dataclass(frozen=True, slots=True)
class ChannelRun:
    hits: int = 0
    skipped: bool = False


@dataclass(frozen=True, slots=True)
class SlotDelivery:
    """The slots one read put in front of one source's agent."""

    source_id: UUID
    read_at: datetime
    items: tuple[SlotEntry, ...]
    channels: tuple[tuple[str, ChannelRun], ...] = ()
    experiment_group: str = ""
    derived: tuple[Mapping[str, Any], ...] = ()
    """The rule of each derived line of the read, as the receipt's `why` says it."""
    weights_version: int | None = None
    guards: tuple[GuardEntry, ...] = ()
    intent: str | None = None
    """`none` for a turn the intent gate served without slots (13, 6.4)."""

    @property
    def gated(self) -> bool:
        return self.intent == GATED_INTENT


@dataclass(frozen=True, slots=True)
class GuardOutcome:
    """What the agent's answer did with one guard: went against it, and whether the SDK said so."""

    entry: GuardEntry
    violated: bool = False
    flagged: bool = False
    """The SDK's backing check named the guard on the agent's turn (and `guard.violated` left then)."""
    evidence: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.entry.public_id,
            "value_type": self.entry.value_type,
            "arm": self.entry.arm,
            "violated": self.violated,
            "flagged": self.flagged,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class SlotOutcome:
    entry: SlotEntry
    used: bool = False
    repeated: bool = False
    contradicted: bool = False
    evidence: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.entry.public_id,
            "kind": self.entry.kind,
            "channels": list(self.entry.channels),
            "position": self.entry.position,
            "used": self.used,
            "repeated": self.repeated,
            "contradicted": self.contradicted,
            "evidence": list(self.evidence),
            **({"value_type": self.entry.value_type} if self.entry.value_type else {}),
            **({"why": dict(self.entry.why)} if self.entry.why is not None else {}),
        }


@dataclass(frozen=True, slots=True)
class MeasuredSlots:
    """One slots delivery with the agent's verdicts on each item."""

    delivery: SlotDelivery
    items: tuple[SlotOutcome, ...]
    guards: tuple[GuardOutcome, ...] = ()
    asked_again: tuple[str, ...] = ()
    """For a gated turn: the agent's questions after it, before its next read, asking the customer for
    something memory holds."""

    def to_json(self) -> dict[str, Any]:
        if self.delivery.gated:
            return {
                "at": self.delivery.read_at.isoformat(),
                "intent": GATED_INTENT,
                "channels": {},
                "items": [],
                "asked_again": bool(self.asked_again),
                "evidence": list(self.asked_again),
            }
        return {
            "at": self.delivery.read_at.isoformat(),
            "channels": {
                name: {"hits": run.hits, **({"skipped": True} if run.skipped else {})}
                for name, run in self.delivery.channels
            },
            "items": [i.to_json() for i in self.items],
            **({"derived": [dict(d) for d in self.delivery.derived]} if self.delivery.derived else {}),
            **(
                {"weights_version": self.delivery.weights_version}
                if self.delivery.weights_version is not None
                else {}
            ),
            **({"guards": [g.to_json() for g in self.guards]} if self.guards else {}),
        }


@dataclass(slots=True)
class SlotCounters:
    """Per retrieval channel: reads it ran in, reads where it missed its deadline, and the slots it
    brought with what the agent did with them. An item two channels ranked counts for both."""

    turns: int = 0
    skipped: int = 0
    slots: int = 0
    used: int = 0
    repeated: int = 0
    contradicted: int = 0

    def add(self, other: "SlotCounters", sign: int = 1) -> None:
        for name in SLOT_COUNTER_FIELDS:
            setattr(self, name, getattr(self, name) + sign * getattr(other, name))

    def negated(self) -> "SlotCounters":
        negated = SlotCounters()
        negated.add(self, sign=-1)
        return negated

    def as_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in SLOT_COUNTER_FIELDS}


SLOT_COUNTER_FIELDS = tuple(SlotCounters.__dataclass_fields__)


@dataclass(frozen=True, slots=True)
class SlotDailyKey:
    day: date
    source_id: UUID
    channel: str
    """The conversation's channel (voice, whatsapp...), as in `context_use_daily`."""
    view: str
    experiment_group: str
    slot_channel: str
    """The retrieval channel that brought the slot: exact, values, lexical, temporal, semantic."""


@dataclass(frozen=True, slots=True)
class SlotDailyRow:
    key: SlotDailyKey
    counters: SlotCounters = field(default_factory=SlotCounters)


def measure_slots(
    deliveries: Sequence[SlotDelivery],
    known: Mapping[str, ManifestItem],
    evidence: SessionEvidence,
    rules: MeasureRules,
) -> list[MeasuredSlots]:
    """The verdicts on every slotted item. The agent's answer to a turn is what the source said, asked or
    did after that read and up to its next slots read; `known` holds what measurement knows of each item
    by public id (predicate, object, and a value's raw text), and an item it does not know is judged by
    its id alone."""
    ordered = sorted(deliveries, key=lambda d: (d.read_at, str(d.source_id)))
    measured = []
    for index, delivery in enumerate(ordered):
        if delivery.gated:
            after = next((d.read_at for d in ordered[index + 1 :] if d.source_id == delivery.source_id), None)
            asked = () if rules.task_session else _asked_again(evidence, delivery, after, known, rules)
            measured.append(MeasuredSlots(delivery, (), (), asked))
            continue
        until = next(
            (d.read_at for d in ordered[index + 1 :] if d.source_id == delivery.source_id and d.items), None
        )
        questions = [] if rules.task_session else _answer(evidence.questions, delivery, until)
        claims, actions = (
            _answer(evidence.claims, delivery, until),
            _answer(evidence.actions, delivery, until),
        )
        answers = _answer(evidence.answers, delivery, until)
        items = []
        for entry in delivery.items:
            item = known.get(entry.public_id) or ManifestItem(entry.public_id, entry.kind)
            items.append(_judge(entry, item, questions, claims, actions, delivery.source_id, rules, answers))
        guards = tuple(_guard(g, known.get(g.public_id), answers) for g in delivery.guards)
        measured.append(MeasuredSlots(delivery, tuple(items), guards))
    return measured


def _asked_again(
    evidence: SessionEvidence,
    delivery: SlotDelivery,
    until: datetime | None,
    known: Mapping[str, ManifestItem],
    rules: MeasureRules,
) -> tuple[str, ...]:
    """The questions after a gated turn that asked the customer for something the pack or its reserve
    held, by the repetition rules of 19.2: what a gate that was wrong would cost."""
    found = []
    for question in _answer(evidence.questions, delivery, until):
        if question.type in (QuestionType.VERIFIES, QuestionType.AMBIGUOUS):
            continue
        if question.type is QuestionType.CONFIRMS and delivery.source_id in rules.confirms_before_acting:
            continue
        if pertinent(known.values(), question.predicate, question.object_ref):
            found.append(str(question.event_id))
    return tuple(dict.fromkeys(found))


def _guard(entry: GuardEntry, item: ManifestItem | None, answers: Sequence[AgentAnswer]) -> GuardOutcome:
    """The SDK's word on the turn, else the rule over the answer's text; a value memory no longer holds
    (erased) is judged by the SDK's word alone."""
    evidence = []
    flagged = False
    for answer in answers:
        named = entry.short_id in answer.guard_violations
        said = (
            item is not None
            and bool(item.value)
            and violates(entry.value_type, item.value or "", answer.text)
        )
        if named or said:
            evidence.append(str(answer.event_id))
            flagged = flagged or named
    return GuardOutcome(entry, bool(evidence), flagged, tuple(evidence))


def _answer[T: (AgentQuestion, AgentClaim, SessionAction, AgentAnswer)](
    turns: Iterable[T], delivery: SlotDelivery, until: datetime | None
) -> list[T]:
    return [
        t
        for t in turns
        if t.source_id == delivery.source_id and t.at > delivery.read_at and (until is None or t.at <= until)
    ]


def _judge(
    entry: SlotEntry,
    item: ManifestItem,
    questions: Sequence[AgentQuestion],
    claims: Sequence[AgentClaim],
    actions: Sequence[SessionAction],
    source_id: UUID,
    rules: MeasureRules,
    answers: Sequence[AgentAnswer] = (),
) -> SlotOutcome:
    used = repeated = contradicted = False
    seen: list[str] = []
    for answer in answers:
        if _states_another(item, answer.text):
            contradicted = True
            seen.append(str(answer.event_id))
    for question in questions:
        if question.type in (QuestionType.VERIFIES, QuestionType.AMBIGUOUS):
            continue
        if question.type is QuestionType.CONFIRMS and source_id in rules.confirms_before_acting:
            continue
        if pertinent([item], question.predicate, question.object_ref):
            repeated = True
            seen.append(str(question.event_id))
    for claim in claims:
        if item.item_id in claim.contradicts or _differs(item, claim, rules):
            contradicted = True
            seen.append(str(claim.event_id))
        elif pertinent([item], claim.predicate, claim.object_ref) or _says_value(item, claim):
            used = True
            seen.append(str(claim.event_id))
    for action in actions:
        closes = action.closes_item_id is not None and action.closes_item_id == item.item_id
        if closes or (action.object_id is not None and action.object_id == item.object_id):
            used = True
            seen.append(str(action.action_id))
    return SlotOutcome(entry, used, repeated, contradicted, tuple(dict.fromkeys(seen)))


def _differs(item: ManifestItem, claim: AgentClaim, rules: MeasureRules) -> bool:
    """A single-valued fact the agent stated with another value (19.2)."""
    if item.kind != "fact" or item.predicate not in rules.single_valued or item.predicate != claim.predicate:
        return False
    if item.value is None or claim.value is None:
        return False
    return normalize_value(item.value) != normalize_value(claim.value)


def _states_another(item: ManifestItem, text: str) -> bool:
    """A settled value the answer contradicts: another value of its type, and never this one (m4)."""
    if item.kind != VALUE_KIND or not item.value or item.value_type not in _VALUE_TYPES:
        return False
    return violates(item.value_type, item.value, text)


_VALUE_TYPES = frozenset(t.value for t in ValueType)


def _says_value(item: ManifestItem, claim: AgentClaim) -> bool:
    """A settled value the agent said back: the same number, however it was punctuated."""
    if item.kind != VALUE_KIND or not item.value or not claim.value:
        return False
    numbers = set(identifiers_in(item.value))
    if numbers:
        return bool(numbers & set(identifiers_in(claim.value)))
    return value_key(item.value) == value_key(claim.value)


def attach(
    outcomes: Sequence[DeliveryOutcome], measured: Sequence[MeasuredSlots]
) -> dict[int, list[MeasuredSlots]]:
    """Each slots delivery joins the pack delivery its read served: the same source's latest pack read at
    or before it, or its first one. A source with no pack delivery has nothing to join."""
    joined: dict[int, list[MeasuredSlots]] = {}
    for slots in measured:
        mine = [(i, o) for i, o in enumerate(outcomes) if o.delivery.source_id == slots.delivery.source_id]
        if not mine:
            continue
        before = [
            (i, o)
            for i, o in mine
            if (o.delivery.delivered_at - slots.delivery.read_at).total_seconds() <= READ_SLACK_S
        ]
        index = max(before, key=lambda p: p[1].delivery.delivered_at)[0] if before else mine[0][0]
        joined.setdefault(index, []).append(slots)
    return joined


def without_slotted_misses(outcome: DeliveryOutcome, slotted: Iterable[MeasuredSlots]) -> DeliveryOutcome:
    """A contradiction of an item the skeleton held back and a slot then carried is judged on the slot,
    never as a selection miss: the agent had it."""
    carried = {i.entry.public_id for s in slotted for i in s.items}
    kept = tuple(
        i for i in outcome.items if not (i.selection_miss and not i.trimmed and i.item_id in carried)
    )
    return outcome if len(kept) == len(outcome.items) else replace(outcome, items=kept)


def with_guard_violations(outcome: DeliveryOutcome, slotted: Iterable[MeasuredSlots]) -> DeliveryOutcome:
    """A guard the agent had and went against is a contradiction of the value it stated, marked
    `guard`: one per value, whatever the number of turns. The control arm's are counted apart."""
    found: dict[str, list[str]] = {}
    for slots in slotted:
        for guard in slots.guards:
            if guard.violated and guard.entry.arm == GUARDS_ARM:
                found.setdefault(guard.entry.public_id, []).extend(guard.evidence)
    if not found:
        return outcome
    kept = tuple(i for i in outcome.items if i.item_id not in found)
    marked = tuple(
        ItemOutcome(item_id, VALUE_KIND, contradicted=True, evidence=tuple(dict.fromkeys(ev)), guard=True)
        for item_id, ev in sorted(found.items())
    )
    return replace(outcome, items=kept + marked)


def guard_violations(row: ContextUseRow) -> int:
    """Guards of the guards arm the row's agent went against: what `guard_violations` counts."""
    if row.not_measured_reason is not None:
        return 0
    return sum(
        1
        for turn in row.slots
        for g in turn.get("guards") or ()
        if g.get("violated") and g.get("arm") == GUARDS_ARM
    )


@dataclass(slots=True)
class GuardCounters:
    """Per value type and arm: the settled values slotted or guarded, and how many the agent contradicted
    (for a guard, went against)."""

    slots: int = 0
    contradicted: int = 0

    def add(self, other: "GuardCounters", sign: int = 1) -> None:
        self.slots += sign * other.slots
        self.contradicted += sign * other.contradicted

    def negated(self) -> "GuardCounters":
        return GuardCounters(-self.slots, -self.contradicted)


@dataclass(frozen=True, slots=True)
class GuardDailyKey:
    day: date
    source_id: UUID
    value_type: str
    arm: str
    """`value` (a settled value slotted, no guard), `guards` or `control` (13, 6.1)."""


@dataclass(frozen=True, slots=True)
class GuardDailyRow:
    key: GuardDailyKey
    counters: GuardCounters = field(default_factory=GuardCounters)


def guard_counters(row: ContextUseRow) -> dict[tuple[str, str], GuardCounters]:
    """What one measured row adds per (value type, arm); like `slot_counters`, measuring again subtracts
    exactly what was added."""
    if row.not_measured_reason is not None:
        return {}
    counted: dict[tuple[str, str], GuardCounters] = {}
    for turn in row.slots:
        for item in turn.get("items") or ():
            if item.get("kind") == VALUE_KIND and item.get("value_type"):
                counters = counted.setdefault((str(item["value_type"]), VALUE_ARM), GuardCounters())
                counters.slots += 1
                counters.contradicted += int(bool(item.get("contradicted")))
        for guard in turn.get("guards") or ():
            if not guard.get("value_type") or not guard.get("arm"):
                continue
            counters = counted.setdefault((str(guard["value_type"]), str(guard["arm"])), GuardCounters())
            counters.slots += 1
            counters.contradicted += int(bool(guard.get("violated")))
    return counted


def slot_counters(row: ContextUseRow) -> dict[str, SlotCounters]:
    """What one measured row adds per retrieval channel; a row not measured adds nothing, so measuring
    again subtracts exactly what was added."""
    if row.not_measured_reason is not None:
        return {}
    counted: dict[str, SlotCounters] = {}
    for turn in row.slots:
        if turn.get("intent") == GATED_INTENT:
            gated = counted.setdefault(GATED, SlotCounters())
            gated.turns += 1
            gated.repeated += int(bool(turn.get("asked_again")))
            continue
        for name, run in (turn.get("channels") or {}).items():
            counters = counted.setdefault(str(name), SlotCounters())
            counters.turns += 1
            counters.skipped += int(bool(isinstance(run, Mapping) and run.get("skipped")))
        for item in turn.get("items") or ():
            for name in item.get("channels") or ():
                counters = counted.setdefault(str(name), SlotCounters())
                counters.slots += 1
                counters.used += int(bool(item.get("used")))
                counters.repeated += int(bool(item.get("repeated")))
                counters.contradicted += int(bool(item.get("contradicted")))
    return counted


def group_slot_daily(
    rows: Iterable[SlotDailyRow], by: Sequence[str] = ("view", "slot_channel")
) -> dict[tuple[str, ...], SlotCounters]:
    grouped: dict[tuple[str, ...], SlotCounters] = {}
    for row in rows:
        key = tuple(str(getattr(row.key, name)) for name in by)
        grouped.setdefault(key, SlotCounters()).add(row.counters)
    return grouped
