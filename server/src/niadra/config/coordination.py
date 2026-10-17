"""The `coordination` document (24.3.4): how each contact purpose fails and whether its outbound contacts
need a token, the contact and paid budgets, the channels' service windows and quiet hours, the ownership
order and the sources that observe it, the suppression reasons, the gateways, the commitment types and what
a handoff's outcome feeds.

Only a space that turned on `coordination` (the `features` document) reads it. By default transactional and
service contacts fail open, and marketing, retention and collection fail closed. The integration role
writes it; a change that touches a purpose that fails closed, the suppression reasons, the collection budget,
the gateways or the contact keys also needs the security role (`security_changes`).
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from niadra.contracts.coordinate import MAX_TOKEN_TTL_S
from niadra.contracts.coordinate import Name as CoordName
from niadra.contracts.signals import Interaction
from niadra.domain.coordinate.model import Compatibility, EffectKind, FailDirection

ClockTime = Annotated[str, StringConstraints(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")]
HolderName = Annotated[str, StringConstraints(min_length=1, max_length=256)]
SecretRef = Annotated[str, StringConstraints(min_length=1, max_length=200)]

COLLECTION = "collection"


class _Doc(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, defer_build=True)


class BudgetSlice(_Doc):
    """A share of a purpose's budget for one funnel stage, named by the check's `intent`: spent until it
    runs out, or spread evenly over `spread_days`."""

    intent: CoordName
    limit: int = Field(ge=1, le=1000)
    pacing: Literal["until_exhausted", "spread"] = "until_exhausted"
    spread_days: int | None = Field(default=None, ge=1, le=90)

    @model_validator(mode="after")
    def _spread_needs_days(self) -> "BudgetSlice":
        if (self.pacing == "spread") != (self.spread_days is not None):
            raise ValueError("spread_days goes with pacing spread, and only with it")
        return self


class ContactBudget(_Doc):
    """Contacts per subject in a rolling window, summed over every agent and vendor of the space."""

    limit: int = Field(ge=1, le=1000)
    per_hours: int = Field(ge=1, le=24 * 90)
    slices: list[BudgetSlice] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _slices_fit(self) -> "ContactBudget":
        if len({s.intent for s in self.slices}) != len(self.slices):
            raise ValueError("a budget slice is declared twice")
        if sum(s.limit for s in self.slices) > self.limit:
            raise ValueError("the slices add up to more than the budget")
        return self


class PurposeRule(_Doc):
    """A contact purpose. `fail` is what a check answers when Niadra cannot decide; `token` makes an
    outbound contact of the purpose need a contact token at the dispatch point, which then refuses it
    without one, so a purpose with a token fails closed."""

    name: CoordName
    fail: FailDirection
    token: bool = False
    budget: ContactBudget | None = None

    @model_validator(mode="after")
    def _token_fails_closed(self) -> "PurposeRule":
        if self.token and self.fail is not FailDirection.CLOSED:
            raise ValueError(f"purpose {self.name}: a purpose with a token fails closed")
        return self


class QuietHours(_Doc):
    """Local times, in the space's timezone unless `timezone` says otherwise. A contact inside them is
    deferred to their end, never dropped."""

    start: ClockTime
    end: ClockTime
    timezone: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _not_empty(self) -> "QuietHours":
        if self.start == self.end:
            raise ValueError("quiet hours start and end at the same time")
        return self


class ChannelRule(_Doc):
    """`free_form_hours` is the service window since the customer's last message (24 on WhatsApp); outside
    it, `template_outside_window` says a paid message template is required."""

    name: CoordName
    free_form_hours: int | None = Field(default=None, ge=1, le=24 * 7)
    template_outside_window: bool = False
    quiet_hours: QuietHours | None = None


class ObservationRule(_Doc):
    """What a system event of `canonical_type` from the source says: from the event's time, its subject (or
    each object it names) is held at `level` by `holder`; without a level, what the source said before ends.
    An observation is never refused, and the most restrictive valid claim still decides who holds."""

    canonical_type: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    target: Literal["subject", "object"] = "subject"
    level: CoordName | None = None
    holder: HolderName | None = None
    holder_kind: Literal["agent", "human", "queue", "system"] = "human"
    intents: frozenset[CoordName] = frozenset()
    valid_s: int | None = Field(default=None, ge=60, le=30 * 86_400)

    @model_validator(mode="after")
    def _holder_with_level(self) -> "ObservationRule":
        if (self.level is None) != (self.holder is None):
            raise ValueError(f"{self.canonical_type}: a level names its holder, and an end neither")
        return self


class OwnershipSource(_Doc):
    """A source that observes who holds a subject (a service panel through a mapping, a middleware's pause
    flag through the worker). `max_validity_s` caps what it states, including what never expires where it
    comes from; `levels` are the ones it may state. A mapped source reads the system events of `source_id`
    through its `rules`; the worker states its observations through the claims route, naming the source."""

    name: CoordName
    via: Literal["mapping", "worker"]
    max_validity_s: int = Field(ge=60, le=30 * 86_400)
    levels: frozenset[CoordName] = Field(min_length=1)
    source_id: UUID | None = None
    rules: list[ObservationRule] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _rules_of_a_mapping(self) -> "OwnershipSource":
        mapped = self.via == "mapping"
        if mapped != (self.source_id is not None) or mapped != bool(self.rules):
            raise ValueError(
                f"ownership source {self.name}: a mapping has a source and rules, a worker neither"
            )
        if len({r.canonical_type for r in self.rules}) != len(self.rules):
            raise ValueError(f"ownership source {self.name}: a canonical type is mapped twice")
        if unknown := sorted({r.level for r in self.rules if r.level is not None} - self.levels):
            raise ValueError(
                f"ownership source {self.name}: rules state levels it may not ({', '.join(unknown)})"
            )
        return self


class OwnershipSettings(_Doc):
    """`levels` go from the most restrictive to the least: a claim at a softer level never demotes a
    stricter one still valid. `declaration_max_s` caps a lease declared through the API."""

    levels: list[CoordName] = Field(
        default_factory=lambda: ["closed", "human_active", "transfer_pending", "soft_pause", "agent_active"],
        min_length=1,
        max_length=10,
    )
    declaration_max_s: int = Field(default=86_400, ge=60, le=86_400)
    sources: list[OwnershipSource] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _known_levels(self) -> "OwnershipSettings":
        if len(set(self.levels)) != len(self.levels):
            raise ValueError("an ownership level is declared twice")
        if len({s.name for s in self.sources}) != len(self.sources):
            raise ValueError("an ownership source is declared twice")
        for source in self.sources:
            if unknown := sorted(source.levels - set(self.levels)):
                raise ValueError(f"ownership source {source.name}: unknown levels {', '.join(unknown)}")
        mapped = [s.source_id for s in self.sources if s.source_id is not None]
        if len(set(mapped)) != len(mapped):
            raise ValueError("two ownership sources read the same source's events")
        return self

    def observation(
        self, source_id: UUID, canonical_type: str
    ) -> "tuple[OwnershipSource, ObservationRule] | None":
        """The mapped source and rule a system event of `source_id` and `canonical_type` is read by."""
        for source in self.sources:
            if source.source_id == source_id:
                rule = next((r for r in source.rules if r.canonical_type == canonical_type), None)
                return (source, rule) if rule is not None else None
        return None


class SuppressionReason(_Doc):
    """Why a subject is not to be contacted for a purpose; `resume_after_hours` lifts the suppression on its
    own, and None keeps it until someone lifts it."""

    name: CoordName
    resume_after_hours: int | None = Field(default=None, ge=1, le=24 * 365)


class Gateway(_Doc):
    """A dispatch point that checks contact tokens offline. `secret_ref` names, in the space's vault, the
    key it shares with Niadra for the token's `rcpt`."""

    gateway_id: CoordName
    secret_ref: SecretRef
    channels: frozenset[CoordName] = Field(min_length=1)


class CommitmentType(_Doc):
    """How a new commitment of the type meets the ones that hold for the subject: the first holds, the best
    (the higher `score`) wins, or an exclusive one holds alone, over every type."""

    name: CoordName
    compatibility: Compatibility
    max_validity_days: int = Field(default=30, ge=1, le=365)


class OutcomeSuppression(_Doc):
    purpose: CoordName
    reason: CoordName
    channel: CoordName | None = None


class HandoffOutcomeRule(_Doc):
    """What an outcome the receiving side reports feeds besides the subject's memory: suppressions of the
    subject, and interactions its signals take (a preference said to the person who took over)."""

    name: CoordName
    suppress: list[OutcomeSuppression] = Field(default_factory=list, max_length=10)
    interactions: list[Interaction] = Field(default_factory=list, max_length=10)


class ReportedOutcome(_Doc):
    """A system event of the receiving side that reports a handoff's outcome without calling the API: a
    ticket closed as solved, a sale won. The event names the handoff by an object reference of type `handoff`
    or by its field `handoff_field`."""

    source_id: UUID
    canonical_type: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    outcome: CoordName
    handoff_field: Annotated[str, StringConstraints(min_length=1, max_length=120)] = "handoff_id"


class HandoffSettings(_Doc):
    """`level` is the ownership level a created handoff holds its subject at for the receiving side until
    the outcome comes back (None holds nothing); `expected_hours` is when an outcome is due, after which the
    handoff is `expired`. `outcomes` is the vocabulary the receiving side reports in, through the API or
    through the system events `reported` maps."""

    level: CoordName | None = "transfer_pending"
    expected_hours: int = Field(default=24, ge=1, le=24 * 30)
    outcomes: list[HandoffOutcomeRule] = Field(default_factory=list, max_length=50)
    reported: list[ReportedOutcome] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _reported_known(self) -> "HandoffSettings":
        names = {o.name for o in self.outcomes}
        for rule in self.reported:
            if names and rule.outcome not in names:
                raise ValueError(f"handoffs.reported: {rule.outcome} is not an outcome of the vocabulary")
        keys = [(r.source_id, r.canonical_type) for r in self.reported]
        if len(set(keys)) != len(keys):
            raise ValueError("handoffs.reported: a source's event type reports two outcomes")
        return self

    def report(self, source_id: UUID, canonical_type: str) -> ReportedOutcome | None:
        """The outcome a system event of `source_id` and `canonical_type` reports, if any."""
        return next(
            (r for r in self.reported if r.source_id == source_id and r.canonical_type == canonical_type),
            None,
        )


class PaidBudget(_Doc):
    """Units a paid operation (a search, a refresh, an expensive call) may spend per object, subject or
    space in a rolling window, in the company's units, never in money. An attempt of unknown cost reserves
    the highest cost measured for the operation, and a paid call that fails still counts. `max_units_per_call`
    is the ceiling of one call."""

    operation: CoordName
    units: int = Field(ge=1, le=1_000_000)
    per_hours: int = Field(ge=1, le=24 * 90)
    scope: Literal["object", "subject", "space"] = "object"
    max_units_per_call: int | None = Field(default=None, ge=1)


class ShadowSettings(_Doc):
    days: int = Field(default=30, ge=1, le=90)
    sample: int = Field(default=50, ge=0, le=500)


class ContactKeySettings(_Doc):
    """The space's contact token key. Raising `generation` rotates it: the new key signs, and the previous
    one is still published as `retiring` until `previous_until`, for the tokens it signed to expire."""

    generation: int = Field(default=1, ge=1, le=1000)
    previous_until: datetime | None = None


WHATSAPP = ChannelRule(name="whatsapp", free_form_hours=24, template_outside_window=True)


def _default_purposes() -> list[PurposeRule]:
    # L23: transactional and service fail open; marketing, retention and collection fail closed.
    return [
        PurposeRule(name="transactional", fail=FailDirection.OPEN),
        PurposeRule(name="service", fail=FailDirection.OPEN),
        PurposeRule(name="marketing", fail=FailDirection.CLOSED, token=True),
        PurposeRule(name="retention", fail=FailDirection.CLOSED, token=True),
        PurposeRule(name=COLLECTION, fail=FailDirection.CLOSED, token=True),
    ]


def _default_reasons() -> list[SuppressionReason]:
    return [
        SuppressionReason(name="opt_out"),
        SuppressionReason(name="in_service"),
        SuppressionReason(name="awaiting_third_party", resume_after_hours=72),
        SuppressionReason(name="grace_period", resume_after_hours=7 * 24),
    ]


def _default_effect_days() -> dict[EffectKind, int | None]:
    # None: the key lasts as long as the fact it is about (one filing per notice, whenever it comes). A
    # delivery's key (one farewell per conversation) holds a week: every key lives in the state store, and a
    # month of a large space's conversations would not fit its 64 MB.
    return {
        EffectKind.DELIVERY: 7,
        EffectKind.NOTICE: 30,
        EffectKind.PAID_CALL: 1,
        EffectKind.EXTERNAL_ACTION: 30,
        EffectKind.DOCUMENT: None,
        EffectKind.FILING: None,
    }


class CoordinationSettings(_Doc):
    purposes: list[PurposeRule] = Field(default_factory=_default_purposes, max_length=30)
    channels: list[ChannelRule] = Field(default_factory=lambda: [WHATSAPP], max_length=20)
    ownership: OwnershipSettings = Field(default_factory=OwnershipSettings)
    suppression_reasons: list[SuppressionReason] = Field(default_factory=_default_reasons, max_length=30)
    gateways: list[Gateway] = Field(default_factory=list, max_length=20)
    commitments: list[CommitmentType] = Field(default_factory=list, max_length=20)
    paid_budgets: list[PaidBudget] = Field(default_factory=list, max_length=30)
    effect_key_days: dict[EffectKind, int | None] = Field(
        default_factory=_default_effect_days,
        description="How long an effect key holds, per kind; null holds it for as long as the fact exists.",
    )
    effect_lease_s: int = Field(
        default=60,
        ge=5,
        le=3600,
        description="How long an attempt may stay in flight before it counts as `unknown_outcome`.",
    )
    decision_valid_s: int = Field(default=30, ge=1, le=300)
    token_ttl_s: int = Field(default=MAX_TOKEN_TTL_S, ge=5, le=MAX_TOKEN_TTL_S)
    contact_keys: ContactKeySettings = Field(default_factory=ContactKeySettings)
    handoffs: HandoffSettings = Field(default_factory=HandoffSettings)
    shadow: ShadowSettings = Field(default_factory=ShadowSettings)

    @model_validator(mode="after")
    def _consistent(self) -> "CoordinationSettings":
        for what, names in (
            ("purpose", [p.name for p in self.purposes]),
            ("channel", [c.name for c in self.channels]),
            ("suppression reason", [r.name for r in self.suppression_reasons]),
            ("gateway", [g.gateway_id for g in self.gateways]),
            ("commitment type", [c.name for c in self.commitments]),
            ("paid budget", [b.operation for b in self.paid_budgets]),
        ):
            if len(set(names)) != len(names):
                raise ValueError(f"a {what} is declared twice")
        if any(p.name == "any" for p in self.purposes):
            # A suppression of `any` purpose holds for every purpose (the suppression-list spec, 6.1).
            raise ValueError("no purpose is named any: a suppression of any purpose holds for every purpose")
        channels = {c.name for c in self.channels}
        for gateway in self.gateways:
            if unknown := sorted(gateway.channels - channels):
                raise ValueError(f"gateway {gateway.gateway_id}: unknown channels {', '.join(unknown)}")
        for kind, days in self.effect_key_days.items():
            if days is not None and not 1 <= days <= 3650:
                raise ValueError(f"effect_key_days.{kind.value}: from 1 to 3650 days, or null")
        levels = set(self.ownership.levels)
        if self.handoffs.level is not None and self.handoffs.level not in levels:
            raise ValueError(f"handoffs.level: {self.handoffs.level} is not an ownership level")
        purposes = {p.name for p in self.purposes}
        reasons = {r.name for r in self.suppression_reasons}
        outcomes = [o.name for o in self.handoffs.outcomes]
        if len(set(outcomes)) != len(outcomes):
            raise ValueError("a handoff outcome is declared twice")
        for outcome in self.handoffs.outcomes:
            for entry in outcome.suppress:
                if (
                    entry.purpose not in purposes
                    or entry.reason not in reasons
                    or entry.channel
                    not in {
                        None,
                        *channels,
                    }
                ):
                    raise ValueError(f"handoff outcome {outcome.name}: an unknown purpose, reason or channel")
        return self


def security_changes(before: CoordinationSettings, after: CoordinationSettings) -> list[str]:
    """The parts of a change that also need the security role: any purpose that fails closed before or after
    it, the collection budget, the suppression reasons, and what a gateway checks tokens with (the gateways
    and the contact keys). Names parts, never values."""
    old = {p.name: p for p in before.purposes}
    new = {p.name: p for p in after.purposes}
    changed: list[str] = []
    for name in sorted(old.keys() | new.keys()):
        was, now = old.get(name), new.get(name)
        if was == now:
            continue
        closed = any(p is not None and p.fail is FailDirection.CLOSED for p in (was, now))
        budget_moved = (was.budget if was else None) != (now.budget if now else None)
        if closed or (name == COLLECTION and budget_moved):
            changed.append(f"purposes.{name}")
    for part in ("suppression_reasons", "gateways", "contact_keys"):
        if getattr(before, part) != getattr(after, part):
            changed.append(part)
    return changed
