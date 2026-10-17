"""The `where` expression of history navigation and the `limit` of a search."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from niadra.contracts.context import HistoryFilters, SearchRequest, TimelineRequest
from niadra.domain.serve.where import MAX_CONDITIONS, WhereError, parse_where
from niadra.domain.vocabulary import Verification
from niadra.testing.cell import SPACE_ID, Cell, principal
from tests.unit.serve.harness import SUBJECT, Harness

AGENT = principal()

ROW = {
    "id": "episode:1",
    "kind": "episode",
    "channel": "whatsapp",
    "category": "billing",
    "outcome": "unresolved",
    "source_id": "s1",
    "vendor": "acme",
    "at": datetime(2026, 9, 10, 12, tzinfo=UTC),
    "valid_until": None,
    "confidence": 0.8,
    "text": "Cliente reclamou da Fatura de agosto",
    "object_type": None,
    "object_namespace": None,
}


@pytest.mark.parametrize(
    ("tree", "expected"),
    [
        ({"kind": "episode"}, True),
        ({"kind": ["fact", "episode"]}, True),
        ({"kind": {"nin": ["fact", "episode"]}}, False),
        ({"channel": "whatsapp", "category": "voice"}, False),
        ({"OR": [{"channel": "voice"}, {"category": "billing"}]}, True),
        ({"NOT": {"vendor": "acme"}}, False),
        ({"NOT": [{"vendor": "acme"}, {"channel": "voice"}]}, True),
        ({"at": {"gte": "2026-09-01", "lt": "2026-10-01"}}, True),
        ({"at": {"gt": "2026-09-10T12:00:00+00:00"}}, False),
        ({"confidence": {"gte": 0.9}}, False),
        ({"text": {"icontains": "fatura"}}, True),
        ({"text": {"contains": "fatura"}}, False),
        ({"text": {"icontains": "AGOSTO"}}, True),
        ({"valid_until": {"exists": False}}, True),
        ({"object_type": {"ne": "invoice"}}, True),
        ({"object_type": {"gt": "a"}}, None),
        ({"AND": [{"kind": "episode"}, {"OR": [{"vendor": "other"}, {"outcome": "unresolved"}]}]}, True),
    ],
)
def test_the_grammar(tree: dict[str, object], expected: bool | None) -> None:
    if expected is None:
        with pytest.raises(WhereError):
            parse_where(tree)
        return
    assert parse_where(tree).matches(ROW) is expected


@pytest.mark.parametrize(
    "tree",
    [
        {},
        {"customer": "x"},
        {"AND": []},
        {"AND": {"kind": "episode"}},
        {"kind": {"like": "e%"}},
        {"at": {"gte": "yesterday"}},
        {"confidence": {"gte": "high"}},
        {"kind": {"in": []}},
        {"text": "exact"},
        {"NOT": {"NOT": {"NOT": {"NOT": {"NOT": {"NOT": {"kind": "fact"}}}}}}},
        {"OR": [{"kind": "fact"}] * (MAX_CONDITIONS + 1)},
    ],
)
def test_what_it_refuses_names_the_path_and_never_the_value(tree: dict[str, object]) -> None:
    with pytest.raises(WhereError) as refused:
        parse_where(tree)
    assert "yesterday" not in str(refused.value)
    assert "high" not in str(refused.value)


def test_the_contract_refuses_a_bad_expression_with_a_validation_error() -> None:
    with pytest.raises(ValidationError):
        HistoryFilters(where={"customer_phone": "+5511"})
    assert HistoryFilters(where={"kind": "episode"}).where == {"kind": "episode"}


def proven(cell: Cell) -> Harness:
    h = Harness(cell)
    h.world.proven = Verification.V2
    return h


def search(query: str, **extra: object) -> SearchRequest:
    base = {"subject": SUBJECT, "query": query, "verification": Verification.V2, "conversation_id": "c1"}
    return SearchRequest(**(base | extra))  # type: ignore[arg-type]


async def test_where_narrows_search_and_timeline_after_the_policy(cell: Cell) -> None:
    h = proven(cell)
    everything = await h.serve.search(AGENT, search("fatura visita problema"))
    kinds = {i.kind for i in everything.items}
    assert len(kinds) > 1
    no_episodes = HistoryFilters(where={"NOT": {"kind": "episode"}})
    narrowed = await h.serve.search(AGENT, search("fatura visita problema", filters=no_episodes))
    assert narrowed.items
    assert all(i.kind != "episode" for i in narrowed.items)
    # The policy still withholds what it withheld: `where` never widens.
    assert narrowed.withheld == everything.withheld
    timeline = await h.serve.timeline(
        AGENT,
        TimelineRequest(
            subject=SUBJECT,
            verification=Verification.V2,
            conversation_id="c1",
            filters=HistoryFilters(where={"kind": "action"}),
        ),
    )
    assert timeline.items
    assert {i.kind for i in timeline.items} == {"action"}


async def test_where_reads_the_vendor_of_the_source(cell: Cell) -> None:
    h = proven(cell)
    vendors = {s.vendor for s in cell.config.space(SPACE_ID).sources if s.vendor}
    everything = await h.serve.search(AGENT, search("fatura visita problema"))
    none = await h.serve.search(
        AGENT,
        search("fatura visita problema", filters=HistoryFilters(where={"vendor": "nobody-sells-this"})),
    )
    assert everything.items
    assert not none.items
    if vendors:
        some = await h.serve.search(
            AGENT,
            search(
                "fatura visita problema",
                filters=HistoryFilters(
                    where={"OR": [{"vendor": sorted(vendors)}, {"vendor": {"exists": False}}]}
                ),
            ),
        )
        assert [i.id for i in some.items] == [i.id for i in everything.items]


async def test_limit_caps_the_items_and_keeps_the_order(cell: Cell) -> None:
    h = proven(cell)
    plain = await h.serve.search(AGENT, search("fatura visita problema"))
    assert len(plain.items) > 2
    capped = await h.serve.search(AGENT, search("fatura visita problema", limit=2))
    assert [i.id for i in capped.items] == [i.id for i in plain.items[:2]]
