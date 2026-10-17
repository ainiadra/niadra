"""Handles, assertions, membership, profiles, links, merge log, blocked values, suggestions (5.3)."""

from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from niadra.domain.identity.handles import HandleKey, NewHandle
from niadra.domain.identity.listing import ListCursor, ListedProfile
from niadra.domain.identity.model import AssertionRecord, AssertionState, MergeLogEntry, SuggestionRecord
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope
from niadra.domain.records import HandleRecord, LinkRecord, ProfileRecord
from niadra.domain.vocabulary import AssertionMethod, HandleStatus, HandleType, SubjectKind, Verification


class IdentityRepository(Protocol):
    """Owned by the `identity` module. The first five methods are read by other modules; the rest
    are the module's own. Handle values arrive and leave in clear: the adapter hashes and seals them."""

    async def find_handle(self, type: HandleType, scope: str, value: str) -> HandleRecord | None:
        """Exact lookup by normalized value; the adapter hashes it with the space's HMAC key."""
        ...

    async def get_handles(self, handle_ids: Sequence[UUID]) -> list[HandleRecord]: ...

    async def profile_of(self, handle_id: UUID) -> ProfileRecord | None: ...

    async def get_profile(self, profile_id: UUID) -> ProfileRecord | None:
        """The profile with `handle_ids` filled from the current membership."""
        ...

    async def get_profiles(self, profile_ids: Sequence[UUID]) -> list[ProfileRecord]:
        """The live ones of these profiles, each as `get_profile` reads it, in one statement."""
        ...

    async def active_links(self, handle_ids: Sequence[UUID]) -> list[LinkRecord]:
        """Person <-> organization links where either side is one of the handles (21)."""
        ...

    async def find_handles(self, keys: Sequence[HandleKey]) -> list[HandleRecord]: ...

    async def retire_handle(self, handle_id: UUID) -> None:
        """The value leaves the handle: no lookup finds the row again, and its keyed hash stays
        for `retired_handles`. Profile, memory and status stay as they are."""
        ...

    async def retired_handles(self, keys: Sequence[HandleKey]) -> list[HandleRecord]:
        """Rows that once held these values and were retired."""
        ...

    async def add_handles(self, handles: Sequence[NewHandle], at: datetime) -> list[HandleRecord]:
        """Inserts the handles another transaction has not, in one statement and in key order, so two
        attaches that name the same new handles never deadlock. Returns the rows this call created."""
        ...

    async def touch_handles(self, handle_ids: Sequence[UUID], at: datetime) -> None:
        """`first_seen` only moves back and `last_seen` only forward: replays never regress them."""
        ...

    async def raise_level(self, handle_ids: Sequence[UUID], level: Verification) -> None:
        """The handle's level only goes up (2.2)."""
        ...

    async def set_status(self, handle_ids: Sequence[UUID], status: HandleStatus) -> None: ...

    async def set_provisional(self, handle_ids: Sequence[UUID], provisional: bool) -> None:
        """Marks the handles as joined by an unconfirmed claim, or confirms them (5.3)."""
        ...

    async def blocked_keys(self, keys: Sequence[HandleKey]) -> set[HandleKey]:
        """The keys whose type and value were blocked through the API (the scope does not count)."""
        ...

    async def add_blocked(self, type: HandleType, value: str, reason: str | None) -> None: ...

    async def list_handles(
        self, types: Sequence[HandleType], after: UUID | None, limit: int
    ) -> list[HandleRecord]:
        """Active handles of the given types in id order, for the offline suggester."""
        ...

    async def memberships(self, handle_ids: Sequence[UUID]) -> dict[UUID, UUID]:
        """handle id -> profile id, for the handles that have one."""
        ...

    async def create_profile(self, profile_id: UUID, kind: SubjectKind, at: datetime) -> ProfileRecord: ...

    async def lock_profiles(self, profile_ids: Sequence[UUID]) -> list[ProfileRecord]:
        """Row locks in id order, so two merges over the same roots never deadlock (5.3).
        Retired profiles are left out."""
        ...

    async def bump_profile(self, profile_id: UUID, expected_version: int, *, privacy: bool) -> ProfileRecord:
        """Raises the membership version, and the privacy version when `privacy`; raises `Conflict`
        when the stored version is not `expected_version` or the profile was retired."""
        ...

    async def retire_profile(self, profile_id: UUID, at: datetime) -> None: ...

    async def revive_profile(self, profile_id: UUID) -> ProfileRecord | None:
        """Brings back a profile retired by a merge that is being undone; None if it was never retired."""
        ...

    async def set_membership(self, handle_ids: Sequence[UUID], profile_id: UUID, version: int) -> None: ...

    async def add_assertions(self, assertions: Sequence[AssertionRecord]) -> None: ...

    async def get_assertions(self, assertion_ids: Sequence[UUID]) -> list[AssertionRecord]: ...

    async def live_assertions(self, handle_ids: Sequence[UUID]) -> list[AssertionRecord]:
        """Applied, unretracted assertions touching any of the handles."""
        ...

    async def pair_asserted(self, handle_a: UUID, handle_b: UUID) -> bool:
        """True when an unretracted, not rejected assertion already links the two handles."""
        ...

    async def separated(self, profile_a: UUID, profile_b: UUID) -> bool:
        """True when a retracted assertion joins a handle of one profile to a handle of the other: someone
        split them, and only a person joins them again (5.3)."""
        ...

    async def recent_counterparts(self, handle_id: UUID, since: datetime) -> list[HandleRecord]:
        """The other side of every unretracted assertion on the handle created since `since`."""
        ...

    async def list_assertions(
        self,
        handle_ids: Sequence[UUID] | None,
        state: AssertionState | None,
        after: UUID | None,
        limit: int,
    ) -> list[AssertionRecord]: ...

    async def set_assertion_state(
        self, assertion_ids: Sequence[UUID], state: AssertionState, note: str | None
    ) -> None:
        """`note` fills `reason` only when the assertion has none, so an operator's reason survives."""
        ...

    async def retract_assertions(self, assertion_ids: Sequence[UUID], at: datetime) -> None: ...

    async def add_merge_log(self, entry: MergeLogEntry) -> bool:
        """False when an entry with this id already exists: the op already ran."""
        ...

    async def merge_logged(self, merge_id: UUID) -> bool: ...

    async def merges_by_assertions(self, assertion_ids: Sequence[UUID]) -> list[MergeLogEntry]:
        """Unreversed merges triggered by these assertions."""
        ...

    async def mark_reversed(self, merge_ids: Sequence[UUID], at: datetime) -> None: ...

    async def add_link(
        self,
        link: LinkRecord,
        *,
        method: AssertionMethod,
        origin_event_id: UUID | None,
        confidence: float,
    ) -> None: ...

    async def get_link(self, link_id: UUID) -> LinkRecord | None: ...

    async def end_link(self, link_id: UUID, at: datetime) -> None: ...

    async def add_suggestion(self, suggestion: SuggestionRecord) -> bool:
        """False when the pair is already open for review, or a reviewer dismissed it."""
        ...

    async def unsettled_suggestion_pairs(self) -> list[tuple[UUID, UUID]]:
        """The handle pairs of the suggestions open for review or dismissed."""
        ...

    async def open_suggestions(self, *, after: UUID | None, limit: int) -> list[SuggestionRecord]:
        """Open suggestions in id order, for review (5.3)."""
        ...

    async def get_suggestion(self, suggestion_id: UUID) -> SuggestionRecord | None: ...

    async def set_suggestion_status(self, suggestion_id: UUID, status: str) -> None: ...

    async def find_profile_by_pseudonym(self, pseudonym: str) -> ProfileRecord | None:
        """The live profile with this pseudonym, with its handles; exact match on an indexed column."""
        ...

    async def list_profiles(
        self,
        *,
        kind: SubjectKind | None,
        after: ListCursor | None,
        ascending: bool,
        limit: int,
        handles_shown: int,
    ) -> tuple[list[ListedProfile], ListCursor | None]:
        """Live profiles by the latest activity on any of their handles, each once, with their
        `handles_shown` latest handles, and where to go on from (None at the end of the list). A page holds
        at most `limit` rows, and fewer when the handles it walked belong to few profiles. The statements
        are fixed by the page, never by the size of the space."""
        ...

    async def recent_profiles(self, kinds: Collection[SubjectKind], window: int) -> list[UUID]:
        """The live profiles of these kinds behind the `window` handles with the latest activity, each once,
        the latest first: the customer list's walk, in one statement whatever the size of the space."""
        ...

    async def pseudonyms(self, profile_ids: Sequence[UUID]) -> dict[UUID, str]:
        """The pseudonym of each profile that exists, retired and erased ones included: a receipt names
        the profile it was written for, whatever became of it."""
        ...

    async def bump_privacy_version(self, profile_id: UUID) -> int:
        """`PrivacyVersioning` (5.11): works on retired and erased profiles too; 0 for an unknown id."""
        ...

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        """`ErasureParticipant` (8.4): the target's handles with their membership, assertions, links and
        suggestions; for a profile also its merge log, and the profile row becomes a tombstone
        that keeps only its id, kind, pseudonym and versions. Handle-level erasure leaves the rest of
        the profile in place: the remaining handles keep their membership."""
        ...

    async def export_rows(
        self, table: str, *, after_seq: int, upto_seq: int, limit: int
    ) -> list[dict[str, Any]]:
        """`ExportSource` for `handles`, `profile_membership`, `profiles`, `profile_links`, `assertions`
        and `merge_log` (22.3). Handle values leave decrypted with their HMAC; an erased
        profile comes as its key plus `erased: True`."""
        ...
