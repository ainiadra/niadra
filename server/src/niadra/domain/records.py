"""Records shared across modules.

These are the shapes one module reads from another through the ports. Repositories return
them decrypted: encryption happens inside the PostgreSQL adapters (5.14.1), so application
code never handles ciphertext and never forgets to encrypt.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from niadra.domain.privacy.purposes import Tags
from niadra.domain.state.observed import Holding
from niadra.domain.state.typed import Typed
from niadra.domain.vocabulary import (
    ActionStatus,
    EventKind,
    FactStatus,
    HandleStatus,
    HandleType,
    MemoryAudience,
    OpenItemKind,
    OpenItemStatus,
    Sensitivity,
    Speaker,
    SpokenBy,
    SubjectKind,
    Verification,
    Visibility,
)


@dataclass(frozen=True, slots=True)
class HandleRecord:
    handle_id: UUID
    type: HandleType
    scope: str
    subject_kind: SubjectKind
    value: str
    status: HandleStatus
    level: Verification
    first_seen: datetime
    last_seen: datetime
    provisional: bool = False
    """Joined to its profile by a claim nobody confirmed yet: reads through it are capped (5.3)."""


@dataclass(frozen=True, slots=True)
class ProfileRecord:
    profile_id: UUID
    kind: SubjectKind
    pseudonym: str
    membership_version: int
    privacy_version: int
    handle_ids: tuple[UUID, ...] = ()
    org_type: str | None = None
    provisional_handle_ids: tuple[UUID, ...] = ()
    """Members joined by a claim nobody confirmed yet (5.3), a subset of `handle_ids`."""
    hidden_handle_ids: tuple[UUID, ...] = ()
    """Only on a reader's view of the profile (`domain.identity.provenance`): members whose memory this
    reader may not see, already left out of `handle_ids`."""


@dataclass(frozen=True, slots=True)
class LinkRecord:
    link_id: UUID
    person_handle_id: UUID
    org_handle_id: UUID
    role: str
    can_see_contacts: bool
    valid_from: datetime
    valid_to: datetime | None


@dataclass(frozen=True, slots=True)
class ObjectKey:
    type: str
    namespace: str
    external_id: str


@dataclass(frozen=True, slots=True)
class EventRecord:
    event_id: UUID
    kind: EventKind
    source_id: UUID
    channel: str
    speaker: Speaker
    occurred_at: datetime
    received_at: datetime
    handle_ids: tuple[UUID, ...]
    text: str | None = None
    conversation_id: str | None = None
    task_id: str | None = None
    canonical_type: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    object_refs: tuple[ObjectKey, ...] = ()
    subjects: tuple[dict[str, Any], ...] = ()
    action: dict[str, Any] | None = None
    visibility: Visibility = Visibility.PUBLIC
    stt_confidence: float | None = None
    speaker_id: str | None = None
    corrects_event_id: UUID | None = None
    # Content format details and the gate's marks (for instance `injection`); never the text itself.
    content_meta: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session_id: UUID
    kind: str
    source_id: UUID
    channel: str
    opened_at: datetime
    last_event_at: datetime
    conversation_id: str | None = None
    task_id: str | None = None
    profile_id: UUID | None = None
    closed_at: datetime | None = None
    close_reason: str | None = None
    proven_level: Verification | None = None
    proven_until: datetime | None = None
    initiated_by: str | None = None
    extracted_version: str | None = None
    reopened: int = 0
    """How many times a late event reopened this session after it closed."""
    ended_at: datetime | None = None
    """When `conversation.ended` or `task.ended` said the activity ended: a turn at or before it belongs
    here whenever it arrives, and one after it starts a new session."""


@dataclass(frozen=True, slots=True)
class Fact:
    fact_id: UUID
    origin_handle_id: UUID
    predicate: str
    value: str
    category: str
    sensitivity: Sensitivity
    speaker: Speaker
    confidence: float
    valid_at: datetime
    created_at: datetime
    status: FactStatus
    last_confirmed_at: datetime
    namespace: str = "tenant"
    invalid_at: datetime | None = None
    expired_at: datetime | None = None
    times_seen: int = 1
    origin_conversation_id: str | None = None
    about_handle_id: UUID | None = None
    object_ref: ObjectKey | None = None
    mention_handle_ids: tuple[UUID, ...] = ()
    evidence_event_ids: tuple[UUID, ...] = ()
    valid_until: datetime | None = None
    """What the application declared about how long it holds (an offer valid until a date): after it the
    fact leaves the context and the search, without changing its status (10, D.1)."""
    sessions_seen: int = 1
    """Distinct sessions whose turns support the value: the fact's confirmation, where `times_seen` counts
    every new piece of evidence (a value repeated ten times in one conversation is one session)."""
    purposes: Tags | None = None
    """The purposes it was collected for and their legal bases then (13, 6.6); None until it is tagged."""
    audience: MemoryAudience | None = None
    """Set on a fact about an object only: who besides the person it is stored under may read it.
    None for every other fact, which never reaches another participant."""

    def valid_at_time(self, now: datetime) -> bool:
        return self.valid_until is None or self.valid_until > now


def declared_validity(events: "list[EventRecord]") -> datetime | None:
    """The earliest `valid_until` the evidence events declared: a fact holds as long as all of them do."""
    ends = [
        datetime.fromisoformat(e.content_meta["valid_until"])
        for e in events
        if e.content_meta and e.content_meta.get("valid_until")
    ]
    return min(ends) if ends else None


@dataclass(frozen=True, slots=True)
class Episode:
    episode_id: UUID
    version: int
    origin_handle_id: UUID
    session_id: UUID
    channel: str
    source_id: UUID
    summary: str
    intent: str
    category: str
    normalized_category: str
    outcome: str
    started_at: datetime
    ended_at: datetime
    resolution: str | None = None
    sentiment: float | None = None
    initiated_by: str | None = None
    conversation_id: str | None = None
    about_handle_id: UUID | None = None
    purposes: Tags | None = None
    spoken_by: SpokenBy | None = None
    """Whose words the conversation holds (`domain.memory.spoken`), for the mirror rule; None when unknown,
    which the rule never lifts."""
    matter: str | None = None
    """The extraction's key for what the conversation is about (`delivery_failed`): conversations about
    the same problem in other words share it, and a count by matter reads it (17, 6.2)."""
    matter_label: str | None = None
    object_refs: tuple[ObjectKey, ...] = ()
    """The objects the conversation was about, as its turns named them or the extraction resolved them:
    the conversations about an object are a filter, never a search."""


@dataclass(frozen=True, slots=True)
class OpenItem:
    item_id: UUID
    origin_handle_id: UUID
    description: str
    owner: str
    status: OpenItemStatus
    created_at: datetime
    origin: dict[str, Any]
    due_at: datetime | None = None
    about_handle_id: UUID | None = None
    object: ObjectKey | None = None
    expected_operation: str | None = None
    closed_by_action_id: UUID | None = None
    closed_at: datetime | None = None
    purposes: Tags | None = None
    kind: OpenItemKind = OpenItemKind.PROMISE
    audience: MemoryAudience | None = None
    """Set on an item about an object only; None for every other item."""


@dataclass(frozen=True, slots=True)
class ProfileAttr:
    origin_handle_id: UUID
    slot: str
    value: str
    source_id: UUID
    observed_at: datetime
    times_seen: int = 1


@dataclass(frozen=True, slots=True)
class BusinessObject:
    object_id: UUID
    key: ObjectKey
    state: dict[str, Any]
    as_of: datetime
    source_id: UUID
    origin_handle_id: UUID | None = None
    record_ref: str | None = None
    state_version: str | None = None
    fields: Holding = field(default_factory=dict)
    """Each field's observations by source (24.3.1); `state` is what they resolve to."""
    revision: int = 0
    """The row's change sequence as read or written: orders the copies of its state the read path keeps."""
    derived_status: str | None = None
    """For a derived object (24.3.7): `current`, or `expired_by_input` once an input it names changed."""
    typed: Typed = field(default_factory=Typed)
    """Latches, computed values' versions, timers and the inputs that expired it (24.3.6 to 24.3.8)."""


@dataclass(frozen=True, slots=True)
class AgentAction:
    action_id: UUID
    event_id: UUID
    source_id: UUID
    operation: str
    status: ActionStatus
    occurred_at: datetime
    object_id: UUID | None = None
    result: str | None = None
    purpose: str | None = None
    closes_item_id: UUID | None = None
    closes_handle_id: UUID | None = None
    confirmed_by_event_id: UUID | None = None
    corrects_action_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Trait:
    trait_id: UUID
    profile_id: UUID
    membership_version: int
    name: str
    value: str
    layer: str
    rule_version: str
    first_seen: datetime
    last_evidence_at: datetime
    expires_at: datetime
    confidence: float
    evidence: tuple[tuple[str, UUID], ...] = ()
    retracted_at: datetime | None = None
