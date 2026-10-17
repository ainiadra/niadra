"""Stands in for the LLM when a cell has no provider key: extraction and the assistant wait."""

from typing import Any

from niadra.domain.common.errors import Unavailable
from niadra.ports.infra import LlmResult


class DisabledLlm:
    async def complete_json(self, **_: Any) -> LlmResult:
        # Unavailable makes the worker retry with backoff, so sessions are extracted once a key exists.
        raise Unavailable("no LLM provider is configured for this cell")

    async def aclose(self) -> None:
        return None
