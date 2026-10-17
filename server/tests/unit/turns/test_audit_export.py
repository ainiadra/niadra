"""No second audit chain (24.5.4): the receipts' links and the day's root go to the company's own chain as
`receipt.recorded` and `audit.root`, and the root carries the kept tier's index, whose rows the company can
read with their digests and fold into the same root."""

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

from niadra.adapters.memory.fakes import FixedClock, InMemoryObjectStore, StaticConfig
from niadra.app.audit import AuditService
from niadra.config.models import FeatureSettings, WebhookEndpoint
from niadra.domain.receipts import ProducerMark
from niadra.domain.tasks import Task, TaskName
from niadra.domain.turns.digest import canonical_json
from niadra.domain.turns.index import entry, entry_digest, index_root
from niadra.domain.turns.records import KeptTurn
from niadra.domain.turns.storage import KeptBy
from niadra.domain.vocabulary import Feature
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, sample_config
from tests.unit.audit.conftest import draft
from tests.unit.turns.conftest import World

DAY = date(2026, 9, 22)
CHAIN = WebhookEndpoint(
    endpoint_id="chain",
    url="https://audit.example/hooks",
    source_id=WHATSAPP_SOURCE,
    event_types=["receipt.recorded", "audit.root"],
    secret_refs=["spaces/x/webhook/chain"],
)


def _kept(turn_id: str, at: datetime) -> KeptTurn:
    return KeptTurn(
        turn_id=turn_id,
        conversation=b"\xaa" * 32,
        source_id=WHATSAPP_SOURCE,
        agent="closing",
        kind="message",
        started_at=at - timedelta(minutes=1),
        fidelity="gold",
        completeness="complete",
        content_mode="pointer",
        replayable=True,
        build_hash=b"\x01" * 32,
        flags=("handoff",),
        kept_by=KeptBy.FLAG,
        kept_at=at,
        kept_until=at + timedelta(days=30),
    )


def test_a_rows_digest_is_its_entry_in_canonical_form_and_the_root_folds_them_by_turn_id() -> None:
    at = datetime(2026, 9, 22, 12, tzinfo=UTC)
    a, b, c = _kept("t-a", at), _kept("t-b", at), _kept("t-c", at)
    assert entry_digest(a) == hashlib.sha256(canonical_json(entry(a))).digest()
    assert set(entry(a)) == {
        "turn_id",
        "source_id",
        "agent",
        "kind",
        "started_at",
        "content_mode",
        "build_hash",
        "kept_at",
    }
    leaf = [hashlib.sha256(b"\x00" + entry_digest(r)).digest() for r in (a, b, c)]
    pair = hashlib.sha256(b"\x01" + leaf[0] + leaf[1]).digest()
    assert index_root([c, a, b]) == hashlib.sha256(b"\x01" + pair + leaf[2]).digest(), (
        "an odd node carried up"
    )
    assert index_root([]) == hashlib.sha256(b"").digest()


async def test_the_index_of_a_day_pages_with_digests_that_make_the_anchored_root(world: World) -> None:
    at = datetime.combine(DAY, datetime.min.time(), UTC)
    rows = [_kept(f"t-{i}", at + timedelta(hours=i)) for i in range(3)] + [
        _kept("t-next", at + timedelta(days=1))
    ]
    async with world.cell.uow(SPACE_ID) as uow:
        await uow.turns.keep(rows)
    first = await world.service.index(world.caller, DAY, None, 2)
    rest = await world.service.index(world.caller, DAY, first.next_cursor, 2)
    listed = first.items + rest.items
    assert [e.turn_id for e in listed] == ["t-0", "t-1", "t-2"]
    assert rest.next_cursor is None
    assert listed[0].digest == f"sha256:{entry_digest(rows[0]).hex()}"
    assert await world.service.day_index(SPACE_ID, DAY) == (3, index_root(rows[:3]))
    world.cell.config.put(sample_config())
    assert await world.service.day_index(SPACE_ID, DAY) is None, "a space that records no turn anchors none"


class Index:
    async def day_index(self, space_id: Any, day: date) -> tuple[int, bytes] | None:
        return 3, b"\x07" * 32


async def test_the_chain_links_and_the_days_root_go_to_the_companys_chain(world: World) -> None:
    config = StaticConfig(
        sample_config().model_copy(
            update={
                "webhook_endpoints": [CHAIN],
                "features": FeatureSettings(enabled=frozenset({Feature.TURNS})),
            }
        )
    )
    objects = InMemoryObjectStore()
    audit = AuditService(world.cell.uow, None, objects, FixedClock(), config=config)  # type: ignore[arg-type]
    audit.turn_index = Index()
    await audit.write([draft(producer="r", seq=i, profile_id=uuid4()) for i in range(1, 4)])
    await audit.write([], [ProducerMark("r", last_seq=3, at=datetime(2026, 9, 23, 0, 0, 1, tzinfo=UTC))])
    await audit.audit_anchor(Task(TaskName.AUDIT_ANCHOR, SPACE_ID, {"day": DAY.isoformat()}))

    async with world.cell.uow(SPACE_ID) as uow:
        deliveries = {d.event_type: d.body["data"] for d in await uow.notify.list_deliveries()}
        root = await uow.audit.get_root(SPACE_ID, DAY)
    links = deliveries["receipt.recorded"]["receipts"]
    assert [link["seq"] for link in links] == [1, 2, 3]
    assert set(links[0]) == {
        "receipt_id",
        "kind",
        "surface",
        "read_at",
        "source_id",
        "slice",
        "day",
        "seq",
        "prev_hash",
        "hash",
    }, "ids and hashes, never who or what was read"
    event = deliveries["audit.root"]
    assert root is not None
    assert (event["day"], event["root"], event["locked"]) == (DAY.isoformat(), root.root.hex(), False)
    assert event["turns"] == {"count": 3, "root": "07" * 32}
    document = json.loads(objects.objects[f"audit/{SPACE_ID}/{DAY.isoformat()}.json"])
    assert document["turns"] == event["turns"], "Niadra's own anchor holds the same index root"
    assert (await audit.verify_chain(SPACE_ID, DAY)).valid
