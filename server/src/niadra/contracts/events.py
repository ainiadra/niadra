"""The write side: `POST /v1/batch` items (5.2, 7.1, 7.3).

A batch mixes items of several `type`s. One bad item never fails the batch: the response
is 207 with one error per rejected item.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from niadra.contracts.common import Handle, IdStr, Model, ObjectRef, ShortStr, Subject
from niadra.contracts.signals import Interaction
from niadra.domain.vocabulary import (
    AssertionMethod,
    EventKind,
    Speaker,
    SubjectKind,
    Verification,
    Visibility,
)

MAX_EVENT_TEXT = 200_000


class SpeakerRef(Model):
    role: Speaker
    id: ShortStr | None = Field(default=None, description="Agent or attendant id inside the source.")


class Content(Model):
    type: Literal["text", "audio", "image", "file"] = "text"
    text: Annotated[str, StringConstraints(max_length=MAX_EVENT_TEXT)] | None = None
    media_ref: IdStr | None = Field(default=None, description="Reference returned by /v1/media/uploads.")
    media_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = None
    transcript: Annotated[str, StringConstraints(max_length=MAX_EVENT_TEXT)] | None = None
    stt_confidence: float | None = Field(default=None, ge=0, le=1)


class VoiceInfo(Model):
    ani: ShortStr | None = None
    dnis: ShortStr | None = None
    trunk: ShortStr | None = None
    network_attestation: Literal["A", "B", "C"] | None = None
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    end_reason: ShortStr | None = None
    recording_ref: IdStr | None = None
    turn_offset_ms: int | None = Field(default=None, ge=0)


class Closes(Model):
    """The open item an action fulfils: by id, or by object and canonical operation (18.4)."""

    item_id: UUID | None = Field(default=None, description="An open item's id.")
    object: ObjectRef | None = None
    operation: ShortStr | None = None

    @model_validator(mode="after")
    def _one_way(self) -> "Closes":
        if bool(self.item_id) == bool(self.object and self.operation):
            raise ValueError("closes takes either item_id, or object and operation")
        return self


class ActionInfo(Model):
    operation: ShortStr = Field(description="Canonical operation, e.g. `credit`, `reschedule`.")
    result: Annotated[str, StringConstraints(max_length=MAX_EVENT_TEXT)] | None = Field(
        default=None,
        description="What the agent did or delivered, in its words, kept whole: as long as an event's text.",
    )
    purpose: ShortStr | None = None
    closes: Closes | None = None
    corrects_action_id: str | None = None


class ContextStamp(Model):
    """Which context an agent's prompt carried and when it went in (19.2): the SDK sets it on the
    agent's turn, so measurement can tell a late context from an unused one."""

    etag: IdStr | None = None
    injected_at: datetime


BackedKind = Literal["amount", "date", "number", "code"]


class UnbackedValue(Model):
    """A value the agent's turn states that nothing it had backs: the kind only, never the value."""

    kind: BackedKind = Field(
        description="`amount` (with a currency or a money word), `date`, `number` (three or more digits) "
        "or `code` (letters and three or more digits)."
    )


class Backing(Model):
    """What the SDK's backing check found in an agent's turn (13, 6.2): every number, date, code and
    amount the turn states, looked up in the pack, the system records, the customer's own words and the
    tool results the agent recorded. Kinds and counts, never a value."""

    checked: int = Field(ge=0, le=500, description="Values the check read in the turn.")
    unbacked_values: list[UnbackedValue] = Field(
        default_factory=list, max_length=100, description="The values with no source, in the order said."
    )
    guard_violations: list[Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{8}$")]] = Field(
        default_factory=list,
        max_length=8,
        description="Short ids (`PackSlot.id`) of the guard lines the turn went against: a value of the "
        "guarded kind other than the guarded one.",
    )

    @model_validator(mode="after")
    def _within_checked(self) -> "Backing":
        if len(self.unbacked_values) > self.checked:
            raise ValueError("unbacked_values cannot outnumber the values checked")
        return self


class ModelUsage(Model):
    """What the model provider reported for the call that produced an agent's turn (15.3, rule 10): the
    SDK reads it from the response of every call `wrap()` sees, or takes it from the caller. Optional;
    only the agent's own turns carry it."""

    provider: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_.-]{0,63}$")] = Field(
        description="Who served the call, lowercase: `openai`, `anthropic`, a router or a cloud."
    )
    model: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,127}$")] = Field(
        description="The model the provider says answered, e.g. `gpt-4.1-2025-04-14`."
    )
    prompt_tokens: int = Field(ge=0, le=100_000_000, description="Every input token, cached ones included.")
    cached_tokens: int = Field(default=0, ge=0, description="Input tokens read from the provider's cache.")
    cache_write_tokens: int = Field(
        default=0, ge=0, description="Input tokens written to the cache (Anthropic's cache creation)."
    )

    @model_validator(mode="after")
    def _parts_fit(self) -> "ModelUsage":
        if self.cached_tokens + self.cache_write_tokens > self.prompt_tokens:
            raise ValueError("cached and written tokens are part of prompt_tokens")
        return self


class EventItem(Model):
    """A message, a system event or an agent action."""

    type: Literal["event"] = "event"
    kind: EventKind = EventKind.MESSAGE
    idempotency_key: IdStr = Field(description="Provider message id, or a UUIDv7 minted by the SDK.")
    channel: ShortStr
    conversation_id: IdStr | None = None
    conversation_aliases: list[IdStr] = Field(default_factory=list, max_length=8)
    task_id: IdStr | None = None
    handles: list[Handle] = Field(default_factory=list, max_length=16)
    subjects: list[Subject] = Field(default_factory=list, max_length=8)
    object_refs: list[ObjectRef] = Field(default_factory=list, max_length=16)
    speaker: SpeakerRef
    direction: Literal["inbound", "outbound"] | None = None
    content: Content | None = None
    occurred_at: datetime
    visibility: Visibility = Visibility.PUBLIC
    verification_hint: Verification | None = None
    canonical_type: ShortStr | None = Field(
        default=None, description="System events only, e.g. `invoice.credited` (5.8)."
    )
    fields: dict[str, Any] = Field(default_factory=dict, description="Structured fields of a system event.")
    action: ActionInfo | None = None
    corrects_event_id: str | None = None
    voice: VoiceInfo | None = None
    context_stamp: ContextStamp | None = None
    usage: ModelUsage | None = Field(
        default=None, description="The model call behind an `ai_agent` message: tokens and prompt cache."
    )
    backing: Backing | None = Field(
        default=None,
        description="An agent's message: which of the values it states have no source, and which guards it "
        "went against, as the SDK's backing check found them.",
    )
    valid_until: datetime | None = Field(
        default=None,
        description="Until when what this event states holds, e.g. an offer valid until a date: after it, "
        "the facts it gave leave the context and the search (`show_expired` still finds them).",
    )

    @model_validator(mode="after")
    def _shape_matches_kind(self) -> "EventItem":
        if self.kind is EventKind.ACTION and self.action is None:
            raise ValueError("an action event needs the `action` block")
        if self.kind is not EventKind.ACTION and self.action is not None:
            raise ValueError("`action` is only valid when kind is `action`")
        if self.kind is EventKind.SYSTEM_EVENT and not self.canonical_type:
            raise ValueError("a system event needs `canonical_type`")
        if self.kind is EventKind.MESSAGE and (
            self.content is None
            or not (self.content.text or self.content.media_ref or self.content.transcript)
        ):
            raise ValueError("a message needs text, a transcript or a media reference")
        if not self.handles and not self.subjects and not self.object_refs:
            raise ValueError("an event needs at least one handle, subject or object")
        if self.usage is not None and (
            self.kind is not EventKind.MESSAGE or self.speaker.role is not Speaker.AI_AGENT
        ):
            raise ValueError("`usage` is only valid on a message of the `ai_agent`")
        if self.backing is not None and (
            self.kind is not EventKind.MESSAGE
            or self.speaker.role not in (Speaker.AI_AGENT, Speaker.HUMAN_AGENT)
        ):
            raise ValueError("`backing` is only valid on a message of an agent")
        return self


class IdentifyItem(Model):
    """States that several handles belong to the same subject (5.3)."""

    type: Literal["identify"] = "identify"
    idempotency_key: IdStr
    handles: list[Handle] = Field(min_length=2, max_length=16)
    method: AssertionMethod = AssertionMethod.EXPLICIT_IDENTIFY
    subject_kind: SubjectKind = SubjectKind.PERSON
    conversation_id: IdStr | None = None
    occurred_at: datetime


class VerifyItem(Model):
    """Raises the verification level of one conversation or task (7.3). Never inferred."""

    type: Literal["verify"] = "verify"
    idempotency_key: IdStr
    method: Literal["otp_whatsapp", "otp_sms", "login", "kba", "network_attestation", "human_agent"]
    level: Verification
    conversation_id: IdStr | None = None
    task_id: IdStr | None = None
    handle: Handle
    valid_until: datetime | None = None
    occurred_at: datetime


class ConversationEndedItem(Model):
    type: Literal["conversation.ended"] = "conversation.ended"
    idempotency_key: IdStr
    conversation_id: IdStr
    occurred_at: datetime


class TaskEndedItem(Model):
    type: Literal["task.ended"] = "task.ended"
    idempotency_key: IdStr
    task_id: IdStr
    occurred_at: datetime


class HandoffItem(Model):
    """A transfer to a human or another agent; the measurement reads it (19.2)."""

    type: Literal["handoff"] = "handoff"
    idempotency_key: IdStr
    conversation_id: IdStr
    target: Literal["human", "agent"]
    target_source: ShortStr | None = None
    reason: ShortStr | None = None
    mode: Literal["warm", "cold"] = "warm"
    occurred_at: datetime


class HeartbeatItem(Model):
    """Periodic counters from the SDK, used to compute per-source coverage (5.2)."""

    type: Literal["heartbeat"] = "heartbeat"
    window_start: datetime
    sent: int = Field(ge=0)


# The agent features' items (7.7): `interaction` and `coordination`.
class InteractionItem(Model):
    """An interaction of a subject, for a source that sends no turn records: what a turn record carries in
    `interactions`, with the subject and the conversation. Accepted only in a space with the `signals`
    agent feature on; a duplicate by `idempotency_key` is applied once."""

    type: Literal["interaction"] = "interaction"
    idempotency_key: IdStr
    handle: Handle
    conversation_id: IdStr | None = None
    agent: ShortStr | None = None
    occurred_at: datetime
    interaction: Interaction


BatchItem = Annotated[
    EventItem
    | IdentifyItem
    | VerifyItem
    | ConversationEndedItem
    | TaskEndedItem
    | HandoffItem
    | InteractionItem
    | HeartbeatItem,
    Field(discriminator="type"),
]

MAX_BATCH_ITEMS = 500


class BatchRequest(Model):
    items: list[BatchItem] = Field(min_length=1, max_length=MAX_BATCH_ITEMS)


class ItemError(Model):
    index: int
    code: str
    detail: str | None = None


class BatchResponse(Model):
    accepted: int
    duplicates: int
    errors: list[ItemError]
    masked: dict[str, int] = Field(
        default_factory=dict,
        description="Values held back from the accepted items before storage, by type (`card`, `cvv`, "
        "`password`, `secret`): the stored text says `[retido:cartão ****1234]` where the value was.",
    )


class MediaUploadRequest(Model):
    content_type: ShortStr
    size_bytes: int = Field(gt=0, le=500 * 1024 * 1024)
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class MediaUploadResponse(Model):
    media_ref: str
    upload_url: str
    upload_headers: dict[str, str] = Field(
        default_factory=dict,
        description="Send exactly these headers with the upload; the store refuses other bytes.",
    )
    expires_at: datetime


class FeedbackRequest(Model):
    """Corrections become events (L1): retract or correct a fact, resolve an open item, record an outcome."""

    idempotency_key: IdStr
    subject: Handle
    action: Literal["retract_fact", "correct_fact", "resolve_open_item", "conversation_outcome"]
    fact_id: UUID | None = None
    open_item_id: UUID | None = Field(default=None, description="The item `resolve_open_item` closes.")
    conversation_id: IdStr | None = None
    value: Annotated[str, StringConstraints(max_length=2000)] | None = None
    reason: Annotated[str, StringConstraints(max_length=500)] | None = None

    @model_validator(mode="after")
    def _fields_match_action(self) -> "FeedbackRequest":
        missing = [name for name in _FEEDBACK_NEEDS[self.action] if not getattr(self, name)]
        if missing:
            raise ValueError(f"{self.action} needs " + " and ".join(missing))
        return self


MAX_FEEDBACK_ITEMS = 500


class FeedbackBatchRequest(Model):
    """Many corrections in one call, each with its own idempotency key; one bad item never fails the rest."""

    items: list[FeedbackRequest] = Field(min_length=1, max_length=MAX_FEEDBACK_ITEMS)


_FEEDBACK_NEEDS: dict[str, tuple[str, ...]] = {
    "retract_fact": ("fact_id",),
    "correct_fact": ("fact_id", "value"),
    "resolve_open_item": ("open_item_id",),
    "conversation_outcome": ("conversation_id", "value"),
}
"""What the memory module needs to apply each correction (5.8); a request without it would be stored
as an event and then skipped, so it is refused here with a 422 instead."""
