"""`POST /v1/assist` and `GET /v1/assist/{id}` (7.1, 22.4)."""

import json
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from niadra.contracts.common import Model
from niadra.domain.configure.model import AssistStatus, AssistTask


class AssistRequest(Model):
    task: AssistTask
    source_id: UUID | None = None
    description: Annotated[str, StringConstraints(min_length=3, max_length=2000)] | None = Field(
        default=None, description="What the agent is for, or the rule in plain words; masked before any use."
    )
    example_payload: dict[str, Any] | None = Field(
        default=None,
        description="An example event of the system, as it will arrive, for a mapping before any event came: "
        "masked on arrival (keys and codes kept, values replaced) and only the masked copy is kept or used.",
    )

    @model_validator(mode="after")
    def _small_example(self) -> "AssistRequest":
        if self.example_payload is not None and len(json.dumps(self.example_payload, default=str)) > 65_536:
            raise ValueError("the example event exceeds 64 KB")
        return self

    @model_validator(mode="after")
    def _inputs_of_the_task(self) -> "AssistRequest":
        if self.task.needs_source and self.source_id is None:
            raise ValueError("this task needs `source_id`")
        if self.task.needs_description and not self.description:
            raise ValueError("this task needs `description`")
        return self


class AssistRunOut(Model):
    run_id: UUID
    task: AssistTask
    status: AssistStatus
    created_at: datetime
    config_type: str | None = None
    document: Any = Field(
        default=None, description="The proposed configuration document, as sent to the control plane."
    )
    diff_hash: str | None = None
    checks: dict[str, Any] = Field(
        default_factory=dict, description="Validation against the samples, inside the cell."
    )
    problems: list[str] = Field(default_factory=list)
    diff_id: str | None = Field(
        default=None, description="The diff waiting for approval in the control plane."
    )
