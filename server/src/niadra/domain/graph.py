"""The Console's relationship graph (5.22): who a customer is across channels, which companies and partners
they belong to, what objects they have, and which agents and systems touched them, as nodes and edges.

Pure: the service reads the rows and decides what the reader may see; this module names the nodes, keeps
each once, and cuts the graph to its limit, nearest first. A node's id is opaque and stable within one
answer only (`n1`, `n2`...); `ref` carries the real id the Console links to.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Any
from uuid import UUID

from niadra.domain.identity.masking import mask_listed
from niadra.domain.identity.rules import handle_weight
from niadra.domain.records import HandleRecord
from niadra.domain.vocabulary import HandleType, SubjectKind

MAX_NODES = 300
"""The hard cap of one answer, whatever `limit` asks."""
AROUND_LIMIT = 150
OVERVIEW_LIMIT = 100
OBJECTS_PER_CUSTOMER = 25
"""A customer's objects in one graph, the latest first; the customer's node says how many there are in all
(`objects_total`), so one busy customer never crowds out the rest of the graph."""
ORGANIZATION_SHARE = 3
"""The overview gives organizations at most one in this many of its customer slots, so the people with the
most activity never crowd the companies out, nor the other way round."""
SYSTEM_FEEDS = frozenset({"push", "pull", "batch"})
"""Source kinds of an object type that make the source a system of record: it sends the object's state."""
STRONG_WEIGHTS = frozenset({"verified", "authenticated", "system"})
_NOT_NAME = re.compile(r"[^a-z0-9]+")


class Pin(IntEnum):
    """What a cut keeps first, in this order, whatever the hop or the kind."""

    CENTER = 1
    IDENTIFIER = 2
    TIE = 3


class NodeKind(StrEnum):
    PERSON = "person"
    ACCOUNT = "account"
    PARTNER = "partner"
    HANDLE = "handle"
    OBJECT = "object"
    AGENT = "agent"
    SYSTEM = "system"


class EdgeKind(StrEnum):
    IDENTIFIES = "identifies"
    MEMBER_OF = "member_of"
    PARTNER_OF = "partner_of"
    OWNS = "owns"
    RELATES_TO = "relates_to"
    ACTED_ON = "acted_on"
    TALKED_WITH = "talked_with"
    REPORTED = "reported"
    HANDED_OFF = "handed_off"
    SUGGESTED_SAME = "suggested_same"


PROFILE_KINDS = {
    SubjectKind.PERSON: NodeKind.PERSON,
    SubjectKind.ACCOUNT: NodeKind.ACCOUNT,
    SubjectKind.PARTNER: NodeKind.PARTNER,
    SubjectKind.STAFF: NodeKind.PERSON,
}
# Which nodes a cut keeps first at the same distance from the center: the customers and companies, then
# their objects, then who touched them, then the identifiers, which the customer page lists anyway.
_CUT_ORDER = {
    NodeKind.PERSON: 0,
    NodeKind.ACCOUNT: 0,
    NodeKind.PARTNER: 0,
    NodeKind.OBJECT: 1,
    NodeKind.AGENT: 2,
    NodeKind.SYSTEM: 2,
    NodeKind.HANDLE: 3,
}
_CHANNELS = {
    HandleType.PHONE_E164: "phone",
    HandleType.WA_ID: "whatsapp",
    HandleType.WA_JID: "whatsapp",
    HandleType.WA_LID: "whatsapp",
    HandleType.WA_BSUID: "whatsapp",
    HandleType.EMAIL: "email",
    HandleType.GOV_ID_HMAC: "document",
    HandleType.APP_USER_ID: "app",
    HandleType.ORG_REGISTRY_HMAC: "registry",
    HandleType.EMAIL_DOMAIN: "email_domain",
    HandleType.ANON_ID: "anonymous",
}


@dataclass(slots=True)
class Node:
    key: str
    kind: NodeKind
    label: str
    ref: str | None
    hop: int
    meta: dict[str, Any]
    order: int
    pin: int = 0
    """0 for a node any cut may leave out; else its rank among the pinned ones (`Pin`)."""


@dataclass(frozen=True, slots=True)
class Edge:
    source: str
    target: str
    kind: EdgeKind
    meta: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Built:
    """The graph as answered: node ids and edge ids assigned, every edge between two kept nodes."""

    nodes: tuple[tuple[str, Node], ...]
    edges: tuple[tuple[str, str, str, Edge], ...]
    """(edge id, from node id, to node id, edge)."""
    truncated: bool
    withheld: int


@dataclass
class GraphBuilder:
    """Collects nodes by an internal key (`profile:<id>`, `object:<id>`...) and edges between keys. A node
    seen twice keeps its first label and the nearest hop; an edge seen twice is kept once.

    A pinned node goes before any other in a cut: the center, its own identifiers and the companies,
    partners and look-alikes tied to it are who the customer is, and a graph without them answers
    nothing. Pins never pass the limit: the center comes first, then its identifiers, then its ties."""

    nodes: dict[str, Node] = field(default_factory=dict)
    edges: dict[tuple[str, str, EdgeKind], Edge] = field(default_factory=dict)
    withheld: int = 0
    cut: bool = False
    """Something was left out before the limit: a customer's objects past `OBJECTS_PER_CUSTOMER`."""

    def node(
        self,
        key: str,
        kind: NodeKind,
        label: str,
        ref: str | None,
        hop: int,
        *,
        pin: int = 0,
        **meta: Any,
    ) -> str:
        found = self.nodes.get(key)
        if found is None:
            self.nodes[key] = Node(key, kind, label, ref, hop, {"hop": hop, **meta}, len(self.nodes), pin)
            return key
        if hop < found.hop:
            found.hop = hop
            found.meta["hop"] = hop
        if pin and (not found.pin or pin < found.pin):
            found.pin = pin
        return key

    def annotate(self, key: str, **meta: Any) -> None:
        if (found := self.nodes.get(key)) is not None:
            found.meta.update(meta)

    def rename(self, key: str, label: str | None, **meta: Any) -> None:
        """The node's label, when there is one, and its meta; a node not in the graph is left alone."""
        if (found := self.nodes.get(key)) is not None:
            found.label = label or found.label
            found.meta.update(meta)

    def edge(self, source: str, target: str, kind: EdgeKind, **meta: Any) -> None:
        if source in self.nodes and target in self.nodes and source != target:
            self.edges.setdefault((source, target, kind), Edge(source, target, kind, meta))

    def withhold(self, count: int = 1) -> None:
        self.withheld += count

    def build(self, limit: int) -> Built:
        ordered = sorted(
            self.nodes.values(), key=lambda n: (n.pin or len(Pin) + 1, n.hop, _CUT_ORDER[n.kind], n.order)
        )
        kept = ordered[:limit]
        kept.sort(key=lambda n: (n.hop, _CUT_ORDER[n.kind], n.order))
        ids = {node.key: f"n{i}" for i, node in enumerate(kept, start=1)}
        edges = [e for e in self.edges.values() if e.source in ids and e.target in ids]
        return Built(
            nodes=tuple((ids[n.key], n) for n in kept),
            edges=tuple(
                (f"e{i}", ids[e.source], ids[e.target], _resolved(e, ids))
                for i, e in enumerate(edges, start=1)
            ),
            truncated=self.cut or len(ordered) > len(kept),
            withheld=self.withheld,
        )


def _resolved(edge: Edge, ids: Mapping[str, str]) -> Edge:
    """An edge whose meta names a node (`about`) names it by its answer id, or not at all when it was cut."""
    about = edge.meta.get("about")
    if about is None:
        return edge
    meta = {k: v for k, v in edge.meta.items() if k != "about"}
    if about in ids:
        meta["about"] = ids[about]
    return Edge(edge.source, edge.target, edge.kind, meta)


def profile_key(profile_id: UUID) -> str:
    return f"profile:{profile_id}"


def handle_key(handle_id: UUID) -> str:
    return f"handle:{handle_id}"


def object_key(object_id: UUID) -> str:
    return f"object:{object_id}"


def source_key(source_id: UUID) -> str:
    return f"source:{source_id}"


def target_key(target: str) -> str:
    return f"target:{target}"


def handle_label(handle: HandleRecord) -> str:
    """Never an identifier in full, whatever the reader's role: the Console's customer list rule."""
    return mask_listed(handle.type, handle.value)


def handle_channel(handle: HandleRecord) -> str:
    """The channel the Console shows next to the masked value; a system id says which system."""
    if handle.type is HandleType.SYSTEM_ID:
        return handle.scope or "system"
    return _CHANNELS[handle.type]


def strength(handle: HandleRecord) -> tuple[str, str]:
    """(strong or weak, the identifier's weight): strong once verified, authenticated or from a system."""
    weight = handle_weight(handle)
    return ("strong" if weight in STRONG_WEIGHTS else "weak"), weight


def plain_name(name: str) -> str:
    """A source's name as object types name their sources: lowercased, each run of other characters `_`."""
    return _NOT_NAME.sub("_", name.lower()).strip("_")


def source_kind(name: str, systems: frozenset[str]) -> NodeKind:
    """A system when an object type takes its state from the source (push, pull or batch) or mirrors it;
    any other source is an agent."""
    return NodeKind.SYSTEM if plain_name(name) in systems else NodeKind.AGENT


def target_label(target: str) -> str:
    """A handoff target as the Console shows it: `queue:fechamento` is `fechamento`."""
    return target.partition(":")[2] or target


@dataclass(frozen=True, slots=True)
class Degree:
    """How connected a candidate of the overview is: its identifiers, people or companies linked, objects and
    conversations with agents."""

    profile_id: UUID
    organization: bool
    handles: int = 0
    links: int = 0
    objects: int = 0
    interactions: int = 0
    last_seen: datetime | None = None

    @property
    def score(self) -> int:
        return self.handles + self.links + self.objects + self.interactions


def most_connected(degrees: Iterable[Degree], slots: int) -> list[Degree]:
    """The top of the overview: organizations take up to one slot in `ORGANIZATION_SHARE`, people the rest,
    and either side's unused slots go to the other. Ties go to the latest activity, then the id."""

    def ranked(rows: Iterable[Degree]) -> list[Degree]:
        return sorted(
            rows, key=lambda d: (-d.score, -(d.last_seen.timestamp() if d.last_seen else 0), d.profile_id)
        )

    all_rows = list(degrees)
    organizations = ranked(d for d in all_rows if d.organization)
    people = ranked(d for d in all_rows if not d.organization)
    orgs_taken = min(len(organizations), max(slots // ORGANIZATION_SHARE, slots - len(people)))
    people_taken = min(len(people), slots - orgs_taken)
    return organizations[:orgs_taken] + people[:people_taken]


def systems_of(declared: Sequence[tuple[Mapping[str, str], str | None]]) -> frozenset[str]:
    """`declared`: per object type, its sources' kinds by name and the system it mirrors."""
    names: set[str] = set()
    for sources, mirrored in declared:
        names |= {plain_name(name) for name, kind in sources.items() if kind in SYSTEM_FEEDS}
        if mirrored:
            names.add(plain_name(mirrored))
    return frozenset(names)
