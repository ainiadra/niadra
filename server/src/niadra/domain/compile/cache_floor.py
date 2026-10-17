"""P6.5: aim at the target model's cache floor (15.3).

Providers cache only prefixes above a floor and fail silently below it. What must cross the
floor is the stable set (agent instructions + B0 + account block + B1). Floors come from the
tenant's table keyed `provider/model` or `provider`, then from the product's table of the models it
knows; an unknown target gets neutral text and no floor, because aiming at the largest floor would
need filler (rule 1 and 3).
"""

import hashlib
import re
from collections.abc import Mapping, Sequence

from niadra.domain.compile.model import CacheDecision, Variant

# Providers with explicit breakpoints; the pack uses at most two of them (rule 5).
EXPLICIT_BREAKPOINT_PROVIDERS = frozenset({"anthropic"})
SHORT_VIEWS = frozenset({"brief", "voice"})
SHORT_VIEW_MAX_FLOOR = 600
NEAR_FLOOR = 0.3
DEFAULT_TTL_S = 300


# Minimum cacheable prefix per Claude model, from Anthropic's prompt-caching reference. It is not
# monotonic across generations, so every model is listed by id; a model not listed gets no default.
CLAUDE_FLOORS: Mapping[str, int] = {
    **dict.fromkeys(
        ("claude-opus-5", "claude-fable-5", "claude-fable-5-1", "claude-mythos-5", "claude-mythos-5-1"), 512
    ),
    **dict.fromkeys(
        (
            "claude-opus-4-8",
            "claude-sonnet-5",
            "claude-sonnet-4-6",
            "claude-sonnet-4-5",
            "claude-opus-4-1",
            "claude-opus-4-0",
            "claude-opus-4",
            "claude-sonnet-4-0",
            "claude-sonnet-4",
        ),
        1024,
    ),
    **dict.fromkeys(("claude-opus-4-7", "claude-3-5-haiku"), 2048),
    **dict.fromkeys(("claude-opus-4-6", "claude-opus-4-5", "claude-haiku-4-5"), 4096),
}
# Providers with one floor for every model that caches: OpenAI's prompt caching and the implicit cache
# of the current Gemini models.
PROVIDER_FLOORS: Mapping[str, int] = {"openai": 1024, "azure_openai": 1024, "google": 4096, "gemini": 4096}

# `anthropic.claude-...` (Bedrock), `anthropic/claude-...` (routers), `...@20251101` (Vertex) and dated
# or versioned snapshots all name the same model.
_VENDOR_PREFIX = re.compile(r"^(?:[a-z]{2,4}\.)?anthropic[./]")
_SNAPSHOT = re.compile(r"(?:@.*|-\d{8}(?:-v\d+:\d+)?|-v\d+:\d+)$")


def _claude_id(model: str) -> str:
    return _SNAPSHOT.sub("", _VENDOR_PREFIX.sub("", model.strip().lower()))


def default_floor(provider: str, model: str | None) -> int | None:
    """The product's floor for a target no tenant entry names."""
    if model is not None and (floor := CLAUDE_FLOORS.get(_claude_id(model))) is not None:
        return floor
    return PROVIDER_FLOORS.get(provider.strip().lower())


def floor_for(floors: Mapping[str, int], variant: Variant) -> int | None:
    """The tenant's `provider/model`, then its `provider`, then the product's table."""
    if variant.provider is None:
        return None
    tenant = floors.get(f"{variant.provider}/{variant.model}") or floors.get(variant.provider)
    return tenant or default_floor(variant.provider, variant.model)


def _aims(floor: int | None, view: str) -> bool:
    # Short views only cache on their own under a low floor (rule 4); otherwise their saving is size.
    return floor is not None and not (view in SHORT_VIEWS and floor > SHORT_VIEW_MAX_FLOOR)


def top_up(
    floors: Mapping[str, int],
    variant: Variant,
    view: str,
    stable_tokens: int,
    spare: Sequence[tuple[str, int]],
) -> tuple[str, ...]:
    """Useful lower-priority stable items that carry the stable set over a nearby floor; never filler."""
    floor = floor_for(floors, variant)
    if floor is None or not _aims(floor, view):
        return ()
    deficit = floor - stable_tokens
    if deficit <= 0 or deficit > NEAR_FLOOR * floor:
        return ()
    added: list[str] = []
    for item_id, tokens in spare:
        if deficit <= 0:
            break
        added.append(item_id)
        deficit -= tokens
    return tuple(added) if deficit <= 0 else ()


def decide_cache(
    floors: Mapping[str, int],
    variant: Variant,
    view: str,
    stable_tokens: int,
    *,
    breakpoints: tuple[int, int],
    added: tuple[str, ...],
    salt_seed: str,
) -> CacheDecision:
    floor = floor_for(floors, variant)
    cacheable = _aims(floor, view) and floor is not None and stable_tokens >= floor
    explicit = cacheable and variant.provider in EXPLICIT_BREAKPOINT_PROVIDERS
    return CacheDecision(
        cacheable=cacheable,
        # Per-tenant salt for self-hosted engines: a cache shared by tenants is a timing side channel (15.6).
        salt=hashlib.sha256(f"niadra-cache:{salt_seed}".encode()).hexdigest()[:16],
        provider=variant.provider,
        model=variant.model,
        floor=floor,
        stable_tokens=stable_tokens,
        ttl_seconds=DEFAULT_TTL_S if cacheable else None,
        breakpoints=breakpoints if explicit else (),
        added_for_floor=added,
    )
