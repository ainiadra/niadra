"""The nightly pass of subject signals (24.3.2, 24.7): the daily aggregate by pseudonym, the priors a read
smooths affinity toward, interleaving, and the pass itself across the real modules."""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

import niadra.app.signals as signals_module
from niadra.adapters.memory.outcomes import InMemoryOutcomeRepository
from niadra.config.measurement import ElementExperiment, MeasurementSettings
from niadra.contracts.privacy import ForgetRequest
from niadra.domain.compile.constraints import build
from niadra.domain.ingest.hot import unpack_envelope
from niadra.domain.privacy.files import from_parquet
from niadra.domain.signals.daily import DailyRow, aggregate, interleaved, priors, sign_test
from niadra.domain.signals.derive import Catalog, Entry, fold
from niadra.domain.signals.storage import daily_key, day_markers, object_aad, priors_key, raw_prefix
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Feature
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell
from tests.unit.flow.harness import Flow
from tests.unit.signals.test_signals_flow import NO_BLACK, SUBJECT, apply, configured, repo, said, turn

DAY = date(2026, 9, 28)
AT = datetime(2026, 9, 28, 15, tzinfo=UTC)
BEGE, AZUL = "item_variant:loja:1", "item_variant:loja:2"


def presented(exposure: str, *items: tuple[int, str, str], list_kind: str = "results") -> dict[str, Any]:
    return {
        "kind": "presented",
        "exposure_id": exposure,
        "list_id": "results",
        "list_kind": list_kind,
        "items": [{"pos": pos, "ref": ref, "shown": {"color": color}} for pos, ref, color in items],
        "delivered_at": AT.isoformat(),
    }


def entry(key: str, interaction: dict[str, Any], minutes: int = 0) -> Entry:
    return Entry(key, AT + timedelta(minutes=minutes), interaction, "s1")


def test_the_daily_aggregate_is_the_fold_of_each_pseudonyms_day() -> None:
    day = [
        entry("e1", presented("x1", (1, BEGE, "bege"), (2, AZUL, "azul"))),
        entry("e2", {"kind": "engaged", "exposure_id": "x1", "ref": BEGE, "how": "detail"}, 1),
    ]
    rows = aggregate({"p1": day}, DAY, Catalog())
    body = fold(None, day, datetime(2026, 9, 29, tzinfo=UTC), Catalog())
    assert {(r.attr, r.value): (r.exposure, r.signal, r.events) for r in rows} == {
        (v.attr, v.value): (round(v.e, 6), round(v.s, 6), v.n) for v in body.values.values()
    }
    assert {r.pseudonym for r in rows} == {"p1"}


def test_a_prior_needs_enough_people_and_is_their_signal_over_their_exposure() -> None:
    rows = [DailyRow(f"p{i}", "item_variant.color", "bege", 2.0, 0.5, 1) for i in range(10)]
    rows += [DailyRow(f"p{i}", "item_variant.color", "azul", 1.0, 1.0, 1) for i in range(9)]
    [found] = priors(rows, 10)
    assert (found.attr, found.value, found.prior, found.exposures, found.people) == (
        "item_variant.color", "bege", 0.25, 20, 10
    )  # fmt: skip


def test_a_prior_moves_what_the_block_infers() -> None:
    # Three sessions engaged with bege: preferred against no prior, not against a population that engages
    # with it as much.
    day = [
        entry("e0", presented("x1", (1, BEGE, "bege"))),
        *(Entry(f"e{i}", AT + timedelta(minutes=20 * i), {"kind": "engaged", "exposure_id": "x1", "ref": BEGE,
                                                    "how": "compare"}, f"s{i}") for i in range(1, 4)),
    ]  # fmt: skip
    body = fold(None, day, AT + timedelta(hours=1), Catalog())
    plain = build(body, for_="self", session=None, now=AT, catalog=Catalog())
    assert [s["value"] for s in plain["soft"]] == ["bege"]
    smoothed = build(
        body, for_="self", session=None, now=AT, catalog=Catalog(), prior=lambda attr, value: 5.0
    )
    assert [(s["value"], s["polarity"]) for s in smoothed.get("soft", [])] == [("bege", "avoid")]


def test_interleaving_credits_the_ranking_that_contributed_what_was_engaged() -> None:
    def shown(exposure: str) -> dict[str, Any]:
        data = presented(exposure, (1, BEGE, "bege"), (2, AZUL, "azul"), list_kind="search")
        data["items"][0]["team"], data["items"][1]["team"] = "a", "b"
        return data

    day = [
        entry("e1", shown("x1")),
        entry("e2", {"kind": "engaged", "exposure_id": "x1", "ref": AZUL, "how": "click"}, 1),
        entry("e3", shown("x2")),
        entry("e4", shown("x3")),
        entry("e5", {"kind": "engaged", "exposure_id": "x3", "ref": BEGE, "how": "add_to_cart"}, 2),
        entry("e6", presented("x4", (1, BEGE, "bege"), list_kind="other_tool")),
    ]
    found = interleaved(day, {"search": "rank_v2"})
    assert set(found) == {("rank_v2", "search")}
    counts = found[("rank_v2", "search")]
    assert (counts.impressions, counts.wins_a, counts.wins_b, counts.ties) == (3, 1, 1, 1)


def test_the_sign_test() -> None:
    assert sign_test(0, 0) is None
    assert sign_test(5, 5) == 1.0
    assert sign_test(10, 0) == pytest.approx(0.001953, abs=1e-6)
    assert sign_test(560, 460) == pytest.approx(0.001963, abs=1e-4)


# The pass across the modules.


async def test_the_nightly_pass_keeps_the_day_the_priors_and_the_interleaving(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(signals_module, "MIN_DISTINCT_PEOPLE", 1)
    experiment = ElementExperiment(
        name="rank_v2",
        kind="interleave",
        tool="search",
        salt="salt-of-rank-v2",
        starts_at=AT - timedelta(days=9),
    )
    config = configured(Feature.SIGNALS, Feature.MEASUREMENT, purposes=True)
    cell.config.put(config.model_copy(update={"measurement": MeasurementSettings(experiments=[experiment])}))
    flow = Flow.build(cell)
    now = cell.clock.now()
    assert (await flow.send(WHATSAPP_SOURCE, said("wa-1", "Quero um vestido", now))).errors == []
    await flow.drain()
    shown = presented("0199a3b2-6d10-7c2a-9e11-4f0b8a7c3e55", (1, BEGE, "bege"), (2, AZUL, "azul"),
                      list_kind="search")  # fmt: skip
    shown["items"][0]["team"], shown["items"][1]["team"] = "a", "b"
    shown["delivered_at"] = now.isoformat()
    click = {"kind": "engaged", "exposure_id": shown["exposure_id"], "ref": AZUL, "how": "click"}
    await apply(flow, turn("t1", "wa-1", now, shown, click, NO_BLACK))
    cell.clock.advance(minutes=6)
    assert await flow.signals.derive_due(SPACE_ID) == 1
    cell.clock.advance(days=1)
    assert await flow.signals.nightly(SPACE_ID) > 0

    day = now.astimezone(UTC).date()
    stored = cell.objects.objects[daily_key(SPACE_ID, day)]
    version, sealed = unpack_envelope(stored)
    plain = await cell.keys.decrypt(SPACE_ID, sealed, object_aad(daily_key(SPACE_ID, day)), version)
    rows = from_parquet(plain)
    assert {r["value"] for r in rows} == {"bege", "azul"}
    assert all(len(r["pseudonym"]) == 32 for r in rows)
    [segment] = [s for s in repo(flow).segment_rows.values() if s.kind == "daily"]
    assert segment.rows == len(rows)
    assert {p.value for p in repo(flow).prior_rows["all"]} == {"bege", "azul"}
    assert await cell.cache.get(priors_key(SPACE_ID)) is not None
    interleave = InMemoryOutcomeRepository(cell.uow.store).interleaved_rows
    [(key, counts)] = interleave.items()
    assert key == (day, "rank_v2", "search")
    assert (counts.impressions, counts.wins_b) == (1, 1)

    # The cache lost the priors: the loop puts them back from the database.
    await cell.cache.delete([priors_key(SPACE_ID)])
    assert await flow.signals.keep_priors(SPACE_ID) == 1
    assert await flow.signals.keep_priors(SPACE_ID) == 0

    # The subject's erasure takes their rows out of every daily aggregate.
    await flow.privacy.forget(
        SPACE_ID, ForgetRequest(target="handle", handle=SUBJECT), requested_by="dpo", idempotency_key="f-1"
    )
    await flow.drain()
    version, sealed = unpack_envelope(cell.objects.objects[daily_key(SPACE_ID, day)])
    plain = await cell.keys.decrypt(SPACE_ID, sealed, object_aad(daily_key(SPACE_ID, day)), version)
    assert from_parquet(plain) == []
    assert [s.rows for s in repo(flow).segment_rows.values() if s.kind == "daily"] == [0]


async def test_the_nightly_pass_lists_one_day_of_markers_not_every_handles_raw_objects(cell: Cell) -> None:
    cell.config.put(configured(Feature.SIGNALS, purposes=True))
    flow = Flow.build(cell)
    now = cell.clock.now()
    assert (await flow.send(WHATSAPP_SOURCE, said("wa-1", "Quero um vestido", now))).errors == []
    await flow.drain()
    await apply(flow, turn("t1", "wa-1", now, NO_BLACK))
    cell.clock.advance(minutes=6)
    assert await flow.signals.derive_due(SPACE_ID) == 1
    day = now.astimezone(UTC).date()
    markers = [k for k in cell.objects.objects if k.startswith(day_markers(SPACE_ID, day))]
    assert len(markers) == 1
    assert cell.objects.objects[markers[0]] == b""
    listed: list[str] = []
    list_keys = cell.objects.list_keys

    async def spy(prefix: str) -> list[str]:
        listed.append(prefix)
        return await list_keys(prefix)

    cell.objects.list_keys = spy  # type: ignore[method-assign]
    cell.clock.advance(days=1)
    await flow.signals.nightly(SPACE_ID)
    assert listed == [day_markers(SPACE_ID, day), day_markers(SPACE_ID, day + timedelta(days=1))]
    # Read, the day's markers go; the raw object stays for its 45 days.
    assert not [k for k in cell.objects.objects if k.startswith(day_markers(SPACE_ID, day))]
    handle = UUID(markers[0].split("/")[-2])
    assert [k for k in cell.objects.objects if k.startswith(raw_prefix(SPACE_ID, handle))]


async def test_an_erasure_takes_the_subjects_raw_markers_too(cell: Cell) -> None:
    cell.config.put(configured(Feature.SIGNALS, purposes=True))
    flow = Flow.build(cell)
    now = cell.clock.now()
    assert (await flow.send(WHATSAPP_SOURCE, said("wa-1", "Quero um vestido", now))).errors == []
    await flow.drain()
    await apply(flow, turn("t1", "wa-1", now, NO_BLACK))
    cell.clock.advance(minutes=6)
    await flow.signals.derive_due(SPACE_ID)
    assert [k for k in cell.objects.objects if "/_days/" in k]
    await flow.privacy.forget(
        SPACE_ID, ForgetRequest(target="handle", handle=SUBJECT), requested_by="dpo", idempotency_key="f-2"
    )
    await flow.drain()
    assert not [k for k in cell.objects.objects if k.startswith("measure/raw/")]


async def test_the_raw_interactions_are_indexed_by_the_derivations_write_and_expire_by_the_index(
    cell: Cell,
) -> None:
    # The `raw_content` retention reads the index, never the space's listing (5.14.0).
    cell.config.put(configured(Feature.SIGNALS, purposes=True))
    flow = Flow.build(cell)
    now = cell.clock.now()
    assert (await flow.send(WHATSAPP_SOURCE, said("wa-1", "Quero um vestido", now))).errors == []
    await flow.drain()
    await apply(flow, turn("t1", "wa-1", now, NO_BLACK))
    cell.clock.advance(minutes=6)
    await flow.signals.derive_due(SPACE_ID)
    kept = f"measure/raw/{SPACE_ID}/"
    (raw,) = [k for k in cell.objects.objects if k.startswith(kept) and "/_days/" not in k]
    assert raw in cell.uow.store.module("privacy")["raw_objects"]
    config = cell.config.space(SPACE_ID)
    settings = config.settings.model_copy(update={"retention_days": {"raw_content": 30}})
    cell.config.put(config.model_copy(update={"settings": settings}))
    cell.clock.advance(days=31)
    await flow.privacy.retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))
    assert raw not in cell.objects.objects
    assert raw not in cell.uow.store.module("privacy")["raw_objects"]


def test_priors_by_source_keep_each_sources_people_apart_and_the_space_whole() -> None:
    store, app = "source-store", "source-app"
    day = {
        f"p{i}": [
            Entry(f"e{i}", AT, presented("x1", (1, BEGE, "bege")), "s1", source=store if i < 10 else app),
            Entry(f"f{i}", AT + timedelta(minutes=1),
                  {"kind": "engaged", "exposure_id": "x1", "ref": BEGE, "how": "click"}, "s1",
                  source=store if i < 10 else app),
        ]
        for i in range(14)
    }  # fmt: skip
    rows = aggregate(day, DAY, Catalog(), by_source=True)
    assert {r.segment for r in rows} == {store, app}
    found = {(p.segment, p.value): p.people for p in priors(rows, 10)}
    # The app's four people are too few for a prior of its own; the space's counts all fourteen.
    assert found == {("all", "bege"): 14, (store, "bege"): 10}
    assert {r.segment for r in aggregate(day, DAY, Catalog())} == {"all"}


async def test_a_read_smooths_toward_its_own_sources_priors_where_they_exist(cell: Cell) -> None:
    from niadra.domain.signals.daily import Prior

    cell.config.put(configured(Feature.SIGNALS, purposes=True))
    flow = Flow.build(cell)
    signals = flow.signals
    await signals._put_priors(
        SPACE_ID,
        [
            Prior("item_variant.color", "bege", 0.1, 100, 10.0, 40),
            Prior("item_variant.color", "bege", 5.0, 50, 250.0, 12, segment=str(WHATSAPP_SOURCE)),
        ],
    )
    raw = await cell.cache.get(priors_key(SPACE_ID))
    known = signals._priors_of(SPACE_ID, raw)
    assert known[(str(WHATSAPP_SOURCE), "item_variant.color", "bege")] == 5.0
    assert known[("all", "item_variant.color", "bege")] == 0.1
