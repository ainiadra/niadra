"""Traits in the erasure cascade (8.4, L17) and the continuous export (22.3)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from niadra.adapters.memory.store import MemoryStore
from niadra.adapters.memory.traits import InMemoryTraitRepository
from niadra.domain.common.ids import uuid7
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.records import Trait
from niadra.domain.traits.evidence import EvidenceRef

T0 = datetime(2026, 9, 22, 14, tzinfo=UTC)


def trait(profile: UUID, name: str) -> Trait:
    return Trait(
        trait_id=uuid7(),
        profile_id=profile,
        membership_version=1,
        name=name,
        value="true",
        layer="stable",
        rule_version="r1",
        first_seen=T0,
        last_evidence_at=T0,
        expires_at=T0 + timedelta(days=90),
        confidence=0.8,
    )


def scope(kind: TargetKind, handles: tuple[UUID, ...], profile: UUID | None, events: tuple[UUID, ...] = ()):
    return ErasureScope(uuid7(), kind, handles, profile, None, T0, event_ids=events)


async def test_erased_events_take_their_evidence_and_the_traits_left_without_any() -> None:
    repo = InMemoryTraitRepository(MemoryStore())
    profile, handle = uuid7(), uuid7()
    e1, e2, episode = uuid7(), uuid7(), uuid7()
    only = trait(profile, "prefers_whatsapp")
    both = trait(profile, "recurring_complaint")
    await repo.save_trait(only, [EvidenceRef("event", e1, handle)])
    await repo.save_trait(both, [EvidenceRef("event", e2, handle), EvidenceRef("episode", episode, handle)])
    rows = await repo.erase_for(scope(TargetKind.CONVERSATION, (handle,), profile, (e1, e2)), limit=10)
    assert sorted(r.table for r in rows) == ["trait_evidence", "trait_evidence", "traits"]
    [left] = await repo.traits_of(profile)
    assert (left.trait_id, left.evidence) == (both.trait_id, (("episode", episode),))
    assert await repo.erase_for(scope(TargetKind.CONVERSATION, (handle,), profile, (e1, e2)), limit=10) == []


async def test_a_profile_erasure_takes_its_traits_foreign_evidence_and_opt_outs() -> None:
    repo = InMemoryTraitRepository(MemoryStore())
    profile, handle = uuid7(), uuid7()
    other_profile, other_handle = uuid7(), uuid7()
    await repo.save_trait(trait(profile, "prefers_whatsapp"), [EvidenceRef("event", uuid7(), handle)])
    # After an unmerge, evidence from the erased handle may back another profile's trait (20.2).
    shared, theirs = trait(other_profile, "late_payer"), uuid7()
    await repo.save_trait(
        shared, [EvidenceRef("event", uuid7(), handle), EvidenceRef("event", theirs, other_handle)]
    )
    await repo.opt_out([handle], T0)
    target = scope(TargetKind.PROFILE, (handle,), profile)
    rows = []
    while len(batch := await repo.erase_for(target, limit=2)) == 2:
        rows += batch
    rows += batch
    assert {r.table for r in rows} == {"traits", "trait_evidence", "trait_opt_outs"}
    assert await repo.traits_of(profile) == []
    [kept] = await repo.traits_of(other_profile)
    assert (kept.trait_id, kept.evidence) == (shared.trait_id, (("event", theirs),))
    assert not await repo.opted_out([handle])


async def test_export_rows_carry_the_sequence_and_skip_erased_traits() -> None:
    repo = InMemoryTraitRepository(MemoryStore())
    profile, handle = uuid7(), uuid7()
    t = trait(profile, "prefers_whatsapp")
    await repo.save_trait(t, [EvidenceRef("event", uuid7(), handle)])
    await repo.retract(t.trait_id, T0)
    [row] = await repo.export_rows("traits", after_seq=0, upto_seq=10**6, limit=10)
    assert (row["trait_id"], row["retracted_at"], "evidence" in row) == (t.trait_id, T0, False)
    [evidence] = await repo.export_rows("trait_evidence", after_seq=0, upto_seq=10**6, limit=10)
    assert evidence["change_seq"] < row["change_seq"]
    await repo.erase_for(scope(TargetKind.PROFILE, (handle,), profile), limit=10)
    assert await repo.export_rows("traits", after_seq=0, upto_seq=10**6, limit=10) == []
