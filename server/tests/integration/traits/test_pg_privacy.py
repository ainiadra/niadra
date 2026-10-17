"""`PgTraitRepository` in the erasure cascade (8.4, L17) and the continuous export (22.3)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.ids import uuid7
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.records import Trait
from niadra.domain.traits.evidence import EvidenceRef

T0 = datetime(2026, 9, 22, 17, tzinfo=UTC)


def trait(profile: UUID, name: str) -> Trait:
    return Trait(uuid7(), profile, 1, name, "true", "stable", "r1", T0, T0, T0 + timedelta(days=90), 0.8)


async def test_erased_events_take_evidence_and_traits_left_without_any(pg_uow: PgUnitOfWorkFactory) -> None:
    space, profile, h = uuid7(), uuid7(), uuid7()
    e1, episode = uuid7(), uuid7()
    only, both = trait(profile, "prefers_whatsapp"), trait(profile, "recurring_complaint")
    async with pg_uow(space) as uow:
        await uow.traits.save_trait(only, [EvidenceRef("event", e1, h)])
        await uow.traits.save_trait(both, [EvidenceRef("event", e1, h), EvidenceRef("episode", episode, h)])
    scope = ErasureScope(uuid7(), TargetKind.CONVERSATION, (h,), profile, "c1", T0, (e1,))
    async with pg_uow(space) as uow:
        rows = await uow.traits.erase_for(scope, limit=10)
        assert sorted(r.table for r in rows) == ["trait_evidence", "trait_evidence", "traits"]
        assert all(r.change_seq for r in rows)
        assert await uow.traits.erase_for(scope, limit=10) == []
        [left] = await uow.traits.traits_of(profile)
        assert (left.trait_id, left.evidence) == (both.trait_id, (("episode", episode),))


async def test_a_profile_erasure_takes_its_traits_foreign_evidence_and_opt_outs(
    pg_uow: PgUnitOfWorkFactory,
) -> None:
    space, profile, h = uuid7(), uuid7(), uuid7()
    other_profile, other_h, theirs = uuid7(), uuid7(), uuid7()
    shared = trait(other_profile, "late_payer")
    async with pg_uow(space) as uow:
        await uow.traits.save_trait(trait(profile, "prefers_whatsapp"), [EvidenceRef("event", uuid7(), h)])
        await uow.traits.save_trait(
            shared, [EvidenceRef("event", uuid7(), h), EvidenceRef("event", theirs, other_h)]
        )
        await uow.traits.opt_out([h], T0)
    scope = ErasureScope(uuid7(), TargetKind.PROFILE, (h,), profile, None, T0)
    tables: list[str] = []
    while True:
        async with pg_uow(space) as uow:
            batch = await uow.traits.erase_for(scope, limit=2)
        tables += [r.table for r in batch]
        if len(batch) < 2:
            break
    assert sorted(set(tables)) == ["trait_evidence", "trait_opt_outs", "traits"]
    async with pg_uow(space) as uow:
        assert await uow.traits.traits_of(profile) == []
        [kept] = await uow.traits.traits_of(other_profile)
        assert kept.evidence == (("event", theirs),)
        assert not await uow.traits.opted_out([h])


async def test_export_reads_traits_and_evidence_by_change_seq(pg_uow: PgUnitOfWorkFactory) -> None:
    space, profile, h = uuid7(), uuid7(), uuid7()
    t = trait(profile, "prefers_whatsapp")
    async with pg_uow(space) as uow:
        await uow.traits.save_trait(t, [EvidenceRef("event", uuid7(), h)])
        [row] = await uow.traits.export_rows("traits", after_seq=0, upto_seq=2**62, limit=10)
        [evidence] = await uow.traits.export_rows("trait_evidence", after_seq=0, upto_seq=2**62, limit=10)
    assert (row["trait_id"], row["value"], "evidence" in row) == (t.trait_id, "true", False)
    assert (evidence["trait_id"], evidence["evidence_kind"]) == (t.trait_id, "event")
    assert row["change_seq"] < evidence["change_seq"]
