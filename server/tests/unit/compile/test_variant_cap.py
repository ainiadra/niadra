"""The per-profile cap of compiled variants: the least read leaves first, a default one never,
reads are recorded by the receipt writer so the read path writes nothing, and a cut pack leaves storage."""

from datetime import UTC, datetime, timedelta

from niadra.app.audit import _project
from niadra.app.compile import MAX_VARIANTS, CompileService, default_variants
from niadra.domain.common.ids import uuid7
from niadra.domain.compile import StoredPack, Variant, profile_target
from niadra.domain.receipts import ReceiptDraft
from niadra.domain.vocabulary import Audience, ReceiptKind, ReceiptSurface, Verification
from niadra.testing.cell import SPACE_ID, Cell, sample_config

T0 = datetime(2026, 9, 24, 12, tzinfo=UTC)


def extra_variants(n: int) -> list[str]:
    return [
        Variant(view="full", purposes=frozenset({f"p{i:02d}"}), audience=Audience.INTERNAL_AGENT).key
        for i in range(n)
    ]


def test_the_least_read_leaves_first_and_a_default_never(cell: Cell) -> None:
    config = sample_config()
    service = CompileService(cell.uow, cell.keys, cell.cache, cell.clock, cell.config)
    defaults = {v.key for v in default_variants(config)}
    others = extra_variants(MAX_VARIANTS)
    # The first extra variant was read a moment ago; the rest long ago or never.
    reads = {others[0]: T0, **{k: T0 - timedelta(days=30) for k in others[1:5]}}
    kept = {v.key for v in service._variants(config, others, [], [], last_read=reads)}
    assert len(kept) == MAX_VARIANTS
    assert defaults <= kept
    assert others[0] in kept
    assert set(others[1:5]) <= kept  # read, even long ago, beats never read
    cut = set(others) - kept
    assert len(cut) == len(defaults) + len(others) - MAX_VARIANTS
    assert not (cut & set(others[:5]))


def test_what_was_just_asked_for_stays_even_if_never_read(cell: Cell) -> None:
    config = sample_config()
    service = CompileService(cell.uow, cell.keys, cell.cache, cell.clock, cell.config)
    others = extra_variants(MAX_VARIANTS)
    new = others[-1]
    kept = {v.key for v in service._variants(config, others[:-1], [new], [], last_read={})}
    assert new in kept


async def test_the_receipt_writer_records_reads_and_the_repository_never_moves_them_back(cell: Cell) -> None:
    profile = uuid7()
    key = f"{profile}/chat:V1"
    stored = StoredPack(key, profile_target(profile), 1, "c1", 1, b"{}", "h", "m", T0)

    def read(at: datetime) -> ReceiptDraft:
        return ReceiptDraft(
            receipt_id=uuid7(),
            space_id=SPACE_ID,
            producer_id="p",
            producer_seq=1,
            kind=ReceiptKind.READ,
            surface=ReceiptSurface.PACK,
            audience=Audience.CUSTOMER_AGENT,
            read_at=at,
            verification=Verification.V1.value,
            extra={"pack_key": key, "generation": "1"},
        )

    async with cell.uow(SPACE_ID) as uow:
        await uow.compile.save_bundle(stored.target, 1, [stored])
        await _project(uow, [read(T0 + timedelta(minutes=5)), read(T0)])
        assert (await uow.compile.get_pack(key)).last_read_at == T0 + timedelta(minutes=5)  # type: ignore[union-attr]
        await _project(uow, [read(T0)])
        assert (await uow.compile.get_pack(key)).last_read_at == T0 + timedelta(minutes=5)  # type: ignore[union-attr]
        await uow.compile.save_bundle(stored.target, 2, [])  # the next generation cut it
        assert await uow.compile.get_pack(key) is None
