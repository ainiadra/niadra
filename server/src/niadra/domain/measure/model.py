"""What context-use measurement reads and writes (19): deliveries, manifest items, agent turns and the
measured rows."""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from niadra.domain.compile.trim import unit_of
from niadra.domain.extract.result import QuestionType

MEASURE_RULES = "m4"
"""Bump when a rule of 19.2 changes; together with the extractor versions it forms `measure_version`.
m3: the per-turn slots are measured; contradicting a slotted item is no selection miss.
m4: a slotted settled value is contradicted when the agent's answer states another value of its type
and never it; a guard line the answer went against is a contradiction of its value (13, 6.1)."""

SESSION_LEVEL_VIEW = ""
"""Daily rows that count sessions (coverage, transfers, recontacts) carry no view."""


class NotMeasured(StrEnum):
    NOT_EXTRACTED = "not_extracted"
    PARTIAL_TRANSCRIPT = "partial_transcript"
    MANIFEST_MISSING = "manifest_missing"
    RECEIPT_GAP = "receipt_gap"


class TransferState(StrEnum):
    NONE = "none"
    READ = "read"
    UNREAD = "unread"
    NOT_INTEGRATED = "not_integrated"


class RecontactScope(StrEnum):
    CATEGORY = "category"
    GENERAL = "general"


@dataclass(frozen=True, slots=True)
class ManifestItem:
    """One entry of a manifest (P7). `value` is joined from memory for facts; manifests never hold values."""

    item_id: str
    kind: str
    predicate: str | None = None
    category: str | None = None
    object_ref: str | None = None
    object_id: str | None = None
    value: str | None = None
    value_type: str | None = None
    """A settled value's type, joined from memory like `value`."""
    bound: tuple[tuple[str, str], ...] = ()
    """(kind, id) of the rows about the object an object's line said."""

    @property
    def unit(self) -> str:
        return unit_of(self.kind, self.category)


@dataclass(frozen=True, slots=True)
class Manifest:
    manifest_hash: str
    view: str
    items: tuple[ManifestItem, ...]
    withheld: tuple[ManifestItem, ...] = ()
    trimmed: tuple[ManifestItem, ...] = ()
    """Items left out because the source did not use their unit (15.8)."""
    tokens_saved: int = 0


@dataclass(frozen=True, slots=True)
class Delivery:
    """Every receipt of one (manifest, source) in the session, collapsed to its first read (19.3)."""

    manifest_hash: str
    source_id: UUID
    delivered_at: datetime
    holdout: bool = False
    experiment_group: str = ""
    etag: str | None = None
    profile_id: UUID | None = None
    """The customer the pack was about, from the receipt: a session with nobody identified in its own
    events is still measured by the profile the agent read."""


@dataclass(frozen=True, slots=True)
class ContextStamp:
    """The SDK's stamp on an agent's turn: the pack its prompt carried and when it went in (5.18, 19.2)."""

    source_id: UUID
    injected_at: datetime
    etag: str | None = None


@dataclass(frozen=True, slots=True)
class AgentQuestion:
    type: QuestionType
    event_id: UUID
    source_id: UUID
    at: datetime
    predicate: str | None = None
    object_ref: str | None = None


@dataclass(frozen=True, slots=True)
class AgentClaim:
    event_id: UUID
    source_id: UUID
    at: datetime
    predicate: str | None = None
    value: str | None = None
    object_ref: str | None = None
    contradicts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentAnswer:
    """An agent's message as measurement reads it: the text, opened for the rules and never stored, and
    what the SDK's backing check reported on it (13, 6.2)."""

    event_id: UUID
    source_id: UUID
    at: datetime
    text: str
    checked: int | None = None
    """Values the SDK's check read; None when the turn carried no report."""
    unbacked: int = 0
    guard_violations: tuple[str, ...] = ()
    """Short ids of the guard lines the SDK found the answer going against."""


@dataclass(frozen=True, slots=True)
class SessionAction:
    """An action a source registered during the session; it counts as use of the item it depends on."""

    action_id: UUID
    source_id: UUID
    at: datetime
    object_id: str | None = None
    closes_item_id: str | None = None


@dataclass(frozen=True, slots=True)
class Handoff:
    at: datetime
    from_source_id: UUID
    target_source_id: UUID | None


@dataclass(frozen=True, slots=True)
class ItemOutcome:
    item_id: str
    kind: str
    delivered: bool = True
    used: bool = False
    repeated: bool = False
    contradicted: bool = False
    evidence: tuple[str, ...] = ()
    unit: str | None = None
    """Set on the items of a measured delivery the agent saw, and on a trimmed item it needed: what the
    trim of its source counts (15.8). Rows measured before the trim existed carry none."""
    selection_miss: bool = False
    """A contradiction of an item the pack did not carry: a selection failure, never the agent's."""
    trimmed: bool = False
    """Left out for lack of use, and the agent needed it: it asked, stated or acted on it."""
    guard: bool = False
    """The contradiction went against a guard line stating this value (13, 6.1)."""

    def to_json(self) -> dict[str, Any]:
        entry: dict[str, Any] = {"id": self.item_id, "kind": self.kind}
        if self.unit is not None:
            entry["unit"] = self.unit
        if self.guard:
            entry["guard"] = True
        if not self.delivered:
            entry |= {"delivered": False, "selection_miss": self.selection_miss}
            if self.trimmed:
                entry["trimmed"] = True
            return {**entry, "evidence": list(self.evidence)}
        return {
            **entry,
            "used": self.used,
            "repeated": self.repeated,
            "contradicted": self.contradicted,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """The measured signals of one delivery; `not_measured` keeps the row visible instead of a zero."""

    delivery: Delivery
    view: str
    items: tuple[ItemOutcome, ...] = ()
    questions: int = 0
    repeated: int = 0
    late: bool = False
    not_measured: NotMeasured | None = None
    tokens_saved: int = 0
    """Tokens the pack left out for lack of use; zero for a holdout, which delivered nothing."""
    object_rows: int = 0
    """Rows about an object the pack's object lines carried."""
    object_rows_used: int = 0
    """Of those, the rows of objects the agent used."""
    instruction_violations: int = 0
    """Agent messages that did what an instruction about an object the pack carried said not to do."""

    @property
    def used(self) -> int:
        return sum(1 for i in self.items if i.used)

    @property
    def contradicted(self) -> int:
        return sum(1 for i in self.items if i.contradicted)


@dataclass(frozen=True, slots=True)
class ContextUseRow:
    """One row of `context_use`: per (conversation, manifest, source, measure version)."""

    conversation_key: str
    manifest_hash: str
    source_id: UUID
    measure_version: str
    session_id: UUID
    profile_id: UUID
    channel: str
    view: str
    experiment_group: str
    items: tuple[dict[str, Any], ...]
    questions: int
    repeated: int
    used: int
    contradicted: int
    late: bool
    measured_at: datetime
    transfer_unread: bool | None = None
    not_measured_reason: str | None = None
    tokens_saved: int = 0
    slots: tuple[dict[str, Any], ...] = ()
    """The per-turn slots this delivery's agent got, one entry per read that carried them: the channels
    that ran and, per slotted item, the channels that brought it and what the agent did with it."""
    object_rows: int = 0
    object_rows_used: int = 0
    instruction_violations: int = 0


@dataclass(frozen=True, slots=True)
class MeasuredSession:
    session_id: UUID
    measure_version: str
    conversation_key: str
    source_id: UUID
    channel: str
    profile_id: UUID | None
    experiment_group: str
    day: date
    ended_at: datetime
    measured_at: datetime
    no_context: bool
    normalized_category: str | None = None
    initiated_by: str | None = None
    transfer_state: TransferState | None = None
    recontact_scope: RecontactScope | None = None


@dataclass(frozen=True, slots=True)
class DailyKey:
    day: date
    source_id: UUID
    channel: str
    view: str
    experiment_group: str


@dataclass(slots=True)
class DailyCounters:
    deliveries: int = 0
    deliveries_used: int = 0
    questions: int = 0
    repeated: int = 0
    contradicted: int = 0
    transfers: int = 0
    transfers_unread: int = 0
    not_measured: int = 0
    recontacts: int = 0
    sessions: int = 0
    late_deliveries: int = 0
    no_context: int = 0
    selection_misses: int = 0
    trimmed_deliveries: int = 0
    tokens_saved: int = 0
    answers_checked: int = 0
    """Agent messages the SDK's backing check read (13, 6.2)."""
    unbacked_values: int = 0
    """Values those messages stated with no source."""
    guard_violations: int = 0
    """Guard lines agents went against (13, 6.1)."""
    object_rows: int = 0
    """Rows about an object delivered on its line."""
    object_rows_used: int = 0
    instruction_violations: int = 0
    """Agent messages against an instruction about an object."""

    def add(self, other: "DailyCounters", sign: int = 1) -> None:
        for name in COUNTER_FIELDS:
            setattr(self, name, getattr(self, name) + sign * getattr(other, name))

    def as_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in COUNTER_FIELDS}


COUNTER_FIELDS = tuple(DailyCounters.__dataclass_fields__)


@dataclass(frozen=True, slots=True)
class DailyRow:
    key: DailyKey
    counters: DailyCounters = field(default_factory=DailyCounters)
