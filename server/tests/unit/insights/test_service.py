from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import fakeredis
import pytest

from niadra.adapters.outbound.redis.cache import RedisCache
from niadra.adapters.outbound.redis.errors import CACHE_FAILURES
from niadra.app.insights import LIMITS_RETRY_S
from niadra.domain.common.errors import Forbidden, InvalidInput, NotFound, RateLimited, Unavailable
from niadra.domain.common.ids import uuid7
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Audience, ReceiptKind, ReceiptSurface, Scope
from niadra.ports.infra import CacheUnavailable
from niadra.testing.cell import SPACE_ID, principal
from tests.unit.insights.conftest import Space, person

ANALYST = person("analysis")
SECURITY = person("security")


async def test_aggregate_groups_and_withholds_small_groups(space: Space) -> None:
    since, until = space.window()
    result = await space.service.aggregate(
        ANALYST, "episodes", ["count", "profiles", "avg_sentiment"], ["normalized_category"], {}, since, until
    )
    assert [(r.group, r.count, r.profiles) for r in result.rows] == [(("billing",), 3, 2)]
    assert result.rows[0].avg_sentiment == pytest.approx((-0.6 - 0.4 + 0.3) / 3)
    assert result.suppressed_groups == 1  # technical_visit has one profile; health never appears


async def test_sensitive_categories_never_reach_an_analyst(space: Space) -> None:
    since, until = space.window()
    rows = (
        await space.service.aggregate(ANALYST, "episodes", ["count"], ["category"], {}, since, until)
    ).rows
    assert "health" not in {r.group[0] for r in rows}
    samples = await space.service.sample_episodes(ANALYST, {}, since, until, limit=50)
    assert {s["pseudonym"] for s in samples.items} == {"p_ana", "p_bia", "p_caio"}
    view = await space.service.get_customer_context(ANALYST, "p_ana")
    assert [f["predicate"] for f in view.facts] == ["preferred_name"]


async def test_every_query_leaves_an_analytics_receipt(space: Space) -> None:
    since, until = space.window()
    await space.service.aggregate(
        ANALYST, "episodes", ["count"], ["outcome"], {"channel": ["whatsapp"]}, since, until
    )
    [receipt] = space.sink.drafts
    assert receipt.kind is ReceiptKind.ANALYTICS
    assert receipt.audience is Audience.ANALYST
    assert receipt.surface is ReceiptSurface.SEARCH
    assert receipt.person_id == ANALYST.user_id
    assert receipt.source_id is None
    assert receipt.space_id == SPACE_ID
    assert receipt.producer_id == "test"
    assert receipt.producer_seq == 1
    assert receipt.extra["tool"] == "aggregate"
    assert len(receipt.extra["result_hash"]) == 64
    assert receipt.query_hash is not None
    assert b"whatsapp" not in receipt.query_hash


async def test_without_a_receipt_there_is_no_answer(space: Space) -> None:
    space.sink.ok = False
    since, until = space.window()
    with pytest.raises(Unavailable):
        await space.service.find_profiles(ANALYST, "episodes", {}, since, until)


async def test_find_profiles_returns_pseudonyms_with_the_reason_and_pages(space: Space) -> None:
    since, until = space.window()
    first = await space.service.find_profiles(
        ANALYST, "episodes", {"outcome": ["unresolved"]}, since, until, limit=1
    )
    assert [i["pseudonym"] for i in first.items] == ["p_ana"]
    assert first.items[0]["reason"].startswith("1 episodes with outcome in (unresolved)")
    second = await space.service.find_profiles(
        ANALYST, "episodes", {"outcome": ["unresolved"]}, since, until, cursor=first.next_cursor, limit=1
    )
    assert [i["pseudonym"] for i in second.items] == ["p_bia"]
    assert second.next_cursor is None
    repeat = await space.service.find_profiles(ANALYST, "episodes", {}, since, until, min_matches=2)
    assert [i["pseudonym"] for i in repeat.items] == ["p_bia"]
    assert space.sink.drafts[0].item_ids == ("p_ana",)


async def test_semantic_search_ranks_profiles_and_redacts_snippets(space: Space) -> None:
    since, until = space.window()
    found = await space.service.search_semantic(ANALYST, "duplicated invoice refund", since, until, limit=2)
    assert found.hits[0]["pseudonym"] in {"p_ana", "p_bia"}
    assert found.scanned == 4
    assert not found.truncated
    ana = next((h for h in found.hits if h["pseudonym"] == "p_ana"), None)
    if ana is not None:
        assert "91234" not in ana["snippet"]
        assert "[phone]" in ana["snippet"]
    assert found.notice


async def test_samples_are_redacted(space: Space) -> None:
    since, until = space.window()
    samples = await space.service.sample_episodes(ANALYST, {"normalized_category": ["billing"]}, since, until)
    assert len(samples.items) == 3
    assert all("91234" not in s["summary"] for s in samples.items)


async def test_customer_context_is_redacted_and_bound_to_the_profile(space: Space) -> None:
    view = await space.service.get_customer_context(ANALYST, "p_ana")
    assert view.facts[0]["value"] == "Ana, e-mail [email]"
    assert [i["description"] for i in view.open_items] == ["Refund the duplicated invoice"]
    assert [t["name"] for t in view.traits] == ["recurring_complaint"]
    receipt = space.sink.drafts[-1]
    assert receipt.profile_id == space.profiles["p_ana"].profile_id
    assert receipt.surface is ReceiptSurface.PACK
    with pytest.raises(NotFound):
        await space.service.get_customer_context(ANALYST, "p_nobody")


async def test_customer_context_leaves_out_a_pattern_an_unconfirmed_link_supports(space: Space) -> None:
    # p_ana's profile holds an e-mail a claim joined and nobody confirmed (5.3): the read model lists it
    # apart, and a pattern its rows support is not hers to show until the link is confirmed.
    ana = space.profiles["p_ana"]
    claimed = uuid7()
    space.repo.add_profile(replace(ana, hidden_handle_ids=(claimed,)), space.now)
    theirs = replace(space.traits.traits[0], trait_id=uuid7(), name="asked_to_cancel")
    space.traits.traits.append(theirs)
    space.traits.supported_by[theirs.trait_id] = {claimed}
    view = await space.service.get_customer_context(ANALYST, "p_ana")
    assert [t["name"] for t in view.traits] == ["recurring_complaint"]


async def test_lineage_explains_facts_and_refuses_items_of_another_profile(space: Space) -> None:
    fact = space.memory.facts[0]
    lineage = await space.service.explain_lineage(ANALYST, "p_ana", fact.fact_id)
    assert lineage["kind"] == "fact"
    assert lineage["evidence_event_ids"] == [str(fact.evidence_event_ids[0])]
    assert "value" not in lineage
    bia_episode = next(
        e for e in space.memory.episodes if e.origin_handle_id in space.profiles["p_bia"].handle_ids
    )
    with pytest.raises(NotFound):
        await space.service.explain_lineage(ANALYST, "p_ana", bia_episode.episode_id)
    health_fact = space.memory.facts[1]
    with pytest.raises(NotFound):
        await space.service.explain_lineage(ANALYST, "p_ana", health_fact.fact_id)
    episode = await space.service.explain_lineage(ANALYST, "p_bia", bia_episode.episode_id)
    assert episode["kind"] == "episode"
    assert episode["source_id"]


async def test_reveal_needs_an_elevated_role_and_a_reason(space: Space) -> None:
    with pytest.raises(Forbidden):
        await space.service.request_reveal(ANALYST, "p_ana", "chargeback investigation #123")
    with pytest.raises(InvalidInput):
        await space.service.request_reveal(SECURITY, "p_ana", "because")
    revealed = await space.service.request_reveal(SECURITY, "p_ana", "chargeback investigation #123")
    assert revealed["handles"] == [{"type": "phone_e164", "value": "+551190000000", "scope": None}]
    receipt = space.sink.drafts[-1]
    assert receipt.purpose == "reveal"
    assert receipt.extra["reason"] == "chargeback investigation #123"


async def test_keys_need_the_analytics_scope_and_admin_to_reveal(space: Space) -> None:
    since, until = space.window()
    agent = principal()
    with pytest.raises(Forbidden):
        await space.service.aggregate(agent, "episodes", ["count"], [], {}, since, until)
    analytics = principal(scopes=frozenset({Scope.ANALYTICS}))
    await space.service.aggregate(analytics, "episodes", ["count"], [], {}, since, until)
    assert space.sink.drafts[-1].source_id == analytics.source_id
    with pytest.raises(Forbidden):
        await space.service.request_reveal(analytics, "p_ana", "chargeback investigation #123")
    elevated = principal(scopes=frozenset({Scope.ANALYTICS, Scope.ADMIN}))
    assert (await space.service.request_reveal(elevated, "p_ana", "chargeback investigation #123"))["handles"]


async def test_vendors_and_other_roles_are_refused(space: Space) -> None:
    with pytest.raises(Forbidden):
        await space.service.describe_schema(person("review"))
    with pytest.raises(Forbidden):
        await space.service.describe_schema(person("analysis", sources=frozenset({uuid7()})))


async def test_queries_are_validated_against_the_catalog(space: Space) -> None:
    since, until = space.window()
    with pytest.raises(InvalidInput):
        await space.service.aggregate(ANALYST, "episodes", ["count"], ["summary"], {}, since, until)
    with pytest.raises(InvalidInput):
        await space.service.aggregate(ANALYST, "actions", ["profiles"], [], {}, since, until)
    with pytest.raises(InvalidInput):
        await space.service.aggregate(
            ANALYST, "episodes", ["count"], [], {}, since, since + timedelta(days=500)
        )
    with pytest.raises(InvalidInput):
        await space.service.find_profiles(ANALYST, "actions", {}, since, until)


async def test_daily_volume_limits(space: Space) -> None:
    since, until = space.window()
    for _ in range(16):
        await space.service.find_profiles(ANALYST, "episodes", {}, since, until)  # 3 profiles each
    with pytest.raises(RateLimited):
        await space.service.find_profiles(ANALYST, "episodes", {}, since, until)
    for _ in range(100 - 17):
        await space.service.describe_schema(person("admin"))
    with pytest.raises(RateLimited):
        await space.service.describe_schema(person("admin"))


async def test_describe_schema_lists_what_can_be_asked(space: Space) -> None:
    schema = await space.service.describe_schema(ANALYST)
    assert "sentiment_band" in schema["entities"]["episodes"]["dimensions"]
    assert schema["values"]["normalized_category"] == ["billing", "technical_visit"]
    assert schema["limits"]["min_group_size"] == 2


async def test_nightly_rollup_feeds_the_overview(space: Space) -> None:
    day = (space.now - timedelta(days=1)).date()
    await space.service.rollup(Task(TaskName.ROLLUP, SPACE_ID, {"day": day.isoformat()}))
    overview = await space.service.overview(ANALYST)
    assert overview.day == day
    assert overview.dimensions["episodes.normalized_category"] == {"billing": 2}
    assert overview.dimensions["open_items.age_band"] == {"8-30d": 2}
    assert "health" not in str(overview.dimensions)
    assert overview.suppressed_buckets > 0
    await space.service.rollup(Task(TaskName.ROLLUP, SPACE_ID, {"day": day.isoformat()}))
    assert (await space.service.overview(ANALYST)).dimensions == overview.dimensions


async def test_semantic_search_uses_the_vectors_stored_with_the_episodes(space: Space) -> None:
    # An episode's vector was stored when it was written; only one without a vector of the
    # question's encoder is encoded at question time, and the ranking is the same either way.
    from niadra.domain.compile.model import ItemKind
    from niadra.domain.memory.vectors import ItemVector

    since, until = space.window()
    before = await space.service.search_semantic(ANALYST, "duplicated invoice refund", since, until, limit=4)
    summaries = {e.episode_id: e.summary for e in space.memory.episodes}
    vectors, model = await space.cell.models.embed(list(summaries.values()))
    unstored = UUID(before.hits[0]["episode_id"])  # this one has no vector yet
    stored = [(e, v) for e, v in zip(summaries, vectors, strict=True) if e != unstored]
    space.memory.vectors = [ItemVector(ItemKind.EPISODE, e, v, model) for e, v in stored]
    after = await space.service.search_semantic(ANALYST, "duplicated invoice refund", since, until, limit=4)
    assert [(h["pseudonym"], h["score"]) for h in after.hits] == [
        (h["pseudonym"], h["score"]) for h in before.hits
    ]
    assert space.sink.drafts[-1].extra["encoded"] == "1"
    space.memory.vectors = [ItemVector(ItemKind.EPISODE, e, v, "an older encoder") for e, v in stored]
    await space.service.search_semantic(ANALYST, "duplicated invoice refund", since, until, limit=4)
    assert space.sink.drafts[-1].extra["encoded"] == str(before.scanned)


async def test_with_the_state_store_down_analytics_fail_closed_and_the_failure_is_counted(
    space: Space, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The daily limits bound bulk extraction and a paid surface: uncounted calls are refused, not let through.
    server = fakeredis.FakeServer()
    server.connected = False
    down = fakeredis.FakeAsyncRedis(server=server)
    monkeypatch.setattr(space.service, "_cache", RedisCache(down, store="state"))
    failed = CACHE_FAILURES.labels("state", "incr_window")
    before = failed._value.get()  # type: ignore[attr-defined]
    try:
        with pytest.raises(Unavailable) as refused:
            await space.service.describe_schema(ANALYST)
        assert refused.value.retry_after_s == LIMITS_RETRY_S
        assert failed._value.get() == before + 1  # type: ignore[attr-defined]
        assert space.sink.drafts == []  # no receipt: nothing was answered
    finally:
        await down.aclose()


async def test_the_volume_limit_fails_closed_too(space: Space, monkeypatch: pytest.MonkeyPatch) -> None:
    since, until = space.window()

    async def unreachable(key: str) -> bytes | None:
        raise CacheUnavailable("state get failed: ConnectionError")

    monkeypatch.setattr(space.cell.cache, "get", unreachable)
    with pytest.raises(Unavailable):
        await space.service.find_profiles(ANALYST, "episodes", {}, since, until)
    assert space.sink.drafts == []
