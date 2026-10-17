"""A subject's signals keep whole what the person said, the inferences they deleted and what they turned down:
only the derived accumulators, computed again from the raw segments, stay within their budget."""

from datetime import UTC, datetime, timedelta

from niadra.domain.signals.body import DERIVED_BYTES, Body, Stated, Value, decode, encode

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def instruction(n: int) -> Stated:
    return Stated(
        key=f"x:{n}",
        attr="",
        op="eq",
        values=[],
        strength="must_not",
        scope="object",
        source="stated",
        said_at=T0 + timedelta(minutes=n),
        object=f"legal_case:court:{n}",
        text=f"Não propor acordo no processo {n}; a diretoria decidiu levar a julgamento.",
    )


def test_the_51st_instruction_a_person_gives_no_longer_pushes_out_the_first() -> None:
    body = Body(T0, stated=[instruction(n) for n in range(300)])
    kept = decode(encode(body))
    assert len(kept.stated) == 300
    assert {s.key for s in kept.stated} == {f"x:{n}" for n in range(300)}


def test_deleted_inferences_and_turned_down_objects_are_all_kept() -> None:
    body = Body(
        T0,
        suppressed={f"v:{n}": T0 for n in range(400)},
        excluded={f"product:shop:{n}": T0 for n in range(400)},
    )
    kept = decode(encode(body))
    assert len(kept.suppressed) == 400
    assert len(kept.excluded) == 400


def test_the_derived_accumulators_alone_keep_their_byte_budget() -> None:
    values = {
        ("color", f"tone-{n}"): Value("color", f"tone-{n}", 1.0, 1.0, 1.0, float(n), []) for n in range(2_000)
    }
    body = Body(T0, values=values, stated=[instruction(n) for n in range(100)])
    kept = decode(encode(body))
    assert len(kept.stated) == 100
    assert len(kept.values) < 2_000
    derived = Body(T0, values=kept.values)
    assert len(encode(derived)) <= DERIVED_BYTES


def test_what_the_person_turned_down_is_still_turned_down_a_year_later() -> None:
    from niadra.domain.signals.derive import tidy

    body = Body(T0, excluded={"product:shop:1": T0, "product:shop:2": T0 - timedelta(days=400)})
    kept = tidy(body, T0 + timedelta(days=365))
    assert set(kept.excluded) == {"product:shop:1", "product:shop:2"}
