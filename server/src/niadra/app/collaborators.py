"""Calls one service makes into another, declared here so both sides agree on the shape.

The composition root passes the real service; tests pass a fake.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from niadra.contracts.common import Handle
from niadra.domain.identity.handles import HandleKey
from niadra.domain.vocabulary import AssertionMethod, SubjectKind, Verification
from niadra.ports.uow import UnitOfWork


@dataclass(frozen=True, slots=True)
class PendingMerge:
    """A merge an attach wrote down: the pending assertions, the `identity_apply` op that carries them and
    the profiles whose lease it needs (5.3)."""

    op_id: UUID
    assertion_ids: tuple[UUID, ...]
    profile_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class AttachResult:
    handle_ids: tuple[UUID, ...]
    """One id per distinct input handle, in input order, after normalization."""
    profile_id: UUID | None
    """The profile the event belongs to now (the anchor rule decides with 2+ candidates, 5.8); None when
    the event names nobody but blocked values, which never gather memory (5.3)."""
    pending_merge: bool
    """True when strong handles resolved to 2+ profiles: a merge is due, written by this attach or already
    queued by an earlier one (5.3)."""
    anchor_handle_id: UUID | None = None
    """The handle that stands for the subject when one of its handles may assert identity (5.8)."""
    evidence: Mapping[HandleKey, UUID] = field(default_factory=dict)
    """The attach's handles, the given ones and those they derive (a WhatsApp id's phone), that are strong
    and active once it ran: what identity lets stand for the subject (5.3)."""
    merge: PendingMerge | None = field(default=None, compare=False)
    """The merge this attach wrote, when it wrote one; an attach that finds it already written answers
    the same without it."""


class IdentityAttacher(Protocol):
    async def attach(
        self,
        uow: UnitOfWork,
        handles: Sequence[Handle],
        *,
        subject_kind: SubjectKind,
        method: AssertionMethod,
        origin_event_id: UUID | None,
        level: Verification,
        at: datetime,
        merge_after_commit: bool = False,
    ) -> AttachResult:
        """The synchronous path of ingestion: create unknown handles, attach them when they resolve to at
        most one profile, never merge inside the transaction (5.3). With `merge_after_commit`, the caller
        runs the merge itself once the transaction commits (`merge_now`), and the `identity_apply` task is
        only a safety net a few seconds later; otherwise the task is queued for the workers."""
        ...

    async def merge_now(self, space_id: UUID, merge: PendingMerge) -> bool:
        """Runs a merge an attach wrote, as `identity_apply` would, under the lease of its profiles. False
        when the lease stayed busy or the merge must be retried: the task left as a safety net runs it."""
        ...

    async def link_subjects(
        self,
        uow: UnitOfWork,
        person_handle_id: UUID,
        org_handle_ids: Sequence[UUID],
        *,
        origin_event_id: UUID | None,
        at: datetime,
    ) -> None:
        """A person and the organizations the same event names become linked (21.2)."""
        ...


class IntakeSettler(Protocol):
    async def settle(self, space_id: UUID) -> None:
        """Stores every batch of the space acknowledged so far (5.2)."""
        ...
