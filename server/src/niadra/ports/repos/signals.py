"""The `signals` module's persistence (6.7): `subject_signals`, `beneficiaries`, `signal_intake` and
`inference_suppressions`. Bodies, labels and pending interactions arrive sealed; a repository never sees their
content."""

from collections.abc import Collection, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from niadra.domain.privacy.erasure import ErasedRow, ErasureScope
from niadra.domain.signals.daily import Prior
from niadra.domain.signals.records import (
    BeneficiaryRow,
    DueSnapshots,
    PendingRow,
    Segment,
    SignalRow,
    SignalWrite,
    Snapshot,
    SuppressionRow,
)


class SignalRepository(Protocol):
    async def add_pending(self, rows: Sequence[PendingRow]) -> None:
        """One statement; a row already there stays as it is, so applying the same turns twice adds once."""
        ...

    async def due_snapshots(
        self, now: datetime, *, window: timedelta, quiet: timedelta, limit: int
    ) -> DueSnapshots:
        """In one statement, the subjects whose pending interactions are ready (the oldest waited `window`,
        or the newest is `quiet` old: the session went quiet), each with its rows, pending interactions and
        deleted inferences, and whether interactions without a subject are due to be checked again
        (`recheck_due`)."""
        ...

    async def unresolved(self, now: datetime, limit: int) -> list[PendingRow]:
        """Pending rows whose conversation had no known subject yet and are due to be checked again
        (`recheck_due`), oldest first."""
        ...

    async def resolve(self, found: Mapping[str, UUID]) -> None:
        """Gives pending rows, by entry key, the handle their conversation now has."""
        ...

    async def checked(self, entry_keys: Sequence[str], at: datetime) -> None:
        """Pending rows still without a subject, looked at `at`: kept, and looked at again later."""
        ...

    async def drop_unattributed(self, sessions: Sequence[str]) -> list[ErasedRow]:
        """For an erasure, before the conversations' rows go: the rows still without a subject of these
        conversations (by keyed digest)."""
        ...

    async def put_segment(self, segment: Segment, at: datetime) -> None:
        """Indexes an object of measurement data, one per kind and start: a rerun replaces it."""
        ...

    async def segments(self, kind: str, since: datetime) -> list[Segment]:
        """The kind's objects starting at or after `since`, oldest first."""
        ...

    async def rewrite_segment(self, segment: Segment, at: datetime) -> None:
        """The same object written again without an erased subject: its size, rows and digest move."""
        ...

    async def expire_segments(self, now: datetime) -> list[str]:
        """Removes the index rows past their `expires_at` and returns their object keys."""
        ...

    async def replace_priors(self, priors: Sequence[Prior], at: datetime) -> None:
        """The priors of every segment become `priors`, in one statement."""
        ...

    async def priors(self) -> list[Prior]:
        """Every segment's."""
        ...

    async def intake_page(self, after: str | None, limit: int) -> list[PendingRow]:
        """Every pending interaction, whether its subject is known or not, by entry key after `after`."""
        ...

    async def pending_by_keys(self, entry_keys: Sequence[str]) -> list[PendingRow]: ...

    async def rewrite_pending(self, rows: Sequence[PendingRow]) -> None:
        """Each row's sealed interaction in place: an erasure redacts someone named in it."""
        ...

    async def pending_page(self, after: str | None, limit: int) -> list[PendingRow]:
        """Pending interactions that name their subject, by entry key after `after`: a rebuild's page."""
        ...

    async def pending_of(self, handle_ids: Sequence[UUID]) -> list[PendingRow]:
        """The interactions of these handles still waiting for their derivation, in one statement."""
        ...

    async def snapshot(self, handle_id: UUID) -> Snapshot:
        """The handle's rows and its pending interactions, in one statement."""
        ...

    async def store(self, write: SignalWrite) -> bool:
        """`store_many` of one subject."""
        ...

    async def store_many(self, writes: Sequence[SignalWrite]) -> set[UUID]:
        """Bodies, beneficiaries and the removal of the folded pending rows of every subject, in one
        statement. Returns the subjects written: one who objected to personalization meanwhile gets only
        the removal."""
        ...

    async def rows_for(self, handle_ids: Sequence[UUID]) -> list[SignalRow]:
        """The rows of these handles, in one statement: the read that fills the cache."""
        ...

    async def rows_page(self, after: tuple[UUID, UUID] | None, limit: int) -> list[SignalRow]:
        """Every subject's rows by handle and beneficiary: what the index of mentions reads once."""
        ...

    async def withdraw(self, handle_ids: Sequence[UUID], markers: Sequence[SignalRow]) -> list[ErasedRow]:
        """Removes every row, beneficiary and pending interaction of the handles and leaves the opted-out
        markers, so nothing is derived for them again."""
        ...

    async def inactive(
        self,
        before: datetime,
        *,
        limit: int,
        keep: Collection[UUID] = (),
        after: tuple[UUID, UUID] | None = None,
    ) -> list[SignalRow]:
        """Rows of subjects inactive since `before` (13 months, 24.5.1) and not decayed since their last
        activity, by key after `after`; never a subject who objected, nor the handles in `keep` (a legal
        hold)."""
        ...

    async def decay(self, rows: Sequence[SignalRow], at: datetime) -> int:
        """Rewrites each row's body with what `inactive` read it for, stamped `decayed_at`, in one statement;
        a row derived again since that read stays as it is. Returns the rows rewritten."""
        ...

    async def drop_untold(self, rows: Sequence[SignalRow]) -> list[ErasedRow]:
        """Deletes the subjects' own rows that hold nothing the person said or decided, in one statement; a
        row derived again since it was read stays."""
        ...

    async def add_suppressions(self, rows: Sequence[SuppressionRow]) -> None:
        """One statement; the same inference deleted again moves its `evidence_until` forward."""
        ...

    async def suppressions_for(self, handle_ids: Sequence[UUID]) -> list[SuppressionRow]: ...

    async def signals_for(self, handle_ids: Sequence[UUID]) -> list[SignalRow]: ...

    async def beneficiaries_for(self, handle_ids: Sequence[UUID]) -> list[BeneficiaryRow]: ...

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        """A profile's or a handle's rows, beneficiaries, pending interactions and inference tombstones; for a
        conversation, the pending interactions said in it (what a body folded is rewritten by the service)."""
        ...
