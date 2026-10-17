"""PostgreSQL implementation of `IdentityRepository`.

Handle values are stored as a keyed HMAC (for lookups and uniqueness) plus AES-GCM ciphertext
bound to `handles:<handle_id>:value_ct`. Every statement also filters by the space pinned in the
transaction, so the queries stay correct under the BYPASSRLS role and use the key prefix.
"""

from collections.abc import Collection, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    Row,
    Table,
    and_,
    case,
    delete,
    exists,
    func,
    insert,
    literal,
    or_,
    select,
    true,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.schema.base import SpaceBase
from niadra.adapters.outbound.postgres.schema.write_path import (
    Assertion,
    BlockedValue,
    HandleRow,
    IdentitySuggestion,
    MergeLog,
    Profile,
    ProfileLink,
    ProfileMembership,
)
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.common.errors import Conflict
from niadra.domain.common.ids import uuid7
from niadra.domain.identity.export import (
    assertion_row,
    erases_identity,
    handle_row,
    link_row,
    membership_row,
    merge_row,
    profile_row,
)
from niadra.domain.identity.handles import HandleKey, NewHandle
from niadra.domain.identity.listing import ListCursor, ListedProfile
from niadra.domain.identity.model import (
    AssertionRecord,
    AssertionState,
    MergeLogEntry,
    MergeOp,
    SuggestionRecord,
    pseudonym,
)
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind
from niadra.domain.records import HandleRecord, LinkRecord, ProfileRecord
from niadra.domain.vocabulary import AssertionMethod, HandleStatus, HandleType, SubjectKind, Verification


def _table(model: type[SpaceBase]) -> Table:
    return SpaceBase.metadata.tables[model.__tablename__]


H = _table(HandleRow)
P = _table(Profile)
PM = _table(ProfileMembership)
A = _table(Assertion)
ML = _table(MergeLog)
L = _table(ProfileLink)
B = _table(BlockedValue)
S = _table(IdentitySuggestion)
# A dismissed pair stays dismissed: the suggester must not reopen what a reviewer rejected.
_UNSETTLED = ("open", "rejected")
LIST_MIN_WINDOW = 20
LIST_WINDOWS = 3
"""The customer list looks at twice the page in handles, and at most this many times, per request."""
RETIRED_MARKER = b"retired:"
_EXPORTED: dict[str, Table] = {
    "handles": H,
    "profile_membership": PM,
    "profiles": P,
    "profile_links": L,
    "assertions": A,
    "merge_log": ML,
}


class PgIdentityRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    # Reads other modules use.

    async def find_handle(self, type: HandleType, scope: str, value: str) -> HandleRecord | None:
        stmt = select(H).where(
            H.c.space_id == CURRENT_SPACE,
            H.c.type == type.value,
            H.c.scope == scope,
            H.c.value_hmac == await self._hmac(type, value),
        )
        row = (await self._db.execute(stmt)).first()
        return await self._handle(row) if row else None

    async def get_handles(self, handle_ids: Sequence[UUID]) -> list[HandleRecord]:
        if not handle_ids:
            return []
        stmt = (
            select(H)
            .where(H.c.space_id == CURRENT_SPACE, H.c.handle_id.in_(list(handle_ids)))
            .order_by(H.c.handle_id)
        )
        return [await self._handle(row) for row in await self._db.execute(stmt)]

    async def profile_of(self, handle_id: UUID) -> ProfileRecord | None:
        # One statement: every read resolves its customer here, inside a transaction that holds one of the
        # space's few pooled connections.
        owner = select(PM.c.profile_id).where(PM.c.space_id == CURRENT_SPACE, PM.c.handle_id == handle_id)
        return await self._profile_where(P.c.profile_id == owner.scalar_subquery())

    async def get_profile(self, profile_id: UUID) -> ProfileRecord | None:
        return await self._profile_where(P.c.profile_id == profile_id)

    async def get_profiles(self, profile_ids: Sequence[UUID]) -> list[ProfileRecord]:
        if not profile_ids:
            return []
        return await self._profiles_where(P.c.profile_id.in_(sorted(set(profile_ids))))

    async def _profile_where(self, condition: Any) -> ProfileRecord | None:
        found = await self._profiles_where(condition)
        return found[0] if found else None

    async def _profiles_where(self, condition: Any) -> list[ProfileRecord]:
        members = PM.alias("members")
        handle_ids = (
            select(func.array_agg(aggregate_order_by(members.c.handle_id, members.c.handle_id)))
            .where(members.c.space_id == P.c.space_id, members.c.profile_id == P.c.profile_id)
            .scalar_subquery()
        )
        # The members a claim joined without confirmation, in the same statement: every read scopes the
        # memory it serves by them (5.3), and a second round trip would cost every read.
        claimed = H.alias("claimed")
        provisional_ids = (
            select(func.array_agg(aggregate_order_by(members.c.handle_id, members.c.handle_id)))
            .select_from(
                members.join(
                    claimed,
                    and_(
                        claimed.c.space_id == members.c.space_id, claimed.c.handle_id == members.c.handle_id
                    ),
                )
            )
            .where(
                members.c.space_id == P.c.space_id,
                members.c.profile_id == P.c.profile_id,
                claimed.c.provisional,
            )
            .scalar_subquery()
        )
        stmt = (
            select(P, handle_ids.label("member_ids"), provisional_ids.label("provisional_ids"))
            .where(P.c.space_id == CURRENT_SPACE, condition, P.c.retired_at.is_(None))
            .order_by(P.c.profile_id)
        )
        return [
            replace(
                _profile(row, tuple(row.member_ids or ())),
                provisional_handle_ids=tuple(row.provisional_ids or ()),
            )
            for row in await self._db.execute(stmt)
        ]

    async def active_links(self, handle_ids: Sequence[UUID]) -> list[LinkRecord]:
        if not handle_ids:
            return []
        ids = list(handle_ids)
        stmt = (
            select(L)
            .where(
                L.c.space_id == CURRENT_SPACE,
                or_(L.c.person_handle_id.in_(ids), L.c.org_handle_id.in_(ids)),
                or_(L.c.valid_to.is_(None), L.c.valid_to > func.now()),
                # A link that starts later is not active yet (21.4).
                L.c.valid_from <= func.now(),
            )
            .order_by(L.c.link_id)
        )
        return [_link(row) for row in await self._db.execute(stmt)]

    # Handles.

    async def find_handles(self, keys: Sequence[HandleKey]) -> list[HandleRecord]:
        if not keys:
            return []
        wanted = [(k.type.value, k.scope, await self._hmac(k.type, k.value)) for k in dict.fromkeys(keys)]
        stmt = select(H).where(
            H.c.space_id == CURRENT_SPACE, tuple_(H.c.type, H.c.scope, H.c.value_hmac).in_(wanted)
        )
        return [await self._handle(row) for row in await self._db.execute(stmt)]

    async def retire_handle(self, handle_id: UUID) -> None:
        # The marker is 24 bytes and a keyed hash 32, so it never matches a lookup; it is unique per row.
        stmt = (
            update(H)
            .where(H.c.space_id == CURRENT_SPACE, H.c.handle_id == handle_id, H.c.former_hmac.is_(None))
            .values(former_hmac=H.c.value_hmac, value_hmac=RETIRED_MARKER + handle_id.bytes)
        )
        await self._db.execute(stmt)

    async def retired_handles(self, keys: Sequence[HandleKey]) -> list[HandleRecord]:
        if not keys:
            return []
        wanted = [(k.type.value, k.scope, await self._hmac(k.type, k.value)) for k in dict.fromkeys(keys)]
        stmt = (
            select(H)
            .where(
                H.c.space_id == CURRENT_SPACE,
                H.c.former_hmac.is_not(None),
                tuple_(H.c.type, H.c.scope, H.c.former_hmac).in_(wanted),
            )
            .order_by(H.c.handle_id)
        )
        return [await self._handle(row) for row in await self._db.execute(stmt)]

    async def add_handles(self, handles: Sequence[NewHandle], at: datetime) -> list[HandleRecord]:
        if not handles:
            return []
        rows: list[tuple[tuple[str, str, bytes], NewHandle, dict[str, Any]]] = []
        for new in dict((h.key, h) for h in handles).values():
            key, handle_id = new.key, uuid7()
            value_ct, dek_version = await self._seal().seal(
                key.value, table="handles", row=handle_id, column="value_ct"
            )
            value_hmac = await self._hmac(key.type, key.value)
            values = {
                "space_id": CURRENT_SPACE,
                "handle_id": handle_id,
                "type": key.type.value,
                "scope": key.scope,
                "subject_kind": new.subject_kind.value,
                "value_hmac": value_hmac,
                "value_ct": value_ct,
                "dek_version": dek_version,
                "status": new.status.value,
                "level": Verification.V0.value,
                "first_seen": at,
                "last_seen": at,
            }
            rows.append(((key.type.value, key.scope, value_hmac), new, values))
        # The unique index's order: two transactions inserting the same keys lock them in the same order.
        rows.sort(key=lambda row: row[0])
        stmt = (
            pg_insert(H)
            .values([values for _, _, values in rows])
            .on_conflict_do_nothing(index_elements=[H.c.space_id, H.c.type, H.c.scope, H.c.value_hmac])
            .returning(H.c.handle_id)
        )
        created = set((await self._db.execute(stmt)).scalars())
        return [
            HandleRecord(
                values["handle_id"],
                new.key.type,
                new.key.scope,
                new.subject_kind,
                new.key.value,
                new.status,
                Verification.V0,
                at,
                at,
            )
            for _, new, values in rows
            if values["handle_id"] in created
        ]

    async def touch_handles(self, handle_ids: Sequence[UUID], at: datetime) -> None:
        await self._update_handles(
            handle_ids, first_seen=func.least(H.c.first_seen, at), last_seen=func.greatest(H.c.last_seen, at)
        )

    async def raise_level(self, handle_ids: Sequence[UUID], level: Verification) -> None:
        # 'V0' < 'V1' < ... < 'V4' as text; `no_customer` is never stored on a handle.
        stmt = (
            update(H)
            .where(
                H.c.space_id == CURRENT_SPACE, H.c.handle_id.in_(list(handle_ids)), H.c.level < level.value
            )
            .values(level=level.value)
        )
        await self._db.execute(stmt)

    async def set_status(self, handle_ids: Sequence[UUID], status: HandleStatus) -> None:
        await self._update_handles(handle_ids, status=status.value)

    async def set_provisional(self, handle_ids: Sequence[UUID], provisional: bool) -> None:
        if handle_ids:
            stmt = (
                update(H)
                .where(
                    H.c.space_id == CURRENT_SPACE,
                    H.c.handle_id.in_(list(handle_ids)),
                    H.c.provisional.is_not(provisional),
                )
                .values(provisional=provisional)
            )
            await self._db.execute(stmt)

    async def blocked_keys(self, keys: Sequence[HandleKey]) -> set[HandleKey]:
        if not keys:
            return set()
        by_hmac: dict[tuple[str, bytes], list[HandleKey]] = {}
        for key in keys:
            by_hmac.setdefault((key.type.value, await self._hmac(key.type, key.value)), []).append(key)
        stmt = select(B.c.type, B.c.value_hmac).where(
            B.c.space_id == CURRENT_SPACE, tuple_(B.c.type, B.c.value_hmac).in_(list(by_hmac))
        )
        found = {(row.type, bytes(row.value_hmac)) for row in await self._db.execute(stmt)}
        return {key for pair, same in by_hmac.items() if pair in found for key in same}

    async def add_blocked(self, type: HandleType, value: str, reason: str | None) -> None:
        stmt = (
            pg_insert(B)
            .values(
                space_id=CURRENT_SPACE,
                type=type.value,
                value_hmac=await self._hmac(type, value),
                reason=reason,
            )
            .on_conflict_do_nothing()
        )
        await self._db.execute(stmt)

    async def list_handles(
        self, types: Sequence[HandleType], after: UUID | None, limit: int
    ) -> list[HandleRecord]:
        stmt = select(H).where(
            H.c.space_id == CURRENT_SPACE,
            H.c.type.in_([t.value for t in types]),
            H.c.status == HandleStatus.ACTIVE.value,
        )
        if after is not None:
            stmt = stmt.where(H.c.handle_id > after)
        rows = await self._db.execute(stmt.order_by(H.c.handle_id).limit(limit))
        return [await self._handle(row) for row in rows]

    # Profiles and membership.

    async def memberships(self, handle_ids: Sequence[UUID]) -> dict[UUID, UUID]:
        if not handle_ids:
            return {}
        stmt = select(PM.c.handle_id, PM.c.profile_id).where(
            PM.c.space_id == CURRENT_SPACE, PM.c.handle_id.in_(list(handle_ids))
        )
        return {row.handle_id: row.profile_id for row in await self._db.execute(stmt)}

    async def create_profile(self, profile_id: UUID, kind: SubjectKind, at: datetime) -> ProfileRecord:
        name = pseudonym(await self._seal().hmac("pseudonym", str(profile_id)))
        stmt = (
            insert(P)
            .values(
                space_id=CURRENT_SPACE, profile_id=profile_id, kind=kind.value, pseudonym=name, created_at=at
            )
            .returning(P)
        )
        return _profile((await self._db.execute(stmt)).one())

    async def lock_profiles(self, profile_ids: Sequence[UUID]) -> list[ProfileRecord]:
        stmt = (
            select(P)
            .where(
                P.c.space_id == CURRENT_SPACE,
                P.c.profile_id.in_(sorted(set(profile_ids))),
                P.c.retired_at.is_(None),
            )
            .order_by(P.c.profile_id)
            .with_for_update()
        )
        return [_profile(row) for row in await self._db.execute(stmt)]

    async def bump_profile(self, profile_id: UUID, expected_version: int, *, privacy: bool) -> ProfileRecord:
        stmt = (
            update(P)
            .where(
                P.c.space_id == CURRENT_SPACE,
                P.c.profile_id == profile_id,
                P.c.membership_version == expected_version,
                P.c.retired_at.is_(None),
            )
            .values(
                membership_version=P.c.membership_version + 1,
                privacy_version=P.c.privacy_version + (1 if privacy else 0),
            )
            .returning(P)
        )
        row = (await self._db.execute(stmt)).first()
        if row is None:
            raise Conflict("the profile changed; retry")
        return _profile(row)

    async def retire_profile(self, profile_id: UUID, at: datetime) -> None:
        stmt = (
            update(P).where(P.c.space_id == CURRENT_SPACE, P.c.profile_id == profile_id).values(retired_at=at)
        )
        await self._db.execute(stmt)

    async def revive_profile(self, profile_id: UUID) -> ProfileRecord | None:
        stmt = (
            update(P)
            .where(
                P.c.space_id == CURRENT_SPACE,
                P.c.profile_id == profile_id,
                P.c.retired_at.is_not(None),
                P.c.erased_at.is_(None),
            )
            .values(retired_at=None)
            .returning(P)
        )
        row = (await self._db.execute(stmt)).first()
        return _profile(row) if row else None

    async def set_membership(self, handle_ids: Sequence[UUID], profile_id: UUID, version: int) -> None:
        if not handle_ids:
            return
        stmt = pg_insert(PM).values(
            [
                {"space_id": CURRENT_SPACE, "handle_id": h, "profile_id": profile_id, "version": version}
                for h in handle_ids
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[PM.c.space_id, PM.c.handle_id],
            set_={"profile_id": stmt.excluded.profile_id, "version": stmt.excluded.version},
        )
        await self._db.execute(stmt)

    # Assertions.

    async def add_assertions(self, assertions: Sequence[AssertionRecord]) -> None:
        if not assertions:
            return
        rows = [
            {
                "space_id": CURRENT_SPACE,
                "assertion_id": a.assertion_id,
                "handle_a": a.handle_a,
                "handle_b": a.handle_b,
                "method": a.method.value,
                "origin_event_id": a.origin_event_id,
                "confidence": a.confidence,
                "level_at_capture": a.level_at_capture.value,
                "state": a.state.value,
                "actor": a.actor,
                "reason": a.reason,
                "created_at": a.created_at,
                "retracted_at": a.retracted_at,
            }
            for a in assertions
        ]
        await self._db.execute(pg_insert(A).values(rows).on_conflict_do_nothing())

    async def get_assertions(self, assertion_ids: Sequence[UUID]) -> list[AssertionRecord]:
        if not assertion_ids:
            return []
        stmt = select(A).where(A.c.space_id == CURRENT_SPACE, A.c.assertion_id.in_(list(assertion_ids)))
        return [_assertion(row) for row in await self._db.execute(stmt.order_by(A.c.assertion_id))]

    async def live_assertions(self, handle_ids: Sequence[UUID]) -> list[AssertionRecord]:
        if not handle_ids:
            return []
        stmt = select(A).where(
            A.c.space_id == CURRENT_SPACE,
            _touching(handle_ids),
            A.c.state == AssertionState.APPLIED.value,
            A.c.retracted_at.is_(None),
        )
        return [_assertion(row) for row in await self._db.execute(stmt.order_by(A.c.assertion_id))]

    async def pair_asserted(self, handle_a: UUID, handle_b: UUID) -> bool:
        pair = or_(
            and_(A.c.handle_a == handle_a, A.c.handle_b == handle_b),
            and_(A.c.handle_a == handle_b, A.c.handle_b == handle_a),
        )
        # A rejected assertion stands for nothing: new evidence for the pair must become pending again.
        stmt = select(
            exists().where(
                A.c.space_id == CURRENT_SPACE,
                pair,
                A.c.retracted_at.is_(None),
                A.c.state != AssertionState.REJECTED.value,
            )
        )
        return bool((await self._db.execute(stmt)).scalar())

    async def separated(self, profile_a: UUID, profile_b: UUID) -> bool:
        if profile_a == profile_b:
            return False
        ma, mb = PM.alias("ma"), PM.alias("mb")
        crossing = or_(
            and_(ma.c.profile_id == profile_a, mb.c.profile_id == profile_b),
            and_(ma.c.profile_id == profile_b, mb.c.profile_id == profile_a),
        )
        stmt = select(
            exists()
            .select_from(
                A.join(ma, and_(ma.c.space_id == A.c.space_id, ma.c.handle_id == A.c.handle_a)).join(
                    mb, and_(mb.c.space_id == A.c.space_id, mb.c.handle_id == A.c.handle_b)
                )
            )
            .where(A.c.space_id == CURRENT_SPACE, A.c.retracted_at.is_not(None), crossing)
        )
        return bool((await self._db.execute(stmt)).scalar())

    async def recent_counterparts(self, handle_id: UUID, since: datetime) -> list[HandleRecord]:
        other = case((A.c.handle_a == handle_id, A.c.handle_b), else_=A.c.handle_a)
        partners = select(other).where(
            A.c.space_id == CURRENT_SPACE,
            _touching([handle_id]),
            A.c.retracted_at.is_(None),
            A.c.created_at >= since,
        )
        stmt = (
            select(H)
            .where(H.c.space_id == CURRENT_SPACE, H.c.handle_id.in_(partners))
            .order_by(H.c.handle_id)
        )
        return [await self._handle(row) for row in await self._db.execute(stmt)]

    async def list_assertions(
        self,
        handle_ids: Sequence[UUID] | None,
        state: AssertionState | None,
        after: UUID | None,
        limit: int,
    ) -> list[AssertionRecord]:
        stmt = select(A).where(A.c.space_id == CURRENT_SPACE)
        if handle_ids is not None:
            stmt = stmt.where(_touching(handle_ids))
        if state is not None:
            stmt = stmt.where(A.c.state == state.value)
        if after is not None:
            stmt = stmt.where(A.c.assertion_id > after)
        rows = await self._db.execute(stmt.order_by(A.c.assertion_id).limit(limit))
        return [_assertion(row) for row in rows]

    async def set_assertion_state(
        self, assertion_ids: Sequence[UUID], state: AssertionState, note: str | None
    ) -> None:
        stmt = (
            update(A)
            .where(A.c.space_id == CURRENT_SPACE, A.c.assertion_id.in_(list(assertion_ids)))
            .values(state=state.value, reason=func.coalesce(A.c.reason, note))
        )
        await self._db.execute(stmt)

    async def retract_assertions(self, assertion_ids: Sequence[UUID], at: datetime) -> None:
        if not assertion_ids:
            return
        stmt = (
            update(A)
            .where(
                A.c.space_id == CURRENT_SPACE,
                A.c.assertion_id.in_(list(assertion_ids)),
                A.c.retracted_at.is_(None),
            )
            .values(retracted_at=at)
        )
        await self._db.execute(stmt)

    # Merge log.

    async def add_merge_log(self, entry: MergeLogEntry) -> bool:
        stmt = (
            pg_insert(ML)
            .values(
                space_id=CURRENT_SPACE,
                merge_id=entry.merge_id,
                op=entry.op.value,
                from_profiles=list(entry.from_profiles),
                to_profiles=list(entry.to_profiles),
                assertion_id=entry.assertion_id,
                handles_moved=list(entry.handles_moved),
                actor=entry.actor,
                reason=entry.reason,
                created_at=entry.created_at,
                reversed_at=entry.reversed_at,
            )
            .on_conflict_do_nothing()
            .returning(ML.c.merge_id)
        )
        return (await self._db.execute(stmt)).first() is not None

    async def merge_logged(self, merge_id: UUID) -> bool:
        stmt = select(exists().where(ML.c.space_id == CURRENT_SPACE, ML.c.merge_id == merge_id))
        return bool((await self._db.execute(stmt)).scalar())

    async def merges_by_assertions(self, assertion_ids: Sequence[UUID]) -> list[MergeLogEntry]:
        if not assertion_ids:
            return []
        stmt = select(ML).where(
            ML.c.space_id == CURRENT_SPACE,
            ML.c.op == MergeOp.MERGE.value,
            ML.c.assertion_id.in_(list(assertion_ids)),
            ML.c.reversed_at.is_(None),
        )
        return [_merge(row) for row in await self._db.execute(stmt.order_by(ML.c.merge_id))]

    async def mark_reversed(self, merge_ids: Sequence[UUID], at: datetime) -> None:
        if not merge_ids:
            return
        stmt = (
            update(ML)
            .where(ML.c.space_id == CURRENT_SPACE, ML.c.merge_id.in_(list(merge_ids)))
            .values(reversed_at=at)
        )
        await self._db.execute(stmt)

    # Links and suggestions.

    async def add_link(
        self, link: LinkRecord, *, method: AssertionMethod, origin_event_id: UUID | None, confidence: float
    ) -> None:
        stmt = insert(L).values(
            space_id=CURRENT_SPACE,
            link_id=link.link_id,
            person_handle_id=link.person_handle_id,
            org_handle_id=link.org_handle_id,
            role=link.role,
            can_see_contacts=link.can_see_contacts,
            valid_from=link.valid_from,
            valid_to=link.valid_to,
            method=method.value,
            origin_event_id=origin_event_id,
            confidence=confidence,
        )
        await self._db.execute(stmt)

    async def get_link(self, link_id: UUID) -> LinkRecord | None:
        row = (
            await self._db.execute(select(L).where(L.c.space_id == CURRENT_SPACE, L.c.link_id == link_id))
        ).first()
        return _link(row) if row else None

    async def end_link(self, link_id: UUID, at: datetime) -> None:
        await self._db.execute(
            update(L).where(L.c.space_id == CURRENT_SPACE, L.c.link_id == link_id).values(valid_to=at)
        )

    async def add_suggestion(self, suggestion: SuggestionRecord) -> bool:
        pair = or_(
            and_(S.c.handle_a == suggestion.handle_a, S.c.handle_b == suggestion.handle_b),
            and_(S.c.handle_a == suggestion.handle_b, S.c.handle_b == suggestion.handle_a),
        )
        taken = select(exists().where(S.c.space_id == CURRENT_SPACE, pair, S.c.status.in_(_UNSETTLED)))
        if (await self._db.execute(taken)).scalar():
            return False
        stmt = insert(S).values(
            space_id=CURRENT_SPACE,
            suggestion_id=suggestion.suggestion_id,
            handle_a=suggestion.handle_a,
            handle_b=suggestion.handle_b,
            score=suggestion.score,
            features=suggestion.features,
            status=suggestion.status,
            created_at=suggestion.created_at,
        )
        await self._db.execute(stmt)
        return True

    async def unsettled_suggestion_pairs(self) -> list[tuple[UUID, UUID]]:
        stmt = select(S.c.handle_a, S.c.handle_b).where(
            S.c.space_id == CURRENT_SPACE, S.c.status.in_(_UNSETTLED)
        )
        return [(row.handle_a, row.handle_b) for row in await self._db.execute(stmt)]

    async def open_suggestions(self, *, after: UUID | None, limit: int) -> list[SuggestionRecord]:
        stmt = select(S).where(S.c.space_id == CURRENT_SPACE, S.c.status == "open")
        if after is not None:
            stmt = stmt.where(S.c.suggestion_id > after)
        rows = await self._db.execute(stmt.order_by(S.c.suggestion_id).limit(limit))
        return [_suggestion(r) for r in rows]

    async def get_suggestion(self, suggestion_id: UUID) -> SuggestionRecord | None:
        stmt = select(S).where(S.c.space_id == CURRENT_SPACE, S.c.suggestion_id == suggestion_id)
        row = (await self._db.execute(stmt)).first()
        return _suggestion(row) if row else None

    async def set_suggestion_status(self, suggestion_id: UUID, status: str) -> None:
        await self._db.execute(
            update(S)
            .where(S.c.space_id == CURRENT_SPACE, S.c.suggestion_id == suggestion_id)
            .values(status=status)
        )

    # Profiles by pseudonym, privacy versions, erasure and export.

    async def find_profile_by_pseudonym(self, pseudonym: str) -> ProfileRecord | None:
        stmt = select(P.c.profile_id).where(
            P.c.space_id == CURRENT_SPACE, P.c.pseudonym == pseudonym, P.c.retired_at.is_(None)
        )
        profile_id = (await self._db.execute(stmt)).scalar_one_or_none()
        return await self.get_profile(profile_id) if profile_id else None

    async def list_profiles(
        self,
        *,
        kind: SubjectKind | None,
        after: ListCursor | None,
        ascending: bool,
        limit: int,
        handles_shown: int,
    ) -> tuple[list[ListedProfile], ListCursor | None]:
        # Two statements per window, each over a bounded set: a window of `ix_handles_last_seen`, then
        # the handles of the profiles it met. A profile stands on its latest handle, so a handle with a
        # later sibling is passed over; a customer with many identifiers can use up a window, and the
        # page then ends short and goes on from its cursor.
        window = max(2 * limit, LIST_MIN_WINDOW)
        listed: list[ListedProfile] = []
        position = after
        for _ in range(LIST_WINDOWS):
            walked = await self._walk({kind} if kind else set(), position, ascending, window)
            members = await self._members({row.profile_id for row in walked}, handles_shown)
            for index, row in enumerate(walked):
                position = ListCursor(row.last_seen, row.handle_id)
                handles, total = members.get(row.profile_id, ((), 0))
                if not handles or handles[0].handle_id != row.handle_id:
                    continue
                listed.append(
                    ListedProfile(
                        row.profile_id, SubjectKind(row.kind), row.pseudonym, row.last_seen, handles, total
                    )
                )
                if len(listed) == limit:
                    ended = len(walked) < window and index == len(walked) - 1
                    return listed, None if ended else position
            if len(walked) < window:
                return listed, None
        return listed, position

    async def recent_profiles(self, kinds: Collection[SubjectKind], window: int) -> list[UUID]:
        walked = await self._walk(set(kinds), None, False, window)
        return list(dict.fromkeys(row.profile_id for row in walked))

    async def _walk(
        self, kinds: Collection[SubjectKind], after: ListCursor | None, ascending: bool, window: int
    ) -> Sequence[Row[Any]]:
        position = tuple_(H.c.last_seen, H.c.handle_id)
        order = (H.c.last_seen, H.c.handle_id) if ascending else (H.c.last_seen.desc(), H.c.handle_id.desc())
        ahead = select(H.c.handle_id, H.c.last_seen).where(H.c.space_id == CURRENT_SPACE)
        if kinds:
            # A profile never mixes kinds (5.3), so the handle's kind, a key of the index, is the profile's.
            ahead = ahead.where(H.c.subject_kind.in_(sorted(k.value for k in kinds)))
        if after is not None:
            edge = tuple_(literal(after.last_seen), literal(after.handle_id))
            ahead = ahead.where(position > edge if ascending else position < edge)
        # The limit sits on the handles alone, and each handle then looks up its profile by primary key:
        # LATERAL with LIMIT fixes that order, so the plan is the index walk whatever the planner
        # believes of the tables (a space not analyzed yet looks empty to it).
        near = ahead.order_by(*order).limit(window).subquery("near")
        member = (
            select(PM.c.profile_id)
            .where(PM.c.space_id == CURRENT_SPACE, PM.c.handle_id == near.c.handle_id)
            .limit(1)
            .lateral("member")
        )
        owner = (
            select(P.c.kind, P.c.pseudonym)
            .where(
                P.c.space_id == CURRENT_SPACE, P.c.profile_id == member.c.profile_id, P.c.retired_at.is_(None)
            )
            .limit(1)
            .lateral("owner")
        )
        stmt = select(
            near.c.handle_id, near.c.last_seen, member.c.profile_id, owner.c.kind, owner.c.pseudonym
        ).select_from(near.join(member, true()).join(owner, true()))
        rows = (await self._db.execute(stmt)).all()
        return sorted(rows, key=lambda row: (row.last_seen, row.handle_id), reverse=not ascending)

    async def _members(
        self, profile_ids: set[UUID], shown: int
    ) -> dict[UUID, tuple[tuple[HandleRecord, ...], int]]:
        """Each profile's `shown` latest handles, the latest first, and how many it has."""
        if not profile_ids:
            return {}
        # Each membership row looks up its handle by primary key, in that order, as the walk does.
        handle = (
            select(*(c for c in H.c if c.name != "space_id"))
            .where(H.c.space_id == CURRENT_SPACE, H.c.handle_id == PM.c.handle_id)
            .limit(1)
            .lateral("handle")
        )
        rank = func.row_number().over(
            partition_by=PM.c.profile_id, order_by=(handle.c.last_seen.desc(), handle.c.handle_id.desc())
        )
        ranked = (
            select(
                PM.c.profile_id,
                handle,
                rank.label("rank"),
                func.count().over(partition_by=PM.c.profile_id).label("total"),
            )
            .select_from(PM.join(handle, true()))
            .where(PM.c.space_id == CURRENT_SPACE, PM.c.profile_id.in_(list(profile_ids)))
            .subquery()
        )
        latest = select(ranked).where(ranked.c.rank <= shown).order_by(ranked.c.profile_id, ranked.c.rank)
        handles: dict[UUID, list[HandleRecord]] = {}
        totals: dict[UUID, int] = {}
        for row in await self._db.execute(latest):
            handles.setdefault(row.profile_id, []).append(await self._handle(row))
            totals[row.profile_id] = row.total
        return {profile_id: (tuple(found), totals[profile_id]) for profile_id, found in handles.items()}

    async def pseudonyms(self, profile_ids: Sequence[UUID]) -> dict[UUID, str]:
        if not profile_ids:
            return {}
        stmt = select(P.c.profile_id, P.c.pseudonym).where(
            P.c.space_id == CURRENT_SPACE, P.c.profile_id.in_(list(profile_ids))
        )
        return {row.profile_id: row.pseudonym for row in await self._db.execute(stmt)}

    async def bump_privacy_version(self, profile_id: UUID) -> int:
        stmt = (
            update(P)
            .where(P.c.space_id == CURRENT_SPACE, P.c.profile_id == profile_id)
            .values(privacy_version=P.c.privacy_version + 1)
            .returning(P.c.privacy_version)
        )
        version = (await self._db.execute(stmt)).scalar_one_or_none()
        return int(version) if version is not None else 0

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if not erases_identity(scope):
            return []
        profile_id = scope.profile_id if scope.kind is TargetKind.PROFILE else None
        handles = await self._target_handles(scope, profile_id)
        steps: list[tuple[Table, str, ColumnElement[bool] | None]] = [
            (A, "assertion_id", _touching(handles) if handles else None),
            (
                L,
                "link_id",
                or_(L.c.person_handle_id.in_(handles), L.c.org_handle_id.in_(handles)) if handles else None,
            ),
            (
                S,
                "suggestion_id",
                or_(S.c.handle_a.in_(handles), S.c.handle_b.in_(handles)) if handles else None,
            ),
            (ML, "merge_id", _merge_touches(profile_id, handles) if profile_id else None),
            (PM, "handle_id", PM.c.handle_id.in_(handles) if handles else None),
            (H, "handle_id", H.c.handle_id.in_(handles) if handles else None),
        ]
        rows: list[ErasedRow] = []
        for table, key, where in steps:
            if where is not None and len(rows) < limit:
                rows += await self._delete(table, key, where, limit - len(rows))
        if len(rows) < limit and profile_id is not None:
            rows += await self._tombstone(profile_id)
        return rows

    async def export_rows(
        self, table: str, *, after_seq: int, upto_seq: int, limit: int
    ) -> list[dict[str, Any]]:
        source = _EXPORTED.get(table)
        if source is None:
            raise ValueError(f"identity does not export {table}")
        stmt = (
            select(source)
            .where(
                source.c.space_id == CURRENT_SPACE,
                source.c.change_seq > after_seq,
                source.c.change_seq <= upto_seq,
            )
            .order_by(source.c.change_seq)
            .limit(limit)
        )
        return [await self._export_row(table, row) for row in await self._db.execute(stmt)]

    async def _target_handles(self, scope: ErasureScope, profile_id: UUID | None) -> list[UUID]:
        """The scope's handles, plus any that joined the profile after the request was accepted."""
        handles = set(scope.handle_ids)
        if profile_id is not None:
            members = select(PM.c.handle_id).where(
                PM.c.space_id == CURRENT_SPACE, PM.c.profile_id == profile_id
            )
            handles |= set((await self._db.execute(members)).scalars())
        return sorted(handles)

    async def _delete(
        self, table: Table, key: str, where: ColumnElement[bool], limit: int
    ) -> list[ErasedRow]:
        column = table.c[key]
        doomed = select(column).where(table.c.space_id == CURRENT_SPACE, where).limit(limit).scalar_subquery()
        seq = table.c.change_seq if "change_seq" in table.c else literal(None)
        stmt = (
            delete(table)
            .where(table.c.space_id == CURRENT_SPACE, column.in_(doomed))
            .returning(column, seq.label("change_seq"))
        )
        return [
            ErasedRow(table.name, {key: str(row[0])}, (), row.change_seq)
            for row in await self._db.execute(stmt)
        ]

    async def _tombstone(self, profile_id: UUID) -> list[ErasedRow]:
        """The profile keeps its id, kind, pseudonym and versions, so the feed keeps counting (5.11)."""
        live = (
            select(P.c.change_seq)
            .where(P.c.space_id == CURRENT_SPACE, P.c.profile_id == profile_id, P.c.erased_at.is_(None))
            .with_for_update()
        )
        seq = (await self._db.execute(live)).scalar_one_or_none()
        if seq is None:
            return []
        await self._db.execute(
            update(P)
            .where(P.c.space_id == CURRENT_SPACE, P.c.profile_id == profile_id)
            .values(
                erased_at=func.now(),
                retired_at=func.coalesce(P.c.retired_at, func.now()),
                org_type=None,
            )
        )
        return [ErasedRow("profiles", {"profile_id": str(profile_id)}, (), seq)]

    async def _export_row(self, table: str, row: Row[Any]) -> dict[str, Any]:
        match table:
            case "handles":
                return handle_row(await self._handle(row), row.value_hmac, row.change_seq)
            case "profile_membership":
                return membership_row(row.handle_id, row.profile_id, row.version, row.change_seq)
            case "profiles":
                return profile_row(
                    _profile(row),
                    created_at=row.created_at,
                    retired_at=row.retired_at,
                    erased=row.erased_at is not None,
                    change_seq=row.change_seq,
                )
            case "profile_links":
                return link_row(
                    _link(row),
                    method=AssertionMethod(row.method),
                    origin_event_id=row.origin_event_id,
                    confidence=row.confidence,
                    change_seq=row.change_seq,
                )
            case "assertions":
                return assertion_row(_assertion(row), row.change_seq)
            case _:
                return merge_row(_merge(row), row.change_seq)

    # Internals.

    def _seal(self) -> Sealer:
        if self._sealer is None:
            raise RuntimeError("identity needs a space-scoped transaction")
        return self._sealer

    async def _hmac(self, type: HandleType, value: str) -> bytes:
        return await self._seal().hmac(f"handle:{type.value}", value)

    async def _handle(self, row: Row[Any]) -> HandleRecord:
        value = await self._seal().open_text(
            row.value_ct, row.dek_version, table="handles", row=row.handle_id, column="value_ct"
        )
        return HandleRecord(
            handle_id=row.handle_id,
            type=HandleType(row.type),
            scope=row.scope,
            subject_kind=SubjectKind(row.subject_kind),
            value=value,
            status=HandleStatus(row.status),
            level=Verification(row.level),
            first_seen=row.first_seen,
            last_seen=row.last_seen,
            provisional=row.provisional,
        )

    async def _update_handles(self, handle_ids: Sequence[UUID], **values: Any) -> None:
        if handle_ids:
            stmt = (
                update(H)
                .where(H.c.space_id == CURRENT_SPACE, H.c.handle_id.in_(list(handle_ids)))
                .values(**values)
            )
            await self._db.execute(stmt)


def _touching(handle_ids: Sequence[UUID]) -> Any:
    ids = list(handle_ids)
    return or_(A.c.handle_a.in_(ids), A.c.handle_b.in_(ids))


def _merge_touches(profile_id: UUID, handle_ids: Sequence[UUID]) -> ColumnElement[bool]:
    """SQL twin of `merge_touches`: the profile on either side, or one of its handles moved."""
    return or_(
        ML.c.from_profiles.any(profile_id),
        ML.c.to_profiles.any(profile_id),
        ML.c.handles_moved.overlap(list(handle_ids)),
    )


def _suggestion(row: Row[Any]) -> SuggestionRecord:
    return SuggestionRecord(
        row.suggestion_id, row.handle_a, row.handle_b, row.score, row.created_at, row.features, row.status
    )


def _profile(row: Row[Any], handle_ids: tuple[UUID, ...] = ()) -> ProfileRecord:
    return ProfileRecord(
        profile_id=row.profile_id,
        kind=SubjectKind(row.kind),
        pseudonym=row.pseudonym,
        membership_version=row.membership_version,
        privacy_version=row.privacy_version,
        handle_ids=handle_ids,
        org_type=row.org_type,
    )


def _link(row: Row[Any]) -> LinkRecord:
    return LinkRecord(
        link_id=row.link_id,
        person_handle_id=row.person_handle_id,
        org_handle_id=row.org_handle_id,
        role=row.role,
        can_see_contacts=row.can_see_contacts,
        valid_from=row.valid_from,
        valid_to=row.valid_to,
    )


def _assertion(row: Row[Any]) -> AssertionRecord:
    return AssertionRecord(
        assertion_id=row.assertion_id,
        handle_a=row.handle_a,
        handle_b=row.handle_b,
        method=AssertionMethod(row.method),
        confidence=row.confidence,
        level_at_capture=Verification(row.level_at_capture),
        state=AssertionState(row.state),
        created_at=row.created_at,
        origin_event_id=row.origin_event_id,
        actor=row.actor,
        reason=row.reason,
        retracted_at=row.retracted_at,
    )


def _merge(row: Row[Any]) -> MergeLogEntry:
    return MergeLogEntry(
        merge_id=row.merge_id,
        op=MergeOp(row.op),
        from_profiles=tuple(row.from_profiles),
        to_profiles=tuple(row.to_profiles),
        handles_moved=tuple(row.handles_moved),
        actor=row.actor,
        created_at=row.created_at,
        assertion_id=row.assertion_id,
        reason=row.reason,
        reversed_at=row.reversed_at,
    )
