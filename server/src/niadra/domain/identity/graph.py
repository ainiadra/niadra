"""A profile is a connected component of the live assertions (2.5); unmerge recomputes them."""

from collections.abc import Iterable
from uuid import UUID


def connected_components(nodes: Iterable[UUID], edges: Iterable[tuple[UUID, UUID]]) -> list[frozenset[UUID]]:
    """Components ordered by their smallest member, so the result is deterministic."""
    parent = {node: node for node in nodes}

    def root(node: UUID) -> UUID:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for a, b in edges:
        if a in parent and b in parent:
            ra, rb = root(a), root(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    groups: dict[UUID, set[UUID]] = {}
    for node in parent:
        groups.setdefault(root(node), set()).add(node)
    return sorted((frozenset(g) for g in groups.values()), key=min)
