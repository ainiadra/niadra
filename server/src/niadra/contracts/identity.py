"""Identity resolution as a first-class API (2.5, 7.1): assertions, merges, unmerges, blocks, links,
profiles.

Handles travel only in request bodies; paths and query strings carry ids (7.0).
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from niadra.contracts.common import Handle, Model, ShortStr
from niadra.domain.vocabulary import AssertionMethod, HandleStatus, HandleType, SubjectKind, Verification

Reason = Annotated[str, StringConstraints(min_length=1, max_length=500)]
AssertionStateName = Literal["applied", "pending", "rejected"]
LinkMethod = Literal["system_import", "co_occurrence", "declared", "login"]


class AssertionIn(Model):
    handles: list[Handle] = Field(min_length=2, max_length=2)
    method: AssertionMethod = AssertionMethod.EXPLICIT_IDENTIFY
    subject_kind: SubjectKind = SubjectKind.PERSON
    reason: Reason | None = None


class AssertionOut(Model):
    assertion_id: UUID
    handle_a: UUID
    handle_b: UUID
    method: AssertionMethod
    state: AssertionStateName
    confidence: float
    level_at_capture: Verification
    created_at: datetime
    origin_event_id: UUID | None = None
    actor: str | None = None
    reason: str | None = None
    retracted_at: datetime | None = None


class RetractIn(Model):
    reason: Reason | None = None


class AcceptIn(Model):
    reason: Reason | None = None


class MergeIn(Model):
    profile_ids: list[UUID] = Field(min_length=2, max_length=2)
    force: bool = Field(
        default=False, description="Overrides the verified-profiles and organization protections (5.3)."
    )
    reason: Reason | None = None

    @model_validator(mode="after")
    def _force_needs_reason(self) -> "MergeIn":
        if self.force and not self.reason:
            raise ValueError("a forced merge needs a reason")
        return self


class UnmergeIn(Model):
    profile_id: UUID
    handle_ids: list[UUID] = Field(default_factory=list, max_length=200)
    assertion_ids: list[UUID] = Field(default_factory=list, max_length=200)
    reason: Reason | None = None

    @model_validator(mode="after")
    def _one_way(self) -> "UnmergeIn":
        if bool(self.handle_ids) == bool(self.assertion_ids):
            raise ValueError("an unmerge takes either handle_ids or assertion_ids")
        return self


class BlockIn(Model):
    handle: Handle
    reason: Reason | None = None


class OperationOut(Model):
    op_id: UUID
    status: Literal["queued", "applied", "linked", "blocked"]
    assertion_id: UUID | None = None
    link_id: UUID | None = None


class LinkIn(Model):
    person: Handle
    organization: Handle
    role: ShortStr = Field(description="buyer, driver, broker, technical_contact, owner...")
    can_see_contacts: bool = False
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    method: LinkMethod = "declared"


class LinkEndIn(Model):
    valid_to: datetime | None = None


class LinkOut(Model):
    link_id: UUID
    person_handle_id: UUID
    org_handle_id: UUID
    role: str
    can_see_contacts: bool
    valid_from: datetime
    valid_to: datetime | None = None


class HandleOut(Model):
    handle_id: UUID
    type: HandleType
    scope: str
    subject_kind: SubjectKind
    value: str = Field(description="Masked by the caller's role.")
    status: HandleStatus
    level: Verification
    weight: Literal["hint", "declared", "verified", "authenticated", "system"] = Field(
        default="declared",
        description="How much the identifier weighs: a hint, declared, verified (V2 or V3), authenticated "
        "(V4), or from a system of record.",
    )
    first_seen: datetime
    last_seen: datetime
    provisional: bool = Field(
        default=False,
        description="Joined to the profile by a claim (`declared`, `explicit_identify`) that the company's "
        "systems, a login or a person have not confirmed: reads through it stay at V1 (5.3).",
    )


class ProfileOut(Model):
    profile_id: UUID
    kind: SubjectKind
    pseudonym: str
    display_name: str | None = Field(
        default=None,
        description="The name to show the customer by: a company's as its own record sent it, to every role; "
        "a person's as their memory holds it, only to a role that reads customer memory and whose policy "
        "lets that item through. None when no name is known or the role may not see it.",
    )
    display_contact: str | None = Field(
        default=None,
        description="A phone (`+55 11 98117-2201`) or, without one, an e-mail to show the customer by when "
        "there is no name: in full for the roles that see contacts, masked for the others "
        "(`+55 ** *****-2201`). None when the customer has neither.",
    )
    membership_version: int
    privacy_version: int
    partial: bool = Field(description="Known only by channel-private ids such as a BSUID (5.3).")
    handles: list[HandleOut]
    links: list[LinkOut]


class SuggestionOut(Model):
    """An offline candidate pair for review (5.3); never applied without a person. Each side comes with
    its profile and its identifier, masked by the caller's role like `GET /v1/profiles/{id}`."""

    suggestion_id: UUID
    handle_a: UUID
    handle_b: UUID
    profile_a: UUID | None = Field(description="None when the handle no longer exists.")
    profile_b: UUID | None
    identifier_a: HandleOut | None
    identifier_b: HandleOut | None
    score: float
    features: dict[str, Any]
    status: Literal["open", "accepted", "rejected"]
    created_at: datetime


class ProfileSearchIn(Model):
    """The value travels in the body (7.0). A pseudonym (`p_...`), an identifier or a name: phones, e-mails,
    WhatsApp ids and CPFs are recognized by format; opaque ids need `type` (and `scope` when scoped). Any
    other value is a name, searched exactly (case, accents and spacing aside) for a role that reads customer
    memory, and refused for the others."""

    query: Annotated[str, StringConstraints(min_length=1, max_length=320)]
    type: HandleType | None = None
    scope: ShortStr | None = None
    limit: int = Field(default=20, ge=1, le=50)


class ProfileMatch(Model):
    profile_id: UUID
    pseudonym: str
    kind: SubjectKind
    display_name: str | None = Field(
        default=None,
        description="The name to show the customer by: a company's as its own record sent it, to every role; "
        "a person's as their memory holds it, only to a role that reads customer memory and whose policy "
        "lets that item through. None when no name is known or the role may not see it.",
    )
    display_contact: str | None = Field(
        default=None,
        description="A phone (`+55 11 98117-2201`) or, without one, an e-mail to show the customer by when "
        "there is no name: in full for the roles that see contacts, masked for the others "
        "(`+55 ** *****-2201`). None when the customer has neither.",
    )
    partial: bool
    handles: list[HandleOut] = Field(description="Masked by the caller's role.")
    last_seen: datetime | None = Field(description="The latest activity on any of the profile's handles.")


class ProfileSearchOut(Model):
    items: list[ProfileMatch]


class ListedHandle(Model):
    type: HandleType
    value: str = Field(
        description="Always masked, whatever the caller's role: a contact by its ends, a document by its "
        "last two characters, an opaque id by its two first and two last."
    )
    last_seen: datetime


class ProfileListed(Model):
    """A row of the customer list: enough to recognize a customer and open their memory. `handles` never
    holds an identifier in full; `display_contact` is in full only for the roles that see contacts."""

    profile_id: UUID
    pseudonym: str
    kind: SubjectKind
    display_name: str | None = Field(
        default=None,
        description="The name to show the customer by: a company's as its own record sent it, to every role; "
        "a person's as their memory holds it, only to a role that reads customer memory and whose policy "
        "lets that item through. None when no name is known or the role may not see it.",
    )
    display_contact: str | None = Field(
        default=None,
        description="A phone (`+55 11 98117-2201`) or, without one, an e-mail to show the customer by when "
        "there is no name: in full for the roles that see contacts, masked for the others "
        "(`+55 ** *****-2201`). None when the customer has neither.",
    )
    handles: list[ListedHandle] = Field(description="The three with the latest activity, the latest first.")
    handle_count: int = Field(description="How many identifiers the profile has.")
    last_seen: datetime = Field(description="The latest activity on any of the profile's identifiers.")
