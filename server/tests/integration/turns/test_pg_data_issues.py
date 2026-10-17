"""Data issues on PostgreSQL (24.3.5, 24.6.6): opening one is one statement of the finder's transaction, the
same problem found again is counted in the open issue with its references merged up to 50, an acknowledged
issue gives way to a new one, and only a new issue gets the task that announces it."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import text

from niadra.adapters.memory.fakes import InMemoryCache, StaticConfig
from niadra.app.data_issues import DataIssuesService
from niadra.config.models import WebhookEndpoint
from niadra.domain.common.ids import uuid7
from niadra.domain.tasks import Task, TaskName
from niadra.domain.turns.issues import IssueQuery, NewDataIssue, new_issue
from niadra.testing.cell import WHATSAPP_SOURCE, sample_config
from tests.integration.conftest import CellStack, CloningUnitOfWork
from tests.integration.turns.test_pg_turns import counting
from tests.unit.audit.fakes import RecordingAdminAudit

NOW = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)


def _refs(start: int, count: int) -> list[str]:
    return [f"item_variant:store:{i}" for i in range(start, start + count)]


async def test_the_same_problem_is_one_open_issue_counted_with_its_references_up_to_50(
    pg_uow: CloningUnitOfWork,
) -> None:
    space = uuid4()
    await pg_uow.ensure(space)
    first = new_issue(
        uuid7(),
        "null_field",
        NOW,
        object_type="item_variant",
        field="price",
        source="catalog",
        refs=_refs(0, 30),
    )
    again = new_issue(
        uuid7(),
        "null_field",
        NOW + timedelta(minutes=5),
        object_type="item_variant",
        field="price",
        source="catalog",
        refs=_refs(20, 40),
    )
    other = new_issue(
        uuid7(), "null_field", NOW, object_type="item_variant", field="size", source="catalog", refs=[]
    )
    async with pg_uow(space) as uow:
        with counting() as statements:
            opened = await uow.turns.open_issue(first, tell=True)
        counted = await uow.turns.open_issue(again, tell=True)
        apart = await uow.turns.open_issue(other, tell=False)
        outbox = (
            await uow.session.execute(text("SELECT task, payload FROM outbox ORDER BY created_at"))
        ).all()
        [row] = [r for r in await uow.turns.issues(IssueQuery()) if r.issue_id == opened]

    assert [s.split()[:2] for s in statements] == [["WITH", "up"]], (
        "one statement in the finder's transaction"
    )
    assert (opened, counted) == (first.issue_id, first.issue_id)
    assert apart == other.issue_id, "another field is another issue"
    assert (row.occurrences, row.opened_at, row.last_seen_at) == (2, NOW, NOW + timedelta(minutes=5))
    assert list(row.refs) == _refs(0, 50), "the ones kept first, then the new ones, up to 50"
    assert [(task, payload) for task, payload in outbox] == [
        ("notify_data_issue", {"issue_id": str(first.issue_id)})
    ], "only a new issue is announced, and only when someone listens"


async def test_an_acknowledged_issue_stays_closed_and_the_next_occurrence_opens_another(
    pg_uow: CloningUnitOfWork,
) -> None:
    space = uuid4()
    await pg_uow.ensure(space)

    def drift(at: datetime) -> NewDataIssue:
        return new_issue(uuid7(), "drift", at, object_type="lawsuit", field=None, source="erp", refs=[])

    async with pg_uow(space) as uow:
        first = await uow.turns.open_issue(drift(NOW), tell=False)
        closed = await uow.turns.acknowledge_issue(first, "user:ops", NOW + timedelta(hours=1))
        twice = await uow.turns.acknowledge_issue(first, "user:other", NOW + timedelta(hours=2))
        missing = await uow.turns.acknowledge_issue(uuid4(), "user:ops", NOW)
        second = await uow.turns.open_issue(drift(NOW + timedelta(days=1)), tell=False)
        open_now = await uow.turns.issues(IssueQuery(open=True))
        done = await uow.turns.issues(IssueQuery(open=False))
        page = await uow.turns.issues(IssueQuery(open=None, kind="drift", limit=1))
        rest = await uow.turns.issues(
            IssueQuery(open=None, before=(page[0].opened_at, page[0].issue_id), limit=5)
        )

    assert closed is not None
    assert missing is None
    assert (closed.acknowledged_at, closed.acknowledged_by) == (NOW + timedelta(hours=1), "user:ops")
    assert twice == closed, "acknowledging again changes nothing"
    assert second != first
    assert [r.issue_id for r in open_now] == [second]
    assert [r.issue_id for r in done] == [first]
    assert [r.issue_id for r in page + rest] == [second, first], "newest opened first, keyset paged"


async def test_the_port_announces_a_new_issue_to_a_subscriber_with_names_and_ids_only(
    pg_uow: CloningUnitOfWork, pg_stack: CellStack
) -> None:
    space = uuid4()
    await pg_uow.ensure(space)
    endpoint = WebhookEndpoint(
        endpoint_id="data",
        url="https://data.example/hooks",
        source_id=WHATSAPP_SOURCE,
        event_types=["data_issue.opened"],
        secret_refs=["spaces/x/webhook/data"],
    )
    config = StaticConfig(sample_config(space).model_copy(update={"webhook_endpoints": [endpoint]}))
    issues = DataIssuesService(pg_uow, pg_stack.clock, config, RecordingAdminAudit(), InMemoryCache())  # type: ignore[arg-type]
    async with pg_uow(space) as uow:
        issue_id = await issues.open(
            uow,
            space,
            "type_undeclared",
            object_type="gift_card",
            source="lookup_card",
            refs=["gift_card:store:7"],
        )
        await issues.open(uow, space, "type_undeclared", object_type="gift_card", source="lookup_card")
        [(task_name, payload)] = (await uow.session.execute(text("SELECT task, payload FROM outbox"))).all()
        await uow.session.execute(text("DELETE FROM outbox"))
    assert task_name == TaskName.NOTIFY_DATA_ISSUE.value
    await issues.notify_opened(Task(TaskName.NOTIFY_DATA_ISSUE, space, payload))
    async with pg_uow(space) as uow:
        [delivery] = await uow.notify.list_deliveries()
    data = delivery.body["data"]
    assert delivery.event_type == "data_issue.opened"
    assert data["issue_id"] == str(issue_id)
    assert (data["kind"], data["object_type"], data["source"], data["refs"]) == (
        "type_undeclared",
        "gift_card",
        "lookup_card",
        ["gift_card:store:7"],
    )
