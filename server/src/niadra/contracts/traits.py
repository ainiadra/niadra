"""Traits on the governance API: retract, dry-run a rule, opt a profile out (7.1, 20)."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints

from niadra.contracts.common import Model

TraitCatalog = Literal[
    "recurring_complaint",
    "broken_company_promise",
    "asked_to_cancel",
    "preferred_channel",
    "does_not_answer_calls",
    "escalated_to_human",
    "declining_sentiment",
    "overdue_invoices",
]


class EvidencePointer(Model):
    kind: str
    id: str


class TraitView(Model):
    trait_id: str
    profile_id: str
    name: str
    layer: str
    rule_version: str
    first_seen: datetime
    last_evidence_at: datetime
    expires_at: datetime
    confidence: float
    retracted_at: datetime | None = None
    evidence: list[EvidencePointer] = Field(default_factory=list)


class TraitRuleDraft(Model):
    """A catalog rule with tenant parameters (20.5); the dry-run never stores it."""

    rule_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.:-]{0,63}$")]
    catalog: TraitCatalog
    version: str = "draft"
    params: dict[str, Any] = Field(default_factory=dict)


class TraitSample(Model):
    profile_id: str
    pseudonym: str
    value: str
    expires_at: datetime
    evidence: list[EvidencePointer]


class TraitDryRunResponse(Model):
    rule_id: str
    window_days: int
    profiles_evaluated: int
    profiles_matching: int
    sample: list[TraitSample]


class OptOutResponse(Model):
    profile_id: str
    opted_out_at: datetime
