"""An object's fields by source, and the rule that moves them (24.3.1; object-state 4.2)."""

from datetime import UTC, datetime, timedelta

from niadra.domain.state.observed import Previous, Report, compare_versions, latest, merge

T0 = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def report(minutes: float, fields: dict[str, object], *, source: str = "erp", **kw: object) -> Report:
    received = kw.pop("received", at(minutes + 1))
    return Report(source, fields, at(minutes), at(minutes), received, **kw)  # type: ignore[arg-type]


def test_a_late_report_never_moves_a_field_back_and_brings_what_no_newer_one_said() -> None:
    held, _ = merge({}, report(10, {"status": "credited", "amount": "40"}))
    held, moved = merge(held, report(1, {"status": "open", "due_on": "2026-10-05"}))
    assert moved == {"due_on"}
    assert latest(held) == {"amount": "40", "due_on": "2026-10-05", "status": "credited"}
    assert held["status"]["erp"].valid_at == at(10)


def test_at_the_same_instant_a_greater_version_moves_and_an_equal_one_does_not() -> None:
    held, _ = merge({}, report(10, {"status": "open"}, version="9"))
    held, moved = merge(held, report(10, {"status": "paid"}, version="10"))  # numeric, not lexical
    assert (moved, latest(held)["status"]) == ({"status"}, "paid")
    for version in ("10", "8"):
        _, moved = merge(held, report(10, {"status": "void"}, version=version))
        assert moved == frozenset()
    assert compare_versions("b", "a") == 1
    assert compare_versions(None, "1") == -1


def test_the_same_report_twice_moves_nothing() -> None:
    once, _ = merge({}, report(10, {"status": "open"}))
    twice, moved = merge(once, report(10, {"status": "open"}))
    assert (twice, moved) == (once, frozenset())


def test_a_newer_observation_is_new_and_a_revision_keeps_the_first_seen_time() -> None:
    first, _ = merge({}, report(10, {"content": "dispositive"}, received=at(11)))
    newer, _ = merge(first, report(20, {"content": "full"}, received=at(30)))
    assert newer["content"]["erp"].known_at == at(30)
    # A type that allows revisions: the same notice again with new content keeps when it was first seen.
    revised, moved = merge(first, report(20, {"content": "full"}, received=at(30)), revisions=True)
    assert moved == {"content"}
    assert (revised["content"]["erp"].value, revised["content"]["erp"].known_at) == ("full", at(11))
    # At the same instant, it is the same observation whatever the type says.
    same, _ = merge(first, report(10, {"content": "full"}, version="2", received=at(40)))
    assert same["content"]["erp"].known_at == at(11)
    again, moved = merge(first, report(10, {"content": "full"}, received=at(40)), revisions=True)
    assert (moved, again["content"]["erp"].known_at) == ({"content"}, at(11))


def test_each_source_keeps_its_own_observation_of_a_field() -> None:
    held, _ = merge({}, report(10, {"fee": 0}, source="quote_api"))
    held, _ = merge(held, report(5, {"fee": 120}, source="budget_document"))
    assert {src: o.value for src, o in held["fee"].items()} == {"quote_api": 0, "budget_document": 120}
    assert latest(held) == {"fee": 0}  # without a declaration, the newest in the world


def test_a_tracked_field_keeps_the_value_a_change_replaced() -> None:
    held, _ = merge({}, report(10, {"price": 229.9, "stock": 3}))
    held, _ = merge(held, report(20, {"price": 189.9, "stock": 2}), tracked={"price"})
    assert held["price"]["erp"].was == Previous(229.9)
    assert held["stock"]["erp"].was is None
    held, _ = merge(held, report(30, {"price": 189.9}), tracked={"price"})
    assert held["price"]["erp"].was == Previous(229.9)  # the same value again is no change
