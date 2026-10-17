"""Checks a cell runs on a space's configuration before applying it (4.7, 4.10).

Pydantic already enforces each document's shape; these are the cross-references between
documents that a shape cannot express. A space that fails any of them keeps its last good
configuration.
"""

from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError

from niadra.config.models import SpaceConfig
from niadra.domain.common.errors import InvalidInput
from niadra.domain.compile.views import template_fields
from niadra.domain.policy.rules import PRODUCT_CATEGORIES
from niadra.domain.policy.sensitive import is_product_sensitive
from niadra.domain.vocabulary import Sensitivity


def parse_space(document: Any, expected_space_id: UUID) -> SpaceConfig:
    try:
        config = SpaceConfig.model_validate(document)
    except ValidationError as exc:
        # Field paths only: values of a configuration document are the tenant's business.
        fields = sorted({".".join(str(p) for p in err["loc"]) for err in exc.errors()})
        raise InvalidInput("invalid space configuration: " + ", ".join(fields)[:500]) from None
    if config.space_id != expected_space_id:
        raise InvalidInput("the document belongs to another space")
    if problems := config_problems(config):
        raise InvalidInput("invalid space configuration: " + "; ".join(problems))
    return config


def config_problems(config: SpaceConfig) -> list[str]:
    sources = {s.source_id for s in config.sources}
    endpoints = {e.endpoint_id for e in config.webhook_endpoints}
    problems = [
        *_duplicates("source", (s.source_id for s in config.sources)),
        *_duplicates("source name", (s.name for s in config.sources)),
        *_duplicates("mapping", (m.source_id for m in config.mappings)),
        *_duplicates("view", (v.name for v in config.views)),
        *_template_problems(config),
        *_duplicates("trait rule", (r.rule_id for r in config.trait_rules)),
        *_duplicates("trigger rule", (r.rule_id for r in config.trigger_rules)),
        *_duplicates("webhook endpoint", (e.endpoint_id for e in config.webhook_endpoints)),
        *_duplicates("export schedule", (e.schedule_id for e in config.export_schedules)),
        *_duplicates("experiment", (e.name for e in config.experiments)),
        *_duplicates("policy category", (c.name for c in config.policy.categories)),
        *_duplicates("predicate", (p.name for p in config.extraction.predicates)),
    ]
    problems += [
        f"mapping for unknown source {m.source_id}" for m in config.mappings if m.source_id not in sources
    ]
    problems += [
        f"webhook endpoint {e.endpoint_id} belongs to an unknown source"
        for e in config.webhook_endpoints
        if e.source_id not in sources
    ]
    problems += [
        f"trigger rule {r.rule_id} targets unknown endpoint {r.endpoint_id}"
        for r in config.trigger_rules
        if r.endpoint_id not in endpoints
    ]
    problems += [
        f"experiment {e.name} names an unknown source"
        for e in config.experiments
        if not (e.source_ids | e.shadow_source_ids) <= sources
    ]
    problems += [
        f"experiment {e.name} ends before it starts"
        for e in config.experiments
        if e.ends_at is not None and e.ends_at <= e.starts_at
    ]
    problems += [
        f"policy category {c.name} is sensitive and cannot mirror"
        for c in config.policy.categories
        if c.mirror and (c.sensitivity is Sensitivity.SENSITIVE or is_product_sensitive(c.name))
    ]
    categories = {c.name for c in config.extraction.categories} | {c.name for c in config.policy.categories}
    problems += [
        f"predicate {p.name} uses unknown category {p.category}"
        for p in config.extraction.predicates
        # The product's own categories are known to the policy without a declaration: the generic
        # observation about an object is filed under `object` in every space.
        if categories and p.category not in categories | PRODUCT_CATEGORIES
    ]
    return problems


def _template_problems(config: SpaceConfig) -> list[str]:
    problems = []
    for view in config.views:
        if view.template is None:
            continue
        try:
            template_fields(view.template)
        except InvalidInput as exc:
            problems.append(f"view {view.name}: {exc.detail}")
    return problems


def _duplicates(what: str, values: Iterable[object]) -> list[str]:
    return [f"duplicate {what} {value}" for value, count in Counter(values).items() if count > 1]


class RevokedKey(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, defer_build=True)
    space_id: UUID
    key_id: str


class AccessFeedChange(BaseModel):
    """One change to people's access or the tenant's SSO, by ids only; the control plane keeps the rest."""

    model_config = ConfigDict(extra="ignore", frozen=True, defer_build=True)
    change_id: UUID
    tenant_id: UUID
    kind: str
    at: datetime
    user_id: UUID | None = None
    actor_id: UUID | None = None


class AccessFeed(BaseModel):
    """The signed feed of access changes a cell writes into each space's receipt chain."""

    model_config = ConfigDict(extra="ignore", frozen=True, defer_build=True)
    cell_id: str
    after: datetime
    changes: list[AccessFeedChange] = []


class RevocationFeed(BaseModel):
    """The signed revocation feed, pulled every 2 s so a cut takes effect in seconds (4.7)."""

    model_config = ConfigDict(extra="forbid", frozen=True, defer_build=True)
    cell_id: str
    seq: int
    keys: list[RevokedKey] = []
    sources: list[UUID] = []
