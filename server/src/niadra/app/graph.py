"""The Console's relationship graph (5.22): one customer's relations, or the space's most connected customers
and companies, with the agents and systems that touched them.

What a reader sees follows the customer page exactly: identifiers masked as the customer list masks them,
the identifiers a claim joined hidden from the roles that may not see them (`governance_view`), and each
object and agent action through the policy of the reader's role (`ConsoleView`). What is left out is
counted in `withheld`. Erased and merged-away profiles are retired and never read.

A graph costs a fixed number of statements: one transaction, per level of depth the handles, the ties,
the objects, the activity and the new profiles, each one statement over the ids already found, and the names
of every customer in it at the end (`app.profile_names`, four statements;
`tests/integration/graph/test_pg_graph.py`).
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from niadra.app.derived import match_values, named_by
from niadra.app.profile_names import Shown, shown_profiles
from niadra.app.profile_view import ConsoleView
from niadra.app.typed_state import Attribution, lifecycle_stage
from niadra.config.models import SpaceConfig
from niadra.contracts.graph import Graph, GraphEdge, GraphNode
from niadra.domain.common.errors import NiadraError, NotFound
from niadra.domain.common.space import Principal
from niadra.domain.compile import MemorySnapshot
from niadra.domain.coordinate.destination import SUBJECT_HMAC, subject_canonical
from niadra.domain.graph import (
    AROUND_LIMIT,
    OBJECTS_PER_CUSTOMER,
    OVERVIEW_LIMIT,
    PROFILE_KINDS,
    Built,
    Degree,
    EdgeKind,
    GraphBuilder,
    NodeKind,
    Pin,
    handle_channel,
    handle_key,
    handle_label,
    most_connected,
    object_key,
    plain_name,
    profile_key,
    source_key,
    source_kind,
    strength,
    systems_of,
    target_key,
    target_label,
)
from niadra.domain.identity.provenance import governance_view
from niadra.domain.memory.bound import MAX_ROWS
from niadra.domain.policy import Effect
from niadra.domain.records import AgentAction, BusinessObject, HandleRecord, ProfileRecord
from niadra.domain.vocabulary import SubjectKind
from niadra.ports.infra import Clock, ConfigProvider, KeyVault
from niadra.ports.repos.graph import Activity, Acts, Reports, Talk, Tie
from niadra.ports.signals import NoObjectInstructions, ObjectInstructions
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

OVERVIEW_WINDOW = 4
"""The overview walks this many handles per customer slot, the latest active first, for its candidates."""
_WALKS = (SubjectKind.ACCOUNT, SubjectKind.PARTNER, SubjectKind.PERSON)


class GraphService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        keys: KeyVault,
        clock: Clock,
        config: ConfigProvider,
        instructions: ObjectInstructions | None = None,
    ) -> None:
        """`instructions` holds what people said must or must not be done about an object: the edge from a
        person to an object counts them."""
        self._uow = uow
        self._keys = keys
        self._clock = clock
        self._config = config
        self._instructions = instructions or NoObjectInstructions()

    async def around(
        self, principal: Principal, profile_id: UUID, *, depth: int = 1, limit: int = AROUND_LIMIT
    ) -> Graph:
        """The customer and what relates to them, `depth` levels out. A level expands customers and
        companies only: an agent, a system or an identifier never brings in everyone it touched."""
        space_id = principal.space.space_id
        config = self._config.space(space_id)
        async with self._uow(space_id) as uow:
            center = await uow.identity.get_profile(profile_id)
            if center is None:
                raise NotFound("profile not found")
            walk = _Walk(
                principal, config, uow, self._keys, center=center.profile_id, instructions=self._instructions
            )
            frontier = [walk.add_profile(center, hop=0, pin=Pin.CENTER)]
            for hop in range(1, depth + 1):
                frontier = await walk.expand(frontier, hop, last=hop == depth)
            walk.name(await shown_profiles(uow, principal, config, walk.views.values()))
        walk.relate()
        return _graph(str(profile_id), walk.graph.build(limit), self._clock.now())

    async def overview(self, principal: Principal, *, limit: int = OVERVIEW_LIMIT) -> Graph:
        """The most connected customers and companies among the latest active, their ties to each other,
        and the agents and systems that talked with them or worked on their objects."""
        space_id = principal.space.space_id
        config = self._config.space(space_id)
        window = limit * OVERVIEW_WINDOW
        async with self._uow(space_id) as uow:
            # One walk per kind: each reads `ix_handles_last_seen` in order and stops at its window.
            candidates = [p for kind in _WALKS for p in await uow.identity.recent_profiles({kind}, window)]
            profiles = [governance_view(principal, p) for p in await uow.identity.get_profiles(candidates)]
            # Identifiers are no nodes here: the ties and totals are read through the ones the reader sees.
            handles = [h for p in profiles for h in p.handle_ids]
            ties = await uow.graph.ties(handles)
            activity = await uow.graph.activity(
                profile_ids=[p.profile_id for p in profiles], handle_ids=handles, owner_handle_ids=handles
            )
            sources = _ranked_sources(activity, config, limit)
            degrees = _degrees(profiles, ties, activity)
            top = most_connected(degrees.values(), limit - len(sources))
            chosen = {d.profile_id for d in top}
            by_id = {p.profile_id: p for p in profiles}
            names = await shown_profiles(uow, principal, config, [by_id[p] for p in chosen])
        walk = _Walk(principal, config, None, self._keys, shows_handles=False)
        for degree in top:
            walk.add_profile(
                by_id[degree.profile_id],
                hop=0,
                links=degree.links,
                objects=degree.objects,
                interactions=degree.interactions,
                score=degree.score,
            )
        for source_id in sources:
            walk.source(source_id, hop=0)
        walk.name(names)
        walk.tie_edges(ties, keep=chosen)
        walk.activity_edges(activity, {p: by_id[p] for p in chosen}, hop=0)
        built = walk.graph.build(limit)
        truncated = built.truncated or len(top) < len(degrees)
        return _graph(None, Built(built.nodes, built.edges, truncated, built.withheld), self._clock.now())


@dataclass
class _Walk:
    """One graph being read: the nodes so far, and what each node's reader view and policy decided."""

    principal: Principal
    config: SpaceConfig
    uow: UnitOfWork | None
    keys: KeyVault
    center: UUID | None = None
    shows_handles: bool = True
    """Whether identifiers are nodes, so one hidden from the reader is a node withheld."""
    graph: GraphBuilder = field(default_factory=GraphBuilder)
    views: dict[UUID, ProfileRecord] = field(default_factory=dict)
    hidden: set[UUID] = field(default_factory=set)
    handle_owner: dict[UUID, UUID] = field(default_factory=dict)
    objects: dict[UUID, BusinessObject] = field(default_factory=dict)
    instructions: ObjectInstructions = field(default_factory=NoObjectInstructions)
    said: dict[tuple[UUID, UUID], dict[str, int]] = field(default_factory=dict)
    """(profile, object) -> how many observations and instructions the person said about the object."""

    def __post_init__(self) -> None:
        self.names = {s.source_id: s.name for s in self.config.sources}
        self.by_name = {plain_name(s.name): s.source_id for s in self.config.sources}
        self.systems = systems_of(
            [
                ({n: spec.kind for n, spec in t.sources.items()}, t.mirror_of.system if t.mirror_of else None)
                for t in self.config.object_types.types
            ]
        )

    def add_profile(self, profile: ProfileRecord, *, hop: int, pin: int = 0, **meta: Any) -> ProfileRecord:
        """Adds the customer as this reader sees them; an identifier hidden from the reader is withheld."""
        if (seen := self.views.get(profile.profile_id)) is not None:
            self._profile_node(seen, hop, pin, meta)
            return seen
        view = governance_view(self.principal, profile)
        self.views[view.profile_id] = view
        self.hidden |= set(view.hidden_handle_ids)
        if self.shows_handles:
            self.graph.withhold(len(view.hidden_handle_ids))
        for handle_id in view.handle_ids:
            self.handle_owner[handle_id] = view.profile_id
        self._profile_node(view, hop, pin, meta)
        return view

    def _profile_node(self, view: ProfileRecord, hop: int, pin: int, meta: Mapping[str, Any]) -> None:
        self.graph.node(
            profile_key(view.profile_id),
            PROFILE_KINDS[view.kind],
            view.pseudonym,
            str(view.profile_id),
            hop,
            pin=pin,
            pseudonym=view.pseudonym,
            handles=len(view.handle_ids),
            **meta,
        )

    def name(self, shown: Mapping[UUID, Shown]) -> None:
        """Each customer's node goes by the name or the contact its reader sees (`app.profile_names`); the
        pseudonym stays in `meta` as a reference, and names the node only when neither is known."""
        for profile_id, found in shown.items():
            self.graph.rename(
                profile_key(profile_id),
                found.name or found.contact,
                display_name=found.name,
                display_contact=found.contact,
            )

    async def expand(self, frontier: Sequence[ProfileRecord], hop: int, *, last: bool) -> list[ProfileRecord]:
        """The level `hop` around `frontier`: its identifiers, ties, objects and activity, and the customers
        and companies the ties reach, which the next level expands unless this is the `last`."""
        assert self.uow is not None
        uow = self.uow
        handle_ids = [h for p in frontier for h in p.handle_ids]
        handles = await uow.identity.get_handles(handle_ids)
        for handle in handles:
            self._handle(handle, hop)
        ties = await uow.graph.ties(handle_ids)
        involved = await uow.objects.objects_involving(handle_ids, per_handle=OBJECTS_PER_CUSTOMER)
        subjects = await self._subjects(handles)
        await self._count_said(handle_ids, [obj for obj, _ in involved])
        activity = await uow.graph.activity(
            profile_ids=[p.profile_id for p in frontier],
            object_ids=[obj.object_id for obj, _ in involved],
            handle_ids=handle_ids,
            subject_hmacs=list(subjects),
            customers={p.profile_id: p.handle_ids for p in frontier},
        )
        reached = {
            side
            for tie in ties
            for side in (tie.a_profile, tie.b_profile)
            if side is not None and side not in self.views and not ({tie.a, tie.b} & self.hidden)
        }
        found = await uow.identity.get_profiles(sorted(reached)) if reached else []
        own = self._own_ties(ties)
        added = [self.add_profile(p, hop=hop, pin=Pin.TIE if p.profile_id in own else 0) for p in found]
        self.tie_edges(ties)
        mine = self._place(frontier, involved, hop, activity.involved)
        self.activity_edges(
            activity, {p.profile_id: p for p in frontier}, hop, objects=mine, subjects=subjects
        )
        return [] if last else added

    def _own_ties(self, ties: Iterable[Tie]) -> set[UUID]:
        """The customers the center's own ties reach: its companies and partners and its look-alikes. Pinned
        with the center; an organization's members are not, they may be many."""
        if self.center is None:
            return set()
        found: set[UUID] = set()
        for tie in ties:
            if tie.kind == "link" and tie.a_profile == self.center and tie.b_profile is not None:
                found.add(tie.b_profile)
            elif tie.kind != "link" and self.center in (tie.a_profile, tie.b_profile):
                found |= {p for p in (tie.a_profile, tie.b_profile) if p is not None}
        return found - {self.center}

    def _handle(self, handle: HandleRecord, hop: int) -> None:
        owner = self.handle_owner.get(handle.handle_id)
        if owner is None:
            return
        level, weight = strength(handle)
        key = self.graph.node(
            handle_key(handle.handle_id),
            NodeKind.HANDLE,
            handle_label(handle),
            None,
            hop,
            pin=Pin.IDENTIFIER if owner == self.center else 0,
            type=handle.type.value,
            channel=handle_channel(handle),
            weight=weight,
            provisional=handle.provisional,
            last_seen=handle.last_seen.isoformat(),
        )
        self.graph.edge(
            key,
            profile_key(owner),
            EdgeKind.IDENTIFIES,
            strength=level,
            weight=weight,
            since=_at(handle.first_seen),
        )

    async def _subjects(self, handles: Iterable[HandleRecord]) -> dict[bytes, UUID]:
        """The keyed hashes coordination names these handles by (24.3.4), each with its customer."""
        assert self.uow is not None
        found: dict[bytes, UUID] = {}
        for handle in handles:
            owner = self.handle_owner.get(handle.handle_id)
            if owner is None or not handle.value:
                continue
            try:
                canonical, _ = subject_canonical(
                    handle.type, handle.value, handle.scope or None, handle.subject_kind
                )
            except NiadraError:
                continue  # a handle coordination never keys a subject by
            digest = await self.keys.hmac(self.uow.space_id, SUBJECT_HMAC, canonical.encode())
            found[digest] = owner
        return found

    def _place(
        self,
        frontier: Sequence[ProfileRecord],
        involved: Sequence[tuple[BusinessObject, tuple[tuple[UUID, str], ...]]],
        hop: int,
        totals: Mapping[UUID, int],
    ) -> dict[UUID, UUID]:
        """Adds each customer's `OBJECTS_PER_CUSTOMER` latest objects the reader's policy lets them see, its
        owner before a participant (the customer page's roles), and returns each placed object's customer.
        The customer's node says how many it has in all (`objects_total`); fewer shown cuts the graph."""
        ids = {p.profile_id for p in frontier}
        roles: dict[UUID, dict[UUID, str]] = defaultdict(dict)
        for obj, taking_part in involved:
            owner = self.handle_owner.get(obj.origin_handle_id) if obj.origin_handle_id else None
            if owner in ids:
                roles[owner][obj.object_id] = "owner"
            for handle_id, _ in taking_part:
                if (profile_id := self.handle_owner.get(handle_id)) in ids:
                    roles[profile_id].setdefault(obj.object_id, "participant")
        objects = {obj.object_id: obj for obj, _ in involved}
        newest = {obj.object_id: n for n, (obj, _) in enumerate(involved)}
        placed: dict[UUID, UUID] = {}
        denied: set[UUID] = set()
        for profile in frontier:
            total = totals.get(profile.profile_id, 0)
            self.graph.annotate(profile_key(profile.profile_id), objects_total=total)
            latest = sorted(roles.get(profile.profile_id, {}).items(), key=lambda item: newest[item[0]])
            mine = dict(latest[:OBJECTS_PER_CUSTOMER])
            if total > len(mine):
                self.graph.cut = True
            if not mine:
                continue
            view = self._console(profile, [objects[o] for o in mine])
            for object_id, role in mine.items():
                effect = view.effect(f"object:{object_id}")
                if effect is None:
                    denied.add(object_id)
                    continue
                self._object(objects[object_id], effect, hop)
                placed.setdefault(object_id, profile.profile_id)
                said = self.said.get((profile.profile_id, object_id), {})
                self.graph.edge(
                    profile_key(profile.profile_id), object_key(object_id), EdgeKind.OWNS, role=role, **said
                )
        self.graph.withhold(len(denied - set(placed)))
        return placed

    async def _count_said(self, handle_ids: Sequence[UUID], objects: Sequence[BusinessObject]) -> None:
        """What each person said about each object: two statements, none without objects."""
        assert self.uow is not None
        if not objects:
            return
        by_key = {o.key: o.object_id for o in objects}
        refs = {f"{o.key.type}:{o.key.namespace}:{o.key.external_id}": o.object_id for o in objects}
        facts = await self.uow.memory.object_facts(list(by_key), handle_ids, per_object=MAX_ROWS)
        held = await self.instructions.held(self.uow, self.uow.space_id, handle_ids, sorted(refs))
        counts: dict[tuple[UUID, UUID], dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for fact in facts:
            owner = self.handle_owner.get(fact.origin_handle_id)
            if owner is not None and fact.object_ref in by_key:
                counts[(owner, by_key[fact.object_ref])]["observations"] += 1
        for said in held:
            owner = self.handle_owner.get(said.handle_id)
            if owner is not None and said.object in refs:
                counts[(owner, refs[said.object])]["instructions"] += 1
        for key, found in counts.items():
            self.said[key] = dict(found)

    def _console(
        self,
        profile: ProfileRecord,
        objects: Sequence[BusinessObject] = (),
        actions: Sequence[AgentAction] = (),
    ) -> ConsoleView:
        snapshot = MemorySnapshot(profile=profile, objects=tuple(objects), actions=tuple(actions))
        return ConsoleView.of(self.principal, self.config, snapshot)

    def _object(self, obj: BusinessObject, effect: Effect, hop: int) -> None:
        masked = effect is Effect.MASK
        declared = self.config.object_types.get(obj.key.type)
        state: str | None = None
        if not masked:
            if declared is not None:
                stage = lifecycle_stage(obj.fields, declared, Attribution(declared, self.names))
                state = stage.state if stage else None
            elif isinstance(found := obj.state.get("state"), str):
                state = found
            self.objects[obj.object_id] = obj
        self.graph.node(
            object_key(obj.object_id),
            NodeKind.OBJECT,
            f"{obj.key.type} {obj.key.external_id}",
            str(obj.object_id),
            hop,
            type=obj.key.type,
            namespace=obj.key.namespace,
            external_id=obj.key.external_id,
            state=state,
            state_label=None,
            masked=masked,
            as_of=_at(obj.as_of),
        )

    def source(self, source_id: UUID, hop: int) -> str:
        name = self.names.get(source_id)
        kind = source_kind(name, self.systems) if name else NodeKind.AGENT
        settings = self.config.source(source_id)
        return self.graph.node(
            source_key(source_id),
            kind,
            name or f"source {str(source_id)[:8]}",
            str(source_id),
            hop,
            channel=settings.channel if settings else None,
            vendor=settings.vendor if settings else None,
        )

    def _target(self, target: str, hop: int) -> str:
        """A handoff's target: the agent it names when it is one of the space's sources, else the queue or
        team it names, which has no screen of its own."""
        named = self.by_name.get(plain_name(target_label(target)))
        if named is not None:
            return self.source(named, hop)
        return self.graph.node(
            target_key(target), NodeKind.AGENT, target_label(target), None, hop, target=target
        )

    def tie_edges(self, ties: Iterable[Tie], keep: set[UUID] | None = None) -> None:
        """Links and open suggestions between customers already in the graph (`keep`: only those)."""
        for tie in ties:
            a, b = tie.a_profile, tie.b_profile
            if a is None or b is None or a == b or {tie.a, tie.b} & self.hidden:
                continue
            if keep is not None and not {a, b} <= keep:
                continue
            first, second = profile_key(a), profile_key(b)
            match tie.kind:
                case "link":
                    org = self.views.get(b)
                    kind = (
                        EdgeKind.PARTNER_OF if org and org.kind is SubjectKind.PARTNER else EdgeKind.MEMBER_OF
                    )
                    self.graph.edge(first, second, kind, role=tie.role, since=_at(tie.since))
                case "suggestion":
                    self.graph.edge(first, second, EdgeKind.SUGGESTED_SAME, score=tie.score)

    def activity_edges(
        self,
        activity: Activity,
        frontier: Mapping[UUID, ProfileRecord],
        hop: int,
        *,
        objects: Mapping[UUID, UUID] | None = None,
        subjects: Mapping[bytes, UUID] | None = None,
    ) -> None:
        """Who talked with, acted on, reported about or handed off the customers of `frontier` and the
        objects placed for them (`objects`, each with its customer). An action reaches the graph only when
        the reader's policy lets them see it, as on the customer page."""
        placed = objects or {}
        for talk in activity.talks:
            if talk.profile_id in frontier:
                agent = self.source(talk.source_id, hop)
                self.graph.edge(
                    agent,
                    profile_key(talk.profile_id),
                    EdgeKind.TALKED_WITH,
                    count=talk.count,
                    last_at=_at(talk.last_at),
                    channels=list(talk.channels),
                )
        seen: dict[UUID, list[tuple[Acts, str]]] = defaultdict(list)
        for acts in activity.acts:
            target, profile_id = self._acted(acts.object_id, acts.handle_id, placed)
            if target is not None and profile_id is not None and profile_id in frontier:
                seen[profile_id].append((acts, target))
        for profile_id, rows in seen.items():
            view = self._console(frontier[profile_id], actions=[acts.latest for acts, _ in rows])
            for acts, target in rows:
                # Every action of a customer reads under the same attributes (5.22), so its newest decides.
                if view.effect(f"action:{acts.latest.action_id}") is None:
                    continue
                agent = self.source(acts.source_id, hop)
                self.graph.edge(
                    agent,
                    target,
                    EdgeKind.ACTED_ON,
                    count=acts.count,
                    last_at=_at(acts.last_at),
                    operations=list(acts.operations),
                )
        for reports in activity.reports:
            target, profile_id = self._acted(reports.object_id, reports.handle_id, placed)
            if target is not None and profile_id in frontier:
                system = self.source(reports.source_id, hop)
                self.graph.edge(
                    system, target, EdgeKind.REPORTED, count=reports.count, last_at=_at(reports.last_at)
                )
        for handoff in activity.handoffs:
            about = (subjects or {}).get(handoff.subject_hmac)
            if about is None or about not in frontier:
                continue
            agent = self.source(handoff.source_id, hop)
            self.graph.edge(
                agent,
                self._target(handoff.target, hop),
                EdgeKind.HANDED_OFF,
                count=handoff.count,
                status=handoff.status,
                outcome=handoff.outcome,
                last_at=_at(handoff.last_at),
                about=profile_key(about),
            )

    def _acted(
        self, object_id: UUID | None, handle_id: UUID | None, placed: Mapping[UUID, UUID]
    ) -> tuple[str | None, UUID | None]:
        """The node an action or a report lands on, and its customer: a placed object, or the customer of
        the handle (an action that closes a promise; the overview's totals by owner)."""
        if object_id is not None:
            owner = placed.get(object_id)
            return (object_key(object_id), owner) if owner is not None else (None, None)
        owner = self.handle_owner.get(handle_id) if handle_id is not None else None
        return (profile_key(owner), owner) if owner is not None else (None, None)

    def relate(self) -> None:
        """Relations between objects already in the graph: a composite's path names the other, or a reverse
        relation finds it by the value it holds. Read from the states in hand; a masked object relates
        nothing, since its state is what the relation reads."""
        by_key = {(o.key.type, o.key.namespace, o.key.external_id): o for o in self.objects.values()}
        for obj in self.objects.values():
            declared = self.config.object_types.get(obj.key.type)
            if declared is None:
                continue
            for name, relation in declared.relations.items():
                related = [
                    found.object_id
                    for entry in named_by(relation, obj.key.namespace, obj.state)
                    if (found := by_key.get((entry.type, entry.namespace, entry.external_id))) is not None
                ]
                for value in match_values(relation, obj.state):
                    related += [
                        other.object_id
                        for other in self.objects.values()
                        if other.key.type == relation.type and (relation.match, value) in other.typed.matched
                    ]
                for other_id in dict.fromkeys(related):
                    self.graph.edge(
                        object_key(obj.object_id), object_key(other_id), EdgeKind.RELATES_TO, relation=name
                    )


def _ranked_sources(activity: Activity, config: SpaceConfig, limit: int) -> list[UUID]:
    """The space's sources for the overview, the busiest first, at most a quarter of its nodes."""
    busy: dict[UUID, int] = defaultdict(int)
    counted: list[Talk | Acts | Reports] = [*activity.talks, *activity.acts, *activity.reports]
    for row in counted:
        busy[row.source_id] += row.count
    configured = [s.source_id for s in config.sources]
    ordered = sorted(configured, key=lambda s: (-busy.get(s, 0), str(s)))
    return ordered[: max(1, limit // 4)]


def _degrees(
    profiles: Sequence[ProfileRecord], ties: Sequence[Tie], activity: Activity
) -> dict[UUID, Degree]:
    owner = {h: p.profile_id for p in profiles for h in p.handle_ids}
    links: dict[UUID, int] = defaultdict(int)
    for tie in ties:
        if tie.kind == "suggestion" or tie.a_profile == tie.b_profile:
            continue
        for side in {tie.a_profile, tie.b_profile}:
            if side is not None:
                links[side] += 1
    objects: dict[UUID, int] = defaultdict(int)
    for handle_id, count in activity.owned.items():
        if handle_id in owner:
            objects[owner[handle_id]] += count
    interactions: dict[UUID, int] = defaultdict(int)
    last: dict[UUID, datetime] = {}
    for talk in activity.talks:
        interactions[talk.profile_id] += talk.count
        last[talk.profile_id] = max(last.get(talk.profile_id, talk.last_at), talk.last_at)
    return {
        p.profile_id: Degree(
            p.profile_id,
            p.kind.is_organization,
            handles=len(p.handle_ids),
            links=links[p.profile_id],
            objects=objects[p.profile_id],
            interactions=interactions[p.profile_id],
            last_seen=last.get(p.profile_id),
        )
        for p in profiles
    }


def _graph(center: str | None, built: Built, now: datetime) -> Graph:
    return Graph(
        center=center,
        nodes=[
            GraphNode(id=node_id, kind=node.kind.value, label=node.label, ref=node.ref, meta=node.meta)
            for node_id, node in built.nodes
        ],
        edges=[
            GraphEdge(id=edge_id, from_=source, to=target, kind=edge.kind.value, meta=dict(edge.meta))
            for edge_id, source, target, edge in built.edges
        ],
        truncated=built.truncated,
        withheld=built.withheld,
        generated_at=now,
    )


def _at(moment: datetime) -> str:
    return moment.isoformat()
