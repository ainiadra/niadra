"""Each source's pack leaves out what that source never used (15.8), on the read path and in
the compiler's eager set."""

from dataclasses import replace
from typing import Any

from niadra.adapters.memory.measure import InMemoryMeasureRepository
from niadra.config.models import ContextTrimSettings
from niadra.contracts.context import ContextRequest
from niadra.domain.compile import Variant, pack_key
from niadra.domain.measure.trim import ContextTrim, TrimUnit
from niadra.domain.vocabulary import DeliveryPath, Verification
from niadra.testing.cell import SPACE_ID, VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal, sample_config
from tests.unit.compile.factories import PROFILE, T0, marina, open_item
from tests.unit.serve.harness import SUBJECT, Harness

AGENT = principal()
VOICE = principal(VOICE_SOURCE)
UNITS = ("episode:technical_visit", "fact:contract")
TRIM = ContextTrim(WHATSAPP_SOURCE, "chat", tuple(TrimUnit(u, 60) for u in UNITS), T0, 28, 50)


def ask(**extra: object) -> ContextRequest:
    defaults = {"subject": SUBJECT, "verification": Verification.V1, "conversation_id": "wa-now"}
    return ContextRequest(**(defaults | extra))  # type: ignore[arg-type]


async def put_trim(cell: Cell, trim: ContextTrim) -> None:
    await InMemoryMeasureRepository(cell.uow.store).put_trim(trim)


async def manifest(h: Harness, manifest_hash: str | None) -> dict[str, Any]:
    assert manifest_hash is not None
    async with h.uow(SPACE_ID) as uow:
        found = await uow.compile.get_manifest(manifest_hash)
    assert found is not None
    return found


async def test_a_source_reads_a_pack_without_what_it_never_used_and_others_read_the_whole(cell: Cell) -> None:
    h = Harness(cell)
    await put_trim(cell, TRIM)
    lean = await h.serve.context(AGENT, ask())
    assert "fibra 500 mega" not in (lean.text or "")
    recorded = await manifest(h, lean.manifest_hash)
    assert recorded["trim"]["units"] == list(UNITS)
    assert recorded["trim"]["tokens_saved"] > 0
    assert any(e["reason"] == "unused" for e in recorded["excluded"])
    # The voice agent never had a trim: it reads the whole pack, under another key.
    whole = await h.serve.context(VOICE, ask(conversation_id="call-1"))
    assert "fibra 500 mega" in (whole.text or "")
    assert whole.etag != lean.etag


async def test_the_lean_pack_is_compiled_ahead_and_cached_like_any_other(cell: Cell) -> None:
    h = Harness(cell)
    await put_trim(cell, TRIM)
    await h.compile()
    first = await h.serve.context(AGENT, ask(conversation_id="c1"))
    again = await h.serve.context(AGENT, ask(conversation_id="c2"))
    assert first.path is DeliveryPath.T0
    assert (again.path, again.etag) == (DeliveryPath.T0, first.etag)
    lean = Variant(verification=Verification.V1, purposes=frozenset({"support"}), trim=frozenset(UNITS))
    async with h.uow(SPACE_ID) as uow:
        assert await uow.compile.get_pack(pack_key(PROFILE, lean.key)) is not None


async def test_an_opted_out_or_critical_source_and_a_disabled_space_read_the_whole_pack(cell: Cell) -> None:
    h = Harness(cell)
    await put_trim(cell, TRIM)
    config = sample_config()
    exempt = [
        {"context_trim": ContextTrimSettings(opt_out_sources=frozenset({WHATSAPP_SOURCE}))},
        {"context_trim": ContextTrimSettings(enabled=False)},
    ]
    for change in exempt:
        cell.config.put(config.model_copy(update={"settings": config.settings.model_copy(update=change)}))
        assert "fibra 500 mega" in ((await h.serve.context(AGENT, ask(conversation_id="x"))).text or "")
    sources = [s.model_copy(update={"critical": s.source_id == WHATSAPP_SOURCE}) for s in config.sources]
    cell.config.put(config.model_copy(update={"sources": sources}))
    critical = principal(WHATSAPP_SOURCE, cell.config.space(SPACE_ID))
    assert "fibra 500 mega" in ((await h.serve.context(critical, ask(conversation_id="y"))).text or "")


async def test_a_conversation_keeps_its_pinned_pack_when_the_trim_changes(cell: Cell) -> None:
    # 15.8: never swap the pack of a conversation in progress; the next one reads the new trim.
    h = Harness(cell)
    await h.compile()
    first = await h.serve.context(AGENT, ask(conversation_id="c1"))
    await put_trim(cell, TRIM)
    h.cell.clock.advance(minutes=2)
    same = await h.serve.context(AGENT, ask(conversation_id="c1"))
    assert (same.text, same.etag) == (first.text, first.etag)
    await h.serve.drain()  # the read renewed the space's trims in the background
    other = await h.serve.context(AGENT, ask(conversation_id="c2"))
    assert "fibra 500 mega" not in (other.text or "")


async def test_a_pack_of_an_older_generation_is_never_served_from_the_cache(cell: Cell) -> None:
    # A variant the compiler stopped keeping warm (a trim no source reads with any more) keeps its old
    # pack in the cache and in the database; after the memory changes, a read recompiles instead.
    h = Harness(cell)
    await put_trim(cell, TRIM)
    await h.compile()
    await h.serve.context(AGENT, ask(conversation_id="c1"))
    await put_trim(cell, replace(TRIM, units=()))
    h.cell.clock.advance(minutes=2)
    # The visit was done; a new promise takes its place in the skeleton's pending lines.
    h.world.replace_profile(replace(marina(), open_items=(open_item(2, "Enviar segunda via"),)))
    await h.compile()
    await put_trim(cell, TRIM)
    h.cell.clock.advance(minutes=2)
    fresh = await h.serve.context(AGENT, ask(conversation_id="c3"))
    assert fresh.path is DeliveryPath.T2
    assert "Enviar segunda via" in (fresh.text or "")
