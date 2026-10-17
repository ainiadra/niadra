"""The durable record of coordination (24.3.4): written in batches from the confirmed queue, and read to
reload the state store after it came back empty, for the suppression list's changes, the handoffs and the
shadow reports; kept per class for its retention and erased with the subject."""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from niadra.domain.coordinate.model import EffectSnapshot
from niadra.domain.coordinate.records import OwnershipRecord, SpaceBatch
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope


@dataclass(frozen=True, slots=True)
class SuppressionRow:
    suppression_id: UUID
    handle_hmac: bytes
    handle_ct: bytes
    dek_version: int
    purpose: str
    channel: str | None
    source_id: UUID | None
    since: datetime
    until: datetime | None
    lifted_at: datetime | None
    change_seq: int
    window: str | None = None
    """The local hours it holds in (`Window.encode`), or None for every hour."""


@dataclass(frozen=True, slots=True)
class SpendRow:
    spend_id: UUID
    budget: str
    slice: str | None
    units: int
    spent_at: datetime
    until: datetime


@dataclass(frozen=True, slots=True)
class CommitmentRow:
    external_id: str
    commitment_id: UUID
    type: str
    compatibility: str
    valid_until: datetime
    score: float | None
    agent: str | None
    made_at: datetime


@dataclass(frozen=True, slots=True)
class HandoffRow:
    """A handoff with its package and its subject sealed; `subject_hmac` names the subject as coordination
    keys it."""

    handoff_id: UUID
    subject_hmac: bytes | None
    subject_ct: bytes | None
    source_id: UUID
    agent: str | None
    target: str
    package_ct: bytes
    package_sha256: bytes
    dek_version: int
    status: str
    outcome: str | None
    created_at: datetime
    expected_by: datetime | None
    accepted_at: datetime | None = None
    closed_at: datetime | None = None
    named: frozenset[bytes] = frozenset()
    """What its package names a person by (`domain.privacy.mentions`), keyed-hashed: indexed with it."""


@dataclass(frozen=True, slots=True)
class SealedTerms:
    """A commitment's terms as stored, for an erasure to open and redact."""

    commitment_id: UUID
    subject_hmac: bytes | None
    terms_ct: bytes
    dek_version: int


@dataclass(frozen=True, slots=True)
class SealedOutcome:
    """A handoff's outcome detail as stored, under its own key version."""

    outcome_ct: bytes
    dek_version: int


@dataclass(frozen=True, slots=True)
class ReportRow:
    report_id: UUID
    status: str
    days: int
    requested_at: datetime
    finished_at: datetime | None
    summary: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Overview:
    """What coordination holds and decided, in counts: each tuple is its grouping columns, then counts."""

    ownership: list[tuple[str, str, str, str, int, int]]
    """(kind, level, via, holder kind, active claims, distinct holders), the claims that hold now."""
    effects: list[tuple[str, str, int]]
    """(kind, state, count) of the effects reserved in the window."""
    awaiting_decision: int
    """Effects whose outcome nobody knows, at any age: a person or a system event must settle them."""
    overlaps: int
    """Subjects and objects two holders claimed at the same time in the window."""
    commitments_superseded: int
    handoffs_expired: int
    tokens_reused: int
    budgets: list[tuple[str, int, int, int]]
    """(budget, subjects with a live spend, of them at the limit, units spent), for the budgets asked."""
    contacts: list[tuple[str, str, str, int]]
    """(purpose, direction, state, count) of the contacts in the window."""


@dataclass(frozen=True, slots=True)
class TargetState:
    """What the durable record holds of one subject, object or space, live at `now`: its claims (not
    released, not expired), a subject's suppressions (not lifted, not lapsed) and commitments that hold, and
    the budget spends still in their window. `target` is the subject's keyed hash, the object as
    `<type>\\t<namespace>\\t<id>` encoded, or empty for the space; `kind` says which."""

    target: bytes
    kind: str
    claims: tuple[OwnershipRecord, ...]
    suppressions: tuple[SuppressionRow, ...]
    spends: tuple[SpendRow, ...] = ()
    commitments: tuple[CommitmentRow, ...] = ()

    @property
    def is_object(self) -> bool:
        return self.kind == "object"


class CoordinateRepository(Protocol):
    """Owned by the `coordinate` module; no other module reads or writes its tables."""

    async def write(self, batch: SpaceBatch) -> None:
        """One statement for the whole batch: an effect keeps its latest state, a release or a lift only ends
        what it names."""
        ...

    async def effect(self, key_hash: bytes) -> EffectSnapshot | None: ...

    async def target(self, target: bytes, *, kind: str, now: datetime) -> TargetState: ...

    async def live_effects(
        self, now: datetime, after: bytes | None, limit: int
    ) -> list[tuple[bytes, EffectSnapshot]]:
        """Effects whose key still holds, by key hash, for a reload of the whole space."""
        ...

    async def live_targets(
        self, now: datetime, after: tuple[str, bytes] | None, limit: int
    ) -> list[TargetState]:
        """Every target with a live claim, suppression or budget spend, by kind and target, for a reload of
        the whole space."""
        ...

    async def suppression_changes(
        self, after_seq: int | None, limit: int, now: datetime
    ) -> Sequence[SuppressionRow]:
        """Without `after_seq`, the entries in force; with it, every entry changed since, lifted ones too.
        Oldest change first."""
        ...

    async def subject_effects(
        self, subject_hmac: bytes, now: datetime, limit: int
    ) -> list[tuple[bytes, EffectSnapshot]]:
        """The effects checks reserved for the subject whose keys still hold, by key hash, latest first."""
        ...

    async def add_handoff(self, handoff: HandoffRow) -> None: ...

    async def handoff(self, handoff_id: UUID) -> HandoffRow | None: ...

    async def move_handoff(
        self,
        handoff_id: UUID,
        *,
        status: str,
        from_statuses: Sequence[str],
        at: datetime,
        outcome: str | None = None,
        outcome_ct: SealedOutcome | None = None,
        named: frozenset[bytes] = frozenset(),
    ) -> HandoffRow | None:
        """The handoff after the move, when it was in one of `from_statuses`; None otherwise. `named` is what
        the outcome names a person by, indexed in the same statement."""
        ...

    async def terms_of(self, commitment_ids: Sequence[UUID]) -> list[SealedTerms]: ...

    async def terms_page(self, after: UUID | None, limit: int) -> list[SealedTerms]:
        """The space's commitments by id after `after`."""
        ...

    async def rewrite_terms(self, terms: SealedTerms) -> None:
        """A commitment's terms in place: an erasure redacts someone named in them."""
        ...

    async def outcome_of(self, handoff_id: UUID) -> SealedOutcome | None: ...

    async def handoffs_page(self, after: UUID | None, limit: int) -> list[HandoffRow]:
        """The space's handoffs by id after `after`."""
        ...

    async def rewrite_handoff(self, handoff: HandoffRow, outcome: SealedOutcome | None) -> None:
        """A handoff's package, subject and outcome in place, sealed again: an erasure redacts someone named
        in them."""
        ...

    async def add_report(self, report_id: UUID, days: int, requested_at: datetime) -> None: ...

    async def finish_report(
        self,
        report_id: UUID,
        *,
        status: str,
        summary: dict[str, Any],
        sample_ct: bytes | None,
        dek_version: int | None,
        at: datetime,
    ) -> None: ...

    async def report(self, report_id: UUID) -> ReportRow | None: ...

    async def reports(self, before: UUID | None, limit: int) -> list[ReportRow]:
        """The shadow reports, newest first."""
        ...

    async def overview(self, since: datetime, now: datetime, limits: dict[str, int]) -> Overview:
        """The Console's picture of coordination (admin, off the hot path): `limits` names each contact
        budget and its limit, to count the subjects at it."""
        ...

    async def tokens_used_twice(self, since: datetime) -> int:
        """Contacts since `since` whose token was declared used more than once."""
        ...

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        """The subject's claims, contacts, budget spends, effects, commitments and handoffs, found by the
        keyed hashes of its handles (`scope.subject_hashes`). Suppressions stay: they prove the person asked
        not to be contacted."""
        ...

    async def drop_expired_contact_weeks(self, now: datetime, held_subjects: Collection[bytes] = ()) -> int:
        """The weeks of the contact log past retention, dropped whole; a week with a held subject's contact,
        or one whose lock is not granted at once, stays for `expire`. Returns how many weeks went."""
        ...

    async def expire(
        self,
        now: datetime,
        *,
        limit: int,
        held_subjects: Collection[bytes] = (),
        held_handles: Collection[UUID] = (),
    ) -> dict[str, int]:
        """One pass of the retention of each class (24.5.1), at most `limit` rows each: claims 30 days after
        they ended, contacts after 90 days, spends when their window ends, effects when their key does,
        commitments 180 days after they ended, handoffs after 180 days (and those past their outcome's
        deadline become `expired`), suppressions 395 days after they ended, and shadow reports after 13
        months. What names a held subject or handle (a legal hold) stays."""
        ...
