"""The read side: `POST /v1/context`, history navigation and business objects (5.11, 7.1, 7.2, 16)."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator, model_validator

from niadra.contracts.common import Handle, IdStr, Model, ObjectRef, ShortStr, SourceCoverage
from niadra.contracts.coordinate import CoordinationBlock
from niadra.contracts.signals import BudgetBlock, ConstraintsBlock
from niadra.contracts.state import StateView
from niadra.domain.serve.where import parse_where
from niadra.domain.vocabulary import DeliveryPath, EventKind, Verification

View = Annotated[
    str, StringConstraints(pattern=r"^(voice|chat|brief|full|custom|account|partner|task:[a-z0-9_]{1,40})$")
]
Include = Literal["state", "constraints", "coordination", "budget"]


class TargetModel(Model):
    """The LLM that will read the pack; lets the compiler aim at its cache floor (15.3)."""

    provider: ShortStr
    model: ShortStr


class ContextRequest(Model):
    subject: Handle | None = None
    object: ObjectRef | None = None
    profile_id: UUID | None = Field(
        default=None,
        description="The pseudonymous profile instead of a handle: the read of `GET /v1/context` with the "
        "conversation id in the body, never in the URL.",
    )
    about: Handle | None = Field(
        default=None, description="The account or partner the person acts for (21.4)."
    )
    view: View = "chat"
    verification: Verification = Verification.V0
    conversation_id: IdStr | None = None
    task_id: IdStr | None = None
    query: Annotated[str, StringConstraints(max_length=2000)] | None = Field(
        default=None,
        description="The customer's last turn. It selects the per-turn `slots` and never the pinned `text`, "
        "which is the same bytes with or without it. `brief` and `full` have no slots.",
    )
    delta: bool = False
    target: TargetModel | None = None
    known_etag: str | None = None
    format: Literal["text", "json"] = Field(
        default="text",
        description="`json` also returns `pack`: the same pack as typed sections, in the `context-pack.v1` "
        "shape, for programs that build their own prompt.",
    )
    explain: bool = Field(
        default=False,
        description='With `format: "json"` and `query`: each of `pack.slots` also says `why` it '
        "was chosen (its position in each retrieval channel, each channel's weighted share of the fused "
        "score, the weights' version, the rule of a derived line). It changes nothing else: the pinned "
        "text, the slots chosen and the receipt are the same bytes with or without it.",
    )
    include: list[Include] = Field(
        default_factory=list,
        max_length=4,
        description="Blocks read in the same round trip, each returned in the answer's field of the same "
        "name and recorded in a receipt of its own. Each needs its agent feature on in the space; without "
        "`include` the answer carries none of them.",
    )

    @model_validator(mode="after")
    def _one_target(self) -> "ContextRequest":
        if sum(x is not None for x in (self.subject, self.object, self.profile_id)) != 1:
            raise ValueError("pass exactly one of `subject`, `object` or `profile_id`")
        if len(set(self.include)) != len(self.include):
            raise ValueError("name each block of `include` once")
        if self.conversation_id and self.task_id:
            raise ValueError("pass `conversation_id` or `task_id`, not both")
        if self.explain and self.format != "json":
            raise ValueError('`explain` returns its answer in `pack.slots`: pass `format: "json"`')
        return self


class VerificationResult(Model):
    requested: Verification
    effective: Verification
    reason: str | None = Field(
        default=None,
        description="Why effective is lower: `source_ceiling`, `not_proven`, or `unconfirmed_link` (the "
        "read went through an identifier joined to the profile by a claim nobody confirmed yet, so it stays "
        "at V1 until the company's systems, a login or a person confirm the link).",
    )


class LiveTurn(Model):
    """A recent turn from another conversation, not yet absorbed by the pack (B3). It passes the same
    policy as the pack's items: the level the conversation proved and the reader's purposes decide."""

    at: datetime
    channel: str
    kind: EventKind
    speaker: str
    text: str
    source_id: str


class CacheDirectives(Model):
    breakpoints: list[int] = Field(description="Character offsets where a cache breakpoint may be placed.")
    ttl_seconds: int | None = None
    floor_tokens: int | None = None
    cacheable: bool
    salt: str = Field(description="Stable cache salt for self-hosted engines (15.6).")


class PackSection(Model):
    name: Literal[
        "account",
        "customer",
        "facts",
        "stable_patterns",
        "meta",
        "history",
        "summary",
        "episodes",
        "volatile_patterns",
        "actions",
        "objects",
        "pending",
    ] = Field(description="Stable across languages; read this, never `label`.")
    label: str = Field(description="The line's label in the space's language, as the text shows it.")
    layer: Literal["account", "stable", "volatile"]
    lines: list[str]


class SlotChannelRank(Model):
    """One retrieval channel's part in a slot's fused score (weighted reciprocal rank fusion)."""

    channel: str = Field(description="`exact`, `values`, `lexical`, `temporal`, `semantic` or `linked`.")
    position: int = Field(description="1-based, in that channel's own ranking for the turn.")
    weight: float = Field(description="The channel's weight in this fusion.")
    contribution: float = Field(description="`weight / (60 + position)`: what the channel added to `score`.")


class SlotWhy(Model):
    """Why a line took a slot (`explain`). Ids, positions and numbers; never a line or a value."""

    item_id: str | None = Field(
        default=None, description="The item, as manifests and context-use name it; absent for a derived line."
    )
    score: float | None = Field(
        default=None, description="The fused score: the sum of the channels' contributions."
    )
    channels: list[SlotChannelRank] = Field(
        default_factory=list, description="Each channel that ranked the item, in fusion order."
    )
    weights_version: int | None = Field(
        default=None, description="The space's learned fusion weights used; absent for the defaults."
    )
    excerpt: bool = Field(
        default=False, description="The line was cut to the sentences that answer the turn."
    )
    rule: str | None = Field(
        default=None,
        description="For a derived line: `guard_<value type>` (a guard line), `count_topic_complaints`, "
        "`count_topic` (the conversations about the matter asked), `count_complaints`, `count_conversations` "
        "(by the category of the best match), `no_record` or `withheld_may_hold`.",
    )
    basis: dict[str, Any] = Field(
        default_factory=dict,
        description="For a derived line, what the rule counted or missed: `basis` (the conversation that "
        "chose the category, or the value a guard states), `category`, `counted`, `window_days`; for a count "
        "by the matter asked, `units` (what the question says, in words and phrases) and `matched` (how many "
        "of them a conversation answers to be counted); `asked_types`, `identifiers` (how many numbers "
        "the turn named, never which), `withheld`; for a guard, `value_type` and `origin` (who stated the "
        "value).",
    )


class PackSlot(Model):
    """One line of the per-turn slots: an item the customer's last turn selected, a guard
    (a value the agent must not state otherwise), or a line derived from memory (`count`, `no_record`,
    `withheld`)."""

    section: str = Field(
        description="The pack section the item comes from (`episodes`, `objects`...), `guard` for a guard "
        "line, or `derived`."
    )
    id: str | None = Field(
        default=None,
        description="The short id of the item the line states: the last eight hex digits of its public id. "
        "An agent's turn names a guard it went against by it. Absent for a derived line.",
    )
    derived: Literal["count", "no_record", "withheld"] | None = None
    channels: list[str] = Field(
        default_factory=list,
        description="The channels that ranked the item: `exact`, `lexical`, `temporal`, `values`, "
        "`semantic`, `linked`.",
    )
    text: str = Field(description="The line as `slots` prints it.")
    why: SlotWhy | None = Field(default=None, description="With `explain`: why this line was chosen.")


class PackGuard(Model):
    """What one guard line states (13, 6.1): the value memory holds for a kind the turn asked about, by
    the precedence of who stated it, so the agent's answer can be checked against it at once."""

    id: str = Field(description="The value's short id, as the guard line's `PackSlot.id`.")
    value_type: str = Field(
        description="`protocol`, `ticket`, `order`, `record`, `receipt`, `postal_code`, `amount`, `date` "
        "or `code`."
    )
    value: str = Field(description="The value as the line writes it.")


class PackStamp(Model):
    """What an agent's turn should carry to say which pack its prompt held (spec 8.5)."""

    etag: str
    version: str
    as_of: datetime | None = None
    manifest_hash: str | None = None


class ContextPack(Model):
    """The pack as data (`format: "json"`): the same bytes as `text`, split by section in layer order."""

    spec: Literal["context-pack.v1"] = Field(
        default="context-pack.v1",
        description="The shape's version.",
    )
    view: str
    verification: Verification = Field(description="The effective level.")
    withheld: int
    as_of: datetime | None = None
    preamble: str = Field(
        description="The tenant block: the line that says the content is data, not instructions, and the "
        "usage rules."
    )
    sections: list[PackSection]
    variables: dict[str, str] = Field(default_factory=dict)
    stamp: PackStamp
    slots: list[PackSlot] = Field(
        default_factory=list,
        description="The lines of `slots`, typed. Not part of the pinned sections.",
    )


class ContextResponse(Model):
    not_modified: bool = False
    text: str | None = None
    variables: dict[str, str] = Field(default_factory=dict)
    version: str
    etag: str
    manifest_hash: str | None = None
    as_of: datetime | None = None
    lag_seconds: float | None = None
    coverage: list[SourceCoverage] = Field(default_factory=list)
    verification: VerificationResult
    withheld: int = Field(
        default=0, description="Items and `live` turns the policy left out for this reader and level."
    )
    live: list[LiveTurn] = Field(
        default_factory=list,
        description="Recent turns of the customer's other conversations not yet in the compiled context, "
        "oldest first: at most the newest 50, from the newest 100 turns of each of the customer's handles.",
    )
    live_complete: bool = Field(
        default=True,
        description="False when the hot layer was unavailable, a turn could not be read, or a handle had "
        "more recent turns than were read.",
    )
    delta: str | None = Field(
        default=None,
        description="With `delta`: what memory learned since this agent's last read, for the end of the "
        "prompt. The turn block after `text` is `live`, then `slots`, then `delta`.",
    )
    slots: str | None = Field(
        default=None,
        description="With `query`: what the customer's last turn selected from memory for this "
        "turn. It goes in the turn block after `text`, in this order: the `live` turns, then `slots`, then "
        "`delta`. Never part of `text`, which stays the same bytes for the whole conversation.",
    )
    guards: list[PackGuard] = Field(
        default_factory=list,
        description="What the guard lines among `slots` state, typed. An SDK checks the agent's "
        "answer against them and names a guard the answer went against on the agent's turn "
        "(`backing.guard_violations`).",
    )
    cache: CacheDirectives | None = None
    timing: dict[str, float] = Field(default_factory=dict)
    path: DeliveryPath
    degraded: bool = False
    about_unlinked: bool = Field(
        default=False,
        description="`about` named an account or partner with no active link to the subject: the pack is the "
        "subject's own, without that organization's block. It never says whether the organization exists.",
    )
    pack: ContextPack | None = Field(
        default=None, description='With `format: "json"`; absent when `text` is absent.'
    )
    constraints: ConstraintsBlock | None = Field(
        default=None,
        description='With `include: ["constraints"]`: what the subject wants, refuses and is, for the tools '
        "of this turn (`constraints.v0`).",
    )
    state: StateView | None = Field(
        default=None,
        description='With `include: ["state"]`: the subject\'s objects of the declared types, each as a '
        "`display` read serves it (`object-state.v1`): values with their logical value, stamps, source and "
        "freshness computed now, and what may not be claimed with them.",
    )
    coordination: CoordinationBlock | None = Field(
        default=None,
        description='With `include: ["coordination"]`: who holds the subject, the purposes it may not be '
        "contacted for, the contacts each purpose with a budget has left and the commitments that hold. "
        "Advice for the turn: only a check decides.",
    )
    budget: BudgetBlock | None = Field(
        default=None,
        description='With `include: ["budget"]`: what the pack costs per section, what this agent already '
        "spent in the conversation and the case, and what the measurement says can go; shown, never "
        "enforced.",
    )


class PrefetchRequest(Model):
    """`POST /v1/context/prefetch`: the customer's turn so far, sent while they are still speaking, so the
    read that answers the turn finds its work done. Same target, view and level as that read."""

    subject: Handle | None = None
    object: ObjectRef | None = None
    about: Handle | None = Field(
        default=None, description="The account or partner the person acts for (21.4)."
    )
    view: View = "voice"
    verification: Verification = Verification.V0
    conversation_id: IdStr | None = None
    task_id: IdStr | None = None
    query: Annotated[str, StringConstraints(min_length=1, max_length=2000)] = Field(
        description="The partial transcript. The read with this same `query` is served from the prefetch; "
        "a longer transcript still finds the customer's memory already open."
    )

    @model_validator(mode="after")
    def _one_target(self) -> "PrefetchRequest":
        if (self.subject is None) == (self.object is None):
            raise ValueError("pass exactly one of `subject` or `object`")
        if self.conversation_id and self.task_id:
            raise ValueError("pass `conversation_id` or `task_id`, not both")
        return self


class PrefetchResponse(Model):
    queued: bool = Field(
        description="False when there is nothing to prepare: the turn is a closed form (thanks, a short "
        "confirmation, a greeting) that gets no slots, the view has no slots (`brief`, `full`), or the "
        "server is at its bound of prefetches. The read answers the same either way."
    )


ItemStatus = Literal["open", "overdue", "resolved"]


class ClosedBy(Model):
    """What closed an open item: the same object the `open_item.closed` webhook carried."""

    kind: Literal["action", "system_event", "conversation", "feedback"]
    action_id: str | None = Field(default=None, description="`action`: the agent action that closed it.")
    event_id: str | None = Field(
        default=None,
        description="The event that closed it: the action's, the system of record's, or the feedback's.",
    )
    session_id: str | None = Field(default=None, description="`conversation`: the session that resolved it.")
    operation: str | None = Field(
        default=None, description="`action` and `system_event`: the operation done."
    )
    source_id: str | None = Field(default=None, description="`feedback`: the source that sent it.")


class HistoryFilters(Model):
    since: datetime | None = None
    until: datetime | None = None
    when: Annotated[str, StringConstraints(min_length=1, max_length=100)] | None = Field(
        default=None,
        description="A time expression in the customer's words (`last week`, `semana passada`, `en marzo`, "
        "`ontem`), in Portuguese, English or Spanish, read in the space's time zone from the conversation's "
        "last event. It narrows `since` and `until`; one the server does not read is listed in `ignored`.",
    )
    show_expired: bool = Field(
        default=False, description="Also items whose validity ended (`valid_until` in the past)."
    )
    channels: list[ShortStr] = Field(default_factory=list)
    categories: list[ShortStr] = Field(default_factory=list)
    item_kinds: list[Literal["episode", "fact", "open_item", "action", "object", "trait", "system_event"]] = (
        Field(
            default_factory=list,
            description=(
                "`system_event` finds the system events of the customer's recent history, each its own row, "
                "and also the objects they changed. The timeline lists conversations, "
                "agent actions and system events; `fact`, `open_item`, `trait` and `object` named here are "
                "listed too, each at its own date."
            ),
        )
    )
    item_statuses: list[ItemStatus] = Field(
        default_factory=list,
        description="The open items to list, by status: `open`, `overdue`, `resolved`. Empty, the rows are "
        "the open and overdue items and the promises kept in the last 7 days, as the context shows them; "
        "`resolved` reaches every resolved item, each with `closed_at` and `closed_by`. Named here, the "
        "timeline lists open items too, as `item_kinds` `open_item` does. Rows of other kinds pass as they "
        "are.",
    )
    outcome: ShortStr | None = None
    object: ObjectRef | None = None
    where: dict[str, Any] | None = Field(
        default=None,
        description="Conditions joined by `AND`, `OR` and `NOT` over the row fields `id`, `kind`, `channel`, "
        "`category`, `outcome`, `source_id`, `vendor`, `at`, `valid_until`, `confidence`, `text`, "
        "`object_type` and `object_namespace`. A bare value means `eq`, a list means `in`, sibling keys are "
        "ANDed; operators `eq`, `ne`, `in`, `nin`, `gt`, `gte`, `lt`, `lte`, `contains`, `icontains` (case "
        'and accents folded) and `exists`. Example: `{"AND": [{"kind": ["episode", "action"]}, '
        '{"NOT": {"vendor": "acme"}}, {"at": {"gte": "2026-09-01"}}]}`. It narrows the rows '
        "the policy already let through, after the other filters. At most 64 conditions, 6 levels deep.",
    )

    @field_validator("where")
    @classmethod
    def _where_parses(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            parse_where(value)
        return value


class SearchRequest(Model):
    subject: Handle
    about: Handle | None = None
    query: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    filters: HistoryFilters = Field(default_factory=HistoryFilters)
    max_tokens: int = Field(default=800, ge=50, le=4000)
    verification: Verification = Verification.V0
    conversation_id: IdStr | None = None
    task_id: IdStr | None = None
    limit: int | None = Field(
        default=None, ge=1, le=100, description="At most this many items, after the token budget."
    )


class HistoryItem(Model):
    id: str
    kind: str
    text: str
    at: datetime
    valid_until: datetime | None = None
    channel: str | None = None
    source_id: str | None = None
    outcome: str | None = None
    confidence: float | None = None
    origin_event_id: str | None = None
    object: ObjectRef | None = Field(
        default=None,
        description="The business object the row is about: an open item's or a fact's, an object's own, the "
        "one a system event touched.",
    )
    expected_operation: ShortStr | None = Field(
        default=None,
        description="An open item's: the operation that closes it when an action or a system event confirms "
        "it on its object or on a part of it. Absent: only a later conversation or a correction closes it.",
    )
    status: ItemStatus | None = Field(default=None, description="An open item's status.")
    closed_at: datetime | None = Field(default=None, description="A resolved item's: when it was closed.")
    closed_by: ClosedBy | None = Field(
        default=None,
        description="A resolved item's: what closed it.",
    )
    item_kind: Literal["promise", "dispute", "request"] | None = Field(
        default=None,
        description="An open item's: a promise someone made, a dispute the customer raised or a request.",
    )
    audience: Literal["shared", "internal"] | None = Field(
        default=None,
        description="A fact's or an open item's about an object: `shared` reaches every participant of the "
        "object, a customer-facing agent included; `internal`, the internal readers only.",
    )


class Recurrence(Model):
    category: str
    occurrences: int
    window_days: int
    last_at: datetime | None = None
    last_outcome: str | None = None
    last_resolution: str | None = None


class TimeWindow(Model):
    since: datetime | None = None
    until: datetime | None = None


class SearchResponse(Model):
    items: list[HistoryItem]
    recurrence: Recurrence | None = None
    withheld: int = 0
    as_of: datetime | None = None
    tokens_used: int
    degraded: str | None = Field(default=None, description="`text_only` when the encoder was unavailable.")
    window: TimeWindow | None = Field(
        default=None, description="The period `when` was read as, when it was read."
    )
    ignored: list[str] = Field(
        default_factory=list, description="Filters the server could not read and left out, e.g. `when`."
    )
    about_unlinked: bool = Field(
        default=False,
        description="`about` named an account or partner with no active link to the subject: the rows are "
        "the subject's own. It never says whether the organization exists.",
    )


class TimelineRequest(Model):
    subject: Handle | None = None
    profile_id: UUID | None = Field(
        default=None,
        description="The pseudonymous profile instead of a handle: `GET /v1/history/timeline` with the "
        "conversation id in the body.",
    )
    about: Handle | None = None
    filters: HistoryFilters = Field(default_factory=HistoryFilters)
    cursor: str | None = None
    limit: int = Field(default=20, ge=1, le=100)
    verification: Verification = Verification.V0
    conversation_id: IdStr | None = None

    @model_validator(mode="after")
    def _one_target(self) -> "TimelineRequest":
        if (self.subject is None) == (self.profile_id is None):
            raise ValueError("pass exactly one of `subject` or `profile_id`")
        return self


class OpenItemRequest(Model):
    """`GET /v1/history/items/{item_id}` with the conversation id and the customer in the body (7.0)."""

    item_id: IdStr
    profile_id: UUID | None = None
    subject: Handle | None = None
    about: Handle | None = Field(
        default=None,
        description="The account or partner the person acts for (21.4): opens what the person's view of it "
        "shows, as the pack's account block. Needs `subject`.",
    )
    verification: Verification = Verification.V0
    conversation_id: IdStr | None = None


class TimelineResponse(Model):
    items: list[HistoryItem]
    next_cursor: str | None = None
    withheld: int = 0
    as_of: datetime | None = None
    window: TimeWindow | None = None
    ignored: list[str] = Field(default_factory=list)
    about_unlinked: bool = Field(
        default=False,
        description="`about` named an account or partner with no active link to the subject: the rows are "
        "the subject's own. It never says whether the organization exists.",
    )


class Promise(Model):
    by: Literal["company", "customer"]
    what: str
    due_at: datetime | None = None
    status: str


class ItemVersion(Model):
    version: int
    changed_at: datetime
    what_changed: str = Field(description="`created`, `outcome`, `resolution`, `summary`, `state`...")


class OpenedItem(Model):
    id: str
    kind: Literal["episode", "object"]
    summary: str
    requested: str | None = None
    promises: list[Promise] = Field(default_factory=list)
    outcome: str | None = None
    resolution: str | None = None
    derived: list[HistoryItem] = Field(default_factory=list)
    timeline: list[HistoryItem] = Field(default_factory=list)
    as_of: datetime | None = None
    versions: list[ItemVersion] = Field(
        default_factory=list,
        description="Earlier and current versions, oldest first, when the item has them.",
    )


class ToolDefinition(Model):
    """A function-calling tool definition in the most common JSON Schema shape (16.3)."""

    type: Literal["function"] = "function"
    function: dict[str, Any]
