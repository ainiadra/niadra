"""A diff that would leave a rule accepted and silent is refused when sent and approved (4.7, 20.5, 22.1).

The cell ignores a pattern rule the catalog or the sensitive-category barrier refuses, and never fires a
warning rule whose endpoint cannot read it; the control plane now says so before anyone approves.
"""

import ast
from pathlib import Path
from typing import Any

import pytest

from niadra.app.control_config import OUTBOUND_EVENT_TYPES
from niadra.config.models import SpaceConfig
from niadra.config.starter import STARTER_EXTRACTION
from niadra.domain.common.errors import InvalidInput
from niadra.domain.common.ids import uuid7
from niadra.domain.control.records import Caller, ConfigVersion, DiffStatus
from tests.unit.control.conftest import World


def _endpoint(world: World, source: str, endpoint_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "endpoint_id": endpoint_id,
        "url": f"https://{endpoint_id}.acme.example/hooks",
        "source_id": str(world.boot.keys[source].key.source_id),
        "event_types": ["trigger.fired"],
        "secret_refs": [f"spaces/{world.boot.space.space_id}/webhook/{endpoint_id}"],
        **extra,
    }


def _rule(rule_id: str, condition: str, endpoint_id: str, **params: Any) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "condition": condition,
        "version": "1",
        "endpoint_id": endpoint_id,
        "params": params,
    }


def _pattern(rule_id: str, catalog: str, **params: Any) -> dict[str, Any]:
    return {"rule_id": rule_id, "catalog": catalog, "version": "1", "params": params}


async def _apply(world: World, admin: Caller, name: str, document: Any) -> None:
    diff = await world.service.submit_diff(admin, world.boot.space.space_id, name, document, "set up")
    assert (await world.service.approve_diff(admin, diff.diff_id)).status is DiffStatus.APPLIED


async def _refused(world: World, admin: Caller, name: str, document: Any) -> str:
    with pytest.raises(InvalidInput) as refused:
        await world.service.submit_diff(admin, world.boot.space.space_id, name, document, "try")
    return str(refused.value.detail)


async def test_a_pattern_rule_outside_the_catalog_limits_or_on_a_sensitive_category_is_refused(
    world: World,
) -> None:
    admin = await world.admin()
    too_long = await _refused(
        world, admin, "trait-rules", [_pattern("complaints", "recurring_complaint", window_days=400)]
    )
    assert "trait-rules complaints" in too_long
    assert "window_days" in too_long
    health = await _refused(
        world, admin, "trait-rules", [_pattern("health", "recurring_complaint", category="health")]
    )
    assert "sensitive or not allowed" in health
    typo = await _refused(world, admin, "trait-rules", [_pattern("late", "overdue_invoices", min_cont=3)])
    assert "unknown rule parameters: min_cont" in typo
    # Within the catalog, the rule goes to approval as before.
    await _apply(
        world, admin, "trait-rules", [_pattern("late", "overdue_invoices", min_count=3, window_days=90)]
    )


async def test_a_warning_rule_whose_endpoint_cannot_read_it_is_refused(world: World) -> None:
    admin = await world.admin()
    await _apply(
        world,
        admin,
        "webhook-endpoints",
        [
            _endpoint(world, "whatsapp", "chat"),
            _endpoint(world, "billing", "erp"),
            _endpoint(world, "billing", "audit", event_types=["action.recorded"]),
        ],
    )
    await _apply(world, admin, "trait-rules", [_pattern("late", "overdue_invoices")])

    identity = await _refused(
        world, admin, "trigger-rules", [_rule("ambiguous", "identity.ambiguous", "chat")]
    )
    assert "endpoint chat cannot receive it (audience_cannot_read_identity)" in identity
    unsubscribed = await _refused(
        world, admin, "trigger-rules", [_rule("promises", "promise.overdue", "audit")]
    )
    assert "(endpoint_not_subscribed)" in unsubscribed
    # Overdue invoices reach billing only (20.3): the chat agent's source has the support purpose.
    purpose = await _refused(
        world, admin, "trigger-rules", [_rule("debt", "trait.present", "chat", name="late")]
    )
    assert "(purpose_not_allowed)" in purpose
    missing = await _refused(
        world, admin, "trigger-rules", [_rule("debt", "trait.present", "erp", name="gone")]
    )
    assert "params.name names no pattern rule in use" in missing

    await _apply(
        world,
        admin,
        "trigger-rules",
        [_rule("debt", "trait.present", "erp", name="late"), _rule("ambiguous", "identity.ambiguous", "erp")],
    )


async def test_an_endpoint_subscribed_to_an_event_niadra_never_sends_is_refused(world: World) -> None:
    admin = await world.admin()
    typo = await _refused(
        world,
        admin,
        "webhook-endpoints",
        [_endpoint(world, "billing", "erp", event_types=["trigger.fired", "trigger.fird"])],
    )
    assert "webhook-endpoints erp: event_types.1 is not an event type Niadra sends" in typo
    assert "trigger.fird" not in typo
    await _apply(
        world,
        admin,
        "webhook-endpoints",
        [
            _endpoint(world, "billing", "erp", event_types=sorted(OUTBOUND_EVENT_TYPES)),
            _endpoint(world, "whatsapp", "all", event_types=["*"]),
            # A SIEM stream takes receipt kinds, which its own model checks.
            _endpoint(world, "billing", "siem", event_types=["read", "denied"], format="ocsf"),
        ],
    )


def _mapping(world: World, source: str, mode: str) -> dict[str, Any]:
    return {
        "source_id": str(world.boot.keys[source].key.source_id),
        "version": "m1",
        "auth": {"mode": mode, "secret_ref": f"spaces/{world.boot.space.space_id}/webhook-in/{source}"},
        "rules": [{"match": "type", "idempotency_key": "id", "occurred_at": "at"}],
    }


async def test_a_webhook_mapping_on_a_client_certificate_is_accepted(world: World) -> None:
    # The edge's mTLS address asks for the certificate and forwards it; the cell pins it.
    admin = await world.admin()
    await _apply(world, admin, "mappings", [_mapping(world, "billing", "mtls")])


async def test_a_change_that_silences_a_rule_in_use_is_refused_but_turning_an_endpoint_off_is_a_choice(
    world: World,
) -> None:
    admin = await world.admin()
    await _apply(world, admin, "webhook-endpoints", [_endpoint(world, "billing", "erp")])
    await _apply(world, admin, "trait-rules", [_pattern("late", "overdue_invoices")])
    await _apply(world, admin, "trigger-rules", [_rule("debt", "trait.present", "erp", name="late")])

    unsubscribed = await _refused(
        world,
        admin,
        "webhook-endpoints",
        [_endpoint(world, "billing", "erp", event_types=["action.recorded"])],
    )
    assert "trigger-rules debt: endpoint erp cannot receive it (endpoint_not_subscribed)" in unsubscribed
    orphaned = await _refused(
        world, admin, "trait-rules", [{**_pattern("late", "overdue_invoices"), "enabled": False}]
    )
    assert "trigger-rules debt: params.name names no pattern rule in use" in orphaned

    await _apply(world, admin, "webhook-endpoints", [_endpoint(world, "billing", "erp", enabled=False)])
    # A rule that is off is not checked, and a rule aimed at an endpoint that is off waits for it.
    await _apply(
        world,
        admin,
        "trigger-rules",
        [
            {**_rule("debt", "trait.present", "erp", name="late"), "enabled": False},
            _rule("x", "recontact", "erp"),
        ],
    )


async def test_a_policy_that_turns_a_category_sensitive_passes_even_when_a_pattern_rule_stops(
    world: World,
) -> None:
    admin = await world.admin()
    await _apply(world, admin, "trait-rules", [_pattern("orders", "recurring_complaint", category="orders")])
    # Protecting people always passes; the cell stops deriving the pattern (20.4).
    policy = {"version": "p9", "categories": [{"name": "orders", "sensitivity": "sensitive"}]}
    await _apply(world, admin, "policies", policy)
    # From then on, the pattern rule document is held to the barrier when it changes.
    renamed = [_pattern("orders-2", "recurring_complaint", category="orders")]
    assert "sensitive or not allowed" in await _refused(world, admin, "trait-rules", renamed)


async def test_a_space_holds_at_most_25_pattern_rules_and_50_warning_rules(world: World) -> None:
    admin = await world.admin()
    patterns = [_pattern(f"p{i}", "asked_to_cancel") for i in range(26)]
    assert "trait-rules: 26 rules, at most 25 per space" in await _refused(
        world, admin, "trait-rules", patterns
    )
    await _apply(world, admin, "trait-rules", patterns[:25])

    await _apply(world, admin, "webhook-endpoints", [_endpoint(world, "billing", "erp")])
    rules = [_rule(f"r{i}", "recontact", "erp") for i in range(51)]
    assert "trigger-rules: 51 rules, at most 50 per space" in await _refused(
        world, admin, "trigger-rules", rules
    )
    await _apply(world, admin, "trigger-rules", rules[:50])


async def test_a_pattern_rule_over_events_never_asks_past_the_hot_window(world: World) -> None:
    # System events leave the live database after events_hot_days (90 by default).
    admin = await world.admin()
    wide = _pattern("inv", "overdue_invoices", window_days=180)
    refused = await _refused(world, admin, "trait-rules", [wide])
    assert "trait-rules inv: window_days 180 counts system events, which stay 90 days" in refused
    await _apply(world, admin, "trait-rules", [_pattern("inv", "overdue_invoices", window_days=90)])
    # A rule over conversations keeps its 1 to 365 days.
    await _apply(world, admin, "trait-rules", [_pattern("cancel", "asked_to_cancel", window_days=365)])


async def test_an_export_schedule_writes_in_the_spaces_region_and_to_a_place_of_its_own(world: World) -> None:
    # The run refused a destination outside the region; now the diff is refused before approval,
    # and two schedules never overwrite each other's files.
    admin = await world.admin()

    def schedule(schedule_id: str, **extra: Any) -> dict[str, Any]:
        return {
            "schedule_id": schedule_id,
            "destination_kind": "s3",
            "bucket": "acme-lake",
            "prefix": "niadra/",
            "region": "sa-east-1",
            "tables": ["facts"],
            "credential_ref": f"spaces/{world.boot.space.space_id}/export/{schedule_id}",
        } | extra

    elsewhere = await _refused(world, admin, "export-schedules", [schedule("eu", region="eu-west-1")])
    assert "export-schedules eu: region is not the space's region" in elsewhere
    twice = await _refused(world, admin, "export-schedules", [schedule("a"), schedule("b")])
    assert "export-schedules b: another schedule has its bucket and prefix" in twice
    await _apply(world, admin, "export-schedules", [schedule("a"), schedule("b", prefix="niadra-2/")])


async def test_a_space_holds_at_most_8_task_views(world: World) -> None:
    admin = await world.admin()
    views = [{"name": f"task:t{i}", "purposes": ["billing"]} for i in range(9)]
    assert "views: 9 task views, at most 8 per space" in await _refused(world, admin, "views", views)
    await _apply(world, admin, "views", views[:8])


async def test_approval_checks_the_diff_again_against_the_documents_of_now(world: World) -> None:
    admin = await world.admin()
    space = world.boot.space.space_id
    await _apply(world, admin, "webhook-endpoints", [_endpoint(world, "billing", "erp")])
    pending = await world.service.submit_diff(
        admin, space, "trigger-rules", [_rule("r", "recontact", "erp")], "recontact warning"
    )
    await _apply(
        world,
        admin,
        "webhook-endpoints",
        [_endpoint(world, "billing", "erp", event_types=["action.recorded"])],
    )

    with pytest.raises(InvalidInput) as refused:
        await world.service.approve_diff(admin, pending.diff_id)
    assert "(endpoint_not_subscribed)" in str(refused.value.detail)


async def test_a_problem_already_stored_never_blocks_an_unrelated_change(world: World) -> None:
    admin = await world.admin()
    space = world.boot.space.space_id
    await _apply(world, admin, "webhook-endpoints", [_endpoint(world, "whatsapp", "chat")])
    # Stored before these checks existed: a warning the chat agent can never receive.
    async with world.uow() as uow:
        version = await uow.config.current_version(space) + 1
        rules = [_rule("ambiguous", "identity.ambiguous", "chat")]
        await uow.config.add_version(
            ConfigVersion(space, version, "trigger-rules", rules, uuid7(), world.clock.now())
        )

    await _apply(world, admin, "review-sampling", {"base_rate": 0.05})
    await _apply(
        world,
        admin,
        "webhook-endpoints",
        [_endpoint(world, "whatsapp", "chat", url="https://new.acme.example/h")],
    )
    # Fixing it is a change like any other.
    await _apply(
        world,
        admin,
        "trigger-rules",
        [{**_rule("ambiguous", "identity.ambiguous", "chat"), "enabled": False}],
    )


def _sent_event_types() -> set[str]:
    """Every literal event type the cell hands to the outgoing webhook queue."""
    app = Path(__file__).resolve().parents[3] / "src" / "niadra" / "app"
    sent: set[str] = set()
    for module in app.rglob("*.py"):
        for node in ast.walk(ast.parse(module.read_text())):
            if not isinstance(node, ast.Call) or len(node.args) < 3:
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            arg = node.args[2]
            if (
                name in ("enqueue_event", "_announce")
                and isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
            ):
                sent.add(arg.value)
    return sent


def test_every_event_type_the_cell_sends_can_be_subscribed_and_nothing_else() -> None:
    sent = _sent_event_types()
    assert {"trigger.fired", "action.recorded", "trait.added", "erasure.completed"} <= sent
    missing = sent - OUTBOUND_EVENT_TYPES
    assert not missing, f"add {sorted(missing)} to OUTBOUND_EVENT_TYPES in niadra/app/control_config.py"
    silent = OUTBOUND_EVENT_TYPES - sent
    assert not silent, f"the cell never sends {sorted(silent)}: remove them from OUTBOUND_EVENT_TYPES"


async def test_a_subscription_to_a_type_the_cell_never_sends_is_refused(world: World) -> None:
    # The cell never sends these three, so a subscription to them would stay silent.
    admin = await world.admin()
    for silent in ("memory.updated", "open_item.due", "usage.threshold"):
        refused = await _refused(
            world, admin, "webhook-endpoints", [_endpoint(world, "billing", "erp", event_types=[silent])]
        )
        assert "event_types.0 is not an event type Niadra sends" in refused


async def test_a_source_that_owns_an_endpoint_a_siem_stream_and_a_mapping_can_be_revoked(
    world: World,
) -> None:
    # Revoking such a source used to fail with 422: the published config still named it. Now its
    # objects stay in the documents, as history, and leave the cell's configuration with it.
    admin = await world.admin()
    space = world.boot.space.space_id
    billing = world.boot.keys["billing"].key.source_id
    await _apply(world, admin, "mappings", [_mapping(world, "billing", "hmac")])
    await _apply(
        world,
        admin,
        "webhook-endpoints",
        [
            _endpoint(world, "billing", "erp", event_types=["trigger.fired"]),
            _endpoint(world, "billing", "siem", event_types=["read"], format="ocsf"),
            _endpoint(world, "whatsapp", "chat", event_types=["action.recorded"]),
        ],
    )
    await _apply(world, admin, "trait-rules", [_pattern("late", "overdue_invoices")])
    await _apply(world, admin, "trigger-rules", [_rule("debt", "trait.present", "erp", name="late")])

    await world.service.revoke_source(admin, billing, "contract ended")

    snapshot = await world.service.snapshot(world.boot.space.cell_id)
    published = snapshot.documents[snapshot.manifest["spaces"][str(space)]]
    assert published.get("mappings", []) == []
    assert [e["endpoint_id"] for e in published["webhook_endpoints"]] == ["chat"]
    assert published.get("trigger_rules", []) == []
    _, stored = await world.service.config_document(admin, space, "webhook-endpoints")
    assert {e["endpoint_id"] for e in stored} == {"erp", "siem", "chat"}

    # The space keeps changing: a diff that keeps the inert entries passes, one that adds a new one does not.
    await _apply(
        world,
        admin,
        "webhook-endpoints",
        [*stored, _endpoint(world, "whatsapp", "crm", event_types=["conversation.outcome"])],
    )
    refused = await _refused(
        world, admin, "webhook-endpoints", [*stored, _endpoint(world, "billing", "late", event_types=["*"])]
    )
    assert "late belongs to a revoked source" in refused


async def test_a_warning_rule_aimed_at_a_siem_stream_is_refused(world: World) -> None:
    # The stream carries receipts in OCSF; a warning would pass every other check and never be sent.
    admin = await world.admin()
    await _apply(
        world,
        admin,
        "webhook-endpoints",
        [_endpoint(world, "billing", "siem", event_types=["*"], format="ocsf")],
    )
    refused = await _refused(world, admin, "trigger-rules", [_rule("promises", "promise.overdue", "siem")])
    assert "endpoint siem cannot receive it (endpoint_is_siem_stream)" in refused


def test_a_view_template_with_logic_is_refused_and_custom_is_not_a_task_view() -> None:
    # The control plane refuses what the cell would refuse, and `custom` leaves the 8 free.
    from niadra.app.control_config import semantic_problems
    from niadra.config.models import TaskView
    from niadra.testing.cell import sample_config

    views = [TaskView(name=f"task:t{i}") for i in range(8)]
    good = sample_config().model_copy(
        update={"views": [*views, TaskView(name="custom", template="{facts}\n{pending}")]}
    )
    assert not [p for p in semantic_problems(good) if p.startswith("views")]
    bad = good.model_copy(update={"views": [*views, TaskView(name="custom", template="{facts.value}")]})
    assert [p for p in semantic_problems(bad) if p.startswith("views")] == [
        "views custom: a view template names only sections: "
        "actions, customer, episodes, facts, history, meta, objects, pending, stable_patterns, summary, "
        "volatile_patterns"
    ]


async def test_a_document_naming_a_secret_of_another_space_is_refused(world: World) -> None:
    # The vault is the cell's; a reference outside `spaces/<this space>/` is refused before approval.
    admin = await world.admin()
    other = f"spaces/{uuid7()}/model/llm"
    endpoint = _endpoint(world, "billing", "crm", authorization_ref=other)
    refused = await _refused(world, admin, "webhook-endpoints", [endpoint])
    assert "webhook-endpoints crm: authorization_ref is not a secret of this space" in refused
    mapping = _mapping(world, "billing", "url_token")
    mapping["auth"]["secret_ref"] = other
    refused = await _refused(world, admin, "mappings", [mapping])
    assert "auth.secret_ref is not a secret of this space" in refused


async def test_a_field_at_its_default_stays_out_of_the_published_document(world: World) -> None:
    # 03/10/2026: a new optional field (`extraction.operations`) reached every stored document at its default,
    # and the cells of the version before it, whose models forbid what they do not know, dropped the spaces.
    # At its default it stays out, so the version before reads what this one writes, until a diff sets it.
    admin = await world.admin()
    space = str(world.boot.space.space_id)

    def published(snapshot: Any) -> dict[str, Any]:
        document: dict[str, Any] = snapshot.documents[snapshot.manifest["spaces"][space]]
        return document

    before = published(await world.service.snapshot(world.boot.space.cell_id))
    assert "operations" not in before.get("extraction", {})
    assert SpaceConfig.model_validate(before).extraction.operations == []
    schema = STARTER_EXTRACTION.model_dump(mode="json", exclude_defaults=True)
    await _apply(world, admin, "schemas", {**schema, "version": "ops-1", "operations": ["file"]})
    after = published(await world.service.snapshot(world.boot.space.cell_id))
    assert after["extraction"]["operations"] == ["file"]


async def test_a_predicate_of_a_category_the_space_never_declared_is_refused_as_the_cell_would(
    world: World,
) -> None:
    # The cell refuses such a configuration and keeps the space's last good one in silence, so every later
    # change of the space waited behind it; the control plane now refuses it at the diff.
    admin = await world.admin()
    schema = STARTER_EXTRACTION.model_dump(mode="json", exclude_defaults=True)
    known = schema["predicates"][0]["category"]
    stray = {**schema, "predicates": [*schema["predicates"], {"name": "mood", "category": "nowhere"}]}
    detail = await _refused(world, admin, "schemas", stray)
    assert "the cell would refuse this configuration: predicate mood uses unknown category nowhere" in detail
    await _apply(
        world,
        admin,
        "schemas",
        {**schema, "predicates": [*schema["predicates"], {"name": "mood", "category": known}]},
    )
