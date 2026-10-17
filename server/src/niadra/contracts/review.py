"""`GET /v1/review/queue`, `POST /v1/review/{sample}/verdicts` and the agreement metric (22.2)."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field, StringConstraints

from niadra.contracts.common import Model
from niadra.contracts.measure import RateOut
from niadra.contracts.traits import EvidencePointer
from niadra.domain.review.verdicts import TargetKind


class ReviewFact(Model):
    fact_id: UUID
    predicate: str
    value: str
    category: str
    evidence_event_ids: list[UUID]
    sensitive: bool = Field(description="Sensitive items show only the item and the turns that support it.")


class ReviewOpenItem(Model):
    item_id: UUID
    description: str
    owner: str = Field(description="`company` or `customer`.")
    promise: bool = Field(description="An item the company owns: something it promised.")
    status: str
    due_at: datetime | None
    evidence_event_ids: list[UUID]


class ReviewTrait(Model):
    trait_id: UUID
    name: str
    value: str
    first_seen: datetime
    confidence: float
    evidence: list[EvidencePointer] = Field(description="The items of this conversation it rests on.")


class ReviewSignal(Model):
    target_id: str
    manifest_hash: str
    item_id: str
    signal: str = Field(description="`used`, `repeated`, `contradicted` or `selection_miss`.")


class ReviewContradiction(Model):
    target_id: str = Field(description="What a verdict on the pair names: `<fact id>:<fact id>:<origin>`.")
    fact_id: UUID = Field(description="The newer fact of the pair.")
    other_fact_id: UUID
    origin: str = Field(
        description="`model` when the extraction marked the conflict, `band` when the nightly comparison of "
        "the facts of one predicate found it without a model."
    )
    predicate: str
    value: str
    other_value: str


class ReviewSampleOut(Model):
    sample_id: UUID
    session_id: UUID
    source_id: UUID
    channel: str
    condition: str
    purpose: str | None
    created_at: datetime
    facts: list[ReviewFact]
    open_items: list[ReviewOpenItem] = Field(
        default_factory=list, description="Pending items and promises the conversation left."
    )
    traits: list[ReviewTrait] = Field(
        default_factory=list, description="Patterns of the customer that rest on this conversation."
    )
    signals: list[ReviewSignal]
    contradictions: list[ReviewContradiction] = Field(
        default_factory=list,
        description="Live pairs of facts in conflict where one fact rests on this conversation; judge each "
        "`holds` when the two really conflict. A verdict changes nothing: the wrong fact leaves through "
        "feedback.",
    )
    withheld: int = Field(description="Items the sample's purpose does not allow the reviewer to see.")


class ReviewQueue(Model):
    items: list[ReviewSampleOut]


class VerdictIn(Model):
    target: TargetKind = Field(description="`fact`, `open_item`, `trait`, `signal` or `contradiction`.")
    target_id: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    verdict: Annotated[str, StringConstraints(min_length=1, max_length=32)]


class VerdictsRequest(Model):
    verdicts: list[VerdictIn] = Field(min_length=1, max_length=200)


class VerdictsResponse(Model):
    sample_id: UUID
    recorded: int
    reviewed_at: datetime


class AgreementOut(Model):
    target: str = Field(
        description="`fact`, `open_item`, `signal`, `trait` or `contradiction`; `signal:used`, "
        "`signal:repeated`, `signal:contradicted` or `signal:selection_miss` for each signal of the "
        "measurement; `contradiction:model` and `contradiction:band` for the pairs each origin found; `all`."
    )
    judged: int
    agreed: int
    system_agreement: RateOut | None
    reviewer_pairs: int
    reviewer_agreement: RateOut | None


class ReviewCase(Model):
    sample_id: UUID
    target: str
    target_id: str
    verdict: str
    now: str = Field(
        description="What the system says today: `marked` or `not_marked` for a signal, `active` or `gone` "
        "for a fact or an open item, `not_measured` or `unknown` when there is nothing to compare."
    )
    passes: bool | None = Field(
        description="Whether today's answer agrees with the verdict; null when unknown."
    )


class CaseTally(Model):
    target: str = Field(description="`fact`, `open_item` or `signal:<name>`.")
    cases: int
    passing: int
    failing: int


class ReviewCases(Model):
    """Every verdict is a test of the space: it runs against today's memory and measurement, so a new
    extractor or new measurement rules show at once which judged cases they now get wrong."""

    since: datetime
    tallies: list[CaseTally]
    failing: list[ReviewCase] = Field(description="The cases that fail today, newest first, up to 50.")


class AgreementReport(Model):
    since: datetime
    targets: list[AgreementOut]
