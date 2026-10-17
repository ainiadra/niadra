"""`PgSessionRepository.erase_for`: the sessions of a forgotten profile or conversation (8.4)."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from tests.integration.sessions.test_pg_sessions import NOW, _session


async def test_sessions_go_by_profile_or_by_conversation(pg_uow: PgUnitOfWorkFactory) -> None:
    space, profile = uuid4(), uuid4()
    async with pg_uow(space) as uow:
        for conversation, owner in (("c-1", profile), ("c-2", profile), ("c-3", uuid4())):
            await uow.sessions.create_session(space, replace(_session(conversation), profile_id=owner))

    def scope(kind: TargetKind, conversation: str | None = None) -> ErasureScope:
        return ErasureScope(uuid4(), kind, (uuid4(),), profile, conversation, NOW)

    async with pg_uow(space) as uow:
        by_conversation = await uow.sessions.erase_for(scope(TargetKind.CONVERSATION, "c-3"), limit=10)
        by_handle = await uow.sessions.erase_for(scope(TargetKind.HANDLE), limit=10)
        first = await uow.sessions.erase_for(scope(TargetKind.PROFILE), limit=1)
        rest = await uow.sessions.erase_for(scope(TargetKind.PROFILE), limit=10)
        left = [await uow.sessions.open_session(conversation_id=c) for c in ("c-1", "c-2", "c-3")]

    assert [r.table for r in by_conversation] == ["sessions"]
    assert all(r.change_seq for r in by_conversation + first + rest)
    assert by_handle == []
    assert (len(first), len(rest)) == (1, 1)
    assert left == [None, None, None]


async def test_the_pending_end_of_a_forgotten_conversation_goes_with_it(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()
    async with pg_uow(space) as uow:
        for conversation in ("c-1", "c-2"):
            await uow.sessions.save_pending_end(
                space, conversation_id=conversation, ended_at=NOW, expires_at=NOW + timedelta(days=1)
            )
        scope = ErasureScope(uuid4(), TargetKind.CONVERSATION, (), None, "c-1", NOW)
        erased = await uow.sessions.erase_for(scope, limit=10)
        again = await uow.sessions.erase_for(scope, limit=10)
        left = [await uow.sessions.pending_end(conversation_id=c, now=NOW) for c in ("c-1", "c-2")]
    assert [(r.table, r.key) for r in erased] == [
        ("session_ends", {"kind": "conversation", "thread_id": "c-1"})
    ]
    assert again == []
    assert left == [None, NOW]
