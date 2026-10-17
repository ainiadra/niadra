"""The last stores an erasure reaches, on PostgreSQL (5.14.0): an agent action's result, a business object,
its computed values, a settled value and a note proposal are indexed inside the statement that writes them,
so no write runs a statement more; the rows written before the index existed are read into it by the first
erasure, and redacted in place."""

from datetime import UTC, datetime
from uuid import uuid4

from niadra.app.named_rows import NamedRowsService
from niadra.app.raw_index import sealed_tokens
from niadra.domain.agent_memory.model import NoteDraft, NoteKind, NoteOrigin, ProposalRecord, ProposalStatus
from niadra.domain.common.ids import uuid7
from niadra.domain.memory.values import SettledValue, ValueType
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.privacy.holds import Held
from niadra.domain.privacy.mentions import MentionStore
from niadra.domain.privacy.raw_index import ERASED, handle_tokens
from niadra.domain.records import AgentAction, BusinessObject, ObjectKey
from niadra.domain.state.observed import Observation
from niadra.domain.state.typed import NewValue, Typed
from niadra.domain.vocabulary import ActionStatus, HandleType, Speaker
from niadra.testing.cell import BILLING_SOURCE, WHATSAPP_SOURCE
from tests.integration.conftest import CloningUnitOfWork
from tests.integration.privacy.test_raw_index_pg import statements

EMAIL = "clara@exemplo.com"
NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
STORES = [
    MentionStore.ACTION,
    MentionStore.OBJECT,
    MentionStore.VALUE,
    MentionStore.SETTLED,
    MentionStore.PROPOSAL,
]
PLAIN = handle_tokens([(HandleType.EMAIL, EMAIL)])


def action() -> AgentAction:
    return AgentAction(
        uuid7(), uuid7(), BILLING_SOURCE, "notify", ActionStatus.CONFIRMED, NOW, result=f"avisada em {EMAIL}"
    )


def invoice(owner: object) -> BusinessObject:
    seen = Observation(EMAIL, NOW, NOW, NOW)
    return BusinessObject(
        uuid7(),
        ObjectKey("invoice", "erp", str(uuid4())),
        {"notify": EMAIL, "amount": "990.00"},
        NOW,
        BILLING_SOURCE,
        owner,  # type: ignore[arg-type]
        fields={"notify": {"erp": seen}, "amount": {"erp": Observation("990.00", NOW, NOW, NOW)}},
        typed=Typed(matched=(("notify", EMAIL),)),
    )


def computed(name: str) -> NewValue:
    return NewValue(
        name, 1, {"to": EMAIL}, None, "contact@1", b"h", ("notify",), NOW, None, (), "unknown", None, NOW
    )


def settled(owner: object) -> SettledValue:
    return SettledValue(
        uuid7(),
        owner,
        ValueType.CODE,
        EMAIL,
        NOW,
        Speaker.CUSTOMER,
        uuid7(),
        "whatsapp",
        WHATSAPP_SOURCE,
        NOW,  # type: ignore[arg-type]
    )


def proposal(status: ProposalStatus) -> ProposalRecord:
    draft = NoteDraft(kind=NoteKind.PROCEDURE, title="Contato", body=f"Escrever para {EMAIL}.")
    return ProposalRecord(uuid7(), WHATSAPP_SOURCE, NoteOrigin.AGENT, draft, status, NOW, "source:x")


def indexing(seen: list[str]) -> tuple[int, int]:
    """Statements that write the index on their own, and statements that carry it with their write."""
    alone = sum(s.lstrip().upper().startswith("INSERT INTO TEXT_MENTIONS") for s in seen)
    return alone, sum("named_mentions" in s for s in seen)


async def test_each_store_is_indexed_inside_the_statement_that_writes_it(pg_uow: CloningUnitOfWork) -> None:
    space, owner = uuid7(), uuid4()
    obj, done, drafting = invoice(owner), action(), proposal(ProposalStatus.DRAFTING)
    value = settled(owner)
    writes = []
    async with pg_uow(space) as uow:
        await uow.objects.add_action(action())  # the space's data key is made with the first seal
        with statements() as seen:
            await uow.objects.add_action(done)
        writes.append(("add_action", len(seen), indexing(seen)))
        with statements() as seen:
            await uow.memory.insert_values([value])
        writes.append(("insert_values", len(seen), indexing(seen)))
        with statements() as seen:
            await uow.agent_memory.add_proposal(drafting)
        writes.append(("add_proposal", len(seen), indexing(seen)))
        with statements() as seen:
            assert await uow.agent_memory.save_draft(drafting)
        writes.append(("save_draft", len(seen), indexing(seen)))
        stored = await uow.objects.add_object(obj)
        with statements() as seen:
            await uow.objects.set_state(
                stored.object_id,
                state=obj.state,
                fields=obj.fields,
                as_of=NOW,
                source_id=BILLING_SOURCE,
                state_version=None,
                record_ref=None,
                typed=obj.typed,
            )
        writes.append(("set_state", len(seen), indexing(seen)))
        with statements() as seen:
            await uow.typed.add_values({stored.object_id: [computed("contact")]}, None)
        writes.append(("add_values", len(seen), indexing(seen)))
        with statements() as seen:
            pass
    assert writes == [
        ("add_action", 1, (0, 1)),
        ("insert_values", 1, (0, 1)),
        ("add_proposal", 1, (0, 1)),
        ("save_draft", 1, (0, 1)),
        ("set_state", 1, (0, 1)),
        ("add_values", 1, (0, 1)),
    ]
    assert indexing(seen) == (0, 0), "the commit has nothing left to index"

    tokens = await sealed_tokens(pg_uow._keys, space, PLAIN)
    async with pg_uow(space) as uow:
        found = await uow.mentions.naming(sorted(tokens), PLAIN, stores=STORES, after=None, limit=500)
    assert {k.store for k in found} == set(STORES)


async def test_rows_written_before_the_index_are_read_into_it_and_redacted(pg_uow: CloningUnitOfWork) -> None:
    space, owner = uuid7(), uuid4()
    obj, done, pending, value = invoice(owner), action(), proposal(ProposalStatus.PENDING), settled(owner)
    async with pg_uow(space) as uow:
        await uow.objects.add_action(done)
        await uow.memory.insert_values([value])
        await uow.agent_memory.add_proposal(pending)
        stored = await uow.objects.add_object(obj)
        await uow.objects.set_state(
            stored.object_id,
            state=obj.state,
            fields=obj.fields,
            as_of=NOW,
            source_id=BILLING_SOURCE,
            state_version=None,
            record_ref=None,
            typed=obj.typed,
        )
        await uow.typed.add_values({stored.object_id: [computed("contact")]}, None)
    tokens = await sealed_tokens(pg_uow._keys, space, PLAIN)
    async with pg_uow(space) as uow:
        # As if written before the index existed.
        await uow.mentions.drop(
            await uow.mentions.naming(sorted(tokens), PLAIN, stores=STORES, after=None, limit=500)
        )
        assert await uow.objects.matching([("invoice", "notify", EMAIL)], limit=5) == {
            ("invoice", "notify", EMAIL): [obj.key]
        }

    scope = ErasureScope(uuid4(), TargetKind.HANDLE, (uuid4(),), uuid4(), None, NOW)
    done_rows = await NamedRowsService(pg_uow, pg_uow._keys).redact_named(space, scope, PLAIN, tokens, Held())

    assert (done_rows.rows, done_rows.in_source) == (5, 1)
    async with pg_uow(space) as uow:
        [kept_action] = await uow.objects.actions_by_ids([done.action_id])
        [kept_value] = await uow.memory.values_by_ids([value.value_id])
        kept_proposal = await uow.agent_memory.get_proposal(pending.proposal_id)
        kept_object = await uow.objects.get_object(obj.key)
        kept_computed = await uow.typed.values_named([(stored.object_id, "contact", 1)])
        relation = await uow.objects.matching([("invoice", "notify", EMAIL)], limit=5)
        by_key = await uow.memory.values_by_keys([owner], [value.key])
        left = await uow.mentions.naming(sorted(tokens), PLAIN, stores=STORES, after=None, limit=500)
    assert kept_action.result == f"avisada em {ERASED}"
    assert kept_value.raw == ERASED
    assert by_key == []
    assert kept_proposal is not None
    assert kept_proposal.draft.body == f"Escrever para {ERASED}."
    assert kept_object is not None
    assert kept_object.state == {"notify": ERASED, "amount": "990.00"}
    assert kept_object.fields["notify"]["erp"].value == ERASED
    assert kept_object.typed.erased == {t.hex() for t in tokens}
    assert relation == {("invoice", "notify", EMAIL): []}, "a reverse relation no longer finds it by her"
    assert kept_computed == {(stored.object_id, "contact", 1): {"to": ERASED}}
    assert left == []
