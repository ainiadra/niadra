"""The configuration assistant: masking, checks of the proposal and the flow with a scripted LLM (22.4)."""

import json
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from niadra.adapters.memory.configure import InMemoryAssistRepository
from niadra.adapters.memory.fakes import InMemoryObjectStore, ScriptedLlm
from niadra.app.configure import AssistService
from niadra.contracts.configure import AssistRequest
from niadra.domain.common.errors import InvalidInput, NotFound, RateLimited
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, SpaceRef
from niadra.domain.configure.mapping_check import RuleExpressions, check_mapping
from niadra.domain.configure.masking import contains_removed, mask_payloads, mask_text
from niadra.domain.configure.model import AssistStatus, AssistTask
from niadra.domain.configure.policy_check import CategoryAccess, loosened
from niadra.domain.records import EventRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Environment, EventKind, Speaker
from niadra.ports.proposals import ConfigProposal
from niadra.testing.cell import (
    BILLING_SOURCE,
    SPACE_ID,
    TENANT_ID,
    WHATSAPP_SOURCE,
    Cell,
    PatchedUnitOfWorkFactory,
)

PII = (
    "marina.souza@example.com",
    "+55 11 98765-4321",
    "123.456.789-09",
    "Marina Souza",
    "Rua das Flores 1200",
)


def invoice_payload(i: int, kind: str = "invoice.paid") -> dict[str, Any]:
    return {
        "type": kind,
        "id": f"evt_{i:04d}",
        "created_at": "2026-09-20T10:00:00Z",
        "customer": {"email": PII[0], "phone": PII[1], "document": PII[2], "name": PII[3]},
        "invoice": {"number": 4400 + i, "amount": 129.9, "status": "PAID"},
    }


class TestMasking:
    def test_text_loses_emails_phones_documents_and_names(self):
        removed: set[str] = set()
        masked = mask_text(f"Oi, sou a {PII[3]}, meu e-mail é {PII[0]}, cpf {PII[2]}, tel {PII[1]}", removed)
        for value in PII[:4]:
            assert value not in masked
        assert "<email>" in masked
        assert "<document>" in masked
        assert "<phone>" in masked
        assert "<name>" in masked
        assert PII[0] in removed

    def test_payloads_keep_structure_and_shared_codes_only(self):
        masked = mask_payloads([invoice_payload(i) for i in range(5)])
        text = json.dumps(masked.samples)
        for value in PII:
            assert value not in text
        first = masked.samples[0]
        assert first["type"] == "invoice.paid"
        assert first["invoice"]["status"] == "PAID"
        assert first["customer"]["email"] == "<string:personal>"
        assert first["invoice"]["amount"] == "<number>"
        assert first["created_at"] == "<datetime>"
        assert first["id"].startswith("<string:")

    def test_a_rare_code_is_masked(self):
        masked = mask_payloads([invoice_payload(1, "invoice.voided"), invoice_payload(2), invoice_payload(3)])
        assert masked.samples[0]["type"].startswith("<string:")

    def test_leak_guard_ignores_codes_but_catches_values(self):
        removed = {"invoice.paid", "Marina Souza", "4471"}
        assert not contains_removed('{"match": "type == \'invoice.paid\'"}', removed)
        assert contains_removed('{"text": "marina souza"}', removed)


class TestChecks:
    def test_mapping_report_counts_and_labels_unmapped_types(self):
        payloads = [invoice_payload(i) for i in range(3)] + [
            invoice_payload(9, "invoice.voided"),
            {"weird": 1},
        ]
        rules = [
            RuleExpressions("type == 'invoice.paid'", ("id", "created_at")),
            RuleExpressions("[[", ("id",)),
        ]
        report = check_mapping(rules, payloads)
        assert (report.samples, report.mapped, report.unmapped) == (5, 3, 2)
        assert dict(report.unmapped_types)["invoice.voided"] == 1
        assert report.invalid_expressions == ("[[",)
        assert all(not t.startswith("evt_") for t, _ in report.unmapped_types)

    def test_policy_may_only_get_stricter(self):
        now = {
            "billing": CategoryAccess(2, 0, frozenset({"billing"})),
            "contact": CategoryAccess(1, 0, frozenset()),
        }
        stricter = {
            "billing": CategoryAccess(3, 1, frozenset({"billing"})),
            "contact": CategoryAccess(1, 0, frozenset({"support"})),
        }
        looser = {"billing": CategoryAccess(1, 0, frozenset({"billing", "sales"}))}
        assert loosened(now, stricter, frozenset({"d"}), frozenset({"d"})) == []
        assert loosened(now, looser, frozenset({"d"}), frozenset()) == [
            "deny_rule_removed:d",
            "lower_verification:billing",
            "wider_purposes:billing",
            "category_removed:contact",
        ]


class RecordingProposals:
    def __init__(self, fail: Exception | None = None) -> None:
        self.sent: list[ConfigProposal] = []
        self.fail = fail

    async def submit(self, proposal: ConfigProposal) -> str:
        if self.fail:
            raise self.fail
        self.sent.append(proposal)
        return "diff-1"


@dataclass
class SampleIngest:
    """What the assistant reads from ingest: keys of raw bodies kept in object storage, and messages."""

    refs: list[tuple[UUID, str]] = field(default_factory=list)
    events: list[EventRecord] = field(default_factory=list)

    async def raw_refs(self, source_id: UUID, *, limit: int) -> list[tuple[UUID, str]]:
        return self.refs[:limit]

    async def recent_messages(self, source_id: UUID, *, limit: int) -> list[EventRecord]:
        return [e for e in self.events if e.source_id == source_id][:limit]


class World:
    def __init__(self, cell: Cell, *answers: Any, proposals: RecordingProposals | None = None) -> None:
        self.cell = cell
        self.llm = ScriptedLlm(*answers)
        self.objects = InMemoryObjectStore()
        payloads = [invoice_payload(i) for i in range(8)] + [invoice_payload(20, "invoice.voided")]
        self.ingest = SampleIngest()
        for i, payload in enumerate(payloads):
            key = f"raw/{SPACE_ID}/{i}.json"
            self.objects.objects[key] = json.dumps(payload).encode()
            self.ingest.refs.append((uuid7(), key))
        # Erased since it was listed, and a body that never was JSON: both are skipped.
        self.ingest.refs += [(uuid7(), "raw/gone.json"), (uuid7(), "raw/not-json.json")]
        self.objects.objects["raw/not-json.json"] = b"<xml/>"
        self.proposals = proposals or RecordingProposals()
        uow = PatchedUnitOfWorkFactory(cell.uow, ingest=self.ingest)
        self.service = AssistService(
            uow, self.llm, cell.clock, cell.config, self.proposals, objects=self.objects
        )
        self.person = PersonPrincipal(
            SpaceRef(SPACE_ID, TENANT_ID, "us-east-2", Environment.SANDBOX),
            uuid7(),
            frozenset({"integration"}),
        )

    async def run(self, body: AssistRequest) -> Any:
        created = await self.service.request(self.person, body)
        [task] = [t for t in self.cell.uow.store.module("pipeline")["outbox"] if t.name == TaskName.ASSIST][
            -1:
        ]
        await self.service.assist(task)
        return await self.service.get(self.person, created.run_id)


MAPPING_ANSWER = {
    "document": {
        "version": "v1",
        "rules": [
            {
                "match": "type == 'invoice.paid'",
                "kind": "system_event",
                "canonical_type": "invoice.paid",
                "idempotency_key": "id",
                "occurred_at": "created_at",
                "channel": "erp",
                "handles": [{"type": "email", "value": "customer.email"}],
                "object_refs": [{"type": "invoice", "namespace": "erp", "id": "to_string(invoice.number)"}],
            }
        ],
    }
}


async def test_mapping_proposal_is_checked_and_only_the_document_leaves(cell: Cell):
    world = World(cell, MAPPING_ANSWER)
    out = await world.run(AssistRequest(task=AssistTask.WEBHOOK_MAPPING, source_id=BILLING_SOURCE))

    assert out.status is AssistStatus.DELIVERED
    assert out.diff_id == "diff-1"
    assert (out.checks["mapped"], out.checks["unmapped"]) == (8, 1)
    assert out.checks["unmapped_types"] == [{"type": "invoice.voided", "count": 1}]
    [sent] = world.proposals.sent
    assert sent.config_type == "mappings"
    assert sent.document[0]["source_id"] == str(BILLING_SOURCE)
    assert {f.name for f in fields(sent)} == {"space_id", "run_id", "config_type", "document", "reason"}
    run = await InMemoryAssistRepository(cell.uow.store).get_run(out.run_id)
    assert run is not None
    assert len(run.sample_ids) == 9
    # The call's tokens and cost are usage of the space, recorded once for the run (5.15).
    [(metric, tokens, cost, _)] = cell.uow.store.module("metering")["events"].values()
    usage = run.proposal["usage"]
    assert (metric, tokens, cost) == (
        "assistant_tokens",
        usage["input_tokens"] + usage["output_tokens"],
        usage["cost_micros"],
    )


async def test_no_personal_value_reaches_the_prompt(cell: Cell):
    world = World(cell, MAPPING_ANSWER)
    await world.run(AssistRequest(task=AssistTask.WEBHOOK_MAPPING, source_id=BILLING_SOURCE))
    [call] = world.llm.calls
    prompt = call["system"] + call["user"]
    for value in (*PII, "evt_0001", "4401"):
        assert value not in prompt
    assert call["purpose"] == "assistant"


async def test_transcripts_are_masked_for_the_schema_task(cell: Cell):
    answer = {
        "document": {"version": "s2", "predicates": [{"name": "delivery_change", "category": "orders"}]}
    }
    world = World(cell, answer)
    at = datetime(2026, 9, 20, tzinfo=UTC)
    world.ingest.events = [
        EventRecord(
            uuid7(),
            EventKind.MESSAGE,
            WHATSAPP_SOURCE,
            "whatsapp",
            Speaker.CUSTOMER,
            at,
            at,
            (),
            text=f"Oi, aqui é a {PII[3]}, meu email {PII[0]}",
        )
    ]
    out = await world.run(AssistRequest(task=AssistTask.EXTRACTION_SCHEMA, source_id=WHATSAPP_SOURCE))
    assert out.status is AssistStatus.DELIVERED
    assert out.checks == {"new_predicates": ["delivery_change"]}
    prompt = world.llm.calls[0]["user"]
    assert PII[0] not in prompt
    assert "Souza" not in prompt
    assert "<email>" in prompt


async def test_a_diff_that_echoes_a_value_is_rejected_and_stays_in_the_cell(cell: Cell):
    leaky = json.loads(json.dumps(MAPPING_ANSWER))
    leaky["document"]["rules"][0]["match"] = f"customer.email == '{PII[0]}'"
    world = World(cell, leaky)
    out = await world.run(AssistRequest(task=AssistTask.WEBHOOK_MAPPING, source_id=BILLING_SOURCE))
    assert out.status is AssistStatus.REJECTED
    assert "diff_contains_personal_data" in out.problems
    assert world.proposals.sent == []


async def test_a_looser_policy_is_rejected(cell: Cell):
    answer = {"document": {"version": "p2", "categories": [{"name": "contact", "min_verification": "V0"}]}}
    world = World(cell, answer)
    out = await world.run(
        AssistRequest(
            task=AssistTask.SOURCE_POLICY,
            source_id=WHATSAPP_SOURCE,
            description=f"Agente de suporte; contato {PII[1]}",
        )
    )
    assert out.status is AssistStatus.REJECTED
    assert "lower_verification:contact" in out.problems
    assert PII[1] not in world.llm.calls[0]["user"]


async def test_a_trigger_rule_is_appended_to_the_current_list(cell: Cell):
    answer = {
        "kind": "trigger",
        "document": {
            "rule_id": "overdue_promise",
            "condition": "promise.overdue",
            "version": "1",
            "endpoint_id": "crm",
        },
    }
    world = World(cell, answer)
    out = await world.run(
        AssistRequest(task=AssistTask.TRIGGER_OR_TRAIT_RULE, description="avise quando a promessa vencer")
    )
    assert out.status is AssistStatus.DELIVERED
    assert out.config_type == "trigger-rules"
    assert out.document[-1]["rule_id"] == "overdue_promise"
    assert out.checks["dry_run"] == "not_run"


async def test_invalid_answers_fail_without_delivery(cell: Cell):
    world = World(cell, "not json", {"document": {"rules": "nope"}})
    first = await world.run(AssistRequest(task=AssistTask.WEBHOOK_MAPPING, source_id=BILLING_SOURCE))
    second = await world.run(AssistRequest(task=AssistTask.WEBHOOK_MAPPING, source_id=BILLING_SOURCE))
    assert (first.status, first.problems) == (AssistStatus.FAILED, ["invalid_answer"])
    assert second.status is AssistStatus.REJECTED
    assert second.problems[0].startswith("invalid_document:rules")
    assert world.proposals.sent == []


async def test_the_control_plane_refusal_marks_the_run_failed(cell: Cell):
    world = World(cell, MAPPING_ANSWER, proposals=RecordingProposals(fail=InvalidInput("refused")))
    out = await world.run(AssistRequest(task=AssistTask.WEBHOOK_MAPPING, source_id=BILLING_SOURCE))
    assert (out.status, out.problems) == (AssistStatus.FAILED, ["refused_by_control_plane"])


async def test_a_redelivered_task_does_not_call_the_llm_twice(cell: Cell):
    world = World(cell, MAPPING_ANSWER)
    out = await world.run(AssistRequest(task=AssistTask.WEBHOOK_MAPPING, source_id=BILLING_SOURCE))
    await world.service.assist(Task(TaskName.ASSIST, SPACE_ID, {"run_id": str(out.run_id)}))
    assert len(world.llm.calls) == 1
    assert len(world.proposals.sent) == 1


async def test_monthly_cap_and_unknown_sources(cell: Cell):
    world = World(cell)
    for _ in range(20):
        await world.service.request(
            world.person, AssistRequest(task=AssistTask.EXTRACTION_SCHEMA, source_id=WHATSAPP_SOURCE)
        )
    with pytest.raises(RateLimited) as exc:
        await world.service.request(
            world.person, AssistRequest(task=AssistTask.EXTRACTION_SCHEMA, source_id=WHATSAPP_SOURCE)
        )
    assert timedelta(seconds=exc.value.retry_after_s) < timedelta(days=31)
    with pytest.raises(NotFound):
        await world.service.request(
            world.person,
            AssistRequest(task=AssistTask.SOURCE_POLICY, source_id=UUID(int=1), description="suporte"),
        )


def test_the_request_names_what_each_task_needs():
    with pytest.raises(ValueError, match="source_id"):
        AssistRequest(task=AssistTask.WEBHOOK_MAPPING)
    with pytest.raises(ValueError, match="description"):
        AssistRequest(task=AssistTask.TRIGGER_OR_TRAIT_RULE)


async def test_runs_are_listed_newest_first_with_a_cursor(cell: Cell):
    world = World(cell)
    created = [
        await world.service.request(
            world.person, AssistRequest(task=AssistTask.WEBHOOK_MAPPING, source_id=BILLING_SOURCE)
        )
        for _ in range(3)
    ]
    first = await world.service.runs(world.person, cursor=None, limit=2)
    rest = await world.service.runs(world.person, cursor=first.next_cursor, limit=2)
    assert [r.run_id for r in first.items + rest.items] == [r.run_id for r in reversed(created)]
    assert rest.next_cursor is None
    with pytest.raises(InvalidInput):
        await world.service.runs(world.person, cursor="not-a-run", limit=2)


async def test_a_source_without_events_is_mapped_from_a_masked_example(cell: Cell):
    # A person pastes one example event; only its masked copy is kept, sent and checked.
    world = World(cell, MAPPING_ANSWER)
    world.ingest.refs.clear()
    example = invoice_payload(1)
    out = await world.run(
        AssistRequest(task=AssistTask.WEBHOOK_MAPPING, source_id=BILLING_SOURCE, example_payload=example)
    )
    assert out.status is AssistStatus.DELIVERED
    assert (out.checks["mapped"], out.checks["unmapped"]) == (1, 0)
    [call] = world.llm.calls
    for value in (*PII, "evt_0001"):
        assert value not in call["user"]
    assert "invoice.paid" in call["user"]  # codes stay, so the mapping can name the event type
    run = await InMemoryAssistRepository(cell.uow.store).get_run(out.run_id)
    assert run is not None
    assert PII[0] not in json.dumps(run.proposal)
    assert run.sample_ids == ("example",)


def test_an_example_event_is_bounded():
    with pytest.raises(ValueError, match="64 KB"):
        AssistRequest(
            task=AssistTask.WEBHOOK_MAPPING, source_id=BILLING_SOURCE, example_payload={"x": "y" * 70_000}
        )
