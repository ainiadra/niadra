"""The analytical read models against PostgreSQL: aggregates, profiles, sealed summaries and rollups."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import insert

from niadra.adapters.memory.fakes import FixedClock
from niadra.adapters.outbound.keys.local import LocalKeyVault
from niadra.adapters.outbound.postgres.schema.memory import (
    AgentActionRow,
    BusinessObjectRow,
    EpisodeRow,
    OpenItemRow,
    TraitEvidence,
    TraitRow,
)
from niadra.adapters.outbound.postgres.schema.write_path import HandleRow, Profile, ProfileMembership
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.ids import uuid7
from niadra.domain.insights.model import (
    AggregateQuery,
    Entity,
    Metric,
    RollupRow,
    Selection,
)
from tests.integration.conftest import CloningUnitOfWork

CLOCK = FixedClock()
NOW = CLOCK.now()


@dataclass
class Seeded:
    uow: PgUnitOfWorkFactory
    keys: LocalKeyVault
    space_id: UUID
    profiles: dict[str, UUID]


@pytest.fixture
async def seeded(pg_uow: CloningUnitOfWork) -> Seeded:
    keys = pg_uow._keys
    uow = pg_uow
    space_id = uuid7()
    profiles: dict[str, UUID] = {}
    async with uow(space_id) as tx:
        db = tx.session
        people = [("p_ana", "unresolved", -0.5), ("p_bia", "unresolved", -0.3), ("p_caio", "resolved", 0.6)]
        for index, (pseudonym, outcome, sentiment) in enumerate(people):
            profile_id, handle_id = uuid7(), uuid7()
            profiles[pseudonym] = profile_id
            await db.execute(
                insert(Profile).values(
                    space_id=space_id,
                    profile_id=profile_id,
                    kind="person",
                    pseudonym=pseudonym,
                    created_at=NOW - timedelta(days=1),
                )
            )
            await db.execute(
                insert(ProfileMembership).values(
                    space_id=space_id, handle_id=handle_id, profile_id=profile_id, version=1
                )
            )
            episode_id = uuid7()
            summary = f"summary of {pseudonym}, phone +55 11 91234-567{index}"
            ct, dek = await keys.encrypt(
                space_id, summary.encode(), f"episodes:{episode_id}:summary_ct".encode()
            )
            await db.execute(
                insert(EpisodeRow).values(
                    space_id=space_id,
                    origin_handle_id=handle_id,
                    episode_id=episode_id,
                    version=1,
                    session_id=uuid7(),
                    channel="whatsapp",
                    source_id=uuid7(),
                    summary_ct=ct,
                    intent="complaint",
                    category="invoice" if index < 2 else "health",
                    normalized_category="billing" if index < 2 else "health",
                    outcome=outcome,
                    sentiment=sentiment,
                    started_at=NOW - timedelta(hours=2 + index),
                    ended_at=NOW - timedelta(hours=1 + index),
                    dek_version=dek,
                )
            )
            await db.execute(
                insert(OpenItemRow).values(
                    space_id=space_id,
                    origin_handle_id=handle_id,
                    item_id=uuid7(),
                    description_ct=b"x",
                    owner="company",
                    status="open",
                    kind="promise",
                    origin={},
                    created_at=NOW - timedelta(days=10 * index + 1),
                    dek_version=1,
                )
            )
            await db.execute(
                insert(TraitRow).values(
                    space_id=space_id,
                    trait_id=uuid7(),
                    profile_id=profile_id,
                    membership_version=1,
                    name="recurring_complaint",
                    value_ct=b"x",
                    layer="volatile",
                    rule_version="1",
                    first_seen=NOW,
                    last_evidence_at=NOW - timedelta(hours=1),
                    expires_at=NOW + timedelta(days=30),
                    confidence=0.8,
                    dek_version=1,
                )
            )
            await db.execute(
                insert(BusinessObjectRow).values(
                    space_id=space_id,
                    object_id=uuid7(),
                    type="invoice",
                    namespace="erp",
                    external_id=str(index),
                    origin_handle_id=handle_id,
                    as_of=NOW - timedelta(hours=3),
                    source_id=uuid7(),
                )
            )
        await db.execute(
            insert(AgentActionRow).values(
                space_id=space_id,
                action_id=uuid7(),
                event_id=uuid7(),
                source_id=uuid7(),
                operation="credit",
                status="confirmed",
                occurred_at=NOW - timedelta(hours=1),
                dek_version=1,
            )
        )
    return Seeded(uow, keys, space_id, profiles)


def _window(entity: Entity, filters: dict[str, tuple[str, ...]] | None = None) -> Selection:
    return Selection(entity, filters or {}, NOW - timedelta(days=60), NOW + timedelta(hours=1))


async def test_aggregates_group_by_structured_columns_and_count_profiles(seeded: Seeded) -> None:
    query = AggregateQuery(_window(Entity.EPISODES), tuple(Metric), ("outcome", "sentiment_band"))
    async with seeded.uow(seeded.space_id) as uow:
        rows = await uow.insights.aggregate(query, exclude_categories={"health"})
        by_entity = {
            entity: await uow.insights.aggregate(AggregateQuery(_window(entity), (Metric.COUNT,), ()), set())
            for entity in Entity
        }
        ages = await uow.insights.aggregate(
            AggregateQuery(_window(Entity.OPEN_ITEMS, {"status": ("open",)}), (Metric.COUNT,), ("age_band",)),
            set(),
        )
    assert [(r.group, r.count, r.profiles) for r in rows] == [(("unresolved", "negative"), 2, 2)]
    assert rows[0].avg_sentiment == pytest.approx(-0.4)
    assert {e: r[0].count for e, r in by_entity.items()} == {
        Entity.EPISODES: 3,
        Entity.OPEN_ITEMS: 3,
        Entity.TRAITS: 3,
        Entity.ACTIONS: 1,
        Entity.OBJECTS: 3,
        Entity.PROFILES: 3,
    }
    assert sorted((r.group[0], r.count) for r in ages) == [("0-2d", 1), ("8-30d", 2)]


async def test_find_profiles_pages_by_pseudonym_and_filters(seeded: Seeded) -> None:
    unresolved = _window(Entity.EPISODES, {"outcome": ("unresolved",)})
    async with seeded.uow(seeded.space_id) as uow:
        first = await uow.insights.find_profiles(unresolved, set(), 1, None, 1)
        rest = await uow.insights.find_profiles(unresolved, set(), 1, first[-1].pseudonym, 10)
        all_objects = await uow.insights.find_profiles(_window(Entity.OBJECTS), set(), 1, None, 10)
        other_space = uuid7()
    async with seeded.uow(other_space) as uow:
        nothing = await uow.insights.find_profiles(_window(Entity.EPISODES), set(), 1, None, 10)
    assert [m.pseudonym for m in first] == ["p_ana"]
    assert [m.pseudonym for m in rest] == ["p_bia"]
    assert [m.pseudonym for m in all_objects] == ["p_ana", "p_bia", "p_caio"]
    assert nothing == []


async def test_episode_texts_come_back_sealed_and_open_with_the_space_key(seeded: Seeded) -> None:
    async with seeded.uow(seeded.space_id) as uow:
        texts = await uow.insights.episode_texts(_window(Entity.EPISODES), {"health"}, 10, newest_first=True)
        ref = await uow.insights.profile_by_pseudonym("p_bia")
    assert [t.pseudonym for t in texts] == ["p_ana", "p_bia"]
    opened = await seeded.keys.decrypt(
        seeded.space_id, texts[0].summary.ciphertext, texts[0].summary.aad, texts[0].summary.dek_version
    )
    assert opened.decode().startswith("summary of p_ana")
    assert ref is not None
    assert ref.profile_id == seeded.profiles["p_bia"]
    assert len(ref.handle_ids) == 1


async def test_rollups_replace_the_day_and_serve_the_latest(seeded: Seeded) -> None:
    day = NOW.date()
    async with seeded.uow(seeded.space_id) as uow:
        assert await uow.insights.latest_rollups() == (None, [])
        await uow.insights.write_rollups(
            day - timedelta(days=1), [RollupRow("episodes.intent", "complaint", 9)]
        )
        await uow.insights.write_rollups(day, [RollupRow("episodes.intent", "complaint", 3)])
        await uow.insights.write_rollups(day, [RollupRow("episodes.intent", "complaint", 4)])
        latest = await uow.insights.latest_rollups()
    assert latest == (day, [RollupRow("episodes.intent", "complaint", 4)])


async def _claimed(seeded: Seeded, pseudonym: str) -> UUID:
    """A caller's e-mail a claim joined to `pseudonym` and nobody confirmed (5.3), with a conversation of
    its real owner's: an unresolved invoice complaint, an open item, an object and the pattern it supports."""
    handle_id = uuid7()
    async with seeded.uow(seeded.space_id) as tx:
        db = tx.session
        await db.execute(
            insert(HandleRow).values(
                space_id=seeded.space_id,
                handle_id=handle_id,
                type="email",
                subject_kind="person",
                value_hmac=handle_id.bytes,
                first_seen=NOW,
                last_seen=NOW,
                provisional=True,
                dek_version=1,
            )
        )
        await db.execute(
            insert(ProfileMembership).values(
                space_id=seeded.space_id,
                handle_id=handle_id,
                profile_id=seeded.profiles[pseudonym],
                version=2,
            )
        )
        episode_id = uuid7()
        ct, dek = await seeded.keys.encrypt(
            seeded.space_id, b"her dispute, protocol 81220", f"episodes:{episode_id}:summary_ct".encode()
        )
        await db.execute(
            insert(EpisodeRow).values(
                space_id=seeded.space_id,
                origin_handle_id=handle_id,
                episode_id=episode_id,
                version=1,
                session_id=uuid7(),
                channel="email",
                source_id=uuid7(),
                summary_ct=ct,
                intent="complaint",
                category="invoice",
                normalized_category="billing",
                outcome="unresolved",
                sentiment=-0.9,
                started_at=NOW - timedelta(minutes=30),
                ended_at=NOW - timedelta(minutes=10),
                dek_version=dek,
            )
        )
        await db.execute(
            insert(OpenItemRow).values(
                space_id=seeded.space_id,
                origin_handle_id=handle_id,
                item_id=uuid7(),
                description_ct=b"x",
                owner="company",
                status="open",
                kind="promise",
                origin={},
                created_at=NOW - timedelta(hours=1),
                dek_version=1,
            )
        )
        await db.execute(
            insert(BusinessObjectRow).values(
                space_id=seeded.space_id,
                object_id=uuid7(),
                type="invoice",
                namespace="erp",
                external_id="claimed",
                origin_handle_id=handle_id,
                as_of=NOW - timedelta(hours=1),
                source_id=uuid7(),
            )
        )
        trait_id = uuid7()
        await db.execute(
            insert(TraitRow).values(
                space_id=seeded.space_id,
                trait_id=trait_id,
                profile_id=seeded.profiles[pseudonym],
                membership_version=2,
                name="asked_to_cancel",
                value_ct=b"x",
                layer="stable",
                rule_version="1",
                first_seen=NOW,
                last_evidence_at=NOW - timedelta(minutes=10),
                expires_at=NOW + timedelta(days=30),
                confidence=0.8,
                dek_version=1,
            )
        )
        await db.execute(
            insert(TraitEvidence).values(
                space_id=seeded.space_id,
                trait_id=trait_id,
                origin_handle_id=handle_id,
                evidence_kind="episode",
                evidence_id=episode_id,
            )
        )
    return handle_id


async def test_an_unconfirmed_links_rows_count_nowhere_until_it_is_confirmed(seeded: Seeded) -> None:
    claimed = await _claimed(seeded, "p_caio")
    unresolved = _window(Entity.EPISODES, {"outcome": ("unresolved",)})

    async def read() -> tuple[dict[Entity, int], list[str], list[str], tuple[UUID, ...], tuple[UUID, ...]]:
        async with seeded.uow(seeded.space_id) as uow:
            counts = {
                entity: sum(
                    r.count
                    for r in await uow.insights.aggregate(
                        AggregateQuery(_window(entity), (Metric.COUNT,), ()), set()
                    )
                )
                for entity in (Entity.EPISODES, Entity.OPEN_ITEMS, Entity.OBJECTS, Entity.TRAITS)
            }
            found = [m.pseudonym for m in await uow.insights.find_profiles(unresolved, set(), 1, None, 10)]
            texts = await uow.insights.episode_texts(_window(Entity.EPISODES), set(), 10, newest_first=True)
            ref = await uow.insights.profile_by_pseudonym("p_caio")
        assert ref is not None
        return counts, found, [t.pseudonym for t in texts], ref.handle_ids, ref.hidden_handle_ids

    counts, found, texts, members, hidden = await read()
    # Her complaint would make p_caio an unhappy customer, and her pattern his; neither counts.
    assert counts == {Entity.EPISODES: 3, Entity.OPEN_ITEMS: 3, Entity.OBJECTS: 3, Entity.TRAITS: 3}
    assert found == ["p_ana", "p_bia"]
    assert texts == ["p_ana", "p_bia", "p_caio"]
    assert claimed not in members
    assert hidden == (claimed,)

    async with seeded.uow(seeded.space_id) as tx:
        await tx.identity.set_provisional([claimed], False)  # a system of record confirms the link
    counts, found, texts, members, hidden = await read()
    assert counts == {Entity.EPISODES: 4, Entity.OPEN_ITEMS: 4, Entity.OBJECTS: 4, Entity.TRAITS: 4}
    assert found == ["p_ana", "p_bia", "p_caio"]
    assert texts[0] == "p_caio"
    assert claimed in members
    assert hidden == ()
