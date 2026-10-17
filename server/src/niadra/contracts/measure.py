"""`GET /v1/context-use` and `GET /v1/context-use/{conversation_id}` (7.1, 19.4)."""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from niadra.contracts.common import IdStr, Model
from niadra.domain.measure.aggregate import Rate


class RateOut(Model):
    value: float
    low: float = Field(description="Lower bound of the 95% Wilson interval.")
    high: float
    n: int = Field(description="The denominator: small samples get wide intervals.")

    @classmethod
    def of(cls, rate: Rate | None) -> "RateOut | None":
        return cls(value=rate.value, low=rate.low, high=rate.high, n=rate.n) if rate else None


class ContextUseBucket(Model):
    day: date | None = None
    source_id: UUID | None = None
    vendor: str | None = Field(
        default=None, description='From the source configuration; "" when a source has none.'
    )
    channel: str | None = None
    view: str | None = Field(
        default=None, description="Empty for session-level counters (coverage, transfers, recontacts)."
    )
    experiment_group: str | None = None
    sessions: int
    no_context: int = Field(description="Sessions without any `context()` read: coverage, never a zero rate.")
    deliveries: int
    not_measured: int
    late_deliveries: int = Field(description="Context delivered after the agent's first turn.")
    deliveries_used: int
    questions: int
    repeated: int
    contradicted: int
    selection_misses: int = Field(
        description="Contradictions of items the pack did not carry; never the agent's."
    )
    transfers: int
    transfers_unread: int
    recontacts: int
    trimmed_deliveries: int = Field(
        default=0, description="Deliveries whose pack left out what that agent did not use (15.8)."
    )
    tokens_saved: int = Field(
        default=0,
        description="Tokens those packs left out, summed over deliveries: the compiler's estimate, the one "
        "its budgets use.",
    )
    answers_checked: int = Field(
        default=0,
        description="Agent answers the SDK's backing check read (session-level rows, per agent source).",
    )
    unbacked_values: int = Field(
        default=0,
        description="Numbers, dates, codes and amounts those answers stated that no source the agent had "
        "backs: the pack, the system records, the customer's words, the tool results it recorded.",
    )
    unbacked_per_1000: float | None = Field(
        default=None, description="`unbacked_values` per thousand checked answers; null without any."
    )
    guard_violations: int = Field(
        default=0, description="Guard lines the agents went against (each also counts in `contradicted`)."
    )
    object_rows: int = Field(
        default=0,
        description="Rows about an object (what its participants said, its instructions, its open items) "
        "delivered on the object's line.",
    )
    object_rows_used: int = Field(default=0, description="Of those, the rows of objects the agent used.")
    instruction_violations: int = Field(
        default=0,
        description="Agent messages that did what an instruction about an object the pack carried said not "
        "to do: a settlement proposed on a case whose instruction says not to propose one.",
    )
    usage_rate: RateOut | None = None
    repetition_rate: RateOut | None = None
    transfer_unread_rate: RateOut | None = None
    recontact_rate: RateOut | None = None


class EstimateOut(Model):
    value: float
    low: float
    high: float


class SignalReview(Model):
    signal: str = Field(description="`used`, `repeated`, `contradicted` or `selection_miss`.")
    judged: int = Field(description="Reviewers' verdicts on marks of this signal.")
    confirmed: int = Field(description="Verdicts that say the mark holds.")
    agreement: RateOut | None = Field(
        default=None, description="`confirmed` over `judged`, with its interval; null without verdicts."
    )
    contract_ready: bool = Field(
        default=False,
        description="True when the lower bound of `agreement` reaches 0.90: the signal can enter a contract.",
    )
    measured: int = Field(default=0, description="The signal's count in these buckets.")
    calibrated: EstimateOut | None = Field(
        default=None,
        description="`measured` corrected by the share of marks reviewers confirmed, with that share's "
        "interval: the marks review would keep. It corrects wrong marks only, never missed ones.",
    )


class TrimUnitOut(Model):
    unit: str = Field(description="`kind:category`, e.g. `fact:contract` or `episode:technical_visit`.")
    kind: str
    category: str | None = None
    deliveries: int = Field(description="Measured deliveries of the window that carried it; none used it.")

    @classmethod
    def of(cls, unit: str, deliveries: int) -> "TrimUnitOut":
        kind, _, category = unit.partition(":")
        return cls(unit=unit, kind=kind, category=category or None, deliveries=deliveries)


class ContextTrimOut(Model):
    """What one source's packs of one view leave out because it never used them (15.8)."""

    source_id: UUID
    view: str
    active: bool = Field(description="False when the space turned trimming off or the source is exempt.")
    inactive_reason: Literal["disabled", "opt_out", "critical"] | None = None
    units: list[TrimUnitOut]
    derived_at: datetime = Field(description="The measurement that last derived it.")
    window_days: int
    min_deliveries: int


class SlotChannelUse(Model):
    """What one retrieval channel brought to the per-turn slots of one view, and what
    agents did with it (11, 3.4). A slot two channels ranked counts for both."""

    view: str
    slot_channel: str = Field(
        description="The retrieval channel: `exact`, `values`, `lexical`, `temporal` or `semantic`."
    )
    turns: int = Field(description="Measured reads with slots in which the channel ran.")
    skipped: int = Field(
        description="Of those, reads where it missed its deadline and the slots went without it."
    )
    slots: int = Field(description="Slotted items the channel ranked.")
    used: int
    repeated: int = Field(description="Slotted items the agent asked the customer for again.")
    contradicted: int
    usage_rate: RateOut | None = Field(default=None, description="`used` over `slots`, with its interval.")
    weight: float = Field(description="The channel's weight in this view's fusion today.")
    default_weight: float


class GatedTurnsOut(Model):
    """The intent gate (13, 6.4), per view: turns made only of thanks, a short confirmation, a
    request to repeat or a greeting, served the pinned skeleton without slots, and how many the agent then
    followed by asking the customer for something memory held: what the gate cost."""

    view: str
    turns: int = Field(description="Measured reads the gate served without slots.")
    asked_again: int = Field(
        description="Of those, reads after which the agent asked the customer for something the pack held."
    )
    asked_again_rate: RateOut | None = Field(default=None, description="`asked_again` over `turns`.")


class GuardTypeUse(Model):
    """Guard lines (13, 6.1), per value type and arm over the report's period: how often agents
    contradicted a settled value slotted without a guard (`value`), went against a guard (`guards`), or
    would have gone against the guard the control group never saw (`control`)."""

    value_type: str
    arm: Literal["value", "guards", "control"]
    slots: int = Field(description="Values slotted (`value`) or guards written (`guards`, `control`).")
    contradicted: int = Field(description="Of those, contradicted, or went against.")
    contradiction_rate: RateOut | None = None


class WeightRunOut(Model):
    day: date
    computed_at: datetime
    version: int
    outcome: Literal["applied", "unchanged", "no_verdicts", "vetoed"]
    guard: str | None = Field(
        default=None,
        description="The control group's say: `no_experiment`, `too_small`, `holds`, `worse` or `narrowed`.",
    )


class RetrievalWeightsOut(Model):
    """The space's learned fusion weights (11, 3.4): moved once a night by the slots' verdicts, at most
    10% a week, within half and double the default, never while the control group says memory did worse."""

    version: int = Field(description="Goes up each night a weight moved; 0 while the defaults hold.")
    weights: dict[str, dict[str, float]] = Field(
        description="Per view, the weight of each channel; a view not listed fuses with the defaults."
    )
    defaults: dict[str, float]
    runs: list[WeightRunOut] = Field(description="The last nightly runs, newest first.")


class LearnedViewOut(Model):
    """One reader's learned view (13, 6.3): per section, the ceiling its skeleton compiles with and the
    view's own, in tokens; `slots` is the per-turn slots' budget."""

    source_id: UUID
    target: str = Field(description="The target model the reads named, `provider/model`, or `neutral`.")
    view: str
    factors: dict[str, float] = Field(description="Per section, times the view's own budget (0.5 to 1.5).")
    budgets: dict[str, int] = Field(description="Per section (`fact`, `episode`, `action`, `slots`), tokens.")
    defaults: dict[str, int]
    tokens_delta: int = Field(
        description="`budgets` minus `defaults` over the sections: at most this many tokens more (or fewer, "
        "when negative) per read than the view's own."
    )


class ViewRunOut(Model):
    day: date
    computed_at: datetime
    version: int
    outcome: Literal["applied", "unchanged", "no_verdicts", "vetoed"]
    guard: str | None = Field(
        default=None,
        description="The space experiment's say: `no_experiment`, `too_small`, `holds`, `worse`, `narrowed`.",
    )


class ViewBudgetsOut(Model):
    """The views agents and models learned to use (13, 6.3): moved once a night by what the agents used and
    missed, at most 10% a week, within half and one and a half times the view's own, never while a control
    group says it did worse. A fixed share of the customers keeps the view's own budgets to compare."""

    version: int = Field(
        description="Goes up each night a learned view moved; 0 while every view is its own."
    )
    control_fraction: float = Field(description="The share of customers who keep the view's own budgets.")
    readers: list[LearnedViewOut] = Field(
        description="Per source, target model and view that learned something."
    )
    runs: list[ViewRunOut] = Field(description="The last nightly runs, newest first.")


class ClaimRates(Model):
    """What one agent's outputs claimed over the period: the verdicts by category, from the turn records."""

    source_id: UUID
    agent: str
    turns: int
    claims: int
    evidenced: int = Field(
        description="Claims whose verdict stands on evidence: `matched`, `quoted_found`, `anchored`."
    )
    categories: dict[str, dict[str, int]] = Field(description="Category -> verdict -> claims.")


class ConstraintRates(Model):
    """What one agent's tools honored of the constraints block over the period: sent, verifiable and violated,
    never only sent, since a tool may relax a filter on its own."""

    source_id: UUID
    agent: str
    turns: int
    sent: int = Field(description="Hard constraints the calls carried.")
    checked: int = Field(description="Results read against them.")
    verifiable: int = Field(description="Results that held every constrained field.")
    violated: int = Field(description="Results that broke a hard constraint the tool did not say it relaxed.")
    unverifiable: int = Field(description="Results that lacked a constrained field.")
    relaxed_declared: int = Field(description="Calls whose tool said it relaxed a constraint.")
    relaxed_suspected: int = Field(
        description="Calls whose results suggest a relaxation the tool did not declare."
    )
    items_presented: int = Field(description="Items the agent's lists showed.")
    violations_per_1000_items: float | None = None


class CostRates(Model):
    """What one agent's turns cost over the period, per turn, from the turn records, against what the company
    measured the same agent costs with memory off, when it declared that."""

    source_id: UUID
    agent: str
    turns: int
    cost_per_turn_usd: float | None
    model_calls_per_turn: float | None = Field(description="Each one reads the prompt's prefix again.")
    tool_calls_per_turn: float | None
    tokens_in_per_turn: float | None
    tokens_cached_per_turn: float | None
    tokens_out_per_turn: float | None
    memory_off_cost_per_turn_usd: float | None = Field(
        default=None,
        description="The cost per turn the company measured with memory off, from the `measurement` "
        "document; absent when it declared none.",
    )
    difference_usd: float | None = Field(
        default=None, description="`cost_per_turn_usd - memory_off_cost_per_turn_usd`: above zero costs more."
    )


class ContextUseReport(Model):
    since: date
    until: date
    group_by: list[str]
    buckets: list[ContextUseBucket]
    review: list[SignalReview] = Field(
        default_factory=list,
        description="How far sample review confirms each signal behind these numbers: verdicts on the same "
        "sources over the period, or its last 90 days when it is longer (22.2).",
    )
    trims: list[ContextTrimOut] = Field(
        default_factory=list,
        description="Per source and view, the item units its packs leave out today for lack of use, with "
        "the evidence; `tokens_saved` in the buckets is what that saved in the period (15.8).",
    )
    slot_channels: list[SlotChannelUse] = Field(
        default_factory=list,
        description="By channel: per view and retrieval channel, the per-turn slots of the period and "
        "their use, under the same filters as the buckets.",
    )
    retrieval_weights: RetrievalWeightsOut | None = Field(
        default=None,
        description="The fusion weights the slots use today.",
    )
    guards: list[GuardTypeUse] = Field(
        default_factory=list,
        description="Per value type and arm, the guards of the period and the contradictions they are "
        "measured against, for the sources the report covers.",
    )
    claims: list[ClaimRates] | None = Field(
        default=None,
        exclude_if=lambda v: v is None,
        description="With the `claims` agent feature on: per source and agent, the claims of its outputs by "
        "category and verdict, from the turn records.",
    )
    constraints: list[ConstraintRates] | None = Field(
        default=None,
        exclude_if=lambda v: v is None,
        description="With the `signals` agent feature on: per source and agent, the hard constraints its "
        "tools were sent, could verify and violated, and the items its lists showed.",
    )
    cost: list[CostRates] | None = Field(
        default=None,
        exclude_if=lambda v: v is None,
        description="With the `turns` agent feature on: per source and agent, calls, tokens and money per "
        "turn, against the cost with memory off the company measured.",
    )
    view_budgets: ViewBudgetsOut | None = Field(
        default=None,
        description="The views agents and models learned to use today, for the sources the report covers.",
    )
    gated_turns: list[GatedTurnsOut] = Field(
        default_factory=list,
        description="Per view, the turns the intent gate served without slots and how often the agent asked "
        "again after one, under the same filters as the buckets.",
    )


class ContextUseEntry(Model):
    manifest_hash: str
    source_id: UUID
    measure_version: str
    channel: str
    view: str
    experiment_group: str
    items: list[dict[str, Any]]
    questions: int
    repeated: int
    used: int
    contradicted: int
    late: bool
    transfer_unread: bool | None = None
    not_measured_reason: str | None = None
    tokens_saved: int = Field(default=0, description="Tokens this delivery's pack left out for lack of use.")
    measured_at: datetime
    slots: list[dict[str, Any]] = Field(
        default_factory=list,
        description="One entry per read of this delivery that carried slots: `channels` (hits "
        "per retrieval channel, `skipped`), `items` (per slot: `id`, `kind`, `channels`, `position`, "
        "`used`, `repeated`, `contradicted`, and `why`: `score`, per channel its `position`, `weight` and "
        "`contribution`), `derived` (the rule of each derived line) and "
        "`weights_version`. The same numbers as the slots receipt, never a line.",
    )


class ContextUseDetail(Model):
    conversation_id: str
    entries: list[ContextUseEntry]


class MeasuredConversation(Model):
    """One measured conversation or task, to open without typing its id."""

    conversation_id: str = Field(description="The conversation id, or `task:<id>` for a task.")
    source_id: UUID
    channel: str
    ended_at: datetime
    measured_at: datetime
    no_context: bool = Field(description="True when the agent read no context at all in it.")
    experiment_group: str = ""


class MeasuredConversationPage(Model):
    items: list[MeasuredConversation]
    next_cursor: str | None = None


class ConversationRef(Model):
    """A conversation or task id in a body: it may be a phone number or an e-mail (7.0)."""

    conversation_id: IdStr
