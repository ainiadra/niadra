"""The push of shared objects answers against the hot layer, and the consolidator writes later with the same
rule: whatever the order of the pods' statements, and even when the hot layer lost its copy in between, the
durable row and the hot layer end with every field at the greatest version any applied push carried."""

from hypothesis import given, settings
from hypothesis import strategies as st

from niadra.domain.state.push import FieldWrite, Held, PushOutcome, decide, join, merge

FIELDS = ("available", "price_sale", "price_list")


def payload(version: int, name: str) -> str:
    # One content per version, as the contract asks of a source: a retry carries the same bytes.
    return f"{name}@{version}"


def test_the_three_answers() -> None:
    held = {"price_sale": FieldWrite(5, "price_sale@5")}
    assert decide(None, 9, {"price_sale": "x"}) == (PushOutcome.OUT_OF_SET, None)
    outcome, after = decide(held, 4, {"price_sale": "price_sale@4"})
    assert outcome is PushOutcome.STALE_VERSION
    assert after == held
    outcome, after = decide(held, 5, {"price_sale": "price_sale@5"})
    assert outcome is PushOutcome.STALE_VERSION, "a retry of the same version changes nothing"
    outcome, after = decide(held, 4, {"price_sale": "price_sale@4", "available": "available@4"})
    assert outcome is PushOutcome.APPLIED, "an older push still brings a field nobody wrote"
    assert after == {"price_sale": FieldWrite(5, "price_sale@5"), "available": FieldWrite(4, "available@4")}


def test_a_field_keeps_the_greatest_version_in_any_order() -> None:
    a, _ = merge({}, 3, {"price_sale": "p3", "available": "a3"})
    b, _ = merge({}, 7, {"price_sale": "p7"})
    assert join(a, b) == join(b, a) == {"price_sale": FieldWrite(7, "p7"), "available": FieldWrite(3, "a3")}
    assert join(join(a, b), b) == join(a, b)


push = st.tuples(
    st.just("push"),
    st.integers(0, 2),
    st.integers(0, 12),
    st.sets(st.sampled_from(FIELDS), min_size=1),
)
flush = st.tuples(st.just("flush"), st.integers(0, 2))
evict = st.tuples(st.just("evict"))


@settings(max_examples=400, deadline=None)
@given(st.lists(st.one_of(push, flush, evict), max_size=40))
def test_the_durable_row_reaches_what_the_hot_layer_served(events: list[tuple[object, ...]]) -> None:
    hot: Held | None = {}
    durable: Held = {}
    pending: dict[int, Held] = {0: {}, 1: {}, 2: {}}
    applied: Held = {}
    for event in events:
        kind = event[0]
        if kind == "push":
            _, pod, version, names = event
            assert isinstance(pod, int)
            assert isinstance(version, int)
            assert isinstance(names, set)
            if hot is None:
                hot = dict(durable)  # warmed again from the database
            fields = {name: payload(version, name) for name in sorted(names)}
            outcome, after = decide(hot, version, fields)
            assert outcome is not PushOutcome.OUT_OF_SET
            if outcome is PushOutcome.APPLIED:
                assert after is not None
                hot = after
                pending[pod], _ = merge(pending[pod], version, fields)
                applied, _ = merge(applied, version, fields)
            else:
                assert all(hot[name].version >= version for name in names)
        elif kind == "flush":
            pod = event[1]
            assert isinstance(pod, int)
            durable = join(durable, pending[pod])
            pending[pod] = {}
            if hot is not None:
                hot = join(hot, durable)
        else:
            hot = None
    for pod in pending:
        durable = join(durable, pending[pod])
        if hot is not None:
            hot = join(hot, durable)
    assert durable == applied
    if hot is not None:
        assert hot == durable
