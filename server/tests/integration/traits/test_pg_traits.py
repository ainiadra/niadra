"""`PgTraitRepository` against a real PostgreSQL 17."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.ids import uuid7
from niadra.domain.records import Trait
from niadra.domain.traits.evidence import EvidenceRef

# `active_for` compares with the database clock, so expiries are relative to the real now.
NOW = datetime.now(UTC).replace(microsecond=0)


def _trait(
    profile: UUID, name: str = "recurring_complaint", expires_in: timedelta = timedelta(days=40)
) -> Trait:
    return Trait(
        uuid7(), profile, 1, name, "technical_visit", "volatile", "v3", NOW, NOW, NOW + expires_in, 0.9
    )


async def test_save_replace_and_read_traits_with_evidence(pg_uow: PgUnitOfWorkFactory) -> None:
    space, profile, h1, h2 = uuid7(), uuid7(), uuid7(), uuid7()
    first = _trait(profile)
    ev = [
        EvidenceRef("episode", uuid7(), h1),
        EvidenceRef("episode", uuid7(), h2),
        EvidenceRef("event", uuid7(), h1),
    ]
    async with pg_uow(space) as uow:
        await uow.traits.save_trait(first, ev)
        raw = (
            await uow.session.execute(
                text("SELECT value_ct FROM traits WHERE trait_id = :t"), {"t": first.trait_id}
            )
        ).scalar_one()
        assert b"technical_visit" not in raw
    async with pg_uow(space) as uow:
        [stored] = await uow.traits.traits_of(profile)
        assert stored.value == "technical_visit"
        assert set(stored.evidence) == {(e.kind, e.id) for e in ev}
        # a new derivation for the same (profile, name) replaces the row in place, evidence included
        await uow.traits.save_trait(
            replace(first, trait_id=uuid7(), value="invoice", membership_version=2), ev[:1]
        )
    async with pg_uow(space) as uow:
        [replaced] = await uow.traits.traits_of(profile)
        assert replaced.trait_id == first.trait_id
        assert replaced.value == "invoice"
        assert replaced.membership_version == 2
        assert (await uow.traits.evidence_of([first.trait_id]))[first.trait_id] == (ev[0],)
        assert [t.trait_id for t in await uow.traits.traits_with_evidence_from([h1])] == [first.trait_id]
        assert await uow.traits.traits_with_evidence_from([h2]) == []
        assert await uow.traits.get_trait(first.trait_id) == replaced


async def test_active_expiring_retracted_and_deleted(pg_uow: PgUnitOfWorkFactory) -> None:
    space, profile = uuid7(), uuid7()
    live = _trait(profile, "a")
    expired = _trait(profile, "b", expires_in=timedelta(days=-1))
    retracted = _trait(profile, "c")
    ev = [EvidenceRef("episode", uuid7(), uuid7())]
    async with pg_uow(space) as uow:
        for t in (live, expired, retracted):
            await uow.traits.save_trait(t, ev)
        await uow.traits.retract(retracted.trait_id, NOW)
    async with pg_uow(space) as uow:
        assert [t.name for t in await uow.traits.active_for(profile)] == ["a"]
        assert [t.name for t in await uow.traits.expiring(NOW)] == ["b"]
        assert [t.name for t in await uow.traits.traits_of(profile)] == ["a", "b", "c"]
        await uow.traits.delete_trait(expired.trait_id)
    async with pg_uow(space) as uow:
        assert [t.name for t in await uow.traits.traits_of(profile)] == ["a", "c"]
        assert await uow.traits.evidence_of([expired.trait_id]) == {}
    async with pg_uow(uuid7()) as uow:
        assert await uow.traits.traits_of(profile) == []


async def test_opt_out_is_kept_per_handle(pg_uow: PgUnitOfWorkFactory) -> None:
    space, h1, h2 = uuid7(), uuid7(), uuid7()
    async with pg_uow(space) as uow:
        assert not await uow.traits.opted_out([h1, h2])
        await uow.traits.opt_out([h1], NOW)
        await uow.traits.opt_out([h1], NOW)
    async with pg_uow(space) as uow:
        assert await uow.traits.opted_out([h2, h1])
        assert not await uow.traits.opted_out([h2])
        # The access package lists the objection itself.
        assert await uow.traits.opt_outs([h2, h1]) == [(h1, NOW)]
        assert await uow.traits.opt_outs([]) == []
    async with pg_uow(uuid7()) as uow:
        assert not await uow.traits.opted_out([h1])
