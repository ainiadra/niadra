"""A space's configuration with what its traffic showed beneath it.

The control plane signs what the company declared; the cell infers, from the space's own traffic, the types
the company did not declare and the fields its declared types leave out (`app.observed_config`). A process
serves both, merged here: the declaration wins, key by key (P2), and an observed type that does not hold
together with the declared registry is left out (`ObjectTypeRegistry.under`).
"""

from collections.abc import Mapping, Sequence
from typing import Any

from niadra.config.models import SpaceConfig
from niadra.domain.objects.operations import canonical_operation
from niadra.domain.objects.words import Words

MAX_DECLARED_OPERATIONS = 100
"""The extraction vocabulary's own bound (`ExtractionSchema.operations`)."""


def with_observed(
    config: SpaceConfig,
    observed: Sequence[Mapping[str, Any]],
    operations: Sequence[str] = (),
    words: Words | None = None,
) -> SpaceConfig:
    """`config` with `observed` types under its declared registry, worded in the space's words, and the
    operations its systems confirm after the ones it declared (a declared operation keeps its description)."""
    update: dict[str, Any] = {}
    if observed or (words and (words.fields or words.values or words.types)):
        update["object_types"] = config.object_types.under(observed, words)
    declared = config.extraction.operation_names()
    known = {canonical_operation(name) for name in declared}
    added = [name for name in operations if canonical_operation(name) not in known]
    room = MAX_DECLARED_OPERATIONS - len(declared)
    if added and room > 0:
        update["extraction"] = config.extraction.model_copy(
            update={"operations": [*config.extraction.operations, *added[:room]]}
        )
    return config.model_copy(update=update) if update else config


def observed_types(document: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The types of a space's observed document, or none."""
    return [dict(t) for t in (document or {}).get("types") or []]


def observed_words(document: Mapping[str, Any] | None) -> Words:
    """The space's words its observed document keeps (`app.observed_words`), or none, with each type's own
    under `types`. A document of a release before the types' words reads the same, with none of a type."""
    said = (document or {}).get("words") or {}
    types = {
        str(name): Words(dict(own.get("fields") or {}), dict(own.get("values") or {}))
        for name, own in (said.get("types") or {}).items()
        if isinstance(own, Mapping)
    }
    return Words(dict(said.get("fields") or {}), dict(said.get("values") or {}), types)


def observed_operation_names(document: Mapping[str, Any] | None) -> list[str]:
    """The operations a space's observed document says its systems confirm, or none."""
    return [str(name) for name in (document or {}).get("operations") or []]
