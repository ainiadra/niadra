"""Runs of the configuration assistant (22.4)."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

MONTHLY_RUNS = 20
MAPPING_SAMPLES = 100
TRANSCRIPT_SAMPLES = 200
"""Ceilings of 22.5: runs per month and samples per run."""


class AssistTask(StrEnum):
    WEBHOOK_MAPPING = "webhook_mapping"
    EXTRACTION_SCHEMA = "extraction_schema"
    SOURCE_POLICY = "source_policy"
    TRIGGER_OR_TRAIT_RULE = "trigger_or_trait_rule"

    @property
    def needs_source(self) -> bool:
        return self is not AssistTask.TRIGGER_OR_TRAIT_RULE

    @property
    def needs_description(self) -> bool:
        return self in (AssistTask.SOURCE_POLICY, AssistTask.TRIGGER_OR_TRAIT_RULE)


class AssistStatus(StrEnum):
    QUEUED = "queued"
    PROPOSED = "proposed"
    DELIVERED = "delivered"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AssistRun:
    """`proposal` stays in the cell (request, document, checks, usage); only the document leaves it."""

    run_id: UUID
    task: AssistTask
    status: AssistStatus
    requested_by: UUID
    created_at: datetime
    sample_ids: tuple[str, ...] = ()
    diff_hash: str | None = None
    proposal: dict[str, Any] = field(default_factory=dict)
