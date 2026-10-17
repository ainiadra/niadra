"""The validated output of one extraction (5.7) and the run that produced it.

Everything here already passed the hard validation: evidence points at events of the session,
ids point at items that were sent as candidates, enums are closed. `reconcile` applies it and
the measurement (19.3) reads the agent blocks, so the shape is stored with the run.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from niadra.domain.memory.values import ValueType
from niadra.domain.records import ObjectKey
from niadra.domain.vocabulary import OpenItemKind, Sensitivity, Speaker


class RunStatus(StrEnum):
    OK = "ok"
    INVALID = "invalid"
    FAILED = "failed"  # the template episode stands in; the nightly job extracts again (5.7)
    MINIMAL = "minimal"  # the decision models said the session is not worth an LLM call


class FactOp(StrEnum):
    ADD = "add"
    RETIRE = "retire"


class QuestionType(StrEnum):
    ASKS_DATA = "asks_data"
    CONFIRMS = "confirms"
    VERIFIES = "verifies"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class MemoryRef:
    """A fact or an open item: memory rows are keyed by their origin handle (L6)."""

    origin_handle_id: UUID
    id: UUID


@dataclass(frozen=True, slots=True)
class EpisodeDraft:
    summary: str
    intent: str
    category: str
    outcome: str
    resolution: str | None = None
    sentiment: float | None = None
    matter: str | None = None
    """What the conversation is about, as a short English key the customer's other conversations share
    (`internet_down`): a count by matter reads it, whatever words each conversation used (17, 6.2)."""
    matter_label: str | None = None
    """The key in a few words of the customer's language, for the count line."""


@dataclass(frozen=True, slots=True)
class FactCommand:
    op: FactOp
    predicate: str
    evidence_event_ids: tuple[UUID, ...]
    namespace: str = "tenant"
    value: str = ""
    category: str = ""
    sensitivity: Sensitivity = Sensitivity.NORMAL
    speaker: Speaker = Speaker.CUSTOMER
    certainty: float = 1.0
    valid_at: datetime | None = None
    target: MemoryRef | None = None
    contradicts: tuple[MemoryRef, ...] = ()
    about_handle_id: UUID | None = None
    object_ref: ObjectKey | None = None
    quarantined: bool = False
    from_voice: bool = False


@dataclass(frozen=True, slots=True)
class OpenItemDraft:
    description: str
    owner: str
    evidence_event_ids: tuple[UUID, ...]
    due_at: datetime | None = None
    object: ObjectKey | None = None
    expected_operation: str | None = None
    about_handle_id: UUID | None = None
    kind: OpenItemKind = OpenItemKind.PROMISE
    grounded: bool = True
    """Its object is one its own turns name (tagged with it, or writing its key); False when only the model
    cited it, as for an attendant's untagged turns."""
    relayed: bool = False
    """Said by an agent talking with the customer: its words may tell an open item again with a day of its own
    making, so its due never keeps it apart from that item (`domain.memory.items.to_judge_retelling`)."""
    agent_day: bool = False
    """Its due day was written only by that agent's turn: a day memory holds no item of the customer's due on
    is dropped when the item is written (`app.memory`), as the attendant's "até 27/10", the case's deadline to
    contest, taken for the day the client was to send the extratos (juridico-zero, 07/10/2026)."""


@dataclass(frozen=True, slots=True)
class HoursDraft:
    """The local hours a customer said they may be contacted in, on a channel (None: every channel): not
    before `not_before`, not after `not_after` (HH:MM), on `days` (ISO weekdays; empty for every day), or,
    with `lift`, that the hours they set before no longer hold. Kept as a suppression of every purpose that
    holds outside those hours (the suppression-list spec, 6.1), and as the person's contact window in their
    block."""

    channel: str | None
    not_before: str | None
    not_after: str | None
    days: tuple[int, ...]
    lift: bool
    evidence_event_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class InstructionDraft:
    """What the person said must or must not be done about one object, or, naming none,
    about the organization they speak of (a client of the firm): never inferred, never about neither."""

    text: str
    strength: str
    object: ObjectKey | None
    evidence_event_ids: tuple[UUID, ...]
    attr: str | None = None
    """`type.field` of the object's type, when the instruction is about one of its fields: a tool binding can
    then apply it."""
    about_handle_id: UUID | None = None
    """The organization an instruction about no object is about: its readers get it, as an object's do."""
    only_with: str | None = None
    """The one person it holds with, as the turns name them ("never discuss amounts with his wife Clara":
    Clara); None when it holds with anyone dealing with the object or organization. For a contact preference,
    the person it is the preference of."""
    contact: bool = False
    """How or when a person wants to be reached (a channel, hours): it says how the company reaches out,
    never what may be said to them."""


@dataclass(frozen=True, slots=True)
class SlotDraft:
    slot: str
    value: str
    evidence_event_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class ValueDraft:
    """A value the session settled, as written in the turns that state it (11, 3.2)."""

    type: ValueType
    raw: str
    evidence_event_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class AgentQuestion:
    type: QuestionType
    event_id: UUID
    predicate: str | None = None
    object_ref: ObjectKey | None = None
    """The business object asked about: measurement matches it against the items of the pack (19.2)."""


@dataclass(frozen=True, slots=True)
class AgentClaim:
    event_id: UUID
    value: str
    predicate: str | None = None
    object_ref: ObjectKey | None = None
    contradicts: tuple[UUID, ...] = ()
    """Candidate facts the statement contradicts: measurement tells a contradiction of what the pack
    carried from a selection miss, a fact the pack left out (19.2)."""


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    episode: EpisodeDraft | None
    """None for an internal agent's task that only acted (`minimal.is_agent_task`): its actions are memory
    already, and it was no conversation with the customer."""
    facts: tuple[FactCommand, ...] = ()
    open_items: tuple[OpenItemDraft, ...] = ()
    resolved_items: tuple[MemoryRef, ...] = ()
    profile: tuple[SlotDraft, ...] = ()
    agent_questions: tuple[AgentQuestion, ...] = ()
    agent_claims: tuple[AgentClaim, ...] = ()
    values: tuple[ValueDraft, ...] = ()
    dropped: int = 0
    flagged_event_ids: tuple[UUID, ...] = ()
    """Turns the decision models flagged as injection: the session's actions go to quarantine (18.4)."""
    withheld: int = 0
    """Numbers replaced by a marker because memory never keeps them (`domain/extract/withhold.py`)."""
    instructions: tuple[InstructionDraft, ...] = ()
    instruction_unscoped: int = 0
    """Instructions dropped for citing no object."""
    object_unresolved: dict[str, int] = field(default_factory=dict)
    """By type, the natural keys the turns wrote that named no object the customer is tied to."""
    hours: tuple[HoursDraft, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode": _plain(self.episode) if self.episode is not None else None,
            "facts": [_plain(f) for f in self.facts],
            "open_items": [_plain(i) for i in self.open_items],
            "resolved_items": [_plain(r) for r in self.resolved_items],
            "profile": [_plain(s) for s in self.profile],
            "agent_questions": [_plain(q) for q in self.agent_questions],
            "agent_claims": [_plain(c) for c in self.agent_claims],
            "values": [_plain(v) for v in self.values],
            "dropped": self.dropped,
            "flagged_event_ids": _plain(self.flagged_event_ids),
            "withheld": self.withheld,
            "instructions": [_plain(i) for i in self.instructions],
            "instruction_unscoped": self.instruction_unscoped,
            "object_unresolved": dict(self.object_unresolved),
            "hours": [_plain(h) for h in self.hours],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExtractionResult":
        return cls(
            episode=EpisodeDraft(**data["episode"]) if data["episode"] else None,
            facts=tuple(_fact(f) for f in data["facts"]),
            open_items=tuple(_item(i) for i in data["open_items"]),
            resolved_items=tuple(_ref(r) for r in data["resolved_items"]),
            profile=tuple(
                SlotDraft(s["slot"], s["value"], _ids(s["evidence_event_ids"])) for s in data["profile"]
            ),
            agent_questions=tuple(_question(q) for q in data["agent_questions"]),
            agent_claims=tuple(_claim(c) for c in data["agent_claims"]),
            values=tuple(
                ValueDraft(ValueType(v["type"]), v["raw"], _ids(v["evidence_event_ids"]))
                for v in data["values"]
            ),
            dropped=int(data["dropped"]),
            flagged_event_ids=_ids(data["flagged_event_ids"]),
            withheld=int(data["withheld"]),
            instructions=tuple(_instruction(i) for i in data["instructions"]),
            instruction_unscoped=int(data["instruction_unscoped"]),
            object_unresolved={str(k): int(v) for k, v in data["object_unresolved"].items()},
            hours=tuple(_hours(h) for h in data.get("hours") or ()),
        )


@dataclass(frozen=True, slots=True)
class ExtractionRun:
    session_id: UUID
    versions_key: str
    status: RunStatus
    created_at: datetime
    model: str = ""
    provider: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_micros: int = 0
    dropped_items: int = 0
    result: ExtractionResult | None = field(default=None, compare=False)
    applied_profile_id: UUID | None = None
    """The profile memory applied the run to; None before, or when there was nothing to apply it to."""
    applied_work: int | None = None
    """The profile's `compile` request that covers the applied run; None until memory applied it."""


def _plain(obj: Any) -> Any:
    """JSON-ready form of the dataclasses above: UUIDs and datetimes as strings, enums as values."""
    if hasattr(obj, "__dataclass_fields__"):
        return {name: _plain(getattr(obj, name)) for name in obj.__dataclass_fields__}
    if isinstance(obj, tuple | list):
        return [_plain(v) for v in obj]
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, StrEnum):
        return obj.value
    return obj


def _ids(values: list[str]) -> tuple[UUID, ...]:
    return tuple(UUID(v) for v in values)


def _ref(data: dict[str, str]) -> MemoryRef:
    return MemoryRef(UUID(data["origin_handle_id"]), UUID(data["id"]))


def _opt_uuid(value: str | None) -> UUID | None:
    return UUID(value) if value else None


def _opt_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _opt_key(data: dict[str, str] | None) -> ObjectKey | None:
    return ObjectKey(**data) if data else None


def _fact(d: dict[str, Any]) -> FactCommand:
    return FactCommand(
        op=FactOp(d["op"]),
        predicate=d["predicate"],
        evidence_event_ids=_ids(d["evidence_event_ids"]),
        namespace=d["namespace"],
        value=d["value"],
        category=d["category"],
        sensitivity=Sensitivity(d["sensitivity"]),
        speaker=Speaker(d["speaker"]),
        certainty=float(d["certainty"]),
        valid_at=_opt_time(d["valid_at"]),
        target=_ref(d["target"]) if d["target"] else None,
        contradicts=tuple(_ref(r) for r in d["contradicts"]),
        about_handle_id=_opt_uuid(d["about_handle_id"]),
        object_ref=_opt_key(d["object_ref"]),
        quarantined=bool(d["quarantined"]),
        from_voice=bool(d["from_voice"]),
    )


def _item(d: dict[str, Any]) -> OpenItemDraft:
    return OpenItemDraft(
        description=d["description"],
        owner=d["owner"],
        evidence_event_ids=_ids(d["evidence_event_ids"]),
        due_at=_opt_time(d["due_at"]),
        object=_opt_key(d["object"]),
        expected_operation=d["expected_operation"],
        about_handle_id=_opt_uuid(d["about_handle_id"]),
        kind=OpenItemKind(d["kind"]),
        grounded=bool(d["grounded"]),
        relayed=bool(d["relayed"]),
        agent_day=bool(d["agent_day"]),
    )


def _hours(d: dict[str, Any]) -> HoursDraft:
    return HoursDraft(
        channel=d["channel"],
        not_before=d["not_before"],
        not_after=d["not_after"],
        days=tuple(int(x) for x in d["days"]),
        lift=bool(d["lift"]),
        evidence_event_ids=_ids(d["evidence_event_ids"]),
    )


def _instruction(d: dict[str, Any]) -> InstructionDraft:
    return InstructionDraft(
        text=d["text"],
        strength=d["strength"],
        object=_opt_key(d["object"]),
        evidence_event_ids=_ids(d["evidence_event_ids"]),
        attr=d["attr"],
        about_handle_id=_opt_uuid(d["about_handle_id"]),
        only_with=d["only_with"],
        contact=bool(d["contact"]),
    )


def _question(d: dict[str, Any]) -> AgentQuestion:
    return AgentQuestion(
        type=QuestionType(d["type"]),
        event_id=UUID(d["event_id"]),
        predicate=d["predicate"],
        object_ref=_opt_key(d["object_ref"]),
    )


def _claim(d: dict[str, Any]) -> AgentClaim:
    return AgentClaim(
        event_id=UUID(d["event_id"]),
        value=d["value"],
        predicate=d["predicate"],
        object_ref=_opt_key(d["object_ref"]),
        contradicts=_ids(d["contradicts"]),
    )
