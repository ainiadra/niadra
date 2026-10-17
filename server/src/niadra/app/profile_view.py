"""What the Console's reads of one customer share (5.22): the reader's policy and the items' provenance.

A person reads profile data under the same policy as an agent, as the audience class of their role
(`console_reader`), with the compiler's own item attributes, so a governance read and an agent read can
never disagree. The route leaves the `admin` receipt once the read succeeds.
"""

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from niadra.app.compile import compile_settings
from niadra.app.object_memory import gather, memory_out
from niadra.config.models import SpaceConfig
from niadra.contracts.common import ObjectRef
from niadra.contracts.memory import (
    EpisodeOut,
    FactContradictionOut,
    FactHistory,
    FactOut,
    FactRelationOut,
    OpenItemOut,
    Origin,
    ProfileMemory,
    TraitEvidenceOut,
    TraitOut,
)
from niadra.contracts.objects import ActionOut, ProfileObject, ProfileObjects
from niadra.domain.common.errors import NotFound
from niadra.domain.common.space import Principal
from niadra.domain.compile import CompileInput, CompileSettings, MemorySnapshot, Variant
from niadra.domain.compile.candidates import policy_attrs
from niadra.domain.identity.provenance import governance_view
from niadra.domain.memory.bound import for_reader
from niadra.domain.memory.facts import Relation
from niadra.domain.memory.items import evidence_event_ids, noted_at
from niadra.domain.notify.cursor import decode_cursor, encode_cursor
from niadra.domain.policy import Effect, ItemAttrs, Reader, evaluate
from niadra.domain.policy.console import CONSOLE_CONTEXT, console_reader
from niadra.domain.records import (
    AgentAction,
    BusinessObject,
    Episode,
    EventRecord,
    Fact,
    ObjectKey,
    OpenItem,
    ProfileRecord,
    Trait,
)
from niadra.domain.vocabulary import FactStatus, OpenItemStatus, RelationType
from niadra.ports.signals import NoObjectInstructions, ObjectInstructions
from niadra.ports.uow import UnitOfWork

TIMELINE_LIMIT = 200
OBJECTS_LIMIT = 50
ACTIONS_LIMIT = 200
"""A page of the Console's view of a customer: each list says where the next page starts (`*_next`) when it
holds more, never a silent cut."""


@dataclass(frozen=True)
class ConsoleReading:
    """Who reads in the Console and the settings their reads compile under: worked out once for a page of
    customers, then one `ConsoleView` per customer."""

    reader: Reader
    config: SpaceConfig
    settings: CompileSettings

    @classmethod
    def of(cls, principal: Principal, config: SpaceConfig) -> "ConsoleReading":
        settings = compile_settings(config)
        declared = frozenset(
            {p for c in config.policy.categories for p in c.purposes}
            | {p for s in config.sources for p in s.purposes}
            | {p for purposes in settings.trait_purposes.values() for p in purposes}
            | {p.name for p in config.purposes.purposes}
        )
        return cls(console_reader(principal, declared), config, settings)

    def view(self, snapshot: MemorySnapshot) -> "ConsoleView":
        inp = CompileInput(snapshot, Variant(audience=self.reader.audience), self.settings)
        return ConsoleView(self.reader, self.config, policy_attrs(inp))


@dataclass
class ConsoleView:
    reader: Reader
    config: SpaceConfig
    attrs: Mapping[str, ItemAttrs]
    withheld: Counter[str] = field(default_factory=Counter)

    @classmethod
    def of(cls, principal: Principal, config: SpaceConfig, snapshot: MemorySnapshot) -> "ConsoleView":
        return ConsoleReading.of(principal, config).view(snapshot)

    def effect(self, item_key: str) -> Effect | None:
        """ALLOW or MASK for items the reader may see; None for a denied one, which is only counted."""
        # Quarantine keeps injected content away from agents; people reviewing the memory must see it.
        attrs = replace(self.attrs[item_key], quarantined=False)
        decision = evaluate(self.config.policy, self.reader, attrs, CONSOLE_CONTEXT)
        if not decision.included:
            self.withheld[decision.reason.value if decision.reason else "policy"] += 1
            return None
        return decision.effect


async def load_profile(uow: UnitOfWork, profile_id: UUID) -> ProfileRecord:
    profile = await uow.identity.get_profile(profile_id)
    if profile is None:
        raise NotFound("profile not found")
    return profile


async def event_origins(uow: UnitOfWork, event_ids: Iterable[UUID]) -> dict[UUID, Origin]:
    """Provenance of events: metadata only, never their content."""
    wanted = sorted(set(event_ids))
    return {e.event_id: origin_of(e) for e in await uow.ingest.get_events(wanted)} if wanted else {}


def origin_of(event: EventRecord) -> Origin:
    return Origin(
        event_id=str(event.event_id),
        kind=event.kind,
        source_id=str(event.source_id),
        channel=event.channel,
        at=event.occurred_at,
        speaker=event.speaker,
        conversation_id=event.conversation_id,
        task_id=event.task_id,
    )


def earliest(origins: Mapping[UUID, Origin], event_ids: Iterable[UUID]) -> Origin | None:
    found = [origins[e] for e in event_ids if e in origins]
    return (
        min(found, key=lambda o: (o.at or datetime.min.replace(tzinfo=UTC), o.event_id or ""))
        if found
        else None
    )


async def read_memory(
    uow: UnitOfWork,
    principal: Principal,
    config: SpaceConfig,
    profile_id: UUID,
    now: datetime,
    *,
    timeline_cursor: str | None = None,
) -> ProfileMemory:
    """Facts, open items and promises, the episode timeline and traits, every status included; the timeline
    is paged, newest first, and says where its next page starts."""
    profile = await load_profile(uow, profile_id)
    scoped = governance_view(principal, profile)
    handles = scoped.handle_ids
    page, timeline_next = _page(
        await uow.memory.episodes_for(
            handles, limit=TIMELINE_LIMIT + 1, before=decode_cursor(timeline_cursor)
        ),
        TIMELINE_LIMIT,
        lambda e: (e.ended_at, e.episode_id),
    )
    snapshot = MemorySnapshot(
        profile=scoped,
        facts=tuple(await uow.memory.facts_for(handles, statuses=tuple(FactStatus))),
        episodes=tuple(page),
        open_items=tuple(await uow.memory.open_items_for(handles, statuses=tuple(OpenItemStatus))),
        traits=tuple(await traits_in_view(uow, scoped)),
    )
    unconfirmed, unconfirmed_traits = await _unconfirmed(uow, scoped)
    events = [e for f in snapshot.facts for e in f.evidence_event_ids]
    events += [e for i in snapshot.open_items for e in evidence_event_ids(i)]
    events += [ref for t in snapshot.traits for kind, ref in t.evidence if kind == "event"]
    origins = await event_origins(uow, events)
    view = ConsoleView.of(principal, config, snapshot)
    episodes = {e.episode_id: _episode_origin(e) for e in snapshot.episodes}
    known = {**{str(k): v for k, v in origins.items()}, **{str(k): v for k, v in episodes.items()}}
    conflicts = contradictions(await uow.memory.relations_for(handles), snapshot.facts)
    facts = [
        _marked(_fact(view, f, origins, conflicts.get(f.fact_id, ())), f.origin_handle_id in unconfirmed)
        for f in snapshot.facts
    ]
    items = [
        _marked(_open_item(view, i, origins), i.origin_handle_id in unconfirmed) for i in snapshot.open_items
    ]
    timeline = [_marked(_episode(view, e), e.origin_handle_id in unconfirmed) for e in snapshot.episodes]
    traits = [_marked(_trait(view, t, known, now), t.trait_id in unconfirmed_traits) for t in snapshot.traits]
    return ProfileMemory(
        profile_id=str(profile_id),
        policy_version=config.policy.version,
        audience=view.reader.audience,
        facts=[f for f in facts if f],
        open_items=[i for i in items if i],
        timeline=[e for e in timeline if e],
        timeline_next=timeline_next,
        traits=[t for t in traits if t],
        withheld=dict(sorted(view.withheld.items())),
    )


async def read_fact_history(
    uow: UnitOfWork, principal: Principal, config: SpaceConfig, profile_id: UUID, fact_id: UUID
) -> FactHistory:
    """Every value the fact's slot held, oldest first, and the edges between them (who replaced whom)."""
    profile = await load_profile(uow, profile_id)
    scoped = governance_view(principal, profile)
    facts = await uow.memory.facts_for(scoped.handle_ids, statuses=tuple(FactStatus))
    target = next((f for f in facts if f.fact_id == fact_id), None)
    if target is None:
        raise NotFound("fact not found")
    edges = await uow.memory.relations_for(scoped.handle_ids)
    unconfirmed = set(scoped.provisional_handle_ids)
    linked = {e.a.id for e in edges if e.b.id == fact_id} | {e.b.id for e in edges if e.a.id == fact_id}
    slot = (target.predicate, target.origin_handle_id, target.object_ref)
    chain = sorted(
        (f for f in facts if (f.predicate, f.origin_handle_id, f.object_ref) == slot or f.fact_id in linked),
        key=lambda f: (f.valid_at, f.created_at, str(f.fact_id)),
    )
    ids = {f.fact_id for f in chain}
    origins = await event_origins(uow, [e for f in chain for e in f.evidence_event_ids])
    view = ConsoleView.of(principal, config, MemorySnapshot(profile=scoped, facts=tuple(facts)))
    conflicts = contradictions(edges, facts)
    versions = [
        out
        for f in chain
        if (
            out := _marked(
                _fact(view, f, origins, conflicts.get(f.fact_id, ())), f.origin_handle_id in unconfirmed
            )
        )
    ]
    return FactHistory(
        profile_id=str(profile_id),
        fact_id=str(fact_id),
        predicate=target.predicate,
        policy_version=config.policy.version,
        versions=versions,
        relations=[
            FactRelationOut(
                from_fact_id=str(e.a.id), to_fact_id=str(e.b.id), type=e.type.value, origin=e.origin
            )
            for e in edges
            if e.a.id in ids and e.b.id in ids
        ],
        withheld=dict(sorted(view.withheld.items())),
    )


async def read_objects(
    uow: UnitOfWork,
    principal: Principal,
    config: SpaceConfig,
    profile_id: UUID,
    instructions: ObjectInstructions | None = None,
    now: datetime | None = None,
    *,
    objects_cursor: str | None = None,
    actions_cursor: str | None = None,
) -> ProfileObjects:
    """Business objects the profile owns or takes part in, each with the agent actions on it and what its
    owner and participants said about it; both lists are paged, newest first, and say where
    their next page starts."""
    profile = await load_profile(uow, profile_id)
    scoped = governance_view(principal, profile)
    handles = scoped.handle_ids
    objects, objects_next = _page(
        await uow.objects.objects_for(handles, limit=OBJECTS_LIMIT + 1, before=decode_cursor(objects_cursor)),
        OBJECTS_LIMIT,
        lambda o: (o.as_of, o.object_id),
    )
    actions, actions_next = _page(
        await uow.objects.actions_for(handles, limit=ACTIONS_LIMIT + 1, before=decode_cursor(actions_cursor)),
        ACTIONS_LIMIT,
        lambda a: (a.occurred_at, a.action_id),
    )
    snapshot = MemorySnapshot(profile=scoped, objects=tuple(objects), actions=tuple(actions))
    unconfirmed = set(scoped.provisional_handle_ids)
    origins = await event_origins(uow, [a.event_id for a in actions])
    view = ConsoleView.of(principal, config, snapshot)
    shown = [out for a in actions if (out := _action(view, a, origins))]
    gathered = await gather(uow, config, objects, instructions or NoObjectInstructions(), now=now)
    said = {m.object_id: m for m in for_reader(gathered, view.reader.audience)}
    listed: list[ProfileObject] = []
    for obj in objects:
        if (effect := view.effect(f"object:{obj.object_id}")) is None:
            continue
        mine = [a for a in shown if a.object_id == str(obj.object_id)]
        memory = said.get(obj.object_id)
        listed.append(
            _object(obj, effect, owner=obj.origin_handle_id in handles, actions=mine).model_copy(
                update={
                    "unconfirmed_link": obj.origin_handle_id in unconfirmed,
                    "memory": memory_out(memory, now or obj.as_of, console=True)
                    if memory is not None and memory.rows
                    else None,
                }
            )
        )
    placed = {a.id for o in listed for a in o.actions}
    return ProfileObjects(
        profile_id=str(profile_id),
        policy_version=config.policy.version,
        audience=view.reader.audience,
        objects=listed,
        objects_next=objects_next,
        actions=[a for a in shown if a.id not in placed],
        actions_next=actions_next,
        withheld=dict(sorted(view.withheld.items())),
    )


def _page[Row](
    rows: list[Row], limit: int, key: Callable[[Row], tuple[datetime, UUID]]
) -> tuple[list[Row], str | None]:
    """The first `limit` rows of a page read one row past it, and the cursor of the next page when that row
    was there."""
    if len(rows) <= limit:
        return rows, None
    page = rows[:limit]
    return page, encode_cursor(*key(page[-1]))


async def traits_in_view(uow: UnitOfWork, shown: ProfileRecord) -> list[Trait]:
    """The profile's traits, every status, without those a hidden handle's rows support (5.3)."""
    traits = await uow.traits.traits_of(shown.profile_id)
    if not shown.hidden_handle_ids or not traits:
        return traits
    tainted = {t.trait_id for t in await uow.traits.traits_with_evidence_from(shown.hidden_handle_ids)}
    return [t for t in traits if t.trait_id not in tainted]


async def _unconfirmed(uow: UnitOfWork, shown: ProfileRecord) -> tuple[set[UUID], set[UUID]]:
    """The unconfirmed links a reader who may see them sees, and the traits their rows support: what the
    Console marks `unconfirmed_link`. Empty for a confirmed view, which holds none of them."""
    provisional = set(shown.provisional_handle_ids)
    if not provisional:
        return set(), set()
    supported = await uow.traits.traits_with_evidence_from(sorted(provisional, key=str))
    return provisional, {t.trait_id for t in supported}


def _marked[Out: FactOut | OpenItemOut | EpisodeOut | TraitOut](
    out: Out | None, unconfirmed: bool
) -> Out | None:
    if out is None or not unconfirmed:
        return out
    return cast(Out, out.model_copy(update={"unconfirmed_link": True}))


def contradictions(
    edges: Iterable[Relation], facts: Iterable[Fact]
) -> dict[UUID, list[FactContradictionOut]]:
    """Each fact's `CONTRADICTS` pairs among the facts a reader sees, both ways, with who found them; a pair
    is live while both facts are active."""
    shown = {f.fact_id: f for f in facts}
    found: dict[UUID, list[FactContradictionOut]] = {}
    for edge in edges:
        a, b = shown.get(edge.a.id), shown.get(edge.b.id)
        if edge.type is not RelationType.CONTRADICTS or a is None or b is None:
            continue
        live = a.status is FactStatus.ACTIVE and b.status is FactStatus.ACTIVE
        for one, other in ((a, b), (b, a)):
            pair = FactContradictionOut(fact_id=str(other.fact_id), origin=edge.origin, live=live)
            found.setdefault(one.fact_id, []).append(pair)
    return {k: sorted(v, key=lambda p: (p.fact_id, p.origin)) for k, v in found.items()}


def _fact(
    view: ConsoleView,
    fact: Fact,
    origins: Mapping[UUID, Origin],
    conflicts: Sequence[FactContradictionOut] = (),
) -> FactOut | None:
    if (effect := view.effect(f"fact:{fact.fact_id}")) is None:
        return None
    masked = effect is Effect.MASK
    return FactOut(
        id=str(fact.fact_id),
        predicate=fact.predicate,
        value=None if masked else fact.value,
        masked=masked,
        category=fact.category,
        sensitivity=fact.sensitivity,
        status=fact.status,
        confidence=fact.confidence,
        valid_at=fact.valid_at,
        invalid_at=fact.invalid_at,
        last_confirmed_at=fact.last_confirmed_at,
        times_seen=fact.times_seen,
        sessions_seen=fact.sessions_seen,
        contradicts=list(conflicts),
        about_handle_id=str(fact.about_handle_id) if fact.about_handle_id else None,
        object=_ref(fact.object_ref),
        evidence_event_ids=[str(e) for e in fact.evidence_event_ids],
        origin=earliest(origins, fact.evidence_event_ids),
    )


def _open_item(view: ConsoleView, item: OpenItem, origins: Mapping[UUID, Origin]) -> OpenItemOut | None:
    if (effect := view.effect(f"open_item:{item.item_id}")) is None:
        return None
    masked = effect is Effect.MASK
    source = item.origin.get("source_id")
    origin = earliest(origins, evidence_event_ids(item)) or Origin(
        source_id=str(source) if source else None, at=noted_at(item)
    )
    return OpenItemOut(
        id=str(item.item_id),
        description=None if masked else item.description,
        masked=masked,
        owner="company" if item.owner == "company" else "customer",
        promise=item.owner == "company",
        status=item.status,
        due_at=item.due_at,
        created_at=item.created_at,
        object=_ref(item.object),
        expected_operation=item.expected_operation,
        closed_by_action_id=str(item.closed_by_action_id) if item.closed_by_action_id else None,
        closed_at=item.closed_at,
        origin=origin,
    )


def _episode(view: ConsoleView, episode: Episode) -> EpisodeOut | None:
    if (effect := view.effect(f"episode:{episode.episode_id}")) is None:
        return None
    masked = effect is Effect.MASK
    return EpisodeOut(
        id=str(episode.episode_id),
        version=episode.version,
        summary=None if masked else episode.summary,
        masked=masked,
        intent=episode.intent,
        category=episode.category,
        normalized_category=episode.normalized_category,
        outcome=episode.outcome,
        resolution=None if masked else episode.resolution,
        sentiment=episode.sentiment,
        session_id=str(episode.session_id),
        started_at=episode.started_at,
        ended_at=episode.ended_at,
        origin=_episode_origin(episode),
    )


def _episode_origin(episode: Episode) -> Origin:
    return Origin(
        source_id=str(episode.source_id),
        channel=episode.channel,
        at=episode.started_at,
        conversation_id=episode.conversation_id,
    )


def _trait(view: ConsoleView, trait: Trait, origins: Mapping[str, Origin], now: datetime) -> TraitOut | None:
    if (effect := view.effect(f"trait:{trait.trait_id}")) is None:
        return None
    masked = effect is Effect.MASK
    return TraitOut(
        id=str(trait.trait_id),
        name=trait.name,
        value=None if masked else trait.value,
        masked=masked,
        layer=trait.layer,
        rule_version=trait.rule_version,
        first_seen=trait.first_seen,
        last_evidence_at=trait.last_evidence_at,
        expires_at=trait.expires_at,
        confidence=trait.confidence,
        active=trait.retracted_at is None and trait.expires_at > now,
        retracted_at=trait.retracted_at,
        evidence=[TraitEvidenceOut(kind=k, id=str(i), origin=origins.get(str(i))) for k, i in trait.evidence],
    )


def _action(view: ConsoleView, action: AgentAction, origins: Mapping[UUID, Origin]) -> ActionOut | None:
    if (effect := view.effect(f"action:{action.action_id}")) is None:
        return None
    masked = effect is Effect.MASK
    return ActionOut(
        id=str(action.action_id),
        operation=action.operation,
        status=action.status,
        result=None if masked else action.result,
        masked=masked,
        purpose=action.purpose,
        occurred_at=action.occurred_at,
        object_id=str(action.object_id) if action.object_id else None,
        closes_item_id=str(action.closes_item_id) if action.closes_item_id else None,
        confirmed_by_event_id=str(action.confirmed_by_event_id) if action.confirmed_by_event_id else None,
        origin=origins.get(action.event_id)
        or Origin(event_id=str(action.event_id), source_id=str(action.source_id), at=action.occurred_at),
    )


def _object(obj: BusinessObject, effect: Effect, *, owner: bool, actions: list[ActionOut]) -> ProfileObject:
    masked = effect is Effect.MASK
    return ProfileObject(
        id=str(obj.object_id),
        ref=ObjectRef(type=obj.key.type, namespace=obj.key.namespace, id=obj.key.external_id),
        role="owner" if owner else "participant",
        state=None if masked else obj.state,
        masked=masked,
        as_of=obj.as_of,
        source_id=str(obj.source_id),
        record_ref=obj.record_ref,
        actions=actions,
    )


def _ref(key: ObjectKey | None) -> ObjectRef | None:
    return ObjectRef(type=key.type, namespace=key.namespace, id=key.external_id) if key else None
