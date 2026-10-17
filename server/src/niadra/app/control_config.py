"""The configuration documents a diff can change, and how a space's `SpaceConfig` is assembled (4.7, 7.4).

Each `/v1/config/{type}` maps to one field of `SpaceConfig`; sources come from the sources table, since
they are managed with their keys rather than by diff.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from niadra.app.traits import barrier_for
from niadra.config.claims import ClaimContract
from niadra.config.coordination import CoordinationSettings, security_changes
from niadra.config.measurement import MeasurementSettings
from niadra.config.models import (
    AgentMemorySettings,
    Experiment,
    ExportSchedule,
    ExtractionGuidance,
    ExtractionSchema,
    FeatureSettings,
    IngestMaskingSettings,
    MediaSettings,
    Policy,
    PurposeSettings,
    ReviewSampling,
    SourceConfig,
    SpaceConfig,
    TaskView,
    TenantSettings,
    TraitRuleConfig,
    TriggerRuleConfig,
    WebhookEndpoint,
)
from niadra.config.models import Mapping as MappingDoc
from niadra.config.object_types import ObjectTypeRegistry
from niadra.config.object_types import escalate as escalate_object_types
from niadra.config.recording import RecordingSettings
from niadra.config.recording import escalate as escalate_recording
from niadra.config.tool_bindings import ToolBindings
from niadra.config.validation import config_problems
from niadra.domain.common.errors import InvalidInput, NotFound
from niadra.domain.common.space import owns_secret, secret_prefix
from niadra.domain.compile.views import template_fields
from niadra.domain.control.records import Role, Source, Space
from niadra.domain.spaces.directory import PRIMARY_INSTANCE, Tier
from niadra.domain.traits.rules import TraitRule, event_window, released_to, validate_rule
from niadra.domain.triggers.conditions import RepetitionParams, TriggerRule, unreadable_reason

OUTBOUND_EVENT_TYPES = frozenset(
    {
        "action.recorded",
        "agent_memory.note_created",
        "conversation.outcome",
        "erasure.completed",
        "export.completed",
        "export.failed",
        "fact.added",
        "fact.restored",
        "fact.retracted",
        "guard.violated",
        "identity.merged",
        "identity.suggestion",
        "identity.unmerged",
        "import.completed",
        "import.failed",
        "open_item.closed",
        "open_item.reopened",
        "trait.added",
        "trait.expired",
        "trigger.fired",
        "trigger.retracted",
        "usage.budget_reached",
        # The agent features' events (7.7).
        "audit.root",
        "data_issue.opened",
        "receipt.recorded",
        "turn.flagged",
        "content.flagged",
        "object.expired_by_input",
        "object.timer_due",
        "object.value_revised",
        "object.watch_fired",
        "type.drift",
        "review_request.created",
        "review_request.resolved",
    }
)
"""The event types a `standard` webhook endpoint may subscribe to (7.5), besides `*`: exactly the types the
cell sends, which a test reads from the code. A type it never sends would be a subscription that stays
silent, so it is refused."""

MAX_TRAIT_RULES = 25
"""Pattern rules per space, on or off (docs/concepts/patterns)."""
MAX_TRIGGER_RULES = 50
"""Warning rules per space, on or off (docs/concepts/triggers-and-webhooks, 22.5)."""
MAX_TASK_VIEWS = 8
"""Task views per space (docs/concepts/context): each one is a pack the compiler keeps warm per profile."""

# A disabled endpoint is a choice, not a mistake: the rules aimed at it wait until it is on again.
_CHOSEN_SILENCE = frozenset({"endpoint_disabled"})
# Only a change to these documents can leave a rule silent, a webhook open by mistake or a list over its cap.
# A policy or settings change that stops a pattern rule (a category turned sensitive, a shorter allow-list)
# protects people: it always passes.
_RULE_DOCUMENTS = frozenset(
    {"trait-rules", "trigger-rules", "webhook-endpoints", "mappings", "views", "export-schedules"}
)


Escalation = Callable[[Any, Any], frozenset[Role]]


@dataclass(frozen=True, slots=True)
class DocType:
    field: str
    adapter: TypeAdapter[Any]
    roles: frozenset[Role]
    """Who may write and approve diffs of this type, besides admins (5.22)."""
    description: str
    escalate: Escalation | None = None
    """The roles a change needs on top of `roles`, from the validated document before and after it: a change
    that loosens what protects people needs `security` whoever usually edits the document."""

    def escalated(self, before: Any, after: Any) -> frozenset[Role]:
        if self.escalate is None:
            return frozenset()
        return self.escalate(self.adapter.validate_python(before), self.adapter.validate_python(after))


def _doc(
    field: str, model: Any, roles: set[Role], description: str, escalate: Escalation | None = None
) -> DocType:
    return DocType(field, TypeAdapter(model), frozenset(roles), description, escalate)


def _security_when(changes: Callable[[Any, Any], Sequence[str]]) -> Escalation:
    """An escalation from a function that names the parts of a change needing the security role."""
    return lambda before, after: frozenset({Role.SECURITY}) if changes(before, after) else frozenset()


DOC_TYPES: dict[str, DocType] = {
    "policies": _doc(
        "policy", Policy, {Role.SECURITY}, "Who may read what: categories, rules, competitors (5.12)."
    ),
    "schemas": _doc(
        "extraction",
        ExtractionSchema,
        {Role.INTEGRATION},
        "What extraction captures: slots, predicates, categories.",
    ),
    "mappings": _doc(
        "mappings",
        list[MappingDoc],
        {Role.INTEGRATION},
        "How system webhooks and batches become events (5.2).",
    ),
    "views": _doc(
        "views", list[TaskView], {Role.INTEGRATION}, "Task views: what an internal agent's context holds."
    ),
    "trait-rules": _doc(
        "trait_rules",
        list[TraitRuleConfig],
        {Role.INTEGRATION},
        "Which behavior traits are derived, and how (20).",
    ),
    "trigger-rules": _doc(
        "trigger_rules",
        list[TriggerRuleConfig],
        {Role.INTEGRATION},
        "Conditions that notify an endpoint (22.1).",
    ),
    "webhook-endpoints": _doc(
        "webhook_endpoints", list[WebhookEndpoint], {Role.INTEGRATION}, "Where outgoing webhooks go (7.5)."
    ),
    "export-schedules": _doc(
        "export_schedules",
        list[ExportSchedule],
        {Role.SECURITY},
        "Continuous export to the customer's bucket (22.3).",
    ),
    "review-sampling": _doc(
        "review_sampling",
        ReviewSampling,
        {Role.SECURITY, Role.REVIEW},
        "How many sessions go to sampled review (22.2).",
    ),
    "experiments": _doc(
        "experiments",
        list[Experiment],
        {Role.ANALYSIS},
        "Control groups that measure the memory's effect (19.7).",
    ),
    "agent-memory": _doc(
        "agent_memory",
        AgentMemorySettings,
        {Role.INTEGRATION},
        "The agents' own working notes: on or off, and who writes.",
    ),
    "media": _doc(
        "media",
        MediaSettings,
        {Role.SECURITY},
        "Which file and image types have their text read inside the cell.",
    ),
    "extraction-guidance": _doc(
        "extraction_guidance",
        ExtractionGuidance,
        {Role.INTEGRATION},
        "What extraction focuses on and leaves out, for the space and per source, in the company's words.",
    ),
    "ingest-masking": _doc(
        "ingest_masking",
        IngestMaskingSettings,
        {Role.SECURITY},
        "What ingestion holds back from text before storing it: cards, security codes, passwords, secrets.",
    ),
    "purposes": _doc(
        "purposes",
        PurposeSettings,
        {Role.SECURITY},
        "Why each memory item is collected, on which legal basis and for how long (13, 6.6).",
    ),
    "settings": _doc(
        "settings",
        TenantSettings,
        set(),
        "Timezone, locale, retention, extraction tier and context trimming of the space.",
    ),
    # The agent features' documents (7.7).
    "features": _doc(
        "features",
        FeatureSettings,
        {Role.SECURITY},
        "What the space turned on of the agent core: turn records, state, signals, claims, coordination.",
    ),
    "recording": _doc(
        "recording",
        RecordingSettings,
        {Role.INTEGRATION},
        "How turn records are kept: where recorded values live per source, the pins a replay needs, the "
        "kept tier's days and sample.",
        escalate_recording,
    ),
    "object-types": _doc(
        "object_types",
        ObjectTypeRegistry,
        {Role.INTEGRATION},
        "The object types the space declares or derives: fields, sources, freshness, lifecycle, readings.",
        escalate_object_types,
    ),
    "tool-bindings": _doc(
        "tool_bindings",
        ToolBindings,
        {Role.INTEGRATION},
        "The space's tools bound to the fields of its types: arguments, objects in results, capabilities.",
    ),
    "coordination": _doc(
        "coordination",
        CoordinationSettings,
        {Role.INTEGRATION},
        "Contact purposes and how each fails, budgets, channels, ownership, suppression reasons, gateways.",
        _security_when(security_changes),
    ),
    "claim-contract": _doc(
        "claim_contract",
        ClaimContract,
        {Role.INTEGRATION, Role.SECURITY},
        "What an agent may state: claim categories, the evidence each needs, actions, negative corpus.",
    ),
    "measurement": _doc(
        "measurement",
        MeasurementSettings,
        {Role.ANALYSIS},
        "Outcomes and revenue, attribution windows, and experiments on elements, agents and rankings.",
    ),
}


def doc_type(name: str) -> DocType:
    try:
        return DOC_TYPES[name]
    except KeyError:
        raise NotFound("unknown configuration type") from None


def validate_document(name: str, document: Any) -> Any:
    """The document in canonical JSON form. Errors name fields, never echo values (7.0)."""
    spec = doc_type(name)
    try:
        return spec.adapter.dump_python(spec.adapter.validate_python(document), mode="json")
    except ValidationError as exc:
        raise InvalidInput(_describe(exc)) from None


def build_space_config(space: Space, sources: Sequence[Source], documents: Mapping[str, Any]) -> SpaceConfig:
    """A revoked source leaves the cell's configuration with everything it owns: its webhook mapping
    (its webhooks are refused), its endpoints and SIEM streams (nothing more is sent to them) and the
    warning rules aimed at those. The documents keep them, as history; the cell never sees them."""
    fields = {DOC_TYPES[name].field: doc for name, doc in documents.items()}
    try:
        config = SpaceConfig.model_validate(
            {
                "space_id": space.space_id,
                "tenant_id": space.tenant_id,
                "region": space.region,
                "kms_key_arn": space.kms_key_arn,
                "tier": None if space.tier is Tier.T0 else space.tier.value,
                "instance": None if space.instance == PRIMARY_INSTANCE else space.instance,
                "environment": space.environment.value,
                "sources": [_source_config(s) for s in sources if s.revoked_at is None],
                **fields,
            }
        )
    except ValidationError as exc:
        raise InvalidInput(_describe(exc)) from None
    config = _without_revoked(config, {s.source_id for s in sources if s.revoked_at is not None})
    _check_references(config)
    return config


def _without_revoked(config: SpaceConfig, revoked: set[UUID]) -> SpaceConfig:
    if not revoked:
        return config
    endpoints = [e for e in config.webhook_endpoints if e.source_id not in revoked]
    kept = {e.endpoint_id for e in endpoints}
    return config.model_copy(
        update={
            "mappings": [m for m in config.mappings if m.source_id not in revoked],
            "webhook_endpoints": endpoints,
            "trigger_rules": [r for r in config.trigger_rules if r.endpoint_id in kept],
        }
    )


def revoked_owners(name: str, document: Any, revoked: set[UUID]) -> set[str]:
    """What a `mappings` or `webhook-endpoints` document gives to a revoked source: mapping source ids,
    endpoint ids. A diff may keep those entries (they stay inert) but never add one."""
    if name not in ("mappings", "webhook-endpoints") or not revoked or not isinstance(document, list):
        return set()
    key = "source_id" if name == "mappings" else "endpoint_id"
    return {
        str(entry.get(key))
        for entry in document
        if isinstance(entry, dict) and _uuid(entry.get("source_id")) in revoked
    }


def _uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value))
    except ValueError:
        return None


def current_document(config: SpaceConfig, name: str) -> Any:
    spec = doc_type(name)
    return spec.adapter.dump_python(getattr(config, spec.field), mode="json")


def refuse_new_problems(changed: str, before: SpaceConfig | None, after: SpaceConfig) -> None:
    """Refuses a change to the rule documents that would leave a rule accepted and silent (4.7): a pattern
    rule the catalog or the sensitive-category barrier refuses (20.5), a warning rule whose endpoint cannot
    read what it sends (22.1), an endpoint subscribed to an event type Niadra never sends (7.5), a webhook
    mapping authenticated by a client certificate nothing verifies (5.2), more task views than a space holds.
    Only what the change introduces
    counts, so a problem already stored never blocks another change."""
    if changed not in _RULE_DOCUMENTS:
        return
    known = set(semantic_problems(before)) if before is not None else set()
    new = [p for p in semantic_problems(after) if p not in known]
    if new:
        raise InvalidInput("; ".join(new)[:1000])


def refuse_cell_problems(before: SpaceConfig | None, after: SpaceConfig) -> None:
    """Refuses a change the cell's own check (`config.validation.config_problems`) would reject: such a
    configuration never reaches the cell, which keeps the space's last good one in silence, so every later
    change of the space waits behind it. Only what the change introduces counts, as in
    `refuse_new_problems`."""
    known = set(config_problems(before)) if before is not None else set()
    new = [p for p in config_problems(after) if p not in known]
    if new:
        raise InvalidInput("the cell would refuse this configuration: " + "; ".join(new)[:1000])


def semantic_problems(config: SpaceConfig) -> list[str]:
    """What the shapes and references let through but the cell would ignore in silence. Names documents,
    rule and endpoint ids and fields, never the values a person typed."""
    # The same barrier the derivation uses, the categories the space declares as its default allow-list.
    barrier = barrier_for(config)
    problems: list[str] = []
    # The count is part of the text, so a change that grows a list already over the cap is new.
    for name, entries, noun, cap in (
        ("trait-rules", config.trait_rules, "rules", MAX_TRAIT_RULES),
        ("trigger-rules", config.trigger_rules, "rules", MAX_TRIGGER_RULES),
        ("views", [v for v in config.views if v.name.startswith("task:")], "task views", MAX_TASK_VIEWS),
    ):
        if len(entries) > cap:
            problems.append(f"{name}: {len(entries)} {noun}, at most {cap} per space")
    for view in config.views:
        if view.template is not None:
            try:
                template_fields(view.template)
            except InvalidInput as exc:
                problems.append(f"views {view.name}: {exc.detail}")
    patterns = {r.rule_id: r for r in config.trait_rules if r.enabled}
    hot = config.settings.events_hot_days
    for rule in patterns.values():
        trait_rule = TraitRule(rule.rule_id, rule.catalog, rule.version, rule.params)
        try:
            validate_rule(trait_rule, barrier)
        except InvalidInput as exc:
            problems.append(f"trait-rules {rule.rule_id}: {exc.detail}")
            continue
        if (window := event_window(trait_rule, hot)) is not None:
            problems.append(
                f"trait-rules {rule.rule_id}: window_days {window} counts system events, which stay "
                f"{hot} days in the space (settings.events_hot_days)"
            )
    for trigger in config.trigger_rules:
        if not trigger.enabled:
            continue
        on_pattern = trigger.condition == "trait.present"
        pattern = patterns.get(str(trigger.params.get("name"))) if on_pattern else None
        if on_pattern and pattern is None:
            problems.append(f"trigger-rules {trigger.rule_id}: params.name names no pattern rule in use")
            continue
        if trigger.condition == "context_use.repetition_rate" and (
            problem := _repetition_problem(config, trigger.params)
        ):
            problems.append(f"trigger-rules {trigger.rule_id}: {problem}")
            continue
        reason = _unreadable(config, trigger, pattern)
        if reason and reason not in _CHOSEN_SILENCE:
            target = f"endpoint {trigger.endpoint_id}"
            problems.append(f"trigger-rules {trigger.rule_id}: {target} cannot receive it ({reason})")
    destinations: set[tuple[str, str, str]] = set()
    for schedule in config.export_schedules:
        # Checked on every run too; refused here so a schedule that could never run is never approved.
        if schedule.region != config.region:
            problems.append(f"export-schedules {schedule.schedule_id}: region is not the space's region")
        where = (schedule.destination_kind, schedule.bucket, schedule.prefix)
        if where in destinations:
            problems.append(
                f"export-schedules {schedule.schedule_id}: another schedule has its bucket and prefix"
            )
        destinations.add(where)
    problems += _foreign_secrets(config)
    for endpoint in config.webhook_endpoints:
        if endpoint.format != "standard":
            continue
        problems += [
            f"webhook-endpoints {endpoint.endpoint_id}: event_types.{i} is not an event type Niadra sends"
            for i, event_type in enumerate(endpoint.event_types)
            if event_type != "*" and event_type not in OUTBOUND_EVENT_TYPES
        ]
    return problems


def _foreign_secrets(config: SpaceConfig) -> list[str]:
    """Every vault reference must be a secret of this space (the cell refuses any other at use)."""
    named: list[tuple[str, str]] = []
    for mapping in config.mappings:
        if mapping.auth is not None:
            named.append((f"mappings {mapping.source_id}: auth.secret_ref", mapping.auth.secret_ref))
    for endpoint in config.webhook_endpoints:
        where = f"webhook-endpoints {endpoint.endpoint_id}"
        named += [(f"{where}: secret_refs.{i}", ref) for i, ref in enumerate(endpoint.secret_refs)]
        if endpoint.authorization_ref:
            named.append((f"{where}: authorization_ref", endpoint.authorization_ref))
    for schedule in config.export_schedules:
        named.append((f"export-schedules {schedule.schedule_id}: credential_ref", schedule.credential_ref))
    for gateway in config.coordination.gateways:
        named.append((f"coordination gateways {gateway.gateway_id}: secret_ref", gateway.secret_ref))
    prefix = secret_prefix(config.space_id)
    return [
        f"{where} is not a secret of this space ({prefix}...)"
        for where, ref in named
        if not owns_secret(config.space_id, ref)
    ]


def _repetition_problem(config: SpaceConfig, params: dict[str, Any]) -> str | None:
    """The sweep skips a repetition rule it cannot read (app/triggers.py); say so before approval."""
    try:
        parsed = RepetitionParams.of(params)
    except ValueError:
        return "params need above in [0, 1), window_days from 1 to 90 and min_questions of at least 1"
    if parsed.source_id is not None and config.source(parsed.source_id) is None:
        return "params.source_id names no source of the space"
    return None


def _unreadable(
    config: SpaceConfig, trigger: TriggerRuleConfig, pattern: TraitRuleConfig | None
) -> str | None:
    """The cell's own check at firing time (app/triggers.py, target_problem), on the documents of the diff."""
    endpoint = config.endpoint(trigger.endpoint_id)
    source = config.source(endpoint.source_id) if endpoint else None
    purposes, audiences = (
        released_to(pattern.catalog, pattern.purposes) if pattern else (frozenset(), frozenset())
    )
    return unreadable_reason(
        TriggerRule(trigger.rule_id, trigger.condition, trigger.version, trigger.params, trigger.endpoint_id),
        endpoint_found=endpoint is not None,
        endpoint_enabled=bool(endpoint and endpoint.enabled),
        endpoint_types=endpoint.event_types if endpoint else [],
        audience=source.audience if source else None,
        source_purposes=source.purposes if source else frozenset(),
        trait_purposes=purposes,
        trait_audiences=audiences,
        endpoint_format=endpoint.format if endpoint else "standard",
    )


def _check_references(config: SpaceConfig) -> None:
    source_ids = {s.source_id for s in config.sources}
    if any(m.source_id not in source_ids for m in config.mappings):
        raise InvalidInput("a mapping names a source that does not exist in the space")
    if any(e.source_id not in source_ids for e in config.webhook_endpoints):
        raise InvalidInput("a webhook endpoint names a source that does not exist in the space")
    endpoint_ids = {e.endpoint_id for e in config.webhook_endpoints}
    if any(r.endpoint_id not in endpoint_ids for r in config.trigger_rules):
        raise InvalidInput("a trigger rule names an endpoint that does not exist")
    declared = {p.name for p in config.purposes.purposes}
    compatible = config.policy.compatible_purposes
    if {p for key, names in compatible.items() for p in (key, *names)} - declared:
        raise InvalidInput(
            "the policy's compatible purposes name a purpose the purposes document does not declare"
        )


def _source_config(source: Source) -> SourceConfig:
    return SourceConfig(
        source_id=source.source_id,
        name=source.name,
        vendor=source.vendor,
        channel=source.channel,
        purposes=source.purposes,
        audience=source.audience,
        verification_ceiling=source.verification_ceiling,
        confirms_before_acting=source.confirms_before_acting,
        navigation_enabled=source.navigation_enabled,
        trusted_action_ops=source.trusted_action_ops,
        critical=source.critical,
    )


def _describe(exc: ValidationError) -> str:
    return "; ".join(".".join(str(p) for p in e["loc"]) + f": {e['type']}" for e in exc.errors())[:1000]
