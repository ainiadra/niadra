"""A handoff package names the subject's open objects from the `now` view: a final outcome leaves the list."""

from datetime import UTC, datetime

from niadra.app.coordinate import MAX_OPEN_OBJECTS, unfinished_objects
from niadra.contracts.state import ObjectRead, OutcomeState, StateRef, StateView

AT = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def test_only_objects_without_a_final_outcome_are_open() -> None:
    view = StateView(
        objects=[
            ObjectRead(ref=StateRef(type="proposal", namespace="crm", id="p1"), state="sent", as_of=AT),
            ObjectRead(
                ref=StateRef(type="proposal", namespace="crm", id="p0"),
                state="won",
                outcome=OutcomeState(state="won", final=True, at=AT),
            ),
            ObjectRead(
                ref=StateRef(type="order", namespace="erp", id="o1"),
                state="delivered",
                outcome=OutcomeState(state="delivered", final=False, at=AT),
            ),
        ]
    )
    opened, more = unfinished_objects(view)
    assert [(o.ref.id, o.state, o.since) for o in opened] == [("p1", "sent", AT), ("o1", "delivered", None)]
    assert more == 0
    assert unfinished_objects(None) == ([], 0)


def test_a_package_says_how_many_open_objects_it_left_out() -> None:
    # Past the 50 objects a package carries, the rest were left out in silence.
    view = StateView(
        objects=[
            ObjectRead(ref=StateRef(type="order", namespace="erp", id=f"o{n}"), state="open", as_of=AT)
            for n in range(MAX_OPEN_OBJECTS + 7)
        ]
    )
    opened, more = unfinished_objects(view)
    assert (len(opened), more) == (MAX_OPEN_OBJECTS, 7)
