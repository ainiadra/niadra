"""Shared state behind the in-memory repositories; each module owns one namespace."""

from typing import Any


class MemoryStore:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = {}

    def module(self, name: str) -> dict[str, Any]:
        return self.data.setdefault(name, {})
