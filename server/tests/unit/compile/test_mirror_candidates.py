"""The mirror rule in the pack (5.12): under the starter policy a V1 reader gets the
dispute the customer wrote about, never what the company stated about them, never a row of a link nobody
confirmed, never words that name a sensitive topic, and a V0 reader gets none of it."""

from dataclasses import replace

from niadra.config.starter import STARTER_POLICY
from niadra.domain.compile import CompiledPack, CompileInput, MemorySnapshot, Variant, compile_pack
from niadra.domain.compile.candidates import policy_attrs
from niadra.domain.vocabulary import Speaker, SpokenBy, SubjectKind, Verification
from tests.unit.compile.factories import PHONE, episode, fact, profile, uid
from tests.unit.compile.factories import settings as compile_settings

EMAIL = uid(0xE0003)
SUPPORT = frozenset({"support"})

DISPUTE = replace(
    episode(
        1,
        "billing",
        days_ago=4,
        channel="email",
        outcome="pending",
        summary="Cliente escreveu sobre a contestação 70710. O agente informou o protocolo 98429.",
    ),
    spoken_by=SpokenBy.MIXED,
)
# A complaint fact (`billing_issue`) leaves a skeleton as a duplicate of its conversation (tpl-12).
SAID = fact(1, "disputed_invoice", "fatura de agosto contestada", "billing")
STATED = replace(fact(2, "overdraft_balance", "R$ 3.200,00", "billing"), speaker=Speaker.AI_AGENT)
BALANCE = replace(
    episode(2, "billing", days_ago=6, summary="O agente informou o saldo devedor de R$ 3.200,00."),
    spoken_by=SpokenBy.COMPANY,
)
LEGACY = episode(3, "billing", days_ago=8, summary="Cliente pediu segunda via do boleto 55120.")
HEALTH_WORDS = replace(
    episode(
        4, "billing", days_ago=9, summary="Cliente contestou a cobrança e disse que o diagnóstico é asma."
    ),
    spoken_by=SpokenBy.CUSTOMER,
)
CLAIMED = replace(
    episode(
        5, "billing", days_ago=2, channel="email", summary="Cliente contestou a fatura 88120.", handle=EMAIL
    ),
    spoken_by=SpokenBy.CUSTOMER,
)


def snapshot(provisional: tuple = ()) -> MemorySnapshot:  # type: ignore[type-arg]
    return MemorySnapshot(
        profile=replace(profile(handles=(PHONE, EMAIL)), provisional_handle_ids=provisional),
        facts=(SAID, STATED),
        episodes=(DISPUTE, BALANCE, LEGACY, HEALTH_WORDS, CLAIMED),
    )


def compiled(snap: MemorySnapshot, level: Verification, **variant: object) -> CompiledPack:
    settings = compile_settings(policy=STARTER_POLICY)
    options: dict[str, object] = {"purposes": SUPPORT, "verification": level, "slotted": True} | variant
    return compile_pack(CompileInput(snap, Variant(**options), settings))  # type: ignore[arg-type]


def seen(pack: CompiledPack) -> set[str]:
    return {*pack.item_ids, *(r.item_id for r in pack.reserve)}


def test_a_v1_reader_gets_what_the_customer_said_and_nothing_the_company_holds() -> None:
    pack = compiled(snapshot(), Verification.V1)
    visible = seen(pack)
    assert f"episode:{DISPUTE.episode_id}" in visible
    assert f"fact:{SAID.fact_id}" in visible
    assert f"episode:{CLAIMED.episode_id}" in visible  # a confirmed handle: the e-mail is the customer's
    assert f"fact:{STATED.fact_id}" not in visible
    assert f"episode:{BALANCE.episode_id}" not in visible
    assert f"episode:{LEGACY.episode_id}" not in visible
    assert f"episode:{HEALTH_WORDS.episode_id}" not in visible
    assert "3.200" not in pack.text
    assert all("3.200" not in r.line for r in pack.reserve)
    mirrored = {e["id"] for e in pack.manifest["items"] if e.get("rule") == "mirror"}
    held = {r.refs["id"] for r in pack.reserve if r.refs.get("rule") == "mirror"}
    assert str(DISPUTE.episode_id) in mirrored | held


def test_a_v0_reader_never_sees_it() -> None:
    pack = compiled(snapshot(), Verification.V0)
    assert not {i for i in seen(pack) if i.startswith(("episode:", "fact:"))}
    assert "70710" not in pack.text
    assert "98429" not in pack.text


def test_an_unconfirmed_link_never_benefits() -> None:
    # The e-mail joined by a claim nobody confirmed: its rows are never the customer's own words, and a
    # reader through it lifts nothing, not even the confirmed rows.
    claimed = snapshot(provisional=(EMAIL,))
    attrs = policy_attrs(
        CompileInput(claimed, Variant(purposes=SUPPORT), compile_settings(policy=STARTER_POLICY))
    )
    assert not attrs[f"episode:{CLAIMED.episode_id}"].own_words
    through = compiled(claimed, Verification.V1, reader_handle_id=EMAIL)
    assert not {i for i in seen(through) if i.startswith(("episode:", "fact:"))}
    reader = policy_attrs(
        CompileInput(claimed, Variant(purposes=SUPPORT, reader_handle_id=EMAIL), compile_settings())
    )
    assert not any(a.own_words for a in reader.values())


def test_only_a_person_hears_their_own_words() -> None:
    account = replace(snapshot(), profile=replace(profile(kind=SubjectKind.ACCOUNT, handles=(PHONE, EMAIL))))
    pack = compiled(account, Verification.V1)
    assert f"episode:{DISPUTE.episode_id}" not in seen(pack)


def test_the_policy_can_turn_it_off_per_category() -> None:
    categories = [
        c.model_copy(update={"mirror": False}) if c.name == "billing" else c
        for c in STARTER_POLICY.categories
    ]
    policy = STARTER_POLICY.model_copy(update={"categories": categories})
    settings = compile_settings(policy=policy)
    pack = compile_pack(
        CompileInput(
            snapshot(), Variant(purposes=SUPPORT, verification=Verification.V1, slotted=True), settings
        )
    )
    assert not {i for i in seen(pack) if i.startswith(("episode:", "fact:"))}
