"""The four signals of context-use measurement (19.2), computed deterministically from deliveries and
the extraction.

Every "does not count" rule of 19.2 lives here, next to the rule it limits, so the table in the
architecture and this file can be read side by side.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from uuid import UUID

from niadra.domain.extract.result import QuestionType
from niadra.domain.measure.model import (
    AgentAnswer,
    AgentClaim,
    AgentQuestion,
    ContextStamp,
    Delivery,
    DeliveryOutcome,
    Handoff,
    ItemOutcome,
    Manifest,
    ManifestItem,
    SessionAction,
    TransferState,
)
from niadra.domain.measure.normalize import normalize_value

CONTACT_REASON = "contact_reason"
"""A question like "what happened?" asks for the reason of contact: episodes and open items answer it."""

_REASON_KINDS = frozenset({"episode", "open_item"})
TRANSFER_WINDOW = timedelta(minutes=10)
# A warm transfer can read the context a moment before the handoff event is stamped.
_TRANSFER_SKEW = timedelta(seconds=60)


@dataclass(frozen=True, slots=True)
class SessionEvidence:
    questions: tuple[AgentQuestion, ...] = ()
    claims: tuple[AgentClaim, ...] = ()
    actions: tuple[SessionAction, ...] = ()
    first_agent_turn_at: dict[UUID, datetime] = field(default_factory=dict)
    stamps: tuple[ContextStamp, ...] = ()
    answers: tuple[AgentAnswer, ...] = ()
    """The agents' messages, for the rules that read what was said (settled values, guards)."""


@dataclass(frozen=True, slots=True)
class MeasureRules:
    single_valued: frozenset[str] = frozenset()
    confirms_before_acting: frozenset[UUID] = frozenset()
    task_session: bool = False
    """In a task of an internal agent, use is measured by the action and repetition is not measured (19.2)."""


def measure_deliveries(
    deliveries: Sequence[tuple[Delivery, Manifest]], evidence: SessionEvidence, rules: MeasureRules
) -> list[DeliveryOutcome]:
    ordered = sorted(deliveries, key=lambda pair: (pair[0].delivered_at, pair[0].manifest_hash))
    questions = _attribute(ordered, evidence.questions, fallback_to_first=True)
    claims = _attribute(ordered, evidence.claims, fallback_to_first=False)
    actions = _attribute(ordered, evidence.actions, fallback_to_first=False)
    outcomes = []
    for index, (delivery, manifest) in enumerate(ordered):
        # A holdout manifest was never shown to the agent: it only measures what was asked again (19.7),
        # and it says nothing about what the agent uses.
        tally = _Tally(manifest, units=not delivery.holdout)
        if not rules.task_session:
            _count_questions(tally, delivery, questions.get(index, []), rules)
        if not delivery.holdout:
            _count_claims(tally, delivery, claims.get(index, []), rules)
            _count_actions(tally, actions.get(index, []))
        first_turn = evidence.first_agent_turn_at.get(delivery.source_id)
        used = {o.item_id for o in tally.outcomes() if o.used}
        bound = [] if delivery.holdout else [i for i in manifest.items if i.bound]
        outcomes.append(
            DeliveryOutcome(
                delivery=delivery,
                view=manifest.view,
                items=tally.outcomes(),
                questions=tally.questions,
                repeated=tally.repeated,
                late=first_turn is not None and first_turn < context_entered_at(delivery, evidence.stamps),
                tokens_saved=0 if delivery.holdout else manifest.tokens_saved,
                object_rows=sum(len(i.bound) for i in bound),
                object_rows_used=sum(len(i.bound) for i in bound if i.item_id in used),
            )
        )
    return outcomes


def context_entered_at(delivery: Delivery, stamps: Sequence[ContextStamp]) -> datetime:
    """When the context went into the agent's prompt (19.2): the SDK's stamp is the truth, since the
    agent may read the context and inject it only turns later; without a stamp, the receipt's time."""
    injected = [
        s.injected_at
        for s in stamps
        if s.source_id == delivery.source_id and (s.etag is None or delivery.etag in (None, s.etag))
    ]
    return min(injected, default=delivery.delivered_at)


def pertinent(
    items: Iterable[ManifestItem], predicate: str | None, object_ref: str | None
) -> list[ManifestItem]:
    """Same predicate or same object (19.2): reciting the whole pack does not inflate use."""
    found = []
    for item in items:
        same_predicate = predicate is not None and item.predicate == predicate
        same_object = object_ref is not None and item.object_ref == object_ref
        asks_reason = predicate == CONTACT_REASON and item.kind in _REASON_KINDS
        if same_predicate or same_object or asks_reason:
            found.append(item)
    return found


def transfer_state(
    handoffs: Sequence[Handoff],
    reads: Sequence[tuple[UUID, datetime]],
    integrated: frozenset[UUID],
    window: timedelta = TRANSFER_WINDOW,
) -> TransferState:
    """Transfer without read: no receipt of the target source within `window` of the handoff (19.2)."""
    if not handoffs:
        return TransferState.NONE
    states = set()
    for handoff in handoffs:
        target = handoff.target_source_id
        if target is None or target not in integrated:
            # A transfer to a source we do not serve is a coverage gap, never an unread transfer.
            states.add(TransferState.NOT_INTEGRATED)
            continue
        read = any(
            source == target and handoff.at - _TRANSFER_SKEW <= at <= handoff.at + window
            for source, at in reads
        )
        states.add(TransferState.READ if read else TransferState.UNREAD)
    for state in (TransferState.UNREAD, TransferState.READ):
        if state in states:
            return state
    return TransferState.NOT_INTEGRATED


class _Tally:
    def __init__(self, manifest: Manifest, *, units: bool) -> None:
        self.manifest = manifest
        self.units = units
        self.flags = {
            item.item_id: ItemOutcome(item.item_id, item.kind, unit=item.unit if units else None)
            for item in manifest.items
        }
        self.misses: dict[str, ItemOutcome] = {}
        self.questions = 0
        self.repeated = 0

    def mark(
        self,
        item: ManifestItem,
        event_id: UUID,
        *,
        used: bool = False,
        repeated: bool = False,
        contradicted: bool = False,
    ) -> None:
        current = self.flags[item.item_id]
        evidence = (
            current.evidence if str(event_id) in current.evidence else (*current.evidence, str(event_id))
        )
        self.flags[item.item_id] = replace(
            current,
            evidence=evidence,
            used=current.used or used,
            repeated=current.repeated or repeated,
            contradicted=current.contradicted or contradicted,
        )

    def miss(self, item: ManifestItem, event_id: UUID) -> None:
        """The agent contradicted an item the pack did not carry: a selection failure (19.2)."""
        self._absent(item, event_id, selection_miss=True)

    def need(self, item: ManifestItem, event_id: UUID) -> None:
        """The agent needed an item its trim left out: its unit comes back to that source (15.8)."""
        if self.units:
            self._absent(item, event_id, trimmed=True)

    def _absent(
        self, item: ManifestItem, event_id: UUID, *, selection_miss: bool = False, trimmed: bool = False
    ) -> None:
        current = self.misses.get(item.item_id) or ItemOutcome(item.item_id, item.kind, delivered=False)
        evidence = (
            current.evidence if str(event_id) in current.evidence else (*current.evidence, str(event_id))
        )
        self.misses[item.item_id] = replace(
            current,
            evidence=evidence,
            selection_miss=current.selection_miss or selection_miss,
            trimmed=current.trimmed or trimmed,
            unit=item.unit if current.trimmed or trimmed else None,
        )

    def outcomes(self) -> tuple[ItemOutcome, ...]:
        return (*self.flags.values(), *self.misses.values())


def _count_questions(
    tally: _Tally, delivery: Delivery, questions: list[AgentQuestion], rules: MeasureRules
) -> None:
    for question in questions:
        if question.type in (QuestionType.VERIFIES, QuestionType.AMBIGUOUS):
            # Verification the policy requires is never repetition; ambiguous ones stay unclassified.
            continue
        tally.questions += 1
        if question.at <= delivery.delivered_at:
            continue  # only a question asked after the delivery is repetition
        for item in pertinent(tally.manifest.trimmed, question.predicate, question.object_ref):
            tally.need(item, question.event_id)
        matches = pertinent(tally.manifest.items, question.predicate, question.object_ref)
        if not matches:
            continue  # asking about a withheld item, or something the pack did not carry, is not repetition
        if question.type is QuestionType.CONFIRMS and delivery.source_id in rules.confirms_before_acting:
            continue
        tally.repeated += 1
        for item in matches:
            tally.mark(item, question.event_id, repeated=True)


def _count_claims(tally: _Tally, delivery: Delivery, claims: list[AgentClaim], rules: MeasureRules) -> None:
    delivered = tally.manifest.items
    for claim in claims:
        if claim.at <= delivery.delivered_at:
            continue
        contradicted = [item for item in delivered if item.item_id in claim.contradicts]
        if not contradicted and claim.predicate in rules.single_valued and claim.value:
            contradicted = [item for item in delivered if _differs(item, claim)]
        for item in contradicted:
            tally.mark(item, claim.event_id, contradicted=True)
        for item in tally.manifest.withheld:
            # Contradicting what the pack did not carry is a selection failure, never the agent's (19.2).
            if item.item_id in claim.contradicts:
                tally.miss(item, claim.event_id)
        needed = pertinent(tally.manifest.trimmed, claim.predicate, claim.object_ref)
        for item in tally.manifest.trimmed:
            if item.item_id in claim.contradicts or item in needed:
                tally.need(item, claim.event_id)
        contradicted_ids = {item.item_id for item in contradicted}
        for item in pertinent(delivered, claim.predicate, claim.object_ref):
            if item.item_id not in contradicted_ids:
                tally.mark(item, claim.event_id, used=True)


def _count_actions(tally: _Tally, actions: list[SessionAction]) -> None:
    for action in actions:
        for item in tally.manifest.items:
            if _acts_on(action, item):
                tally.mark(item, action.action_id, used=True)
        for item in tally.manifest.trimmed:
            if _acts_on(action, item):
                tally.need(item, action.action_id)


def _acts_on(action: SessionAction, item: ManifestItem) -> bool:
    closes = action.closes_item_id is not None and item.item_id == action.closes_item_id
    return closes or (action.object_id is not None and item.object_id == action.object_id)


def _differs(item: ManifestItem, claim: AgentClaim) -> bool:
    if item.kind != "fact" or item.predicate != claim.predicate or item.value is None or claim.value is None:
        return False
    if item.object_ref and claim.object_ref and item.object_ref != claim.object_ref:
        return False
    return normalize_value(item.value) != normalize_value(claim.value)


def _attribute[T: (AgentQuestion, AgentClaim, SessionAction)](
    ordered: Sequence[tuple[Delivery, Manifest]], turns: Iterable[T], *, fallback_to_first: bool
) -> dict[int, list[T]]:
    """Each turn belongs to the latest earlier delivery of its source, so no turn counts twice."""
    by_delivery: dict[int, list[T]] = {}
    for turn in turns:
        mine = [i for i, (delivery, _) in enumerate(ordered) if delivery.source_id == turn.source_id]
        if not mine:
            continue
        earlier = [i for i in mine if ordered[i][0].delivered_at < turn.at]
        if earlier:
            by_delivery.setdefault(earlier[-1], []).append(turn)
        elif fallback_to_first:
            by_delivery.setdefault(mine[0], []).append(turn)
    return by_delivery
