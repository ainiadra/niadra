"""Reads of a contact of a large account on PostgreSQL (the final zero-touch round, 09/10): an account with
30 cases of 4 children each and 500 contacts. A contact's read must stay within the read budget whatever
the size of the account it speaks for: the account's memory is loaded once per account generation, not
once per contact. Its history searches too, by the customer and `about` the account, with 50 people on
every case."""

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from niadra.adapters.memory.fakes import (
    DeterministicModels,
    DictSecrets,
    FixedClock,
    InMemoryCache,
    InMemoryHotLayer,
    InMemoryObjectStore,
    StaticConfig,
)
from niadra.adapters.memory.signals import InMemorySignalOverlay
from niadra.adapters.outbound.keys.local import LocalKeyVault
from niadra.adapters.outbound.keys.postgres import PgWrappedKeyStore
from niadra.adapters.outbound.postgres.engines import make_engine
from niadra.adapters.outbound.postgres.registry import EngineRegistry
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.app import history_rows
from niadra.app.compile import CompileService
from niadra.app.history_rows import HistoryRows
from niadra.app.identity import IdentityService
from niadra.app.ingest import IngestService
from niadra.app.serve import ServeService
from niadra.app.sessions import SessionService
from niadra.app.signals import ConstraintsReadBlock, SignalsService
from niadra.app.state import StateBlock, StateService
from niadra.config.models import FeatureSettings
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest, ContextResponse, Include, SearchRequest, SearchResponse
from niadra.contracts.ingest import validate_items
from niadra.domain.common.ids import uuid7
from niadra.domain.records import BusinessObject, Fact, ObjectKey, OpenItem
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import (
    DeliveryPath,
    FactStatus,
    Feature,
    HandleType,
    MemoryAudience,
    OpenItemKind,
    OpenItemStatus,
    Sensitivity,
    Speaker,
    SubjectKind,
    Verification,
)
from niadra.testing.cell import WHATSAPP_SOURCE, principal, sample_config
from tests.integration.conftest import PgServer, clone_space
from tests.integration.serve.test_read_include import ConfirmingReceipts, proven_elsewhere

CASES, CHILDREN, CONTACTS = 30, 4, 500
ACCOUNT = Handle(type=HandleType.SYSTEM_ID, value="ACC-1", scope="crm", subject_kind=SubjectKind.ACCOUNT)
LAWYER = Handle(type=HandleType.SYSTEM_ID, value="U-17", scope="crm", subject_kind=SubjectKind.STAFF)
FIRST_READ_STATEMENTS = 20
"""A contact's first read, its pack compiled on the read path (T2), once the account's block was read for its
generation: the subject, the organization, the link and its generation; the conversation's pin, the stored
pack, the contact's own memory (ten statements) and the manifest; two space pins. Whatever the size of the
account: it read 52, 22 of them the account's memory again."""
WARM_READ_STATEMENTS = 3
"""The next reads of the conversation, from T0: the space pin and the subject's handle and profile, read from
the database while its handle settles (`SETTLING`); the link comes from the read state. They compiled the pack
again, 46 statements, until the worker's compilation landed."""


def contact(n: int) -> Handle:
    return Handle(type=HandleType.PHONE_E164, value=f"+5511987{n:06d}")


@dataclass
class World:
    serve: ServeService
    compile: CompileService
    identity: IdentityService
    uow: PgUnitOfWorkFactory
    clock: FixedClock
    space_id: UUID
    statements: list[tuple[object, str]]
    config: StaticConfig
    account_profile: UUID
    ingest: IngestService

    async def prove(self, who: Handle, conversation: str) -> None:
        proof = {
            "type": "verify",
            "idempotency_key": f"{conversation}-proof",
            "method": "otp_sms",
            "level": "V1",
            "conversation_id": conversation,
            "handle": {"type": who.type.value, "value": who.value},
            "occurred_at": self.clock.now().isoformat(),
        }
        valid, errors = validate_items([proof])
        await self.ingest.ingest_items(
            principal(WHATSAPP_SOURCE, self.config.space(self.space_id)), valid, errors
        )

    async def read(
        self, who: Handle, conversation: str, include: Sequence[Include] = ("state", "constraints")
    ) -> tuple[ContextResponse, int, float]:
        before = len(self.statements)
        request = ContextRequest(
            subject=who,
            about=ACCOUNT,
            conversation_id=conversation,
            verification=Verification.V1,
            include=list(include),
        )
        started = time.perf_counter()
        response = await self.serve.context(
            principal(WHATSAPP_SOURCE, self.config.space(self.space_id)), request
        )
        took = time.perf_counter() - started
        mine = asyncio.current_task()
        ran = proven_elsewhere([s for task, s in self.statements[before:] if task is mine])
        await self.serve.drain()
        return response, ran, took


@pytest.fixture
async def world(server: PgServer) -> AsyncIterator[World]:
    space_id = uuid4()
    await asyncio.to_thread(clone_space, space_id)
    config = StaticConfig(
        sample_config(space_id).model_copy(update={"features": FeatureSettings(enabled=frozenset(Feature))})
    )
    cell_engine = make_engine(server.servers.cell_url("system"), pool_size=1, max_overflow=2, pool_timeout=5)
    registry = EngineRegistry(server.servers, cell_engine)
    await registry.refresh()
    keys = LocalKeyVault(b"k" * 32, PgWrappedKeyStore(cell_engine, "account-scale"))
    uow = PgUnitOfWorkFactory(keys, registry, cell_engine)
    clock, cache, hot = FixedClock(), InMemoryCache(), InMemoryHotLayer()
    identity = IdentityService(uow, clock, config)
    ingest = IngestService(
        uow, identity, SessionService(uow, clock, config), keys, hot, clock, config, InMemoryObjectStore(),
        DictSecrets(),
    )  # fmt: skip
    compile_ = CompileService(uow, keys, cache, clock, config, hot=hot)
    serve = ServeService(
        uow, cache, hot, keys, ConfirmingReceipts(), compile_, DeterministicModels(), clock, config,
        "account-scale", databases=registry,
    )  # fmt: skip
    serve.add_block(StateBlock(StateService(uow, cache, keys, clock, config), serve.guarded))
    signals = SignalsService(uow, keys, cache, InMemorySignalOverlay(), InMemoryObjectStore(), clock, config)
    serve.add_block(ConstraintsReadBlock(signals))
    compile_.instructions = signals
    for n in range(CONTACTS):
        await identity.create_link(space_id, contact(n), ACCOUNT, role="contact")
    await identity.create_link(space_id, LAWYER, ACCOUNT, role="lawyer")
    async with uow(space_id) as tx:
        org = await tx.identity.find_handle(HandleType.SYSTEM_ID, "crm", "ACC-1")
        lawyer = await tx.identity.find_handle(HandleType.SYSTEM_ID, "crm", "U-17")
        assert org is not None
        assert lawyer is not None
        account = await tx.identity.profile_of(org.handle_id)
        assert account is not None
        for c in range(CASES):
            key = ObjectKey("lawsuit", "court", f"case-{c}")
            case = await tx.objects.add_object(
                BusinessObject(uuid7(), key, {"case_number": f"{c:07d}-11.2026.8.26.0100", "state": "open"},
                               clock.now(), WHATSAPP_SOURCE, org.handle_id)
            )  # fmt: skip
            await tx.objects.add_participants(case.object_id, [(lawyer.handle_id, "lawyer")])
            for k in range(CHILDREN):
                child = ObjectKey("deadline", "court", f"case-{c}-d{k}")
                await tx.objects.add_object(
                    BusinessObject(uuid7(), child, {"case_id": f"case-{c}", "due_on": "2026-10-30"},
                                   clock.now(), WHATSAPP_SOURCE, org.handle_id)
                )  # fmt: skip
            for i in range(2):
                await tx.memory.insert_open_item(
                    OpenItem(
                        item_id=uuid7(),
                        origin_handle_id=lawyer.handle_id,
                        description=f"Enviar a cópia {i} do caso {c}",
                        owner="company",
                        status=OpenItemStatus.OPEN,
                        created_at=clock.now(),
                        origin={},
                        due_at=clock.now() + timedelta(days=3),
                        object=key,
                        kind=OpenItemKind.PROMISE,
                        audience=MemoryAudience.SHARED,
                    )
                )
                await tx.memory.insert_fact(
                    Fact(
                        fact_id=uuid7(),
                        origin_handle_id=lawyer.handle_id,
                        predicate="observation",
                        value=f"Audiência {i} do caso {c} marcada",
                        category="object",
                        sensitivity=Sensitivity.NORMAL,
                        speaker=Speaker.CUSTOMER,
                        confidence=0.9,
                        valid_at=clock.now(),
                        created_at=clock.now(),
                        status=FactStatus.ACTIVE,
                        last_confirmed_at=clock.now(),
                        object_ref=key,
                        audience=MemoryAudience.SHARED,
                    ),
                    f"case-{c}-{i}",
                )
    await compile_.compile(Task(TaskName.COMPILE, space_id, profile_id=account.profile_id))
    statements: list[tuple[object, str]] = []

    def count(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append((asyncio.current_task(), statement))

    event.listen(Engine, "before_cursor_execute", count)
    try:
        yield World(
            serve, compile_, identity, uow, clock, space_id, statements, config, account.profile_id, ingest
        )
    finally:
        event.remove(Engine, "before_cursor_execute", count)
        await registry.close()
        await cell_engine.dispose()


async def contact_profile(world: World, who: Handle) -> UUID:
    async with world.uow(world.space_id) as tx:
        found = await tx.identity.find_handle(HandleType.PHONE_E164, "", who.value)
        assert found is not None
        profile = await tx.identity.profile_of(found.handle_id)
    assert profile is not None
    return profile.profile_id


async def first_read(world: World, n: int) -> tuple[ContextResponse, int]:
    """A contact linked just now, proved and read at once, before the worker compiles its packs (P6)."""
    newcomer = contact(n)
    await world.identity.create_link(world.space_id, newcomer, ACCOUNT, role="contact")
    await world.prove(newcomer, f"zap-{n}")
    response, ran, _ = await world.read(newcomer, f"zap-{n}")
    return response, ran


async def test_a_contact_just_linked_to_a_large_account_reads_within_the_budget(world: World) -> None:
    # Another contact's first read reads the account's block for its generation.
    warm, _ = await first_read(world, CONTACTS)
    assert warm.path is DeliveryPath.T2
    first, ran = await first_read(world, CONTACTS + 1)
    assert first.path is DeliveryPath.T2
    assert ran <= FIRST_READ_STATEMENTS, ran
    newcomer = contact(CONTACTS + 1)
    for _ in range(3):
        again, warm_ran, _ = await world.read(newcomer, f"zap-{CONTACTS + 1}")
        assert again.path is DeliveryPath.T0
        assert warm_ran <= WARM_READ_STATEMENTS, warm_ran
    # The worker's compilation lands: the reads stay as warm.
    profile_id = await contact_profile(world, newcomer)
    await world.compile.compile(Task(TaskName.COMPILE, world.space_id, profile_id=profile_id))
    again, warm_ran, _ = await world.read(newcomer, f"zap-{CONTACTS + 1}")
    assert again.path is DeliveryPath.T0
    assert warm_ran <= WARM_READ_STATEMENTS, warm_ran


async def test_the_account_block_is_read_again_when_the_account_moves(world: World) -> None:
    await first_read(world, CONTACTS)
    _, ran = await first_read(world, CONTACTS + 1)
    assert ran <= FIRST_READ_STATEMENTS, ran
    # A new generation of the account (a contact said something, an object of it changed): the next first
    # read reads its memory again, once.
    await world.compile.compile(Task(TaskName.COMPILE, world.space_id, profile_id=world.account_profile))
    _, moved = await first_read(world, CONTACTS + 2)
    assert moved > FIRST_READ_STATEMENTS, moved
    _, ran = await first_read(world, CONTACTS + 3)
    assert ran <= FIRST_READ_STATEMENTS, ran
    # So does a privacy change of the account: an erased contact's words leave the block at once.
    async with world.uow(world.space_id) as tx:
        await tx.identity.bump_privacy_version(world.account_profile)
    world.clock.advance(seconds=3)
    await world.serve.refresh_privacy()
    _, erased = await first_read(world, CONTACTS + 4)
    assert erased > FIRST_READ_STATEMENTS, erased


PARTICIPANTS = 50
QUERY = "caso case open"
CUSTOMER_SEARCH_STATEMENTS = 17
"""A customer's first search, its rows read in one transaction: the space pin, the subject's handle and
profile, the conversation's proof, the customer's memory (ten statements), its system events and the vectors
of its rows (two). The next searches replay the rows with no statement while its generation holds. It read
19: the vectors of facts, conversations and items took a statement each."""
ABOUT_SEARCH_STATEMENTS = 21
"""A contact's first search about its account once another contact's search read the account's rows: the
customer's 17, and the organization, the link and its generation. The account's rows (22 statements) are read
once per generation of the account for all its contacts, and the next searches replay both sides with no
statement. Every search about the account read them again, 41 statements a call (09/10/2026)."""


@pytest.fixture
async def crowded(world: World) -> World:
    """The world with 50 people on every case: the lawyer and 49 contacts, each contact on about three."""
    async with world.uow(world.space_id) as tx:
        people = []
        for n in range(CONTACTS):
            found = await tx.identity.find_handle(HandleType.PHONE_E164, "", contact(n).value)
            assert found is not None
            people.append(found.handle_id)
        for c in range(CASES):
            case = await tx.objects.get_object(ObjectKey("lawsuit", "court", f"case-{c}"))
            assert case is not None
            party = [people[(c * 17 + j) % CONTACTS] for j in range(PARTICIPANTS - 1)]
            await tx.objects.add_participants(case.object_id, [(h, "party") for h in party])
    # Past the contacts' first seconds, compiled as the worker compiles them, each in a proved conversation.
    world.clock.advance(minutes=1)
    await world.compile.compile(Task(TaskName.COMPILE, world.space_id, profile_id=world.account_profile))
    for n in (0, 1):
        profile_id = await contact_profile(world, contact(n))
        await world.compile.compile(Task(TaskName.COMPILE, world.space_id, profile_id=profile_id))
        await world.prove(contact(n), f"zap-{n}")
    await world.serve.refresh_privacy()
    return world


async def search(world: World, n: int, *, about: bool) -> tuple[SearchResponse, list[str]]:
    """Contact `n`'s search a second after the last one, and the statements it ran."""
    world.clock.advance(seconds=1)
    before = len(world.statements)
    request = SearchRequest(
        subject=contact(n),
        about=ACCOUNT if about else None,
        query=QUERY,
        conversation_id=f"zap-{n}",
        verification=Verification.V1,
    )
    response = await world.serve.search(
        principal(WHATSAPP_SOURCE, world.config.space(world.space_id)), request
    )
    mine = asyncio.current_task()
    ran = [" ".join(s.split())[:120] for task, s in world.statements[before:] if task is mine]
    await world.serve.drain()
    return response, ran


def from_database(world: World) -> None:
    """The rows held in memory forgotten: the next search reads them from the database."""
    world.serve._history._rows = HistoryRows()


async def test_a_customers_search_reads_its_rows_once_then_replays_them(crowded: World) -> None:
    first, ran = await search(crowded, 0, about=False)
    assert first.items
    assert len(ran) <= CUSTOMER_SEARCH_STATEMENTS, ran
    for _ in range(3):
        again, ran = await search(crowded, 0, about=False)
        assert ran == []
        assert again == first


async def test_an_about_search_reads_the_accounts_rows_once_for_all_its_contacts(crowded: World) -> None:
    first, _ = await search(crowded, 0, about=True)
    assert len(first.items) > len((await search(crowded, 0, about=False))[0].items)
    for _ in range(3):
        again, ran = await search(crowded, 0, about=True)
        assert ran == []
        assert again == first
    # Another contact's first search about the account reads its own rows only.
    other, ran = await search(crowded, 1, about=True)
    assert len(ran) <= ABOUT_SEARCH_STATEMENTS, ran
    again, ran = await search(crowded, 1, about=True)
    assert ran == []
    assert again == other
    # Both answer as a read of the database does.
    from_database(crowded)
    assert (await search(crowded, 1, about=True))[0] == other
    from_database(crowded)
    assert (await search(crowded, 0, about=True))[0] == first


async def test_the_accounts_rows_are_read_again_when_the_account_moves(crowded: World) -> None:
    await search(crowded, 0, about=True)
    _, ran = await search(crowded, 0, about=True)
    assert ran == []
    # A new generation of the account (a contact said something, an object of it changed).
    await crowded.compile.compile(
        Task(TaskName.COMPILE, crowded.space_id, profile_id=crowded.account_profile)
    )
    _, moved = await search(crowded, 0, about=True)
    assert moved
    _, ran = await search(crowded, 0, about=True)
    assert ran == []
    # A privacy change of the account: an erased contact's words leave its rows at once.
    async with crowded.uow(crowded.space_id) as tx:
        await tx.identity.bump_privacy_version(crowded.account_profile)
    crowded.clock.advance(seconds=3)
    await crowded.serve.refresh_privacy()
    _, erased = await search(crowded, 0, about=True)
    assert erased


async def test_old_rows_of_both_sides_are_read_again_in_the_background(
    crowded: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, _ = await search(crowded, 0, about=True)
    monkeypatch.setattr(history_rows, "RENEW_AFTER_S", -1.0)
    before = len(crowded.statements)
    again, ran = await search(crowded, 0, about=True)
    assert ran == []
    assert again == first
    # The answer replayed both sides, and each was read again after it: the person's and the account's.
    renewed = [s for _, s in crowded.statements[before:]]
    assert sum(s.startswith("SELECT events.") for s in renewed) == 2
    monkeypatch.undo()
    _, ran = await search(crowded, 0, about=True)
    assert ran == []
