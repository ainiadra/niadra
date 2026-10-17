import dataclasses
from datetime import date
from itertools import pairwise
from uuid import uuid4

from hypothesis import given
from hypothesis import strategies as st

from niadra.domain.audit.chain import (
    MAX_SLICES,
    ChainHead,
    append,
    canonical,
    empty_head,
    genesis,
    merkle_root,
    slice_for,
    verify,
)
from niadra.testing.cell import SPACE_ID

from .conftest import draft

DAY = date(2026, 9, 22)


def _chain(n: int) -> list:
    linked, _ = append(empty_head(SPACE_ID, 0, DAY), [draft(seq=i + 1) for i in range(n)])
    return linked


def test_an_untouched_chain_verifies_and_ends_at_its_head() -> None:
    linked, head = append(empty_head(SPACE_ID, 0, DAY), [draft(seq=i) for i in range(1, 6)])
    heads, breaks = verify(SPACE_ID, linked)
    assert breaks == []
    assert heads == [head]
    assert [r.seq for r in linked] == [1, 2, 3, 4, 5]
    assert linked[0].prev_hash == genesis(SPACE_ID, 0, DAY)
    assert all(b.prev_hash == a.hash for a, b in pairwise(linked))


def test_changing_any_stored_field_breaks_the_link() -> None:
    linked = _chain(3)
    forged = dataclasses.replace(linked[1], draft=dataclasses.replace(linked[1].draft, purpose="billing"))
    _, breaks = verify(SPACE_ID, [linked[0], forged, linked[2]])
    assert [(b.seq, b.reason) for b in breaks] == [(2, "content_mismatch")]


def test_rehashing_a_forged_link_still_breaks_the_next_one() -> None:
    linked = _chain(3)
    tampered_draft = dataclasses.replace(linked[1].draft, item_ids=("fact-x",))
    rehashed, _ = append(ChainHead(0, DAY, 1, linked[0].hash), [tampered_draft])
    _, breaks = verify(SPACE_ID, [linked[0], rehashed[0], linked[2]])
    assert [(b.seq, b.reason) for b in breaks] == [(3, "prev_hash_mismatch")]


def test_a_deleted_receipt_shows_as_a_gap() -> None:
    linked = _chain(4)
    _, breaks = verify(SPACE_ID, [linked[0], linked[2], linked[3]])
    assert {(b.seq, b.reason) for b in breaks} == {(3, "sequence_gap"), (3, "prev_hash_mismatch")}


def test_a_chain_cannot_move_to_another_day_or_slice() -> None:
    assert genesis(SPACE_ID, 0, DAY) != genesis(SPACE_ID, 1, DAY)
    assert genesis(SPACE_ID, 0, DAY) != genesis(SPACE_ID, 0, date(2026, 9, 23))
    moved = [dataclasses.replace(r, slice=1) for r in _chain(2)]
    _, breaks = verify(SPACE_ID, moved)
    assert breaks


def test_slices_are_stable_and_bounded() -> None:
    profile = uuid4()
    assert slice_for(profile, 16) == slice_for(profile, 16)
    assert slice_for(None, 16) == 0
    assert slice_for(profile, 1) == 0
    assert all(0 <= slice_for(uuid4(), 64) < MAX_SLICES for _ in range(200))
    assert len({slice_for(uuid4(), 16) for _ in range(400)}) == 16


def test_merkle_root_commits_to_every_head() -> None:
    heads = [ChainHead(s, DAY, 10 + s, bytes([s]) * 32) for s in range(5)]
    root = merkle_root(heads)
    assert root == merkle_root(list(reversed(heads)))
    assert root != merkle_root(heads[:4])
    assert root != merkle_root([*heads[:4], ChainHead(4, DAY, 15, b"\x09" * 32)])
    assert merkle_root([]) != merkle_root(heads[:1])


@given(st.lists(st.sampled_from(["billing", "support", None]), min_size=1, max_size=20), st.data())
def test_any_single_edit_is_detected(purposes: list[str | None], data: st.DataObject) -> None:
    drafts = [draft(seq=i + 1, purpose=p) for i, p in enumerate(purposes)]
    linked, _ = append(empty_head(SPACE_ID, 0, DAY), drafts)
    assert verify(SPACE_ID, linked)[1] == []
    victim = data.draw(st.integers(0, len(linked) - 1))
    edited = dataclasses.replace(linked[victim].draft, withheld=linked[victim].draft.withheld + 1)
    linked[victim] = dataclasses.replace(linked[victim], draft=edited)
    assert verify(SPACE_ID, linked)[1]


def test_detail_is_chained() -> None:
    # A denied read keeps its reason on the chain.
    plain = draft(seq=1)
    assert b'"detail":{}' in canonical(plain, slice_=0, day=DAY, seq=1)
    refused = dataclasses.replace(plain, detail={"reason": "forbidden"})
    assert b'"detail":{"reason":"forbidden"}' in canonical(refused, slice_=0, day=DAY, seq=1)
    linked, _ = append(empty_head(SPACE_ID, 0, DAY), [refused, draft(seq=2)])
    forged = [dataclasses.replace(linked[0], draft=dataclasses.replace(refused, detail={"reason": "none"}))]
    _, breaks = verify(SPACE_ID, [*forged, linked[1]])
    assert [(b.seq, b.reason) for b in breaks] == [(1, "content_mismatch")]
