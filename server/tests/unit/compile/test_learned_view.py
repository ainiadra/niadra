"""A skeleton compiled with the ceilings its agent and model learned (13, 6.3): within them, with what
they leave out waiting in the reserve, and a key of its own that a pinned conversation ignores."""

from dataclasses import replace

from niadra.domain.compile import Variant, untrimmed_key
from niadra.domain.compile.model import Section
from niadra.domain.compile.views import learned_ceilings, skeleton_spec, view_spec
from niadra.domain.vocabulary import Verification
from tests.unit.compile.test_compiler import SUPPORT, pack_for


def test_the_learned_ceilings_travel_in_the_key_and_a_pin_ignores_them() -> None:
    plain = Variant(view="voice", purposes=SUPPORT, slotted=True)
    learned = replace(plain, budgets=(("episodes", 60), ("facts", 80)))
    assert learned.key != plain.key
    assert Variant.parse(learned.key) == learned
    trimmed = replace(learned, trim=frozenset({"episode:billing"}))
    assert Variant.parse(trimmed.key) == trimmed
    # A conversation keeps its pinned bytes when its agent's learned view moves overnight.
    assert untrimmed_key(learned.key) == untrimmed_key(plain.key) == untrimmed_key(trimmed.key)
    assert Variant.parse(plain.key).budgets == ()


def test_a_learned_skeleton_compiles_within_its_ceilings_and_holds_the_rest_in_reserve() -> None:
    whole = pack_for(view="chat", slotted=True, verification=Verification.V2)
    # An agent that never used the conversations or the other agents' actions: their ceilings at half.
    ceilings = learned_ceilings("chat", {"episode": 0.5, "action": 0.5})
    limits = dict(ceilings)
    assert limits == {"actions": 50, "episodes": 65}
    small = pack_for(view="chat", slotted=True, verification=Verification.V2, budgets=(("actions", 20),))
    tokens = small.manifest["tokens"]["sections"]
    assert tokens.get("actions", 0) <= 20
    assert small.manifest["tokens"]["total"] < whole.manifest["tokens"]["total"]
    # Nothing is lost: what the smaller skeleton left out waits in the reserve for a slot to bring.
    left = set(whole.item_ids) - set(small.item_ids)
    assert left == {i for i in whole.item_ids if i.startswith("action:")}
    assert left <= {r.item_id for r in small.reserve}
    # The floors hold: the customer line, the facts, the open items and the latest conversation stay.
    assert set(small.item_ids) == set(whole.item_ids) - left


def test_without_learned_ceilings_the_pack_is_todays_to_the_byte() -> None:
    today = pack_for(view="voice", slotted=True)
    again = pack_for(view="voice", slotted=True, budgets=())
    assert (again.text, again.pack_hash, again.variant) == (today.text, today.pack_hash, today.variant)


def test_ceilings_only_where_the_view_has_a_skeleton() -> None:
    voice = skeleton_spec(view_spec("voice")).sections
    assert dict(learned_ceilings("voice", {"episode": 1.5}))["episodes"] == int(
        voice[Section.EPISODES].ceiling * 1.5  # type: ignore[operator]
    )
    assert learned_ceilings("account", {"episode": 0.5}) == ()
