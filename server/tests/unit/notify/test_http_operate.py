"""Governance routes for traits, triggers and webhook deliveries (7.1), with Console roles."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.operate import router
from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.app.configure import AssistService
from niadra.app.notify import NotifyService, enqueue_event
from niadra.app.pipeline import PipelineService
from niadra.app.traits import TraitsService
from niadra.app.triggers import TriggersService
from niadra.config.models import TraitRuleConfig
from niadra.contracts.configure import AssistRequest
from niadra.domain.common.errors import Unauthenticated
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, SpaceRef
from niadra.domain.configure.model import AssistTask
from niadra.domain.notify.webhooks import WebhookSubject
from niadra.domain.records import ProfileRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Environment
from niadra.testing.cell import SPACE_ID, TENANT_ID, Cell
from tests.unit.audit.fakes import RecordingAdminAudit
from tests.unit.objects.fakes import T0, World, config_with, episode, handle, open_item

RECURRING = TraitRuleConfig(rule_id="recurring_complaint", catalog="recurring_complaint", version="v3")


class FakeEdge:
    async def authenticate_person(self, token: str) -> PersonPrincipal:
        if not token.startswith("role:"):
            raise Unauthenticated("bad token")
        space = SpaceRef(SPACE_ID, TENANT_ID, "us-east-2", Environment.SANDBOX)
        return PersonPrincipal(space, uuid7(), frozenset({token.removeprefix("role:")}))


@dataclass
class Env:
    http: httpx.AsyncClient
    world: World
    marina: ProfileRecord
    actions: RecordingAdminAudit
    configure: AssistService


@pytest.fixture
async def env(cell: Cell) -> AsyncIterator[Env]:
    cell.config.put(config_with(trait_rules=[RECURRING]))
    cell.clock.at = T0
    world = World(cell)
    marina = world.identity.add_profile(handle())
    origin = world.identity.handles[marina.handle_ids[0]]
    world.memory.episodes.extend(episode(origin, T0 - timedelta(days=d)) for d in (1, 2, 3))
    world.memory.items.append(open_item(origin, T0 - timedelta(hours=1)))
    world.sessions.active = [marina.profile_id]
    container = SimpleNamespace(
        edge=FakeEdge(),
        traits=TraitsService(world.uow, cell.clock, cell.config),
        triggers=TriggersService(world.uow, cell.clock, cell.config),
        notify=NotifyService(world.uow, cell.egress, cell.secrets, cell.clock, cell.config),
        admin_audit=RecordingAdminAudit(),
        configure=AssistService(world.uow, ScriptedLlm(), cell.clock, cell.config, NoProposals()),
        pipeline=PipelineService(cell.uow, cell.bus, cell.clock),
    )
    await container.traits.derive(Task(TaskName.DERIVE_TRAITS, SPACE_ID, {}, profile_id=marina.profile_id))
    app = create_app("admin", container, [router])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://cell.test"
    ) as http:
        yield Env(http, world, marina, container.admin_audit, container.configure)


def as_role(role: str) -> dict[str, str]:
    return {"authorization": f"Bearer role:{role}"}


async def test_retract_needs_security_or_integration(env: Env) -> None:
    async with env.world.cell.uow(SPACE_ID) as uow:
        [trait] = await uow.traits.traits_of(env.marina.profile_id)
    path = f"/v1/traits/{trait.trait_id}/retract"
    assert (await env.http.post(path, headers=as_role("analysis"))).status_code == 403
    response = await env.http.post(path, headers=as_role("security"))
    assert response.status_code == 200
    assert response.json()["retracted_at"] is not None
    assert (
        await env.http.post(f"/v1/traits/{uuid7()}/retract", headers=as_role("security"))
    ).status_code == 404
    assert env.actions.records == [
        {"action": "traits.retract", "profile_id": env.marina.profile_id, "target": f"trait:{trait.trait_id}"}
    ]


async def test_trait_rule_dry_run_and_validation(env: Env) -> None:
    body: dict[str, Any] = {"rule_id": "rc", "catalog": "recurring_complaint", "params": {"min_count": 3}}
    response = await env.http.post("/v1/traits/rules/dry-run", json=body, headers=as_role("integration"))
    assert response.status_code == 200
    assert response.json()["profiles_matching"] == 1
    sensitive = {**body, "params": {"category": "health"}}
    assert (
        await env.http.post("/v1/traits/rules/dry-run", json=sensitive, headers=as_role("integration"))
    ).status_code == 422
    unknown = {**body, "catalog": "propensity_to_buy"}
    assert (
        await env.http.post("/v1/traits/rules/dry-run", json=unknown, headers=as_role("integration"))
    ).status_code == 422
    assert [(r["action"], r["target"]) for r in env.actions.records] == [
        ("traits.rule_dry_run", "trait_rule:rc")
    ]


async def test_opt_out(env: Env) -> None:
    path = f"/v1/profiles/{env.marina.profile_id}/traits/opt-out"
    assert (await env.http.post(path, headers=as_role("integration"))).status_code == 403
    response = await env.http.post(path, headers=as_role("security"))
    assert response.status_code == 200
    assert response.json()["profile_id"] == str(env.marina.profile_id)
    assert env.actions.records == [
        {"action": "traits.opt_out", "profile_id": env.marina.profile_id, "target": None}
    ]


async def test_trigger_dry_run_and_firings(env: Env) -> None:
    body = {"rule_id": "late_promise", "condition": "promise.overdue", "endpoint_id": "crm"}
    response = await env.http.post("/v1/triggers/dry-run", json=body, headers=as_role("integration"))
    assert response.status_code == 200
    assert response.json()["would_fire"] == 1
    assert env.actions.actions == ["triggers.rule_dry_run"]
    listed = await env.http.get("/v1/triggers/firings", params={"limit": 10}, headers=as_role("analysis"))
    assert listed.status_code == 200
    assert listed.json() == {"items": [], "next_cursor": None}


async def test_deliveries_and_redelivery(env: Env) -> None:
    async with env.world.uow(SPACE_ID) as uow:
        [delivery_id] = await enqueue_event(
            uow, env.world.cell.config.space(SPACE_ID), "memory.updated", WebhookSubject("pse", "person"), {}
        )
    listed = await env.http.get(
        "/v1/webhooks/deliveries", params={"status": "pending"}, headers=as_role("integration")
    )
    assert listed.status_code == 200
    assert str(delivery_id) in {d["delivery_id"] for d in listed.json()["items"]}
    assert "body" not in listed.json()["items"][0]
    response = await env.http.post(
        f"/v1/webhooks/deliveries/{delivery_id}/redeliver", headers=as_role("integration")
    )
    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    assert env.actions.records[-1]["target"] == f"delivery:{delivery_id}"
    bad = await env.http.get(
        "/v1/webhooks/deliveries", params={"cursor": "%%%"}, headers=as_role("integration")
    )
    assert bad.status_code == 422


async def test_a_test_delivery_is_sent_on_request_and_recorded(env: Env) -> None:
    response = await env.http.post("/v1/webhooks/endpoints/crm/test", headers=as_role("integration"))
    assert response.status_code == 202
    assert (response.json()["event_type"], response.json()["endpoint_id"]) == ("webhook.test", "crm")
    assert env.actions.records[-1]["target"] == "endpoint:crm"
    listed = await env.http.get("/v1/webhooks/deliveries", headers=as_role("integration"))
    assert response.json()["delivery_id"] in {d["delivery_id"] for d in listed.json()["items"]}
    missing = await env.http.post("/v1/webhooks/endpoints/nope/test", headers=as_role("integration"))
    assert missing.status_code == 404
    forbidden = await env.http.post("/v1/webhooks/endpoints/crm/test", headers=as_role("analysis"))
    assert forbidden.status_code == 403


class NoProposals:
    async def submit(self, proposal: object) -> str:
        raise AssertionError("not called when listing")


async def test_assistant_runs_are_listed_for_integration(env: Env) -> None:
    person = PersonPrincipal(
        SpaceRef(SPACE_ID, TENANT_ID, "us-east-2", Environment.SANDBOX), uuid7(), frozenset({"integration"})
    )
    run = await env.configure.request(
        person, AssistRequest(task=AssistTask.TRIGGER_OR_TRAIT_RULE, description="warn on late promises")
    )
    assert (await env.http.get("/v1/assist", headers=as_role("analysis"))).status_code == 403
    listed = await env.http.get("/v1/assist", params={"limit": 10}, headers=as_role("integration"))
    assert listed.status_code == 200
    assert [r["run_id"] for r in listed.json()["items"]] == [str(run.run_id)]
    assert listed.json()["next_cursor"] is None


async def test_dead_tasks_are_listed_and_replayed_by_security_with_a_receipt(env: Env) -> None:
    # A failed erasure resumes where it stopped: its buried task goes back with fresh attempts.
    cell = env.world.cell
    erase = Task(TaskName.ERASE, SPACE_ID, {"request_id": str(uuid7())})
    other = Task(TaskName.COMPILE, SPACE_ID, profile_id=env.marina.profile_id)
    async with cell.uow.cell() as uow:
        await uow.dead.bury(erase, 10, "boom", T0)
        await uow.dead.bury(other, 3, "boom", T0)
    assert (await env.http.get("/v1/operations/dead-tasks", headers=as_role("analysis"))).status_code == 403
    listed = (await env.http.get("/v1/operations/dead-tasks", headers=as_role("security"))).json()
    assert {t["name"] for t in listed["items"]} == {"erase", "compile"}
    bad = await env.http.post(
        "/v1/operations/dead-tasks/replay", json={"task": "nope"}, headers=as_role("security")
    )
    assert bad.status_code == 422
    replay = await env.http.post(
        "/v1/operations/dead-tasks/replay", json={"task": "erase"}, headers=as_role("security")
    )
    assert replay.json() == {"replayed": 1}
    assert [t.name for t in cell.bus.published] == [TaskName.ERASE]
    left = (await env.http.get("/v1/operations/dead-tasks", headers=as_role("security"))).json()["items"]
    assert [t["name"] for t in left] == ["compile"]
    assert env.actions.records[-1] == {
        "action": "operations.replay_dead_tasks",
        "profile_id": None,
        "target": "task:erase",
    }
