"""In-memory `IdentityRepository` for unit tests.

Values are kept in clear (there is no sealer here); uniqueness, ordering and the optimistic
version check behave like the PostgreSQL adapter.
"""

import hashlib
import hmac
from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.common.errors import Conflict
from niadra.domain.common.ids import uuid7
from niadra.domain.identity.export import (
    EXPORTED,
    assertion_row,
    erases_identity,
    handle_row,
    link_row,
    membership_row,
    merge_row,
    merge_touches,
    profile_row,
)
from niadra.domain.identity.handles import HandleKey, NewHandle
from niadra.domain.identity.listing import ListCursor, ListedProfile
from niadra.domain.identity.model import (
    AssertionRecord,
    AssertionState,
    MergeLogEntry,
    SuggestionRecord,
    pseudonym,
)
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind
from niadra.domain.records import HandleRecord, LinkRecord, ProfileRecord
from niadra.domain.vocabulary import AssertionMethod, HandleStatus, HandleType, SubjectKind, Verification

_PSEUDONYM_KEY = b"niadra-memory-pseudonym"


@dataclass(slots=True)
class _Profile:
    record: ProfileRecord
    created_at: datetime
    retired_at: datetime | None = None
    erased_at: datetime | None = None


class InMemoryIdentityRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store.module("identity")
        for table in (
            "handles",
            "keys",
            "membership",
            "profiles",
            "assertions",
            "merge_log",
            "links",
            "suggestions",
        ):
            self._s.setdefault(table, {})
        self._s.setdefault("blocked", {})
        self._s.setdefault("link_origin", {})
        # Stands in for `change_seq`: bumped on every insert and update of an exported row.
        self._s.setdefault("change_seq", {})
        self._s.setdefault("seq_counter", {"value": 0})

    @property
    def _handles(self) -> dict[UUID, HandleRecord]:
        table: dict[UUID, HandleRecord] = self._s["handles"]
        return table

    @property
    def _membership(self) -> dict[UUID, tuple[UUID, int]]:
        table: dict[UUID, tuple[UUID, int]] = self._s["membership"]
        return table

    @property
    def _profiles(self) -> dict[UUID, _Profile]:
        table: dict[UUID, _Profile] = self._s["profiles"]
        return table

    @property
    def _assertions(self) -> dict[UUID, AssertionRecord]:
        table: dict[UUID, AssertionRecord] = self._s["assertions"]
        return table

    @property
    def _merge_log(self) -> dict[UUID, MergeLogEntry]:
        table: dict[UUID, MergeLogEntry] = self._s["merge_log"]
        return table

    @property
    def _links(self) -> dict[UUID, LinkRecord]:
        table: dict[UUID, LinkRecord] = self._s["links"]
        return table

    async def find_handle(self, type: HandleType, scope: str, value: str) -> HandleRecord | None:
        handle_id = self._s["keys"].get(HandleKey(type, scope, value))
        return self._handles.get(handle_id) if handle_id else None

    async def get_handles(self, handle_ids: Sequence[UUID]) -> list[HandleRecord]:
        return [self._handles[h] for h in dict.fromkeys(handle_ids) if h in self._handles]

    async def profile_of(self, handle_id: UUID) -> ProfileRecord | None:
        member = self._membership.get(handle_id)
        return await self.get_profile(member[0]) if member else None

    async def get_profile(self, profile_id: UUID) -> ProfileRecord | None:
        profile = self._profiles.get(profile_id)
        if profile is None or profile.retired_at is not None:
            return None
        handle_ids = sorted(h for h, (p, _) in self._membership.items() if p == profile_id)
        provisional = [h for h in handle_ids if h in self._handles and self._handles[h].provisional]
        return replace(
            profile.record, handle_ids=tuple(handle_ids), provisional_handle_ids=tuple(provisional)
        )

    async def get_profiles(self, profile_ids: Sequence[UUID]) -> list[ProfileRecord]:
        found = [await self.get_profile(p) for p in sorted(set(profile_ids))]
        return [p for p in found if p is not None]

    async def active_links(self, handle_ids: Sequence[UUID]) -> list[LinkRecord]:
        wanted = set(handle_ids)
        now = datetime.now(UTC)
        return sorted(
            (
                link
                for link in self._links.values()
                if (link.person_handle_id in wanted or link.org_handle_id in wanted)
                and link.valid_from <= now
                and (link.valid_to is None or link.valid_to > now)
            ),
            key=lambda link: link.link_id,
        )

    async def find_handles(self, keys: Sequence[HandleKey]) -> list[HandleRecord]:
        found = (self._s["keys"].get(k) for k in dict.fromkeys(keys))
        return [self._handles[h] for h in found if h is not None]

    async def retire_handle(self, handle_id: UUID) -> None:
        record = self._handles[handle_id]
        key = HandleKey(record.type, record.scope, record.value)
        if self._s["keys"].get(key) == handle_id:
            del self._s["keys"][key]
            self._s.setdefault("former", {}).setdefault(key, []).append(handle_id)
            self._stamp("handles", handle_id)

    async def retired_handles(self, keys: Sequence[HandleKey]) -> list[HandleRecord]:
        former: dict[HandleKey, list[UUID]] = self._s.get("former", {})
        return [self._handles[h] for k in dict.fromkeys(keys) for h in former.get(k, ())]

    async def add_handles(self, handles: Sequence[NewHandle], at: datetime) -> list[HandleRecord]:
        created: list[HandleRecord] = []
        for new in handles:
            if new.key in self._s["keys"]:
                continue
            key, handle_id = new.key, uuid7()
            self._s["keys"][key] = handle_id
            record = HandleRecord(
                handle_id,
                key.type,
                key.scope,
                new.subject_kind,
                key.value,
                new.status,
                Verification.V0,
                at,
                at,
            )
            self._handles[handle_id] = record
            self._stamp("handles", handle_id)
            created.append(record)
        return created

    async def touch_handles(self, handle_ids: Sequence[UUID], at: datetime) -> None:
        for h in handle_ids:
            old = self._handles[h]
            self._update_handle(
                replace(old, first_seen=min(old.first_seen, at), last_seen=max(old.last_seen, at))
            )

    async def raise_level(self, handle_ids: Sequence[UUID], level: Verification) -> None:
        for h in handle_ids:
            old = self._handles[h]
            if level.rank > old.level.rank:
                self._handles[h] = replace(old, level=level)
                self._stamp("handles", h)

    async def set_status(self, handle_ids: Sequence[UUID], status: HandleStatus) -> None:
        for h in handle_ids:
            self._update_handle(replace(self._handles[h], status=status))

    async def set_provisional(self, handle_ids: Sequence[UUID], provisional: bool) -> None:
        for h in handle_ids:
            self._update_handle(replace(self._handles[h], provisional=provisional))

    async def blocked_keys(self, keys: Sequence[HandleKey]) -> set[HandleKey]:
        return {k for k in keys if (k.type, k.value) in self._s["blocked"]}

    async def add_blocked(self, type: HandleType, value: str, reason: str | None) -> None:
        self._s["blocked"].setdefault((type, value), reason)

    async def list_handles(
        self, types: Sequence[HandleType], after: UUID | None, limit: int
    ) -> list[HandleRecord]:
        rows = sorted(
            (
                h
                for h in self._handles.values()
                if h.type in types
                and h.status is HandleStatus.ACTIVE
                and (after is None or h.handle_id > after)
            ),
            key=lambda h: h.handle_id,
        )
        return rows[:limit]

    async def memberships(self, handle_ids: Sequence[UUID]) -> dict[UUID, UUID]:
        return {h: self._membership[h][0] for h in handle_ids if h in self._membership}

    async def create_profile(self, profile_id: UUID, kind: SubjectKind, at: datetime) -> ProfileRecord:
        digest = hmac.new(_PSEUDONYM_KEY, profile_id.bytes, hashlib.sha256).digest()
        record = ProfileRecord(profile_id, kind, pseudonym(digest), membership_version=1, privacy_version=1)
        self._profiles[profile_id] = _Profile(record, at)
        self._stamp("profiles", profile_id)
        return record

    async def lock_profiles(self, profile_ids: Sequence[UUID]) -> list[ProfileRecord]:
        return [
            self._profiles[p].record
            for p in sorted(set(profile_ids))
            if p in self._profiles and self._profiles[p].retired_at is None
        ]

    async def bump_profile(self, profile_id: UUID, expected_version: int, *, privacy: bool) -> ProfileRecord:
        profile = self._profiles.get(profile_id)
        if (
            profile is None
            or profile.retired_at is not None
            or profile.record.membership_version != expected_version
        ):
            raise Conflict("the profile changed; retry")
        profile.record = replace(
            profile.record,
            membership_version=expected_version + 1,
            privacy_version=profile.record.privacy_version + (1 if privacy else 0),
        )
        self._stamp("profiles", profile_id)
        return profile.record

    async def retire_profile(self, profile_id: UUID, at: datetime) -> None:
        self._profiles[profile_id].retired_at = at
        self._stamp("profiles", profile_id)

    async def revive_profile(self, profile_id: UUID) -> ProfileRecord | None:
        profile = self._profiles.get(profile_id)
        if profile is None or profile.retired_at is None or profile.erased_at is not None:
            return None
        profile.retired_at = None
        self._stamp("profiles", profile_id)
        return profile.record

    async def set_membership(self, handle_ids: Sequence[UUID], profile_id: UUID, version: int) -> None:
        for h in handle_ids:
            if self._membership.get(h) != (profile_id, version):
                self._membership[h] = (profile_id, version)
                self._stamp("profile_membership", h)

    async def add_assertions(self, assertions: Sequence[AssertionRecord]) -> None:
        for assertion in assertions:
            if assertion.assertion_id not in self._assertions:
                self._assertions[assertion.assertion_id] = assertion
                self._stamp("assertions", assertion.assertion_id)

    async def get_assertions(self, assertion_ids: Sequence[UUID]) -> list[AssertionRecord]:
        return [self._assertions[a] for a in dict.fromkeys(assertion_ids) if a in self._assertions]

    async def live_assertions(self, handle_ids: Sequence[UUID]) -> list[AssertionRecord]:
        wanted = set(handle_ids)
        return self._sorted(
            a for a in self._assertions.values() if a.live and {a.handle_a, a.handle_b} & wanted
        )

    async def pair_asserted(self, handle_a: UUID, handle_b: UUID) -> bool:
        pair = {handle_a, handle_b}
        return any(
            {a.handle_a, a.handle_b} == pair
            and a.retracted_at is None
            and a.state is not AssertionState.REJECTED
            for a in self._assertions.values()
        )

    async def separated(self, profile_a: UUID, profile_b: UUID) -> bool:
        sides = {profile_a, profile_b}
        return any(
            a.retracted_at is not None
            and {
                self._membership.get(a.handle_a, (None, 0))[0],
                self._membership.get(a.handle_b, (None, 0))[0],
            }
            == sides
            for a in self._assertions.values()
        )

    async def recent_counterparts(self, handle_id: UUID, since: datetime) -> list[HandleRecord]:
        others = [
            a.other(handle_id)
            for a in self._sorted(self._assertions.values())
            if handle_id in (a.handle_a, a.handle_b) and a.retracted_at is None and a.created_at >= since
        ]
        return await self.get_handles(others)

    async def list_assertions(
        self,
        handle_ids: Sequence[UUID] | None,
        state: AssertionState | None,
        after: UUID | None,
        limit: int,
    ) -> list[AssertionRecord]:
        wanted = set(handle_ids) if handle_ids is not None else None
        rows = self._sorted(
            a
            for a in self._assertions.values()
            if (wanted is None or {a.handle_a, a.handle_b} & wanted)
            and (state is None or a.state is state)
            and (after is None or a.assertion_id > after)
        )
        return rows[:limit]

    async def set_assertion_state(
        self, assertion_ids: Sequence[UUID], state: AssertionState, note: str | None
    ) -> None:
        for a in assertion_ids:
            old = self._assertions[a]
            new = replace(old, state=state, reason=old.reason or note)
            if new != old:
                self._assertions[a] = new
                self._stamp("assertions", a)

    async def retract_assertions(self, assertion_ids: Sequence[UUID], at: datetime) -> None:
        for a in assertion_ids:
            old = self._assertions[a]
            if old.retracted_at is None:
                self._assertions[a] = replace(old, retracted_at=at)
                self._stamp("assertions", a)

    async def add_merge_log(self, entry: MergeLogEntry) -> bool:
        if entry.merge_id in self._merge_log:
            return False
        self._merge_log[entry.merge_id] = entry
        self._stamp("merge_log", entry.merge_id)
        return True

    async def merge_logged(self, merge_id: UUID) -> bool:
        return merge_id in self._merge_log

    async def merges_by_assertions(self, assertion_ids: Sequence[UUID]) -> list[MergeLogEntry]:
        wanted = set(assertion_ids)
        return sorted(
            (
                m
                for m in self._merge_log.values()
                if m.op.value == "merge" and m.assertion_id in wanted and m.reversed_at is None
            ),
            key=lambda m: m.merge_id,
        )

    async def mark_reversed(self, merge_ids: Sequence[UUID], at: datetime) -> None:
        for m in merge_ids:
            self._merge_log[m] = replace(self._merge_log[m], reversed_at=at)
            self._stamp("merge_log", m)

    async def add_link(
        self, link: LinkRecord, *, method: AssertionMethod, origin_event_id: UUID | None, confidence: float
    ) -> None:
        self._links[link.link_id] = link
        self._s["link_origin"][link.link_id] = (method, origin_event_id, confidence)
        self._stamp("profile_links", link.link_id)

    async def get_link(self, link_id: UUID) -> LinkRecord | None:
        return self._links.get(link_id)

    async def end_link(self, link_id: UUID, at: datetime) -> None:
        self._links[link_id] = replace(self._links[link_id], valid_to=at)
        self._stamp("profile_links", link_id)

    async def add_suggestion(self, suggestion: SuggestionRecord) -> bool:
        pair = {suggestion.handle_a, suggestion.handle_b}
        suggestions: dict[UUID, SuggestionRecord] = self._s["suggestions"]
        if any({s.handle_a, s.handle_b} == pair and s.status != "accepted" for s in suggestions.values()):
            return False
        suggestions[suggestion.suggestion_id] = suggestion
        return True

    async def unsettled_suggestion_pairs(self) -> list[tuple[UUID, UUID]]:
        return [(s.handle_a, s.handle_b) for s in self.suggestions if s.status != "accepted"]

    async def open_suggestions(self, *, after: UUID | None, limit: int) -> list[SuggestionRecord]:
        return [
            s for s in self.suggestions if s.status == "open" and (after is None or s.suggestion_id > after)
        ][:limit]

    async def get_suggestion(self, suggestion_id: UUID) -> SuggestionRecord | None:
        found: SuggestionRecord | None = self._s["suggestions"].get(suggestion_id)
        return found

    async def set_suggestion_status(self, suggestion_id: UUID, status: str) -> None:
        suggestions: dict[UUID, SuggestionRecord] = self._s["suggestions"]
        suggestions[suggestion_id] = replace(suggestions[suggestion_id], status=status)

    async def find_profile_by_pseudonym(self, pseudonym: str) -> ProfileRecord | None:
        found = next((p for p in self._profiles.values() if p.record.pseudonym == pseudonym), None)
        return await self.get_profile(found.record.profile_id) if found else None

    async def recent_profiles(self, kinds: Collection[SubjectKind], window: int) -> list[UUID]:
        walked = sorted(
            (
                (h.last_seen, h.handle_id, p)
                for h in self._handles.values()
                if (not kinds or h.subject_kind in kinds)
                and (p := self._membership.get(h.handle_id, (None, 0))[0]) is not None
            ),
            reverse=True,
        )[:window]
        live = [p for _, _, p in walked if (rec := self._profiles.get(p)) and rec.retired_at is None]
        return list(dict.fromkeys(live))

    async def list_profiles(
        self,
        *,
        kind: SubjectKind | None,
        after: ListCursor | None,
        ascending: bool,
        limit: int,
        handles_shown: int,
    ) -> tuple[list[ListedProfile], ListCursor | None]:
        placed: list[tuple[ListCursor, ListedProfile]] = []
        for profile_id, profile in self._profiles.items():
            members = sorted(
                (
                    self._handles[h]
                    for h, (p, _) in self._membership.items()
                    if p == profile_id and h in self._handles
                ),
                key=lambda h: (h.last_seen, h.handle_id),
                reverse=True,
            )
            if profile.retired_at is not None or not members:
                continue
            if kind is not None and profile.record.kind is not kind:
                continue
            record, latest = profile.record, members[0]
            row = ListedProfile(
                profile_id,
                record.kind,
                record.pseudonym,
                latest.last_seen,
                tuple(members[:handles_shown]),
                len(members),
            )
            placed.append((ListCursor(latest.last_seen, latest.handle_id), row))
        placed.sort(key=lambda at: (at[0].last_seen, at[0].handle_id), reverse=not ascending)
        if after is not None:
            edge = (after.last_seen, after.handle_id)
            placed = [
                (at, row)
                for at, row in placed
                if ((at.last_seen, at.handle_id) > edge if ascending else (at.last_seen, at.handle_id) < edge)
            ]
        page = placed[:limit]
        return [row for _, row in page], page[-1][0] if len(placed) > limit else None

    async def pseudonyms(self, profile_ids: Sequence[UUID]) -> dict[UUID, str]:
        return {p: self._profiles[p].record.pseudonym for p in profile_ids if p in self._profiles}

    async def bump_privacy_version(self, profile_id: UUID) -> int:
        profile = self._profiles.get(profile_id)
        if profile is None:
            return 0
        profile.record = replace(profile.record, privacy_version=profile.record.privacy_version + 1)
        self._stamp("profiles", profile_id)
        return profile.record.privacy_version

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if not erases_identity(scope):
            return []
        handles = set(scope.handle_ids)
        profile_id = scope.profile_id if scope.kind is TargetKind.PROFILE else None
        if profile_id is not None:
            handles |= {h for h, (p, _) in self._membership.items() if p == profile_id}
        suggestions: dict[UUID, SuggestionRecord] = self._s["suggestions"]
        doomed: list[tuple[str, str, dict[Any, Any], UUID]] = [
            *(
                ("assertions", "assertion_id", self._assertions, a.assertion_id)
                for a in self._sorted(self._assertions.values())
                if {a.handle_a, a.handle_b} & handles
            ),
            *(
                ("profile_links", "link_id", self._links, link_id)
                for link_id, link in sorted(self._links.items())
                if {link.person_handle_id, link.org_handle_id} & handles
            ),
            *(
                ("identity_suggestions", "suggestion_id", suggestions, s.suggestion_id)
                for s in self.suggestions
                if {s.handle_a, s.handle_b} & handles
            ),
            *(
                ("merge_log", "merge_id", self._merge_log, m.merge_id)
                for m in self.merge_log
                if merge_touches(m, profile_id, handles)
            ),
            *(
                ("profile_membership", "handle_id", self._membership, h)
                for h in sorted(handles & set(self._membership))
            ),
            *(("handles", "handle_id", self._handles, h) for h in sorted(handles & set(self._handles))),
        ]
        rows = [self._erase(table, column, rows, key) for table, column, rows, key in doomed[:limit]]
        if len(rows) < limit and profile_id is not None:
            rows += self._tombstone(profile_id)
        return rows

    async def export_rows(
        self, table: str, *, after_seq: int, upto_seq: int, limit: int
    ) -> list[dict[str, Any]]:
        if table not in EXPORTED:
            raise ValueError(f"identity does not export {table}")
        stamped = sorted(
            (seq, key)
            for (name, key), seq in self._s["change_seq"].items()
            if name == table and after_seq < seq <= upto_seq
        )
        return [self._export(table, key, seq) for seq, key in stamped[:limit]]

    def _export(self, table: str, key: UUID, seq: int) -> dict[str, Any]:
        match table:
            case "handles":
                return handle_row(self._handles[key], None, seq)
            case "profile_membership":
                profile_id, version = self._membership[key]
                return membership_row(key, profile_id, version, seq)
            case "profiles":
                p = self._profiles[key]
                erased = p.erased_at is not None
                return profile_row(
                    p.record, created_at=p.created_at, retired_at=p.retired_at, erased=erased, change_seq=seq
                )
            case "profile_links":
                method, origin, confidence = self._s["link_origin"][key]
                return link_row(
                    self._links[key],
                    method=method,
                    origin_event_id=origin,
                    confidence=confidence,
                    change_seq=seq,
                )
            case "assertions":
                return assertion_row(self._assertions[key], seq)
            case _:
                return merge_row(self._merge_log[key], seq)

    def _update_handle(self, new: HandleRecord) -> None:
        # Only real changes move the sequence here, so replaying an attach leaves the store as it was;
        # PostgreSQL bumps on every UPDATE, which only makes the export re-send an unchanged row.
        if self._handles[new.handle_id] != new:
            self._handles[new.handle_id] = new
            self._stamp("handles", new.handle_id)

    def _stamp(self, table: str, key: UUID) -> None:
        self._s["seq_counter"]["value"] += 1
        self._s["change_seq"][(table, key)] = self._s["seq_counter"]["value"]

    def _erase(self, table: str, column: str, rows: dict[Any, Any], key: UUID) -> ErasedRow:
        del rows[key]
        if table == "handles":
            self._s["keys"] = {k: h for k, h in self._s["keys"].items() if h != key}
        return ErasedRow(table, {column: str(key)}, (), self._s["change_seq"].pop((table, key), None))

    def _tombstone(self, profile_id: UUID) -> list[ErasedRow]:
        profile = self._profiles.get(profile_id)
        if profile is None or profile.erased_at is not None:
            return []
        now = datetime.now(UTC)
        seq = self._s["change_seq"].get(("profiles", profile_id))
        profile.erased_at = now
        profile.retired_at = profile.retired_at or now
        profile.record = replace(profile.record, org_type=None)
        self._stamp("profiles", profile_id)
        return [ErasedRow("profiles", {"profile_id": str(profile_id)}, (), seq)]

    # Test helpers, not part of the port.

    @property
    def suggestions(self) -> list[SuggestionRecord]:
        return sorted(self._s["suggestions"].values(), key=lambda s: s.suggestion_id)

    @property
    def merge_log(self) -> list[MergeLogEntry]:
        return sorted(self._merge_log.values(), key=lambda m: m.merge_id)

    @staticmethod
    def _sorted(assertions: Any) -> list[AssertionRecord]:
        return sorted(assertions, key=lambda a: a.assertion_id)
