"""Governance actions leave `admin` receipts with ids only, on their own producer sequence."""

from collections.abc import Sequence
from dataclasses import replace
from uuid import uuid4

from niadra.adapters.memory.fakes import FixedClock
from niadra.app.admin_audit import READ_EMIT_TIMEOUT_S, AdminAudit
from niadra.domain.common.space import PersonPrincipal, SpaceRef
from niadra.domain.receipts import PERSON_LABEL, ProducerMark, ReceiptDraft
from niadra.domain.vocabulary import Audience, Environment, ReceiptKind, ReceiptSurface, Scope
from niadra.testing.cell import SPACE_ID, TENANT_ID, principal


class RecordingSink:
    def __init__(self, *, up: bool = True) -> None:
        self.up = up
        self.drafts: list[ReceiptDraft] = []
        self.marks: list[ProducerMark] = []

    async def emit(self, drafts: Sequence[ReceiptDraft], timeout_s: float) -> bool:
        if self.up:
            self.drafts.extend(drafts)
        return self.up

    async def mark(self, mark: ProducerMark) -> None:
        self.marks.append(mark)


def person(*roles: str) -> PersonPrincipal:
    return PersonPrincipal(
        space=SpaceRef(SPACE_ID, TENANT_ID, "us-east-2", Environment.SANDBOX),
        user_id=uuid4(),
        roles=frozenset(roles),
    )


async def test_a_person_action_is_an_admin_console_receipt_with_role_and_target() -> None:
    sink, clock = RecordingSink(), FixedClock()
    who, profile = person("privacy", "analysis"), uuid4()
    await AdminAudit(sink, clock, "admin:host:1").record(
        who, action="trait.retract", profile_id=profile, target="trait:abc"
    )
    [draft] = sink.drafts
    assert (draft.kind, draft.surface, draft.audience) == (
        ReceiptKind.ADMIN,
        ReceiptSurface.CONSOLE,
        Audience.HUMAN,
    )
    assert (draft.space_id, draft.person_id, draft.source_id) == (SPACE_ID, who.user_id, None)
    assert draft.profile_id == profile
    assert draft.read_at == clock.now()
    assert draft.detail == {"action": "trait.retract", "target": "trait:abc", "roles": "analysis,privacy"}


async def test_a_person_action_carries_their_name_beside_the_chained_fields() -> None:
    sink = RecordingSink()
    who = replace(person("review"), label="ana@acme.com")
    audit = AdminAudit(sink, FixedClock(), "admin:host:1")
    await audit.record(who, action="review.verdicts", target="sample:abc")
    await audit.record(person("review"), action="review.verdicts", target="sample:abc")
    await audit.record(principal(scopes=frozenset({Scope.ADMIN})), action="forget")
    named, unnamed, key = sink.drafts
    assert named.extra == {PERSON_LABEL: "ana@acme.com"}
    assert "ana@acme.com" not in repr(named.detail)
    assert unnamed.extra == key.extra == {}


async def test_an_admin_key_is_recorded_as_its_source_and_key() -> None:
    sink = RecordingSink()
    key = principal(scopes=frozenset({Scope.ADMIN}))
    await AdminAudit(sink, FixedClock(), "admin:host:1").record(key, action="forget")
    [draft] = sink.drafts
    assert (draft.source_id, draft.person_id, draft.audience) == (key.source_id, None, key.audience)
    assert draft.detail == {"action": "forget", "key_id": key.key_id}


async def test_the_recorder_has_its_own_contiguous_sequence() -> None:
    sink = RecordingSink()
    audit = AdminAudit(sink, FixedClock(), "admin:host:1")
    for _ in range(3):
        await audit.record(person("admin"), action="secret.put")
    assert {d.producer_id for d in sink.drafts} == {"admin:host:1/admin"}
    assert [d.producer_seq for d in sink.drafts] == [1, 2, 3]


async def test_an_outage_keeps_receipts_until_the_next_heartbeat_then_marks() -> None:
    sink = RecordingSink(up=False)
    audit = AdminAudit(sink, FixedClock(), "admin:host:1")
    await audit.record(person("admin"), action="export")
    await audit.heartbeat()
    assert sink.drafts == []
    assert sink.marks == []
    sink.up = True
    await audit.heartbeat(final=True)
    assert [d.detail["action"] for d in sink.drafts] == ["export"]
    [mark] = sink.marks
    assert (mark.producer_id, mark.last_seq, mark.final) == ("admin:host:1/admin", 1, True)


async def test_control_plane_changes_and_agent_memory_reads_join_the_same_sequence() -> None:
    # The access changes and the agent memory receipts: one producer, contiguous sequence numbers.
    sink, clock = RecordingSink(), FixedClock()
    audit = AdminAudit(sink, clock, "admin:host:1")
    await audit.record_system(SPACE_ID, "key.revoked", "key:k_1")
    await audit.record_read(
        principal(),
        kind=ReceiptKind.AGENT_MEMORY,
        surface=ReceiptSurface.AGENT_BLOCK,
        item_ids=("agent_note:n1",),
    )
    system, read = sink.drafts
    assert (system.kind, system.detail) == (
        ReceiptKind.ADMIN,
        {"action": "key.revoked", "target": "key:k_1", "actor": "control_plane"},
    )
    assert (system.person_id, system.source_id) == (None, None)
    assert (read.kind, read.surface, read.item_ids) == (
        ReceiptKind.AGENT_MEMORY,
        ReceiptSurface.AGENT_BLOCK,
        ("agent_note:n1",),
    )
    assert "action" not in read.detail
    assert [d.producer_seq for d in sink.drafts] == [1, 2]


class SlowSink(RecordingSink):
    """A queue that confirms in 0.3 s: it reports a timeout to whoever waits less."""

    def __init__(self) -> None:
        super().__init__()
        self.waited: list[float] = []

    async def emit(self, drafts: Sequence[ReceiptDraft], timeout_s: float) -> bool:
        self.waited.append(timeout_s)
        if timeout_s < 0.3:
            return False
        return await super().emit(drafts, timeout_s)


async def test_a_read_waits_for_its_receipt_only_as_long_as_a_read_does() -> None:
    """An agent's block of notes waited up to half a second for its receipt and timed out at the client
    when the queue was slow (09/10/2026): the receipt now waits in memory, and the heartbeat sends it."""
    sink = SlowSink()
    audit = AdminAudit(sink, FixedClock(), "admin:host:1")
    await audit.record_read(
        principal(),
        kind=ReceiptKind.AGENT_MEMORY,
        surface=ReceiptSurface.AGENT_BLOCK,
        item_ids=("agent_note:1",),
    )
    assert sink.waited == [READ_EMIT_TIMEOUT_S]
    assert sink.drafts == []
    await audit.heartbeat()
    assert [d.surface for d in sink.drafts] == [ReceiptSurface.AGENT_BLOCK]
