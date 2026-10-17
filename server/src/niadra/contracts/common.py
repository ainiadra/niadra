"""Shared building blocks of the public contract.

Personal data (phones, e-mails, documents, free text) is only ever accepted in request
bodies, never in paths or query strings (7.0).
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from niadra.domain.vocabulary import HandleType, SubjectKind

ShortStr = Annotated[str, StringConstraints(min_length=1, max_length=256)]
IdStr = Annotated[str, StringConstraints(min_length=1, max_length=512)]


class Model(BaseModel):
    """Base for every contract model: unknown fields are rejected, so typos fail loudly."""

    # A model's validator and serializer are built on its first use, not at import: every deployable imports
    # every contract, and each process uses only its own routes' (all built, several MiB of each pod).
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True, defer_build=True)


class Handle(Model):
    """An identifier of a subject in some channel or system: a phone, an e-mail, a CRM id."""

    type: HandleType
    value: Annotated[str, StringConstraints(min_length=1, max_length=320)]
    scope: ShortStr | None = Field(
        default=None,
        description="Namespace for scoped identifiers: the WhatsApp Business account for `wa_bsuid`,"
        " the system for `system_id`, the country for `gov_id_hmac`.",
    )
    subject_kind: SubjectKind | None = Field(
        default=None, description="Defaults to `person`, except for organization-only handle types."
    )


class ObjectRef(Model):
    """A business object in a system of record, e.g. `invoice` / `erp` / `0823`."""

    type: ShortStr
    namespace: ShortStr
    id: IdStr


class Subject(Model):
    kind: SubjectKind
    role: ShortStr | None = None
    handles: list[Handle] = Field(min_length=1, max_length=16)


class Cursor(Model):
    cursor: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


class Page[T](Model):
    items: list[T]
    next_cursor: str | None = None


class SourceCoverage(Model):
    source_id: str
    status: str = Field(description="`ok` or `silent` when the source stopped sending (5.2).")
    last_event_at: datetime | None = None


class Problem(Model):
    """RFC 9457 problem details; `code` comes from the versioned error catalog."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    code: str
    request_id: str | None = None
