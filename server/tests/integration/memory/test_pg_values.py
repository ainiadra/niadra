"""`PgMemoryRepository` for settled values (11, 3.2): sealed, partitioned by handle, found by the HMAC
of their key, and gone with their events, conversations, handles and age."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.ids import uuid7
from niadra.domain.memory.values import SettledValue, ValueOrigin, ValueType, value_key
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind
from niadra.domain.records import ObjectKey
from niadra.domain.vocabulary import Speaker

T0 = datetime(2026, 9, 22, 17, tzinfo=UTC)
SOURCE = UUID("0192f5a0-0000-7000-8000-00000000c001")


def value(
    origin: UUID,
    raw: str,
    type: ValueType = ValueType.POSTAL_CODE,
    *,
    at: datetime = T0,
    conversation_id: str = "c1",
    evidence: tuple[UUID, ...] = (),
) -> SettledValue:
    return SettledValue(
        value_id=uuid7(),
        origin_handle_id=origin,
        type=type,
        raw=raw,
        said_at=at,
        speaker=Speaker.CUSTOMER,
        session_id=uuid7(),
        channel="email",
        source_id=SOURCE,
        created_at=T0,
        evidence_event_ids=evidence,
        conversation_id=conversation_id,
        episode_id=uuid7(),
        object_ref=ObjectKey("claim", "erp", "21889"),
        origin=ValueOrigin.EXTRACTION,
    )


async def erase_all(
    pg_uow: PgUnitOfWorkFactory, space: UUID, scope: ErasureScope, limit: int = 2
) -> list[ErasedRow]:
    rows: list[ErasedRow] = []
    while True:
        async with pg_uow(space) as uow:
            batch = await uow.memory.erase_for(scope, limit=limit)
        rows += batch
        if len(batch) < limit:
            return rows


async def test_values_are_sealed_partitioned_and_found_by_key(pg_uow: PgUnitOfWorkFactory) -> None:
    space, marina = uuid7(), uuid7()
    cep = value(marina, "45778-204")
    protocol = value(marina, "81220", ValueType.PROTOCOL, at=T0 - timedelta(days=3))
    async with pg_uow(space) as uow:
        assert await uow.memory.insert_values([cep, protocol]) == 2
        assert await uow.memory.insert_values([cep]) == 0
    async with pg_uow(space) as uow:
        assert await uow.memory.values_for([marina]) == [cep, protocol]
        assert await uow.memory.values_for([marina], types=[ValueType.PROTOCOL]) == [protocol]
        assert await uow.memory.values_for([marina], since=T0 - timedelta(days=1)) == [cep]
        assert await uow.memory.values_by_keys([marina], [value_key("45778204")]) == [cep]
        assert await uow.memory.values_by_keys([uuid7()], [value_key("45778204")]) == []
        raw = await uow.session.execute(
            text("SELECT tableoid::regclass::text, raw_ct FROM settled_values WHERE value_id = :v"),
            {"v": cep.value_id},
        )
        partition, ciphertext = raw.one()
    assert partition.startswith("settled_values_p")
    assert b"45778" not in ciphertext


async def test_values_go_with_their_events_conversations_handles_and_age(pg_uow: PgUnitOfWorkFactory) -> None:
    space, marina, e1 = uuid7(), uuid7(), uuid7()
    by_event = value(marina, "81220", ValueType.PROTOCOL, evidence=(e1, uuid7()))
    by_conversation = value(marina, "45778-204", conversation_id="c2")
    old = value(marina, "11111", ValueType.CODE, conversation_id="c3", at=T0 - timedelta(days=400))
    kept = value(marina, "22222", ValueType.CODE, conversation_id="c3")
    async with pg_uow(space) as uow:
        assert await uow.memory.insert_values([by_event, by_conversation, old, kept]) == 4

    scope = ErasureScope(uuid7(), TargetKind.CONVERSATION, (marina,), None, "c1", T0, event_ids=(e1,))
    assert [r.key["value_id"] for r in await erase_all(pg_uow, space, scope)] == [str(by_event.value_id)]
    scope = ErasureScope(uuid7(), TargetKind.CONVERSATION, (marina,), None, "c2", T0)
    assert [r.table for r in await erase_all(pg_uow, space, scope)] == ["settled_values"]
    async with pg_uow(space) as uow:
        expired = await uow.memory.expire_episodes(T0 - timedelta(days=365), limit=10)
        assert [r.key for r in expired] == [{"origin_handle_id": str(marina), "value_id": str(old.value_id)}]
        assert await uow.memory.values_for([marina]) == [kept]
    scope = ErasureScope(uuid7(), TargetKind.HANDLE, (marina,), None, None, T0)
    assert [r.table for r in await erase_all(pg_uow, space, scope)] == ["settled_values"]
    async with pg_uow(space) as uow:
        assert await uow.memory.values_for([marina]) == []
