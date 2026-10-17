"""The configuration's history on PostgreSQL (24.6.5): one statement per applied snapshot, a row only for a
section whose digest moved, read back newest first for the change log."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from tests.integration.conftest import CloningUnitOfWork
from tests.integration.turns.test_pg_turns import counting

NOW = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)


async def test_a_section_enters_the_history_only_when_its_digest_moves(pg_uow: CloningUnitOfWork) -> None:
    space = uuid4()
    await pg_uow.ensure(space)
    async with pg_uow(space) as uow:
        with counting() as statements:
            await uow.turns.record_config({"recording": b"\x01", "policy": b"\x02"}, 1, NOW)
        await uow.turns.record_config({"recording": b"\x01", "policy": b"\x03"}, 2, NOW + timedelta(hours=1))
        await uow.turns.record_config({"recording": b"\x01", "policy": b"\x03"}, 3, NOW + timedelta(hours=2))
        await uow.turns.record_config({"recording": b"\x04", "policy": b"\x03"}, 3, NOW + timedelta(hours=2))
        history = await uow.turns.config_history(10)
    assert len(statements) == 1
    assert [(h.section, h.version, h.digest) for h in history] == [
        ("recording", 3, b"\x04"),
        ("policy", 2, b"\x03"),
        ("policy", 1, b"\x02"),
        ("recording", 1, b"\x01"),
    ]
