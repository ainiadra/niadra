"""Data-subject rights, exports and tenant secrets (5.14, 7.1, 22.3)."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from niadra.contracts.common import Handle, IdStr, Model

SecretValue = Annotated[str, StringConstraints(min_length=1, max_length=8192)]
PurposeName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.:-]{0,63}$")]


class ForgetRequest(Model):
    """What to forget. Handles travel only in the body, never in a URL (7.0)."""

    target: Literal["profile", "handle", "conversation"]
    profile_id: UUID | None = None
    handle: Handle | None = None
    conversation_id: IdStr | None = None
    purpose: PurposeName | None = Field(
        default=None,
        description="An objection to one purpose instead of an erasure (13, 6.6): the customer's rows "
        "collected only for it are deleted, the others keep their other purposes, and what is written "
        "later leaves it out. A profile or handle target; the answer is final (`completed`). The purpose "
        "must be declared in the space's `purposes` document.",
    )

    @model_validator(mode="after")
    def _one_target(self) -> "ForgetRequest":
        given = {
            "profile": self.profile_id is not None,
            "handle": self.handle is not None,
            "conversation": self.conversation_id is not None,
        }
        if not given[self.target] or sum(given.values()) != 1:
            raise ValueError(f"pass only the field of the `{self.target}` target")
        if self.purpose is not None and self.target == "conversation":
            raise ValueError("an objection to a purpose names a profile or a handle")
        return self


class CorrectionRequest(Model):
    """The data subject's right to correction, exercised by a person with the privacy role. It
    becomes the same `feedback.*` event an agent's `POST /v1/feedback` makes, so the lineage keeps it."""

    profile_id: UUID
    action: Literal["retract_fact", "correct_fact", "resolve_open_item"]
    fact_id: UUID | None = None
    open_item_id: UUID | None = None
    value: Annotated[str, StringConstraints(min_length=1, max_length=2000)] | None = None
    reason: Annotated[str, StringConstraints(max_length=500)] | None = None

    @model_validator(mode="after")
    def _fields_match_action(self) -> "CorrectionRequest":
        needs = {
            "retract_fact": ("fact_id",),
            "correct_fact": ("fact_id", "value"),
            "resolve_open_item": ("open_item_id",),
        }[self.action]
        missing = [name for name in needs if not getattr(self, name)]
        if missing:
            raise ValueError(f"{self.action} needs " + " and ".join(missing))
        return self


class FactRestoreRequest(Model):
    """Undoing a retraction or a correction of a fact, by a person with the security role: the fact comes
    back as it was and the correction that replaced it is retracted. It becomes a `feedback.restore_fact`
    event, so the lineage keeps it. An erased fact or one that came through an unconfirmed link is refused."""

    profile_id: UUID
    reason: Annotated[str, StringConstraints(max_length=500)] | None = None


MAX_CORRECTION_ITEMS = 100


class CorrectionBatchRequest(Model):
    """Several corrections in one request; item `n` takes the idempotency key `<Idempotency-Key>:<n>`."""

    items: list[CorrectionRequest] = Field(min_length=1, max_length=MAX_CORRECTION_ITEMS)


class ErasureOut(Model):
    request_id: str
    status: Literal["pending", "running", "completed", "failed"]
    target_kind: Literal["profile", "handle", "conversation"]
    target_id: str = Field(description="Profile id, handle id or conversation id; never the handle value.")
    requested_at: datetime
    completed_at: datetime | None = None
    erased: dict[str, int] = Field(default_factory=dict, description="Rows erased per table.")
    export_run_ids: list[str] = Field(
        default_factory=list, description="Continuous export runs that already carried erased rows (22.3)."
    )
    receipt_hash: str | None = Field(default=None, description="Hex SHA-256 of the erasure receipt (8.4).")
    retained: dict[str, int] = Field(
        default_factory=dict,
        description="What stayed, by class: turn records a legal hold keeps until its release "
        "(`held_turn_conversations`), and what a legal obligation keeps (`review_requests`, `legal_holds`).",
    )
    object_bound_rows_erased: int = Field(
        default=0,
        description="Of the rows erased, those about a business object: what the person said about a case, "
        "an order, an invoice, which went with them.",
    )
    objects: list[str] = Field(
        default_factory=list,
        description="The objects those rows were about, as `type:namespace:id`, at most 100: the company "
        "records again, in its own system, what it needs of them.",
    )


class ExportRequest(Model):
    """The portable package of one subject (5.14): JSON lines per kind of record, zipped."""

    profile_id: UUID | None = None
    handle: Handle | None = None

    @model_validator(mode="after")
    def _one_subject(self) -> "ExportRequest":
        if (self.profile_id is None) == (self.handle is None):
            raise ValueError("pass exactly one of `profile_id` or `handle`")
        return self


class ExportPackageOut(Model):
    run_id: str
    profile_id: str = Field(description="The subject, also when it was named by a handle.")
    download_url: str = Field(
        description="Presigned link to the zip in object storage; `GET /v1/exports/{run_id}/download` serves "
        "the same file through the cell."
    )
    download_expires_at: datetime
    files: dict[str, str] = Field(description="File name inside the zip -> SHA-256.")
    sha256: str = Field(description="SHA-256 of the zip.")


class ExportRunOut(Model):
    run_id: str
    schedule_id: str
    status: Literal["waiting", "running", "completed", "failed"]
    from_seq: int
    to_seq: int
    started_at: datetime
    finished_at: datetime | None = None
    error: str | None = None
    manifest: dict[str, Any] | None = None


class SecretIn(Model):
    """Write-only: the value goes to the cell vault and is never returned (4.7)."""

    value: SecretValue


class SecretWritten(Model):
    version: int
