"""Turn records, replay, scenarios, data issues and the notification feed (7.7, 24.3).

Owner: the turns module. `TurnRecord` is the `turn-record.v0` open spec: what an agent read, called,
showed, claimed and decided in one turn, with the build pinned, captured by the SDK in the agent's process
and sent later (L22). Large values (tool arguments and results, the pack's text, the UI payload) are blobs
the record points at by a short key; the content mode says whether a blob travels inline (`stored`), stays
in the customer's storage (`pointer`) or is only hashed (`hash_only`).
"""

from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from niadra.contracts.claims import ClaimRecord
from niadra.contracts.common import IdStr, Model, ShortStr
from niadra.contracts.events import ItemError
from niadra.contracts.signals import Interaction
from niadra.domain.turns.issues import DataIssueKind

MAX_TURNS_PER_REQUEST = 50
MAX_BODY_BYTES = 4 * 1024 * 1024
"""The compressed body of one `POST /v1/turns` (zstd or gzip); a larger one answers 413 `turn_too_large`."""
MAX_DECODED_BYTES = 16 * 1024 * 1024
"""The same body decompressed; a larger one answers 413 `turn_too_large` too."""
MAX_SCENARIO_TURNS = 50
MAX_ASSERTIONS = 50
MAX_RUNS = 100

TurnId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]
BlobKey = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z]{1,8}:[A-Za-z0-9_-]{1,32}$"),
    Field(description="A blob of this record, by its key in `blobs`, e.g. `b:1`."),
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
ObjectKey = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}:[^:\s]{1,256}:\S{1,512}$"),
    Field(description="An object as `<type>:<namespace>:<id>`, e.g. `item_variant:store:991`."),
]
Version = Annotated[str, StringConstraints(min_length=1, max_length=128)]

TurnKind = Literal["message", "action", "event", "timer"]
Fidelity = Literal["bronze", "silver", "gold"]
Completeness = Literal["complete", "partial", "incomplete", "unknown"]
ContentMode = Literal["stored", "pointer", "hash_only"]
Flag = Literal[
    "error",
    "guard_acted",
    "guard_budget_exceeded",
    "handoff",
    "assertion_failed",
    "synthetic",
    "incomplete",
    "negative_feedback",
    "truncated",
]
Surface = Literal[
    "pack", "state", "constraints", "coordination", "budget", "history", "agent_memory", "agent_state"
]
PromoteReason = Literal["complaint", "bug_report", "review", "other"]
ReplayMode = Literal["hermetic_turn", "hermetic_conversation", "era_memory"]
PinName = Literal["prompts", "corpus_digest", "model", "assembler", "tool_schemas"]
AssertionKind = Literal[
    "tool_called",
    "tool_not_called",
    "hard_respected",
    "no_denial_with_results",
    "claims_traced",
    "claims_match_state",
    "no_promise_without_action",
    "expected_in_topk",
    "handoff_when",
    "effect_once",
    "budget",
    "lexicon",
    "tools_offered_match",
]
AssertionId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_.-]{0,63}$")]
Verdict = Literal["pass", "flaky", "infrastructure_error", "pin_mismatch", "regression"]


class TurnAgent(Model):
    name: ShortStr = Field(description="The agent's name inside the source, e.g. `closing`.")
    role: ShortStr | None = Field(default=None, description="Free text, e.g. `specialist` or `router`.")
    parent_turn_id: TurnId | None = Field(
        default=None,
        description="The turn that opened this one as a sub-turn (a sub-agent, an agent as tool).",
    )


class TurnCompilerPins(Model):
    compiler: Version | None = Field(
        default=None, description="The context compiler's version, from the pack."
    )
    pack_hash: Version | None = Field(default=None, description="The hash of the pack this turn read.")


class TurnPins(Model):
    """What must be the same for a replay to reproduce this turn. The recording document says which are
    required; a turn without one of them is kept, marked not replayable."""

    prompts: dict[ShortStr, Version] = Field(
        default_factory=dict, max_length=50, description="Prompt name -> version, e.g. `core` -> `v16`."
    )
    corpus_digest: Sha256 | None = Field(
        default=None,
        description="Digest of the files the agent consults, computed in the SDK; never the files.",
    )
    model: Version | None = Field(default=None, description="The exact model the agent called.")
    assembler: Version | None = Field(
        default=None, description="The version of the company's context assembler."
    )
    tool_schemas: dict[ShortStr, Sha256] = Field(
        default_factory=dict, max_length=200, description="Tool name -> hash of its schema."
    )
    niadra: TurnCompilerPins | None = None


class TurnBuild(Model):
    pins: TurnPins = Field(default_factory=TurnPins)
    sdk: ShortStr | None = Field(default=None, description="E.g. `niadra-python/0.7.0`.")
    adapter: ShortStr | None = Field(
        default=None, description="The framework adapter and its version, if any."
    )


class TurnRead(Model):
    """A read the turn made from Niadra, by its version: the pack by ETag, a block by its version."""

    surface: Surface
    etag: Version | None = None
    version: Version | None = None
    blob: BlobKey | None = Field(default=None, description="The content read, when the recording keeps it.")
    receipt_id: IdStr | None = None


class TurnProvenance(Model):
    source: Literal["live", "snapshot", "cache"]
    source_observed_at: datetime
    scope: Literal["global", "customer", "context"] = "global"


class TurnObservation(Model):
    """State a tool result showed, with provenance per field; without provenance it is display only."""

    ref: ObjectKey
    fields: dict[ShortStr, Any] = Field(max_length=200)
    provenance: TurnProvenance | None = None


class TurnConstraints(Model):
    """What the SDK measured of the constraints block on one tool call: sent, checked, violated."""

    constraints: Version = Field(description="The version of the constraints block applied.")
    hard_sent: list[ShortStr] = Field(default_factory=list, max_length=200)
    results_checked: int = Field(ge=0)
    violations: int = Field(ge=0)
    unverifiable: int = Field(ge=0)
    relaxed: Literal["declared", "suspected"] | None = None


class TurnTokens(Model):
    input: int = Field(ge=0, alias="in")
    cached: int = Field(default=0, ge=0)
    output: int = Field(ge=0, alias="out")


class TurnCall(Model):
    """One tool or model call of the turn, copied deep at the moment it happened."""

    call_id: ShortStr
    parent_call_id: ShortStr | None = None
    kind: Literal["tool", "model"]
    name: ShortStr | None = Field(
        default=None, description="The tool's name, or the model's for a model call."
    )
    attempt: int = Field(default=1, ge=1)
    synthetic: bool = False
    cache_hit: bool = False
    status: Literal["ok", "error", "timeout", "cancelled"] = "ok"
    latency_ms: int | None = Field(default=None, ge=0)
    args_hash: Sha256 | None = Field(default=None, description="Of the normalized arguments.")
    args: BlobKey | None = None
    result_model: BlobKey | None = Field(default=None, description="The result as the model saw it.")
    result_ui: BlobKey | None = Field(default=None, description="The result as the interface got it.")
    observations: list[TurnObservation] = Field(default_factory=list, max_length=200)
    applied: TurnConstraints | None = None
    effect_key: IdStr | None = None
    cost_units: dict[ShortStr, int] = Field(default_factory=dict, max_length=20)
    tokens: TurnTokens | None = None
    truncated: bool = Field(default=False, description="Arguments or result arrived cut (OpenTelemetry).")

    @model_validator(mode="after")
    def _tokens_on_model_calls(self) -> "TurnCall":
        if self.tokens is not None and self.kind != "model":
            raise ValueError("`tokens` belongs to a model call")
        return self


class TurnCoordination(Model):
    decision_id: IdStr
    decision: Literal["allow", "defer", "deny", "handoff_to"]


class TurnEffect(Model):
    key: IdStr
    state: Literal["reserved", "done", "failed", "unknown_outcome"]


class TurnOutput(Model):
    event_keys: list[IdStr] = Field(
        default_factory=list,
        max_length=50,
        description="The events `track()` sent for what the turn said: the text is never repeated here.",
    )
    ui: BlobKey | None = None
    document: BlobKey | None = Field(default=None, description="A document the turn wrote, by its blob.")
    handoff_id: IdStr | None = None


class TurnCost(Model):
    usd: float = Field(ge=0)


class TurnBlob(Model):
    """A large value of the record. `sha256` is over its normalized canonical JSON (RFC 8785): the same value
    always has the same hash."""

    sha256: Sha256
    size: int | None = Field(default=None, ge=0, description="Bytes of the canonical form.")
    pointer: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9+.-]*://\S{1,1024}$")] | None = Field(
        default=None, description="Where the customer keeps it, in `pointer` mode."
    )
    content: Any = Field(default=None, description="The value itself, only in `stored` mode.")


class TurnRecord(Model):
    """One turn, from its input (a message, an interface action, an event, a timer firing) to the last thing
    it emitted to the customer or to a document."""

    spec: Literal["turn-record.v0"] = "turn-record.v0"
    turn_id: TurnId
    conversation_id: IdStr | None = None
    task_id: IdStr | None = None
    agent: TurnAgent
    kind: TurnKind = "message"
    started_at: datetime
    ended_at: datetime | None = None
    fidelity: Fidelity = "gold"
    completeness: Completeness = "complete"
    content_mode: ContentMode
    build: TurnBuild = Field(default_factory=TurnBuild)
    reads: list[TurnRead] = Field(default_factory=list, max_length=20)
    calls: list[TurnCall] = Field(default_factory=list, max_length=500)
    interactions: list[Interaction] = Field(default_factory=list, max_length=200)
    claims: list[ClaimRecord] = Field(default_factory=list, max_length=500)
    coordination: list[TurnCoordination] = Field(default_factory=list, max_length=50)
    effects: list[TurnEffect] = Field(default_factory=list, max_length=50)
    output: TurnOutput = Field(default_factory=TurnOutput)
    cost: TurnCost | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    flags: list[Flag] = Field(default_factory=list, max_length=20)
    blobs: dict[BlobKey, TurnBlob] = Field(default_factory=dict, max_length=1000)

    @model_validator(mode="after")
    def _consistent(self) -> "TurnRecord":
        if not (self.conversation_id or self.task_id):
            raise ValueError("a turn belongs to a conversation or a task")
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("`ended_at` is before `started_at`")
        for blob in self.blobs.values():
            if self.content_mode == "pointer" and (blob.pointer is None or blob.content is not None):
                raise ValueError("in `pointer` mode every blob has a pointer and no content")
            if self.content_mode == "hash_only" and (blob.pointer is not None or blob.content is not None):
                raise ValueError("in `hash_only` mode a blob is only its hash")
            if self.content_mode == "stored" and blob.pointer is not None:
                raise ValueError("in `stored` mode a blob travels as content, never as a pointer")
        missing = sorted(key for key in _blob_keys(self) if key not in self.blobs)
        if missing:
            raise ValueError("blob keys without a blob: " + ", ".join(missing))
        call_ids = [call.call_id for call in self.calls]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("a `call_id` repeats")
        return self


def _blob_keys(record: TurnRecord) -> set[str]:
    keys = {read.blob for read in record.reads}
    for call in record.calls:
        keys |= {call.args, call.result_model, call.result_ui}
    keys |= {claim.evidence.anchor for claim in record.claims if claim.evidence}
    keys |= {record.output.ui, record.output.document}
    return {key for key in keys if key is not None}


class TurnRecordingSummary(Model):
    """How the calling source records turns, for the SDK's capture: where the values may live, and the pins
    a turn needs to be replayable."""

    content_mode: ContentMode = Field(
        description="`stored`, `pointer` or `hash_only`; a turn may keep less, never more."
    )
    required_pins: list[PinName] = Field(description="Without one of these, a turn is kept not replayable.")


class TurnsRequest(Model):
    turns: list[TurnRecord] = Field(min_length=1, max_length=MAX_TURNS_PER_REQUEST)


class TurnsResponse(Model):
    """200 when every turn was taken, 207 with one error per rejected turn (by index)."""

    accepted: int
    duplicates: int = Field(description="Turns already recorded: the same turn sent twice is one.")
    errors: list[ItemError] = Field(default_factory=list)


class PromoteRequest(Model):
    """Moves turns from the short tier (every turn) to the kept one, which search reads, by conversation or by
    id, with the reason: the complaint arrives days after the turn."""

    conversation_id: IdStr | None = None
    turn_ids: list[TurnId] = Field(default_factory=list, max_length=500)
    reason: PromoteReason

    @model_validator(mode="after")
    def _one_way(self) -> "PromoteRequest":
        if bool(self.conversation_id) == bool(self.turn_ids):
            raise ValueError("pass `conversation_id` or `turn_ids`, not both")
        return self


class PromoteResponse(Model):
    promoted: int
    already_kept: int
    not_found: int = Field(description="Turns no longer in the short tier, or never recorded.")


class ReplayBlocker(Model):
    """Why a turn cannot be run again: `content_mode` (`hash_only` keeps no value), `fidelity` (only a gold
    record can be replayed), `completeness` (only a complete one) or `missing_pin` (the pin the recording
    requires and the record lacks)."""

    reason: Literal["content_mode", "fidelity", "completeness", "missing_pin"]
    value: ShortStr = Field(description="E.g. `hash_only`, `bronze`, `partial` or the pin's name, `model`.")


class TurnView(Model):
    """A recorded turn as the viewer shows it. In `pointer` and `hash_only` modes, blobs have no content."""

    record: TurnRecord
    tier: Literal["short", "kept"]
    kept_until: datetime | None = None
    replayable: bool = Field(description="Every pin the recording requires is there.")
    replay_blockers: list[ReplayBlocker] = Field(
        default_factory=list, description="Why the turn cannot be run again; empty when it can."
    )


class TurnSearchRequest(Model):
    """A search over the kept tier. The conversation id travels in the body, never in the URL."""

    conversation_id: IdStr | None = None
    agent: ShortStr | None = None
    since: datetime | None = None
    until: datetime | None = None
    flags: list[Flag] = Field(default_factory=list, max_length=20)
    cursor: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=50, ge=1, le=200)


class TurnSummary(Model):
    turn_id: TurnId
    agent: ShortStr
    kind: TurnKind
    started_at: datetime
    tier: Literal["short", "kept"]
    fidelity: Fidelity
    completeness: Completeness
    replayable: bool
    flags: list[Flag] = Field(default_factory=list)


class TurnSearchResponse(Model):
    items: list[TurnSummary]
    next_cursor: str | None = None


class TurnIndexEntry(Model):
    """One row of the kept tier's index, as the day's anchored root counts it: ids, times, the build and the
    mode, never what the turn said. `digest` is SHA-256 over the RFC 8785 form of the other fields."""

    turn_id: TurnId
    source_id: IdStr
    agent: ShortStr
    kind: TurnKind
    started_at: datetime
    content_mode: ContentMode
    build_hash: str | None = None
    kept_at: datetime
    digest: Sha256


class TurnIndexPage(Model):
    """The rows the kept tier took on one UTC day, in turn id order. Their digests, as leaves in that
    order, make the `turns.root` of the day's `audit.root` event (RFC 6962 prefixes, an odd node carried
    up), for the company's own audit chain to anchor."""

    day: date
    items: list[TurnIndexEntry]
    next_cursor: str | None = None


class ReplayAssertion(Model):
    """A structural check on the replayed turn's record, evaluated by the runner. The replay spec fixes each
    kind's `args` and how it is evaluated; a kind the runner cannot evaluate yields `not_checked`."""

    id: AssertionId
    kind: AssertionKind
    args: dict[ShortStr, Any] = Field(default_factory=dict, max_length=20)
    turn_id: TurnId | None = Field(
        default=None, description="The one turn of the scenario it applies to; every turn when absent."
    )
    suggested: bool = Field(default=False, description="Suggested by rule from the recorded turn.")


class ReplayCaseRequest(Model):
    """A case for one turn, alone or as part of a scenario. `build` is the build the runner will run; every
    pin the recording requires must match the recorded one, except the pins in `vary`, which the run changes
    on purpose."""

    turn_id: TurnId
    scenario_id: IdStr | None = None
    mode: ReplayMode = "hermetic_turn"
    build: TurnBuild
    vary: list[PinName] = Field(default_factory=list, max_length=5)


class ReplayInput(Model):
    kind: TurnKind
    text: str | None = Field(default=None, description="The customer's message that opened the turn, masked.")


class ReplayHistoryEntry(Model):
    role: Literal["customer", "agent", "human"]
    text: str | None = None
    at: datetime


class ReplayCase(Model):
    """What the replay runner needs to run a turn again inside the company's boundary: the record, with the
    kept values in `stored` mode and the pointers and digests otherwise; the pins; the conversation's masked
    history up to the turn, ending with the turn's own input; and the assertions. The pack of the time is
    the blob of the record's `pack` read."""

    case_id: IdStr
    turn_id: TurnId
    scenario_id: IdStr | None = None
    mode: ReplayMode
    record: TurnRecord
    required_pins: list[PinName]
    vary: list[PinName] = Field(default_factory=list)
    input: ReplayInput
    history: list[ReplayHistoryEntry] = Field(default_factory=list)
    assertions: list[ReplayAssertion] = Field(default_factory=list)
    expires_at: datetime | None = Field(
        default=None,
        description="When the turn leaves storage, after which a case is `replay_expired`; null while the "
        "space sets no retention for recordings.",
    )


class ScenarioCreate(Model):
    """Turns kept as a scenario, with the assertions they must keep passing; without assertions, the ones
    suggested by rule from the turns."""

    name: ShortStr
    turn_ids: list[TurnId] = Field(min_length=1, max_length=MAX_SCENARIO_TURNS)
    assertions: list[ReplayAssertion] = Field(default_factory=list, max_length=MAX_ASSERTIONS)


class ScenarioFromReport(Model):
    """A bug report becomes a scenario: its turns are kept and assertions suggested by rule, from the turns
    and from words of the description. The description is read, never stored."""

    name: ShortStr | None = None
    conversation_id: IdStr | None = None
    turn_ids: list[TurnId] = Field(default_factory=list, max_length=MAX_SCENARIO_TURNS)
    description: Annotated[str, StringConstraints(max_length=2000)] | None = None

    @model_validator(mode="after")
    def _one_way(self) -> "ScenarioFromReport":
        if bool(self.conversation_id) == bool(self.turn_ids):
            raise ValueError("pass `conversation_id` or `turn_ids`, not both")
        return self


class ScenarioUpdate(Model):
    name: ShortStr | None = None
    assertions: list[ReplayAssertion] | None = Field(default=None, max_length=MAX_ASSERTIONS)
    status: Literal["active", "retired"] | None = None


class Scenario(Model):
    scenario_id: IdStr
    name: str
    origin: Literal["manual", "report"]
    turn_ids: list[TurnId]
    assertions: list[ReplayAssertion]
    status: Literal["active", "retired"]
    version: int = Field(description="Grows with every change of the name, the assertions or the status.")
    created_at: datetime
    updated_at: datetime


class ScenarioPage(Model):
    items: list[Scenario]
    next_cursor: str | None = None


class AssertionOutcome(Model):
    id: AssertionId
    kind: AssertionKind
    outcome: Literal["pass", "fail", "not_checked"]
    detail: Annotated[str, StringConstraints(max_length=500)] | None = None


class ReplayResult(Model):
    """One execution of one case. `infrastructure_error` (the agent raised, a blob could not be fetched or
    did not match its digest, a timeout) is counted apart and never fails an assertion; `pin_mismatch` is a
    case the runner stopped at its pin check."""

    scenario_id: IdStr
    turn_id: TurnId
    case_id: IdStr | None = None
    run: int = Field(ge=1, le=MAX_RUNS)
    status: Literal["completed", "pin_mismatch", "infrastructure_error"]
    paraphrase: bool = Field(
        default=False, description="The run's input was a paraphrase of the recorded one."
    )
    assertions: list[AssertionOutcome] = Field(default_factory=list, max_length=MAX_ASSERTIONS)
    divergent_calls: int = Field(default=0, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    error: Annotated[str, StringConstraints(max_length=500)] | None = None


class ScenarioRunCreate(Model):
    """What the company's CI reports after running the scenarios N times: the statistics stay with Niadra.
    Every pin the recording requires must match each scenario's recorded build, except those in `vary`."""

    scenario_ids: list[IdStr] = Field(min_length=1, max_length=200)
    build: TurnBuild
    mode: ReplayMode = "hermetic_turn"
    runs: int | None = Field(default=None, ge=1, le=MAX_RUNS)
    vary: list[PinName] = Field(default_factory=list, max_length=5)
    results: list[ReplayResult] = Field(min_length=1, max_length=5000)


class AssertionStats(Model):
    """One assertion over the run's completed executions, against its baseline. `p_value` is the one-sided
    Fisher exact test that the run passes less often than the baseline, rounded to 6 decimals."""

    id: AssertionId
    passed: int
    failed: int
    not_checked: int
    baseline_passed: int
    baseline_failed: int
    drop: float = Field(description="The baseline's pass rate minus the run's.")
    p_value: float
    regression: bool
    flaky: bool


class ScenarioVerdict(Model):
    scenario_id: IdStr
    verdict: Verdict
    completed: int
    infrastructure_errors: int
    pin_mismatches: int
    needs_paraphrase: bool = Field(
        description="An assertion passed and failed across the runs: its next run must include paraphrases."
    )
    baseline_run_id: IdStr | None = Field(
        default=None, description="The earlier run compared against; none when it was the recording."
    )
    assertions: list[AssertionStats]


class ScenarioRunSummary(Model):
    scenarios: list[ScenarioVerdict] = Field(default_factory=list)


class ScenarioRun(Model):
    run_id: IdStr
    status: Literal["pending", "done"]
    verdict: Verdict | None = Field(default=None, description="The worst of the scenarios' verdicts.")
    mode: ReplayMode
    vary: list[PinName] = Field(default_factory=list)
    build: TurnBuild
    summary: ScenarioRunSummary
    created_at: datetime


class Change(Model):
    """One entry of the change log a bisection walks: a pin that changed between two builds of an agent, or
    a section of the space's configuration that changed between two applied snapshots (`config`, named by
    the section, with no agent: every agent reads it, and its digests stand for the values)."""

    kind: Literal["prompt", "model", "code", "data", "config", "compiler"]
    at: datetime
    agent: ShortStr | None = None
    name: ShortStr | None = Field(default=None, description="The prompt or the tool, for those kinds.")
    before: str | None = None
    after: str | None = None


class ChangePage(Model):
    items: list[Change]
    next_cursor: str | None = None


class DataIssue(Model):
    """A problem in the company's data, returned to its owner with no personal data: one open issue per
    class, type, field and source, counted each time it is found again."""

    issue_id: IdStr
    kind: DataIssueKind = Field(
        description="`null_field`, `out_of_vocabulary`, `stale_source`, `invalid_value`: a value an agent's"
        " tool showed; `coverage_drop`: a field filled less often than before; `divergence`: two sources of"
        " one value disagree; `drift`: the schema a type mirrors changed, and a new field is already served"
        " to agents as it came (a note, nothing to fix); `field_held`: a new field that may be personal data"
        " or free text, held from agents until the type declares it;"
        " `row_failed`: a row that failed every attempt to be taken in is kept aside, sealed, never deleted,"
        " until an admin retries it (`POST /v1/intake/failed/retry`), `source` naming where it came from;"
        " `rule_conflict`: two rules decide one thing differently; `type_undeclared`: tools"
        " showed objects of a type the space never declared; `budget_reached`: the space spent its day's AI"
        " ceiling for a purpose (`source`), and memory waits for the next UTC day; `inference_limit`: what"
        " the space's systems send passed a limit of what Niadra infers on its own (types, fields or states"
        " of a type, operations), or a name was not safe to use; what passed it is still stored and served"
        " as it came, and `field` names the limit."
    )
    object_type: ShortStr | None = None
    field: ShortStr | None = None
    source: ShortStr | None = Field(
        default=None, description="Where the data came from: a tool or a system of record."
    )
    refs: list[ObjectKey] = Field(
        default_factory=list, max_length=50, description="The first objects it was seen on, up to 50."
    )
    occurrences: int
    opened_at: datetime
    last_seen_at: datetime
    acknowledged_at: datetime | None = Field(
        default=None, description="Once acknowledged, the next occurrence opens a new issue."
    )


class DataIssuePage(Model):
    items: list[DataIssue]
    next_cursor: str | None = None


class FailedIntake(Model):
    """A batch of the company's data that failed every attempt to be taken in: kept aside, sealed as it
    came, never deleted, until an admin retries it. What kind of batch, from which source and when; never
    its content."""

    intake_id: UUID
    kind: Literal["events", "turns"] = Field(
        description="`events` for `POST /v1/events` and the webhooks, `turns` for `POST /v1/turns`."
    )
    source_id: UUID
    received_at: datetime
    failed_at: datetime
    attempts: int = Field(ge=0)


class FailedIntakePage(Model):
    items: list[FailedIntake] = Field(description="Oldest first, up to 500 of each kind.")
    kept: int = Field(
        ge=0,
        description="Every batch kept aside, listed or not; past the listing, `POST /v1/intake/failed/retry`"
        " with no ids retries all of them.",
    )


class FailedIntakeRetry(Model):
    intake_ids: list[UUID] | None = Field(
        default=None, max_length=500, description="The batches to retry; none retries every kept batch."
    )


class FailedIntakeRetried(Model):
    retried: int = Field(ge=0, description="Batches put back in the space's order for a worker to store.")


class Notification(Model):
    """A webhook's body, for the company that pulls instead of receiving it: the same fields, ids and
    pointers only. An event aimed at one endpoint, or about a memory item only some sources may read,
    reaches its webhooks only."""

    id: IdStr = Field(description="The event's id; a webhook delivery of the same event has its own.")
    type: ShortStr
    created_at: datetime
    tenant: IdStr
    subject: dict[str, Any] | None = None
    data: dict[str, Any]
    links: dict[str, str] = Field(default_factory=dict)


class NotificationPage(Model):
    """Oldest first. `next_cursor` is where the next poll starts: the same cursor when nothing is new."""

    items: list[Notification]
    next_cursor: str
