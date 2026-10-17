"""Triggers on the governance API: dry-run a rule and list firings (7.1, 22.1)."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints

from niadra.contracts.common import Model
from niadra.contracts.traits import EvidencePointer

TriggerCondition = Literal[
    "promise.overdue",
    "open_item.due",
    "trait.present",
    "sentiment.drop",
    "identity.ambiguous",
    "fact.contradicted_by_system_event",
    "recontact",
    "object.timer_due",
    "context_use.repetition_rate",
    "source.silent",
    "source.gap",
]


class TriggerRuleDraft(Model):
    rule_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.:-]{0,63}$")]
    condition: TriggerCondition
    version: str = "draft"
    params: dict[str, Any] = Field(default_factory=dict)
    endpoint_id: str
    window_hours: int = Field(default=24, ge=1, le=24 * 90)
    late_policy: Literal["deliver_marked", "suppress"] = "deliver_marked"


class TriggerSample(Model):
    subject_id: str
    pseudonym: str | None = None
    evidence: list[EvidencePointer]


class TriggerDryRunResponse(Model):
    rule_id: str
    window_days: int
    subjects_evaluated: int
    would_fire: int
    target_problem: str | None = Field(
        default=None,
        description="Why the target endpoint could not receive this rule's payload, if it cannot.",
    )
    sample: list[TriggerSample]


class FiringView(Model):
    firing_id: str
    rule_id: str
    rule_version: str
    subject_id: str
    window_key: str
    fired_at: datetime
    late: bool
    retracted_at: datetime | None = None
    evidence: list[EvidencePointer]


class FiringPage(Model):
    items: list[FiringView]
    next_cursor: str | None = None
