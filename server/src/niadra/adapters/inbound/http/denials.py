"""Reads the policy refused leave a receipt too (5.13): "the billing agent tried to read the technical
ticket and was refused" belongs to the audit trail as much as a read that went through.

Only authenticated sources are recorded, and only refusals by scope, purpose, level or link: an unknown
key never reaches a space's chain, so nobody can fill it from outside.
"""

import logging
from typing import Any

from niadra.domain.common.space import Principal, SourcePrincipal
from niadra.domain.vocabulary import ReceiptSurface

log = logging.getLogger(__name__)

DENIAL_CODES = frozenset({"forbidden", "scope_missing", "verification_not_allowed", "about_without_link"})

_EXACT = {"/v1/context": ReceiptSurface.PACK}
_PREFIXES = (
    ("/v1/history/search", ReceiptSurface.SEARCH),
    ("/v1/history/timeline", ReceiptSurface.TIMELINE),
    ("/v1/history/items/", ReceiptSurface.OPEN),
    ("/v1/history/open", ReceiptSurface.OPEN),
    ("/v1/objects/", ReceiptSurface.OBJECT),
    ("/v1/agent-memory/block", ReceiptSurface.AGENT_BLOCK),
    ("/v1/agent-memory/search", ReceiptSurface.AGENT_SEARCH),
)
TOOL_SURFACES = {
    "get_customer_context": ReceiptSurface.PACK,
    "search_customer_history": ReceiptSurface.SEARCH,
    "get_customer_timeline": ReceiptSurface.TIMELINE,
    "open_history_item": ReceiptSurface.OPEN,
    "search_agent_memory": ReceiptSurface.AGENT_SEARCH,
}


def read_surface(path: str) -> ReceiptSurface | None:
    if path in _EXACT:
        return _EXACT[path]
    return next((surface for prefix, surface in _PREFIXES if path.startswith(prefix)), None)


async def record_denial(
    container: Any, principal: Principal | None, surface: ReceiptSurface | None, code: str
) -> None:
    """Best effort after the refusal is decided: a failure here never changes the answer."""
    if surface is None or code not in DENIAL_CODES or not isinstance(principal, SourcePrincipal):
        return
    serve = getattr(container, "serve", None)
    if serve is None:
        return
    try:
        await serve.record_denial(principal, surface, code)
    except Exception:
        log.warning("the receipt of a refused read was not recorded", exc_info=True)
