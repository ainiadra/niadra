"""Governance reads of receipts and lineage (5.13, 7.1): `/v1/receipts` and `/v1/lineage/*`."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from niadra.contracts.common import IdStr, Model
from niadra.domain.vocabulary import Audience, EventKind, ReceiptKind, ReceiptSurface


class ReceiptOut(Model):
    receipt_id: str
    kind: ReceiptKind
    surface: ReceiptSurface
    audience: Audience
    read_at: datetime
    profile_id: str | None = None
    pseudonym: str | None = Field(
        default=None,
        description="The profile's pseudonym, the name to show a person in place of `profile_id`; it is "
        "not part of the chained receipt.",
    )
    source_id: str | None = None
    person_id: str | None = None
    person_label: str | None = Field(
        default=None,
        description="The e-mail of the person who acted, the name to show in place of `person_id`; it is "
        "not part of the chained receipt, and is null for a person the space has not seen act since it "
        "started keeping names.",
    )
    conversation_id: str | None = None
    task_id: str | None = None
    object_id: str | None = None
    about_handle_id: str | None = None
    purpose: str | None = None
    item_ids: list[str] = Field(default_factory=list)
    pack_hash: str | None = None
    manifest_hash: str | None = None
    verification: str | None = None
    policy_version: str | None = None
    experiment_group: str | None = None
    withheld: int = 0
    detail: dict[str, str] = Field(
        default_factory=dict,
        description="Why a `denied` read was refused (`reason`), or the action an `admin` receipt records "
        "(`action`, `target`).",
    )
    slice: int
    day: date
    seq: int
    prev_hash: str = Field(description="Hex SHA-256 of the previous link of this chain.")
    hash: str = Field(description="Hex SHA-256 over the previous hash and this receipt's canonical encoding.")


class LineageEvent(Model):
    """Where an item came from; metadata only, the content stays behind the history routes."""

    event_id: str
    kind: EventKind
    source_id: str
    channel: str
    occurred_at: datetime
    conversation_id: str | None = None
    task_id: str | None = None


class LineageItem(Model):
    id: str
    kind: str | None = None
    evidence_event_ids: list[str] = Field(default_factory=list)
    events: list[LineageEvent] = Field(default_factory=list)


class ReceiptLineage(Model):
    """Answers "why did the agent say X?": receipt -> manifest -> items -> origin events (5.13)."""

    receipt: ReceiptOut
    manifest_found: bool
    items: list[LineageItem] = Field(default_factory=list)
    excluded: list[dict[str, Any]] = Field(
        default_factory=list, description="What the manifest deliberately left out, with the reason (2.3)."
    )


class FactViewers(Model):
    """Answers "who saw fact F?": every receipt that delivered it."""

    fact_id: str
    receipts: list[ReceiptOut]


class ChainHeadOut(Model):
    slice: int
    seq: int
    hash: str


class ChainBreakOut(Model):
    slice: int
    seq: int
    reason: str


class ChainVerification(Model):
    day: date
    valid: bool
    receipts: int
    heads: list[ChainHeadOut]
    breaks: list[ChainBreakOut] = Field(default_factory=list)
    anchored_root: str | None = Field(
        default=None, description="Hex Merkle root written by the daily anchor."
    )
    root_matches: bool | None = Field(default=None, description="None while the day is not anchored yet.")
    anchor_copy_matches: bool | None = Field(
        default=None,
        description="Whether the copy of the root kept outside the database holds the recomputed root; "
        "false when it is missing or differs, null while the day is not anchored or the copy cannot be read.",
    )
    anchor_locked: bool | None = Field(
        default=None,
        description="Whether that copy sits in write-once storage (Object Lock in compliance mode) that "
        "nobody, the cell included, can delete or replace before its retention ends.",
    )


class ReceiptSearch(Model):
    """The filters of `GET /v1/receipts`, in a body: a conversation id never travels in a URL (7.0)."""

    profile_id: UUID | None = None
    source_id: UUID | None = None
    conversation_id: IdStr | None = None
    kind: ReceiptKind | None = None
    since: datetime | None = None
    until: datetime | None = None
    cursor: UUID | None = None
    limit: int = Field(default=50, ge=1, le=200)
