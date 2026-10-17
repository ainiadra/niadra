"""Insights (5.21): eight analytical tools over the whole space, for the tenant's own LLM or BI.

The most dangerous surface of the product, so every call goes through the same gates: the `analytics`
scope or the analysis role, pseudonyms by default, the analyst policy on categories, minimum group
sizes, per-call and per-day volume limits, redacted text, and a receipt of kind `analytics` emitted
before the answer leaves (fail closed: no receipt, no answer).

The daily limits fail closed too, unlike the edge's rate limiter: that one guards agents mid-conversation,
while these bound bulk extraction and a metered, paid surface. With the state store down, a call is refused
with 503 and a short `Retry-After` rather than let through uncounted; the adapter counts the failure in
`niadra_cache_failures_total{store="state"}`, which the state store's alert watches.
"""

import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from niadra.config.models import Policy, SpaceConfig
from niadra.domain.common.errors import (
    Forbidden,
    InvalidInput,
    NotFound,
    RateLimited,
    ScopeMissing,
    Unavailable,
)
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, Principal, SourcePrincipal
from niadra.domain.compile.templates import opening_text
from niadra.domain.extract.result import QuestionType
from niadra.domain.identity.provenance import derivation_view
from niadra.domain.insights.gaps import (
    GAPS_KEEP_DAYS,
    AskedQuestion,
    GapDay,
    GapKey,
    GapKind,
    GapsReport,
    MappedType,
    SourceHint,
    question_gaps,
    slot_signals,
    source_hints,
)
from niadra.domain.insights.gaps import report as gap_report
from niadra.domain.insights.model import (
    CATALOG,
    AggregateQuery,
    AggregateResult,
    Entity,
    InsightsLimits,
    Metric,
    ProfileRef,
    RollupRow,
    Sealed,
    Selection,
    Usage,
    aggregate_query,
    cosine,
    fingerprint,
    match_reason,
    redact,
    result_hash,
    selection,
    suppress_small_groups,
)
from niadra.domain.measure.model import MeasuredSession
from niadra.domain.receipts import ReceiptDraft, person_extra
from niadra.domain.serve.turn import VALUE_TYPES
from niadra.domain.tasks import Task
from niadra.domain.vocabulary import (
    Audience,
    FactStatus,
    OpenItemStatus,
    ReceiptKind,
    ReceiptSurface,
    Scope,
    Sensitivity,
)
from niadra.ports.infra import Clock, ConfigProvider, KeyValueCache, KeyVault, ModelsClient, ReceiptSink
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

READ_ROLES = frozenset({"analysis", "admin"})
REVEAL_ROLES = frozenset({"security", "admin"})
EMBED_BATCH = 64
SNIPPET_CHARS = 280
# (entity, dimension, filters) counted every night for the overview resource (5.21).
ROLLUP_PLAN: tuple[tuple[Entity, str, dict[str, tuple[str, ...]]], ...] = (
    (Entity.EPISODES, "intent", {}),
    (Entity.EPISODES, "outcome", {}),
    (Entity.EPISODES, "normalized_category", {}),
    (Entity.EPISODES, "channel", {}),
    (Entity.EPISODES, "sentiment_band", {}),
    (Entity.TRAITS, "name", {}),
    (Entity.ACTIONS, "operation", {}),
    (Entity.PROFILES, "kind", {}),
    (Entity.OPEN_ITEMS, "age_band", {"status": (OpenItemStatus.OPEN.value,)}),
)
BACKLOG_WINDOW = timedelta(days=3650)
LIMITS_RETRY_S = 5
GAPS_WINDOW_DAYS = 30
GAPS_CATCH_UP_DAYS = 7
GAPS_NIGHT_HOUR = 3
GAPS_PAGE = 1000
GAPS_SESSIONS = 5000
"""Conversations a day's pass reads the agents' questions of; a bigger day counts the first ones by id."""


@dataclass(frozen=True, slots=True)
class ProfilesPage:
    items: list[dict[str, Any]]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class SemanticResult:
    hits: list[dict[str, Any]]
    scanned: int
    truncated: bool
    notice: str


@dataclass(frozen=True, slots=True)
class Samples:
    items: list[dict[str, Any]]
    notice: str


@dataclass(frozen=True, slots=True)
class CustomerView:
    pseudonym: str
    kind: str
    notice: str
    facts: list[dict[str, Any]] = field(default_factory=list)
    episodes: list[dict[str, Any]] = field(default_factory=list)
    open_items: list[dict[str, Any]] = field(default_factory=list)
    traits: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Overview:
    day: date | None
    dimensions: dict[str, dict[str, int]]
    suppressed_buckets: int


class InsightsService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        keys: KeyVault,
        clock: Clock,
        config: ConfigProvider,
        receipts: ReceiptSink,
        models: ModelsClient,
        cache: KeyValueCache,
        limits: InsightsLimits | None = None,
        producer_id: str | None = None,
    ) -> None:
        self._uow = uow
        self._keys = keys
        self._clock = clock
        self._config = config
        self._receipts = receipts
        self._models = models
        self._cache = cache
        self._limits = limits or InsightsLimits()
        self._producer = producer_id or f"insights-{uuid4().hex[:12]}"
        self._seq = itertools.count(1)

    async def describe_schema(self, caller: Principal) -> dict[str, Any]:
        config = await self._admit(caller)
        denied = _denied_categories(config.policy)
        schema = {
            "entities": {
                e.value: {
                    "dimensions": sorted(spec.dimensions),
                    "metrics": sorted(m.value for m in spec.metrics),
                    "description": spec.description,
                }
                for e, spec in CATALOG.items()
            },
            "values": {
                "intent": config.extraction.intents,
                "outcome": config.extraction.outcomes,
                "normalized_category": sorted(
                    {c.normalized_as for c in config.extraction.episode_categories} - denied
                ),
                "channel": sorted({s.channel for s in config.sources if s.channel}),
                "trait": sorted({r.catalog for r in config.trait_rules if r.enabled}),
            },
            "sources": [
                {"source_id": str(s.source_id), "name": s.name, "channel": s.channel} for s in config.sources
            ],
            "limits": {
                "min_group_size": self._limits.min_group_size,
                "max_rows": self._limits.max_rows,
                "max_window_days": self._limits.max_window_days,
                "semantic_scan": self._limits.semantic_scan,
                "max_profiles_per_day": self._limits.max_profiles_per_day,
            },
            "notice": opening_text(config.settings.b0_text, config.settings.locale),
        }
        await self._record(caller, "describe_schema", ReceiptSurface.SEARCH, {}, schema, Usage(0))
        return schema

    async def aggregate(
        self,
        caller: Principal,
        entity: str,
        metrics: Sequence[str],
        group_by: Sequence[str],
        filters: dict[str, list[str]],
        since: datetime,
        until: datetime,
    ) -> AggregateResult:
        config = await self._admit(caller)
        query = aggregate_query(
            selection(entity, filters, since, until, self._limits), metrics, group_by, self._limits
        )
        async with self._uow(caller.space.space_id) as uow:
            rows = await uow.insights.aggregate(query, _denied_categories(config.policy))
        result = suppress_small_groups(query, rows, self._limits)
        request = {
            "entity": entity,
            "metrics": list(metrics),
            "group_by": list(group_by),
            "filters": filters,
            "since": since,
            "until": until,
        }
        await self._record(caller, "aggregate", ReceiptSurface.SEARCH, request, result, Usage(0))
        return result

    async def find_profiles(
        self,
        caller: Principal,
        entity: str,
        filters: dict[str, list[str]],
        since: datetime,
        until: datetime,
        *,
        min_matches: int = 1,
        cursor: str | None = None,
        limit: int = 50,
    ) -> ProfilesPage:
        config = await self._admit(caller)
        chosen = selection(entity, filters, since, until, self._limits)
        if not CATALOG[chosen.entity].per_profile:
            raise InvalidInput("this entity does not belong to profiles")
        limit = max(1, min(limit, self._limits.max_profiles_page))
        async with self._uow(caller.space.space_id) as uow:
            found = await uow.insights.find_profiles(
                chosen, _denied_categories(config.policy), max(1, min_matches), cursor, limit + 1
            )
        page = found[:limit]
        items = [
            {"pseudonym": m.pseudonym, "matches": m.matches, "reason": match_reason(chosen, m.matches)}
            for m in page
        ]
        result = ProfilesPage(items, page[-1].pseudonym if len(found) > limit else None)
        request = {
            "entity": entity,
            "filters": filters,
            "since": since,
            "until": until,
            "min_matches": min_matches,
            "cursor": cursor,
            "limit": limit,
        }
        usage = Usage(len(page), tuple(m.pseudonym for m in page))
        await self._record(caller, "find_profiles", ReceiptSurface.SEARCH, request, result, usage)
        return result

    async def search_semantic(
        self, caller: Principal, text: str, since: datetime, until: datetime, limit: int = 10
    ) -> SemanticResult:
        """Exact cosine between the question and the newest episodes, bounded by `semantic_scan`; `truncated`
        says the scan did not cover the window. Each episode's vector is the one stored with it;
        only an episode without one of the question's encoder is encoded here."""
        config = await self._admit(caller)
        if not text.strip():
            raise InvalidInput("the query text is empty")
        chosen = selection(Entity.EPISODES.value, {}, since, until, self._limits)
        async with self._uow(caller.space.space_id) as uow:
            episodes = await uow.insights.episode_texts(
                chosen, _denied_categories(config.policy), self._limits.semantic_scan, newest_first=True
            )
            stored = await uow.memory.episode_vectors([e.episode_id for e in episodes])
        summaries = [await self._open(caller.space.space_id, e.summary) for e in episodes]
        [question], model = await self._models.embed([text])
        by_episode = {v.item_id: v.vector for v in stored if v.model == model}
        missing = [i for i, e in enumerate(episodes) if e.episode_id not in by_episode]
        computed = dict(zip(missing, await self._embed([summaries[i] for i in missing]), strict=True))
        vectors = [by_episode.get(e.episode_id) or computed[i] for i, e in enumerate(episodes)]
        best: dict[str, tuple[float, int]] = {}
        for i, vector in enumerate(vectors):
            score = cosine(question, vector)
            pseudonym = episodes[i].pseudonym
            if pseudonym not in best or score > best[pseudonym][0]:
                best[pseudonym] = (score, i)
        top = sorted(best.items(), key=lambda kv: -kv[1][0])[: max(1, min(limit, self._limits.max_hits))]
        snippets = await self._redact([summaries[i][:SNIPPET_CHARS] for _, (_, i) in top])
        hits = [
            {
                "pseudonym": pseudonym,
                "score": round(score, 4),
                "episode_id": str(episodes[i].episode_id),
                "ended_at": episodes[i].ended_at,
                "category": episodes[i].category,
                "outcome": episodes[i].outcome,
                "snippet": snippet,
            }
            for (pseudonym, (score, i)), snippet in zip(top, snippets, strict=True)
        ]
        truncated = len(episodes) >= self._limits.semantic_scan
        result = SemanticResult(
            hits, len(episodes), truncated, opening_text(config.settings.b0_text, config.settings.locale)
        )
        usage = Usage(
            len(top), tuple(p for p, _ in top), {"scanned": str(len(episodes)), "encoded": str(len(missing))}
        )
        await self._record(
            caller,
            "search_semantic",
            ReceiptSurface.SEARCH,
            {"text": text, "since": since, "until": until},
            result,
            usage,
        )
        return result

    async def sample_episodes(
        self,
        caller: Principal,
        filters: dict[str, list[str]],
        since: datetime,
        until: datetime,
        limit: int = 10,
    ) -> Samples:
        config = await self._admit(caller)
        chosen = selection(Entity.EPISODES.value, filters, since, until, self._limits)
        limit = max(1, min(limit, self._limits.max_sample))
        async with self._uow(caller.space.space_id) as uow:
            episodes = await uow.insights.episode_texts(
                chosen, _denied_categories(config.policy), limit, newest_first=False
            )
        summaries = await self._redact([await self._open(caller.space.space_id, e.summary) for e in episodes])
        items = [
            {
                "pseudonym": e.pseudonym,
                "episode_id": str(e.episode_id),
                "channel": e.channel,
                "intent": e.intent,
                "category": e.category,
                "outcome": e.outcome,
                "sentiment": e.sentiment,
                "ended_at": e.ended_at,
                "summary": summary,
            }
            for e, summary in zip(episodes, summaries, strict=True)
        ]
        result = Samples(items, opening_text(config.settings.b0_text, config.settings.locale))
        usage = Usage(len({e.pseudonym for e in episodes}), tuple(str(e.episode_id) for e in episodes))
        await self._record(
            caller,
            "sample_episodes",
            ReceiptSurface.SEARCH,
            {"filters": filters, "since": since, "until": until, "limit": limit},
            result,
            usage,
        )
        return result

    async def get_customer_context(self, caller: Principal, pseudonym: str) -> CustomerView:
        """One customer by pseudonym, in the full view for the analyst audience: derived facts and
        summaries only, redacted, never raw transcripts (5.21)."""
        config = await self._admit(caller)
        denied = _denied_categories(config.policy)
        async with self._uow(caller.space.space_id) as uow:
            ref = await _profile(uow, pseudonym)
            facts = [
                f
                for f in await uow.memory.facts_for(ref.handle_ids, (FactStatus.ACTIVE,))
                if f.category not in denied
                and (f.sensitivity is Sensitivity.NORMAL or f.category in _allowed(config.policy))
            ]
            episodes = [
                e
                for e in await uow.memory.episodes_for(ref.handle_ids, limit=20)
                if e.category not in denied and e.normalized_category not in denied
            ]
            items = await uow.memory.open_items_for(
                ref.handle_ids, (OpenItemStatus.OPEN, OpenItemStatus.OVERDUE)
            )
            traits = await uow.traits.active_for(ref.profile_id)
            if ref.hidden_handle_ids and traits:
                # The pseudonym's confirmed members only (5.3): a pattern an unconfirmed link supports
                # stays out, as its rows do.
                tainted = await uow.traits.traits_with_evidence_from(ref.hidden_handle_ids)
                traits = [t for t in traits if t.trait_id not in {x.trait_id for x in tainted}]
        texts = await self._redact(
            [f.value for f in facts] + [e.summary for e in episodes] + [i.description for i in items]
        )
        fact_texts, episode_texts, item_texts = (
            texts[: len(facts)],
            texts[len(facts) : len(facts) + len(episodes)],
            texts[len(facts) + len(episodes) :],
        )
        view = CustomerView(
            pseudonym=ref.pseudonym,
            kind=ref.kind,
            notice=opening_text(config.settings.b0_text, config.settings.locale),
            facts=[
                {
                    "item_id": str(f.fact_id),
                    "predicate": f.predicate,
                    "value": v,
                    "category": f.category,
                    "valid_at": f.valid_at,
                    "confidence": f.confidence,
                }
                for f, v in zip(facts, fact_texts, strict=True)
            ],
            episodes=[
                {
                    "item_id": str(e.episode_id),
                    "channel": e.channel,
                    "intent": e.intent,
                    "category": e.category,
                    "outcome": e.outcome,
                    "sentiment": e.sentiment,
                    "ended_at": e.ended_at,
                    "summary": s,
                }
                for e, s in zip(episodes, episode_texts, strict=True)
            ],
            open_items=[
                {
                    "item_id": str(i.item_id),
                    "description": d,
                    "owner": i.owner,
                    "status": i.status.value,
                    "due_at": i.due_at,
                }
                for i, d in zip(items, item_texts, strict=True)
            ],
            traits=[
                {"name": t.name, "value": t.value, "layer": t.layer, "last_evidence_at": t.last_evidence_at}
                for t in traits
            ],
        )
        item_ids = tuple(x["item_id"] for x in (*view.facts, *view.episodes, *view.open_items))
        usage = Usage(1, item_ids, {"pseudonym": ref.pseudonym})
        await self._record(
            caller,
            "get_customer_context",
            ReceiptSurface.PACK,
            {"pseudonym": pseudonym},
            view,
            usage,
            profile_id=ref.profile_id,
        )
        return view

    async def explain_lineage(self, caller: Principal, pseudonym: str, item_id: UUID) -> dict[str, Any]:
        """Where an item of a customer's memory came from: events, sources and dates, never text (5.13)."""
        denied = _denied_categories((await self._admit(caller)).policy)
        async with self._uow(caller.space.space_id) as uow:
            ref = await _profile(uow, pseudonym)
            fact = next(
                (
                    f
                    for f in await uow.memory.facts_for(ref.handle_ids, tuple(FactStatus))
                    if f.fact_id == item_id
                ),
                None,
            )
            episode = None if fact else await uow.memory.get_episode(item_id)
        if fact is not None and fact.category not in denied:
            lineage: dict[str, Any] = {
                "kind": "fact",
                "item_id": str(fact.fact_id),
                "predicate": fact.predicate,
                "category": fact.category,
                "status": fact.status.value,
                "speaker": fact.speaker.value,
                "created_at": fact.created_at,
                "valid_at": fact.valid_at,
                "times_seen": fact.times_seen,
                "evidence_event_ids": [str(e) for e in fact.evidence_event_ids],
            }
        elif (
            episode is not None
            and episode.origin_handle_id in ref.handle_ids
            and episode.category not in denied
        ):
            lineage = {
                "kind": "episode",
                "item_id": str(episode.episode_id),
                "version": episode.version,
                "session_id": str(episode.session_id),
                "source_id": str(episode.source_id),
                "channel": episode.channel,
                "started_at": episode.started_at,
                "ended_at": episode.ended_at,
            }
        else:
            raise NotFound("unknown item for this pseudonym")
        await self._record(
            caller,
            "explain_lineage",
            ReceiptSurface.OPEN,
            {"pseudonym": pseudonym, "item_id": item_id},
            lineage,
            Usage(1, (str(item_id),)),
            profile_id=ref.profile_id,
        )
        return lineage

    async def request_reveal(self, caller: Principal, pseudonym: str, reason: str) -> dict[str, Any]:
        """Swaps a pseudonym for the real handles: elevated scope, a reason, and the receipt carries both."""
        await self._admit(caller, elevated=True)
        reason = reason.strip()
        if len(reason) < 10:
            raise InvalidInput("a reveal needs a reason of at least 10 characters")
        async with self._uow(caller.space.space_id) as uow:
            ref = await _profile(uow, pseudonym)
            handles = await uow.identity.get_handles(ref.handle_ids)
        revealed = {
            "pseudonym": ref.pseudonym,
            "kind": ref.kind,
            "handles": [{"type": h.type.value, "value": h.value, "scope": h.scope or None} for h in handles],
        }
        usage = Usage(1, (ref.pseudonym,), {"reason": reason[:500]})
        await self._record(
            caller,
            "request_reveal",
            ReceiptSurface.OPEN,
            {"pseudonym": pseudonym},
            revealed,
            usage,
            profile_id=ref.profile_id,
            purpose="reveal",
        )
        return revealed

    async def overview(self, caller: Principal) -> Overview:
        """The `niadra://tenant/overview` resource: the newest nightly rollup, small buckets withheld."""
        await self._admit(caller)
        async with self._uow(caller.space.space_id) as uow:
            day, rows = await uow.insights.latest_rollups()
        dimensions: dict[str, dict[str, int]] = {}
        suppressed = 0
        for row in rows:
            if row.value < self._limits.min_group_size:
                suppressed += 1
                continue
            dimensions.setdefault(row.dimension, {})[row.bucket] = row.value
        result = Overview(day, dimensions, suppressed)
        await self._record(caller, "overview", ReceiptSurface.SEARCH, {}, result, Usage(0))
        return result

    async def rollup(self, task: Task) -> None:
        """Nightly: counts per dimension for one day of the space, replacing any earlier run of that day."""
        config = self._config.space(task.space_id)
        day = (
            date.fromisoformat(task.payload["day"])
            if "day" in task.payload
            else self._clock.now().date() - timedelta(1)
        )
        start = datetime.combine(day, time.min, tzinfo=UTC)
        end = start + timedelta(days=1)
        denied = _denied_categories(config.policy)
        async with self._uow(task.space_id) as uow:
            rows: list[RollupRow] = []
            for entity, dimension, filters in ROLLUP_PLAN:
                since = end - BACKLOG_WINDOW if entity is Entity.OPEN_ITEMS else start
                query = AggregateQuery(Selection(entity, filters, since, end), (Metric.COUNT,), (dimension,))
                for row in await uow.insights.aggregate(query, denied):
                    rows.append(
                        RollupRow(f"{entity.value}.{dimension}", row.group[0] or "unknown", row.count)
                    )
            await uow.insights.write_rollups(day, rows)

    async def gaps(self, caller: Principal, days: int = GAPS_WINDOW_DAYS) -> GapsReport:
        """`GET /v1/insights/gaps` (13, 6.5): what customers asked that memory had no record of, what agents
        had to ask them for, which source could record it, the questions memory answers every time and the
        standing answers proposed for them. Counts by subject and category only; a group with fewer
        customers than the minimum group size is withheld, and so is a category the analyst policy denies."""
        config = await self._admit(caller)
        until = self._clock.now().astimezone(UTC).date()
        since = until - timedelta(days=days)
        async with self._uow(caller.space.space_id) as uow:
            rows = await uow.insights.gap_days(since, until)
            reviews = await uow.insights.reviews()
        denied = _denied_categories(config.policy)
        shown = [r for r in rows if r.key.topic not in denied]
        subjects = {r.key.subject for r in shown}
        result = gap_report(
            shown,
            since=since,
            until=until,
            min_customers=self._limits.min_group_size,
            hints=_source_hints(config, subjects),
            language=config.settings.locale,
        )
        withheld = len({r.key for r in rows if r.key.topic in denied})
        result = replace(result, withheld_groups=result.withheld_groups + withheld, reviews=reviews)
        await self._record(caller, "gaps", ReceiptSurface.SEARCH, {"days": days}, result, Usage(0))
        return result

    async def review_gap(
        self, caller: Principal, key: str, decision: str, note: str | None
    ) -> tuple[str, str, datetime, str | None]:
        """The team's word on a gap or a proposal: accepted (a source to connect, a line to keep) or
        dismissed. Proposals take effect only through the configuration the team approves."""
        await self._admit(caller)
        at = self._clock.now()
        async with self._uow(caller.space.space_id) as uow:
            await uow.insights.put_review(key, decision, _who(caller), at, note)
        return decision, _who(caller), at, note

    async def record_gaps(self, space_id: UUID) -> int:
        """A per-space loop step: once a day, after 03:00 in the space's time zone, the knowledge gaps of
        the days since the last pass (a week at most), from the day's `slots` receipts and the agents'
        questions of the conversations measured that day. Returns how many days it counted."""
        try:
            config = self._config.space(space_id)
        except NotFound:
            return 0
        now = self._clock.now()
        if now.astimezone(ZoneInfo(config.settings.timezone)).hour < GAPS_NIGHT_HOUR:
            return 0
        yesterday = now.astimezone(UTC).date() - timedelta(days=1)
        async with self._uow(space_id) as uow:
            last = await uow.insights.gaps_day()
        first = yesterday - timedelta(days=GAPS_CATCH_UP_DAYS - 1)
        if last is not None:
            first = max(first, date.fromordinal(last + 1))
        counted = 0
        day = first
        while day <= yesterday:
            rows = await self._count_gaps(space_id, day)
            async with self._uow(space_id) as uow:
                await uow.insights.write_gap_days(day, rows)
                await uow.insights.set_gaps_day(day.toordinal())
            counted += 1
            day += timedelta(days=1)
        if counted:
            async with self._uow(space_id) as uow:
                await uow.insights.purge_gap_days(yesterday - timedelta(days=GAPS_KEEP_DAYS))
        return counted

    async def _count_gaps(self, space_id: UUID, day: date) -> list[GapDay]:
        start = datetime.combine(day, time.min, tzinfo=UTC)
        end = start + timedelta(days=1)
        salt = space_id.bytes
        found: dict[GapKey, GapDay] = {}

        def count(key: GapKey, profile_id: UUID) -> None:
            row = found.setdefault(key, GapDay(day, key))
            row.turns += 1
            row.customers.add(salt, profile_id)

        topics: dict[str, str] = {}
        measured: list[MeasuredSession] = []
        before: tuple[datetime, UUID] | None = None
        while True:
            async with self._uow(space_id) as uow:
                page = await uow.measure.measured_sessions(
                    source_ids=None,
                    since=start - timedelta(days=1),
                    until=end,
                    before=before,
                    limit=GAPS_PAGE,
                )
            for session in page:
                if session.normalized_category:
                    topics.setdefault(session.conversation_key, session.normalized_category)
                if start <= session.ended_at < end and session.profile_id is not None:
                    measured.append(session)
            if len(page) < GAPS_PAGE:
                break
            before = (page[-1].ended_at, page[-1].session_id)
        after: UUID | None = None
        while True:
            async with self._uow(space_id) as uow:
                receipts = await uow.audit.slot_receipts(space_id, day, after=after, limit=GAPS_PAGE)
            for receipt in receipts:
                draft = receipt.draft
                if draft.profile_id is None:
                    continue
                topic = topics.get(draft.conversation_id or "", "")
                for kind, subject in slot_signals(draft.detail):
                    count(GapKey(kind, subject, topic), draft.profile_id)
            if len(receipts) < GAPS_PAGE:
                break
            after = receipts[-1].draft.receipt_id
        for session in sorted(measured, key=lambda m: m.session_id)[:GAPS_SESSIONS]:
            assert session.profile_id is not None
            for predicate in await self._questions_unanswered(space_id, session):
                count(
                    GapKey(GapKind.AGENT_QUESTION, predicate, session.normalized_category or ""),
                    session.profile_id,
                )
            for type_ in await self._objects_unresolved(space_id, session):
                count(
                    GapKey(GapKind.OBJECT_UNRESOLVED, type_, session.normalized_category or ""),
                    session.profile_id,
                )
        return sorted(found.values(), key=lambda r: (r.key.kind, r.key.subject, r.key.topic))

    async def _objects_unresolved(self, space_id: UUID, session: MeasuredSession) -> list[str]:
        """The object types whose natural keys the conversation wrote with no object of the customer behind
        them, once per occurrence."""
        async with self._uow(space_id) as uow:
            run = await uow.extract.latest_run(session.session_id)
        found = run.result.object_unresolved if run and run.result else {}
        return [type_ for type_, n in sorted(found.items()) for _ in range(n)]

    async def _questions_unanswered(self, space_id: UUID, session: MeasuredSession) -> list[str]:
        """The predicates the conversation's agents asked the customer for that memory had no row of before
        the question, for a customer memory already knew. Confirmed links only: a row that came through a
        claim nobody confirmed counts nowhere here (5.3)."""
        async with self._uow(space_id) as uow:
            run = await uow.extract.latest_run(session.session_id)
            asked = [
                q
                for q in (run.result.agent_questions if run and run.result else ())
                if q.type is QuestionType.ASKS_DATA and q.predicate
            ]
            if not asked:
                return []
            assert session.profile_id is not None
            profile = await uow.identity.get_profile(session.profile_id)
            if profile is None:
                return []
            handles = derivation_view(profile).handle_ids
            events = {e.event_id: e for e in await uow.ingest.get_events([q.event_id for q in asked])}
            questions = [
                AskedQuestion(str(q.predicate), events[q.event_id].occurred_at)
                for q in asked
                if q.event_id in events
            ]
            if not questions:
                return []
            earliest = min(q.at for q in questions)
            before = await uow.memory.episodes_for(handles, limit=1, before=(earliest, UUID(int=0)))
            facts = await uow.memory.facts_for(handles, statuses=tuple(FactStatus))
        known: dict[str, datetime] = {}
        for fact in facts:
            if fact.predicate not in known or fact.created_at < known[fact.predicate]:
                known[fact.predicate] = fact.created_at
        return question_gaps(questions, known, returning=bool(before))

    async def _admit(self, caller: Principal, *, elevated: bool = False) -> SpaceConfig:
        _authorize(caller, elevated=elevated)
        now = self._clock.now()
        key = f"insights:calls:{caller.space.space_id}:{_who(caller)}:{now.date().isoformat()}"
        try:
            calls = await self._cache.incr_window(key, 86400)
        except ConnectionError as exc:
            raise _limits_unavailable() from exc
        if calls > self._limits.max_calls_per_day:
            raise RateLimited(_until_tomorrow(now), "daily analytics call limit reached")
        return self._config.space(caller.space.space_id)

    async def _charge(self, caller: Principal, profiles: int) -> None:
        """Profiles touched per person or key per day. Read-then-write, so concurrent calls can overshoot by
        at most one call's page, which the per-call limits bound."""
        if profiles == 0:
            return
        now = self._clock.now()
        key = f"insights:profiles:{caller.space.space_id}:{_who(caller)}:{now.date().isoformat()}"
        try:
            spent = int(await self._cache.get(key) or b"0")
        except ConnectionError as exc:
            raise _limits_unavailable() from exc
        if spent + profiles > self._limits.max_profiles_per_day:
            raise RateLimited(
                _until_tomorrow(now), "daily analytics volume reached; bulk extraction is an export"
            )
        try:
            await self._cache.set(key, str(spent + profiles).encode(), ttl_s=86400)
        except ConnectionError as exc:
            raise _limits_unavailable() from exc

    async def _record(
        self,
        caller: Principal,
        tool: str,
        surface: ReceiptSurface,
        request: dict[str, Any],
        result: Any,
        usage: Usage,
        *,
        profile_id: UUID | None = None,
        purpose: str = "analytics",
    ) -> None:
        await self._charge(caller, usage.profiles)
        space_id = caller.space.space_id
        draft = ReceiptDraft(
            receipt_id=uuid7(),
            space_id=space_id,
            producer_id=self._producer,
            producer_seq=next(self._seq),
            kind=ReceiptKind.ANALYTICS,
            surface=surface,
            audience=Audience.ANALYST,
            read_at=self._clock.now(),
            profile_id=profile_id,
            source_id=caller.source_id if isinstance(caller, SourcePrincipal) else None,
            person_id=caller.user_id if isinstance(caller, PersonPrincipal) else None,
            purpose=purpose,
            item_ids=usage.items[:200],
            query_hash=await self._keys.hmac(
                space_id, "insights_query", fingerprint({"tool": tool, **request})
            ),
            extra={
                "tool": tool,
                "profiles": str(usage.profiles),
                "result_hash": result_hash(_plain(result)),
                **usage.extra,
                **person_extra(caller),
            },
        )
        if not await self._receipts.emit([draft], self._limits.receipt_timeout_s):
            raise Unavailable("the analytics receipt could not be recorded")

    async def _open(self, space_id: UUID, sealed: Sealed) -> str:
        return (
            await self._keys.decrypt(space_id, sealed.ciphertext, sealed.aad, sealed.dek_version)
        ).decode()

    async def _redact(self, texts: list[str]) -> list[str]:
        if not texts:
            return []
        spans = await self._models.pii(texts)
        return [
            redact(t, [(s.start, s.end, s.kind) for s in found])
            for t, found in zip(texts, spans, strict=True)
        ]

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH):
            batch, _ = await self._models.embed(texts[start : start + EMBED_BATCH])
            vectors.extend(batch)
        return vectors


def _source_hints(config: SpaceConfig, subjects: set[str]) -> dict[str, list[SourceHint]]:
    """Which sources could record each subject: the words of their mappings, and the schema's predicates
    for every source whose conversations extraction reads."""
    names = {s.source_id: s.name for s in config.sources}
    mapped = [
        MappedType(
            str(m.source_id),
            names.get(m.source_id, str(m.source_id)),
            tuple(
                w
                for rule in m.rules
                for w in (rule.canonical_type or "", *(o.type for o in rule.object_refs), *rule.fields)
                if w
            ),
        )
        for m in config.mappings
    ]
    declared = {p.name for p in config.extraction.predicates}
    talking = [(str(s.source_id), s.name) for s in config.sources if s.channel != "erp"]
    predicates = {name: talking for name in declared}
    return source_hints(subjects, mapped, predicates, VALUE_TYPES)


def _authorize(caller: Principal, *, elevated: bool) -> None:
    if isinstance(caller, SourcePrincipal):
        needed = {Scope.ANALYTICS, Scope.ADMIN} if elevated else {Scope.ANALYTICS}
        if not needed <= caller.scopes:
            raise ScopeMissing(
                "the key lacks the analytics scope" + (" and the admin scope" if elevated else "")
            )
        return
    if caller.allowed_sources is not None:
        raise Forbidden("vendor people only see their own sources")
    if not caller.roles & (REVEAL_ROLES if elevated else READ_ROLES):
        raise Forbidden("the role cannot do this")


def _who(caller: Principal) -> str:
    return f"key:{caller.key_id}" if isinstance(caller, SourcePrincipal) else f"person:{caller.user_id}"


def _allowed(policy: Policy) -> frozenset[str]:
    """Categories an explicit rule opens to the analyst audience (5.21, item 3)."""
    return frozenset(
        r.when.category
        for r in policy.rules
        if r.effect == "allow" and r.when.audience is Audience.ANALYST and r.when.category
    )


def _denied_categories(policy: Policy) -> frozenset[str]:
    """Sensitive and restricted categories stay out of analytics unless the policy opens them to analysts."""
    denied = {c.name for c in policy.categories if c.sensitivity is not Sensitivity.NORMAL}
    denied |= {
        r.when.category
        for r in policy.rules
        if r.effect == "deny" and r.when.category and r.when.audience in (None, Audience.ANALYST)
    }
    return frozenset(denied) - _allowed(policy)


async def _profile(uow: UnitOfWork, pseudonym: str) -> ProfileRef:
    ref = await uow.insights.profile_by_pseudonym(pseudonym)
    if ref is None:
        raise NotFound("unknown pseudonym")
    return ref


def _limits_unavailable() -> Unavailable:
    return Unavailable("the analytics limits cannot be checked right now", retry_after_s=LIMITS_RETRY_S)


def _until_tomorrow(now: datetime) -> int:
    tomorrow = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=now.tzinfo or UTC)
    return max(1, int((tomorrow - now).total_seconds()))


def _plain(value: Any) -> Any:
    """Dataclasses as dicts, so the result hash covers what the caller receives."""
    if hasattr(value, "__dataclass_fields__"):
        return {k: _plain(getattr(value, k)) for k in value.__dataclass_fields__}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    return value
