"""An agent's working state (24.3.9; the agent-state spec): the rules of a write, with no I/O.

A state is a mapping of top-level fields, written by the agent's code and never by a model. A compare-and-
swap replaces it whole at the version it read; a merge by key replaces each field it names and removes a field
written as exactly `{"$delete": true}`, so a removed field never comes back. The size of a state is its
compact JSON with the keys sorted: the sum of each field's fragment (`"name":value`), the braces and the
commas, so the state store checks the cap from the sizes alone, without opening a value.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import orjson

REMOVAL = {"$delete": True}
SCOPE_PURPOSE = "agent_state_scope"
"""The keyed digest a state's scope is stored under: its id may look like a phone number (agent-state 3.2)."""
WORKING_SET_KEPT = timedelta(days=30)
"""How long a state whose type keeps it `while_in_working_set` stays after its last write (agent-state 7.1). A
type that names no retention keeps its states until it names one (memory never cuts): they went
after 30 days, and a named retention was cut to 180."""


def scope_text(kind: str, scope_id: str) -> str:
    return f"{kind}:{scope_id}"


def is_removal(value: Any) -> bool:
    return isinstance(value, dict) and value == REMOVAL


def fragment_size(name: str, value: Any) -> int:
    """Bytes of `"name":value` in the state's compact JSON, keys sorted at every level."""
    return len(orjson.dumps({name: value}, option=orjson.OPT_SORT_KEYS)) - 2


def state_size(fragments: Iterable[int]) -> int:
    sizes = list(fragments)
    return 2 + sum(sizes) + max(len(sizes) - 1, 0)


def written(current: Mapping[str, Any], body: Mapping[str, Any], *, replace: bool) -> dict[str, Any]:
    """The state after a write: `body` whole for a compare-and-swap, else merged by key."""
    if replace:
        return dict(body)
    after = dict(current)
    for name, value in body.items():
        if is_removal(value):
            after.pop(name, None)
        else:
            after[name] = value
    return after


@dataclass(frozen=True, slots=True)
class PackagedState:
    """A state as the customer's data package carries it: what the agent kept, by declared field, and when."""

    scope_kind: str
    agent: str
    version: int
    updated_at: datetime
    valid_until: datetime | None
    fields: dict[str, Any]


@dataclass(frozen=True, slots=True)
class DurableState:
    """A state as its batch writer stores it: sealed whole, with its version and its dates. `subject_key` is
    the space's keyed digest of the customer's handle, as `handles` keeps it."""

    space_id: UUID
    scope_key: bytes
    scope_kind: str
    source_id: UUID
    agent: str
    version: int
    body_ct: bytes
    dek_version: int
    body_bytes: int
    updated_at: datetime
    valid_until: datetime | None
    """None for a type that names no retention: kept until it names one."""
    schema_version: str | None = None
    subject_key: bytes | None = None
    origin_handle_id: UUID | None = None
    named: frozenset[bytes] = frozenset()
    """What its body names a person by (`domain.privacy.mentions`), keyed-hashed: indexed as it is stored."""

    @property
    def row(self) -> str:
        """What its body's ciphertext is bound to, and its entry in the index of mentions."""
        return f"{self.scope_key.hex()}/{self.source_id}/{self.agent}"
