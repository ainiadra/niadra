"""A customer's memory as the Console shows it (5.22): `GET /v1/profiles/{profile_id}/memory`.

Every item says where it came from. Content passes the same policy as an agent read, with the
audience class of the reader's role: masked items keep their shape and lose their content, denied
items are only counted in `withheld`, by reason.
"""

from datetime import datetime
from typing import Literal

from pydantic import Field

from niadra.contracts.common import Model, ObjectRef
from niadra.domain.vocabulary import Audience, EventKind, FactStatus, OpenItemStatus, Sensitivity, Speaker


class Origin(Model):
    """Provenance of an item: the event it came from, its source (agent or system), channel and time."""

    event_id: str | None = None
    kind: EventKind | None = None
    source_id: str | None = None
    channel: str | None = None
    at: datetime | None = None
    speaker: Speaker | None = None
    conversation_id: str | None = None
    task_id: str | None = None


class FactContradictionOut(Model):
    fact_id: str = Field(description="The other fact of the pair.")
    origin: Literal["model", "rule", "band"] = Field(
        description="`model` when the extraction marked the conflict; `band` when the nightly comparison of "
        "the facts of one predicate found it, without a model. A `band` pair is a warning for review: it "
        "never changes a status, and each fact weighs less in the context while the pair is live."
    )
    live: bool = Field(
        description="Both facts are still active. Retracting or correcting either one closes the pair."
    )


class FactOut(Model):
    id: str
    predicate: str
    value: str | None = Field(description="None when the policy masks it for this reader.")
    masked: bool = False
    category: str
    sensitivity: Sensitivity
    status: FactStatus
    confidence: float
    valid_at: datetime
    invalid_at: datetime | None = None
    last_confirmed_at: datetime
    times_seen: int
    sessions_seen: int = Field(
        description="Distinct conversations or tasks whose turns support the value, which is what confirms "
        "it; `times_seen` counts every new piece of evidence.",
    )
    contradicts: list[FactContradictionOut] = Field(
        default_factory=list, description="Facts in conflict with this one, with who found the conflict."
    )
    about_handle_id: str | None = None
    object: ObjectRef | None = None
    evidence_event_ids: list[str] = Field(default_factory=list)
    origin: Origin | None = Field(default=None, description="The earliest evidence event.")
    unconfirmed_link: bool = Field(
        default=False,
        description="It came through an identifier joined to the profile by a claim nobody confirmed yet "
        "(`HandleOut.provisional`). Only the `admin` and `security` roles see such items; every other "
        "reader, agents included, gets the profile without them until the link is confirmed.",
    )


class OpenItemOut(Model):
    id: str
    description: str | None
    masked: bool = False
    owner: Literal["company", "customer"]
    promise: bool = Field(description="An open item the company owns: a promise made to the customer.")
    status: OpenItemStatus
    due_at: datetime | None = None
    created_at: datetime
    object: ObjectRef | None = None
    expected_operation: str | None = None
    closed_by_action_id: str | None = None
    closed_at: datetime | None = None
    origin: Origin | None = None
    unconfirmed_link: bool = Field(
        default=False,
        description="It came through an identifier joined to the profile by a claim nobody confirmed yet "
        "(`HandleOut.provisional`). Only the `admin` and `security` roles see such items; every other "
        "reader, agents included, gets the profile without them until the link is confirmed.",
    )


class EpisodeOut(Model):
    id: str
    version: int
    summary: str | None
    masked: bool = False
    intent: str
    category: str
    normalized_category: str
    outcome: str
    resolution: str | None = None
    sentiment: float | None = None
    session_id: str
    started_at: datetime
    ended_at: datetime
    origin: Origin
    unconfirmed_link: bool = Field(
        default=False,
        description="It came through an identifier joined to the profile by a claim nobody confirmed yet "
        "(`HandleOut.provisional`). Only the `admin` and `security` roles see such items; every other "
        "reader, agents included, gets the profile without them until the link is confirmed.",
    )


class TraitEvidenceOut(Model):
    kind: str = Field(description="`episode`, `event`, `action` or `open_item`.")
    id: str
    origin: Origin | None = None


class TraitOut(Model):
    id: str
    name: str
    value: str | None
    masked: bool = False
    layer: str
    rule_version: str
    first_seen: datetime
    last_evidence_at: datetime
    expires_at: datetime
    confidence: float
    active: bool = Field(description="Neither retracted nor expired.")
    retracted_at: datetime | None = None
    evidence: list[TraitEvidenceOut] = Field(default_factory=list)
    unconfirmed_link: bool = Field(
        default=False,
        description="It came through an identifier joined to the profile by a claim nobody confirmed yet "
        "(`HandleOut.provisional`). Only the `admin` and `security` roles see such items; every other "
        "reader, agents included, gets the profile without them until the link is confirmed.",
    )


class ProfileMemory(Model):
    profile_id: str
    policy_version: str
    audience: Audience = Field(description="The audience class the reader's role reads as.")
    facts: list[FactOut] = Field(default_factory=list)
    open_items: list[OpenItemOut] = Field(default_factory=list)
    timeline: list[EpisodeOut] = Field(default_factory=list, description="Episodes, newest first.")
    timeline_next: str | None = Field(
        default=None,
        description="Where the timeline's next page starts (`timeline_cursor`); null when it holds no more.",
    )
    traits: list[TraitOut] = Field(default_factory=list)
    withheld: dict[str, int] = Field(default_factory=dict, description="Items left out, by reason.")


class FactRelationOut(Model):
    from_fact_id: str = Field(description="The newer fact.")
    to_fact_id: str
    type: str = Field(description="`supersedes` when a value replaced another, `contradicts`, ...")
    origin: Literal["model", "rule", "band"] = Field(
        default="model",
        description="Who drew the edge: the extraction's `model`, a deterministic `rule` (precedence, a "
        "correction) or the nightly similarity `band`, whose edges only warn.",
    )


class FactHistory(Model):
    """`GET /v1/profiles/{profile_id}/facts/{fact_id}/history`: every value one slot held (the predicate,
    on the same handle and object), oldest first, with the edges between them, under the reader's policy."""

    profile_id: str
    fact_id: str
    predicate: str
    policy_version: str
    versions: list[FactOut] = Field(default_factory=list)
    relations: list[FactRelationOut] = Field(default_factory=list)
    withheld: dict[str, int] = Field(default_factory=dict, description="Versions left out, by reason.")
