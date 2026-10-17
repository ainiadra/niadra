"""What waits to be taken in, on PostgreSQL (5.14.0): a pending interaction, a commitment's terms and a
handoff's package and outcome are indexed inside the statement that writes them, so no write runs a statement
more, and an erasure reads and rewrites them in place."""

from datetime import UTC, datetime
from uuid import uuid4

from niadra.app.raw_index import sealed_tokens
from niadra.domain.common.ids import uuid7
from niadra.domain.coordinate.records import CommitmentRecord, SpaceBatch
from niadra.domain.privacy.mentions import MentionStore
from niadra.domain.privacy.raw_index import handle_tokens
from niadra.domain.signals.records import PendingRow
from niadra.domain.vocabulary import HandleType
from niadra.ports.repos.coordinate import HandoffRow, SealedOutcome
from niadra.testing.cell import WHATSAPP_SOURCE
from tests.integration.conftest import CloningUnitOfWork
from tests.integration.privacy.test_last_stores_pg import indexing
from tests.integration.privacy.test_raw_index_pg import statements

EMAIL = "clara@exemplo.com"
NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
PLAIN = handle_tokens([(HandleType.EMAIL, EMAIL)])
STORES = [MentionStore.INTAKE, MentionStore.COMMITMENT, MentionStore.HANDOFF]


async def test_each_waiting_store_is_indexed_inside_the_statement_that_writes_it(
    pg_uow: CloningUnitOfWork,
) -> None:
    space = uuid7()
    tokens = await sealed_tokens(pg_uow._keys, space, PLAIN)
    commitment = CommitmentRecord(
        space, uuid7(), "c1", None, "trial", "first_holds", b"terms", 1, "active", WHATSAPP_SOURCE, "a", 1, 2,
        named=tuple(sorted(tokens)),
    )  # fmt: skip
    handoff = HandoffRow(
        uuid7(), None, None, WHATSAPP_SOURCE, "a", "queue:x", b"p", b"h", 1, "created", None, NOW, None,
        named=tokens,
    )  # fmt: skip
    writes = []
    async with pg_uow(space) as uow:
        await uow.mentions.ready("warm")
        with statements() as seen:
            await uow.signals.add_pending([PendingRow("t:1:0", uuid4(), NOW, b"x", 1, None, named=tokens)])
        writes.append(("add_pending", len(seen), indexing(seen)))
        with statements() as seen:
            await uow.coordinate.write(SpaceBatch(space, (), (), (), (), (), commitments=(commitment,)))
        writes.append(("write", len(seen), indexing(seen)))
        with statements() as seen:
            await uow.coordinate.add_handoff(handoff)
        writes.append(("add_handoff", len(seen), indexing(seen)))
        with statements() as seen:
            moved = await uow.coordinate.move_handoff(
                handoff.handoff_id,
                status="closed",
                from_statuses=["created"],
                at=NOW,
                outcome="sold",
                outcome_ct=SealedOutcome(b"o", 3),
                named=tokens,
            )
        writes.append(("move_handoff", len(seen), indexing(seen)))
    assert moved is not None
    assert writes == [
        ("add_pending", 1, (0, 1)),
        ("write", 1, (0, 1)),
        ("add_handoff", 1, (0, 1)),
        ("move_handoff", 1, (0, 1)),
    ]

    async with pg_uow(space) as uow:
        found = await uow.mentions.naming(sorted(tokens), PLAIN, stores=STORES, after=None, limit=500)
        assert {k.store for k in found} == set(STORES)
        [terms] = await uow.coordinate.terms_of([commitment.commitment_id])
        assert (await uow.coordinate.terms_page(None, 10)) == [terms]
        await uow.coordinate.rewrite_terms(terms.__class__(terms.commitment_id, None, b"redacted", 2))
        assert await uow.coordinate.outcome_of(handoff.handoff_id) == SealedOutcome(b"o", 3)
        [stored] = await uow.coordinate.handoffs_page(None, 10)
        await uow.coordinate.rewrite_handoff(stored, SealedOutcome(b"o2", 4))
        [pending] = await uow.signals.pending_by_keys(["t:1:0"])
        assert await uow.signals.intake_page(None, 10) == [pending]
        await uow.signals.rewrite_pending([PendingRow("t:1:0", pending.handle_id, NOW, b"y", 2)])
    async with pg_uow(space) as uow:
        [terms] = await uow.coordinate.terms_of([commitment.commitment_id])
        assert (terms.terms_ct, terms.dek_version) == (b"redacted", 2)
        assert await uow.coordinate.outcome_of(handoff.handoff_id) == SealedOutcome(b"o2", 4)
        [pending] = await uow.signals.pending_by_keys(["t:1:0"])
        assert (pending.body_ct, pending.dek_version) == (b"y", 2)
