"""Stands in for the cell's `change_seq` sequence on the in-memory tables a module exports (22.3)."""

from collections.abc import Hashable
from typing import Any

from niadra.adapters.memory.store import MemoryStore


class ChangeSeq:
    """One counter for the whole store, like the cell's sequence; each module keeps its rows' values."""

    def __init__(self, store: MemoryStore, module: str) -> None:
        self._counter: dict[str, int] = store.module("cell").setdefault("change_seq", {"value": 0})
        self._rows: dict[str, dict[Hashable, int]] = store.module(module).setdefault("change_seq", {})

    def touch(self, table: str, key: Hashable) -> int:
        """Inserts and updates bump the row's sequence, as the column default and trigger do."""
        self._counter["value"] += 1
        self._rows.setdefault(table, {})[key] = self._counter["value"]
        return self._counter["value"]

    def last(self) -> int:
        return self._counter["value"]

    def of(self, table: str, key: Hashable) -> int | None:
        return self._rows.get(table, {}).get(key)

    def forget(self, table: str, key: Hashable) -> int | None:
        return self._rows.get(table, {}).pop(key, None)

    def between(self, table: str, after: int, upto: int, limit: int) -> list[tuple[int, Any]]:
        """(sequence, key) of the rows with `after < seq <= upto`, in sequence order."""
        found = sorted((seq, key) for key, seq in self._rows.get(table, {}).items() if after < seq <= upto)
        return found[:limit]
