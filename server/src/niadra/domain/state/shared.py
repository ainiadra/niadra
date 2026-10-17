"""Shared objects (24.3.12): an item variant, a network hospital, a court, kept while in the working set.

Push and observation cache a system of record that is not Niadra, so they skip the event log (L2 covers a
customer's events): each field keeps, per source, the latest observation that source reported, and moves
only when a report carries a greater version of that source (object-state 10.1). The hot copy in the cache
store decides a push without a database statement; the consolidator writes what moved with the same rule,
so the stored object reaches the state the hot copy served whatever the order of its statements.

An observation is kept as its value sealed on its own (AAD `shared_objects:<object id>:<field>/<source>`)
beside its stamps in the clear, so neither the hot copy nor the consolidator ever opens a value.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

_NAMESPACE = uuid.UUID("0b8f7c62-4e51-5d1c-9a3e-7f2b6c1d8e40")


@dataclass(frozen=True, slots=True)
class SharedKey:
    type: str
    namespace: str
    external_id: str
    variant: str = ""

    @property
    def ref(self) -> str:
        """The compact reference, `type:namespace:id`, with `#variant` when there is one."""
        base = f"{self.type}:{self.namespace}:{self.external_id}"
        return f"{base}#{self.variant}" if self.variant else base


def shared_key(ref: str) -> SharedKey | None:
    """`type:namespace:id`, optionally with `#variant`."""
    base, _, variant = ref.partition("#")
    parts = base.split(":", 2)
    if len(parts) != 3 or not all(parts):
        return None
    return SharedKey(parts[0], parts[1], parts[2], variant)


def shared_object_id(space_id: UUID, key: SharedKey) -> UUID:
    """The same object has the same id in every process, so a push seals its values without asking the
    database or the hot copy which id the object has."""
    name = f"{space_id}\x1f{key.type}\x1f{key.namespace}\x1f{key.external_id}\x1f{key.variant}"
    return uuid.uuid5(_NAMESPACE, name)


@dataclass(frozen=True, slots=True)
class HotField:
    """One field of one source as the hot copy and the database keep it: the source's version, the value
    sealed (`[data key version, base64 ciphertext]`) and the stamps."""

    version: int
    sealed: tuple[int, str]
    stamps: Mapping[str, Any]

    def meta(self) -> dict[str, Any]:
        return {**self.stamps, "version": self.version}


Slot = tuple[str, str]
"""(field, source)."""


@dataclass(frozen=True, slots=True)
class HotEntry:
    """A shared object in the working set, as the hot copy holds it."""

    object_id: UUID
    fields: Mapping[Slot, HotField]


def slot_name(field: str, source: str) -> str:
    return f"{field}|{source}"


def slot_of(name: str) -> Slot:
    field, _, source = name.partition("|")
    return field, source


def stored(entry: HotEntry) -> tuple[dict[str, Any], dict[str, Any]]:
    """The entry as `shared_objects` keeps it: `state_ct` and `field_meta`, each `{field: {source: ...}}`."""
    state: dict[str, dict[str, Any]] = {}
    meta: dict[str, dict[str, Any]] = {}
    for (field, source), hot in entry.fields.items():
        state.setdefault(field, {})[source] = list(hot.sealed)
        meta.setdefault(field, {})[source] = hot.meta()
    return state, meta


def entry_of(object_id: UUID, state: Mapping[str, Any], meta: Mapping[str, Any]) -> HotEntry:
    fields: dict[Slot, HotField] = {}
    for field, by in meta.items():
        for source, stamps in by.items():
            sealed = state.get(field, {}).get(source)
            if sealed is None:
                continue
            kept = {k: v for k, v in stamps.items() if k != "version"}
            fields[(field, source)] = HotField(int(stamps["version"]), (int(sealed[0]), str(sealed[1])), kept)
    return HotEntry(object_id, fields)
