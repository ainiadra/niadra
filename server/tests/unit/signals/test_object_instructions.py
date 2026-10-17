"""Instructions about an object in the constraints block: hard and never relaxed while the
read is about the object, and held in the signals of who said them under `scope: object`."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from niadra.contracts.signals import ConstraintsBlock
from niadra.domain.compile import constraints
from niadra.domain.compile.constraints import build, with_instructions
from niadra.domain.memory.bound import BoundRow
from niadra.domain.signals.body import Body, Stated, decode, encode
from niadra.domain.signals.derive import Catalog, Entry, fold, holds, stated_token, suppress
from niadra.domain.signals.inferences import inferred
from niadra.domain.signals.text import block_text
from niadra.domain.vocabulary import MemoryAudience

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
REF = "lawsuit:court:case-1"


def instruction(text: str, attr: str | None = None) -> BoundRow:
    return BoundRow(
        kind="instruction",
        row_id=f"x-{text}",
        text=text,
        origin_handle_id=UUID(int=1),
        said_at=NOW,
        audience=MemoryAudience.SHARED,
        strength="must_not",
        attr=attr,
        role="lawyer",
    )


def stated(scope: str, obj: str | None = None) -> Stated:
    return Stated(
        key="x:1", attr="", op="eq", values=[], strength="must_not", scope=scope, source="stated",
        said_at=NOW, object=obj, text="não propor acordo" if obj else None,
        audience="shared" if obj else None,
    )  # fmt: skip


def test_an_object_instruction_holds_only_where_the_read_holds_its_object() -> None:
    said = stated("object", REF)
    assert holds(said, NOW, None, objects={REF})
    assert not holds(said, NOW, None, objects={"lawsuit:court:case-2"})
    assert not holds(said, NOW, None)  # the subject's own block never carries it
    assert holds(said, NOW, None, any_session=True)  # and the body keeps it until it expires


def test_an_object_instruction_reads_back_from_the_sealed_body() -> None:
    body = Body(at=NOW, stated=[stated("object", REF)])
    [back] = decode(encode(body)).stated
    assert (back.object, back.text, back.audience, back.scope) == (
        REF,
        "não propor acordo",
        "shared",
        "object",
    )


def test_the_block_carries_instructions_hard_and_one_with_a_field_for_the_tools() -> None:
    base = build(Body(at=NOW), for_="self", session=None, now=NOW, catalog=Catalog())
    block = with_instructions(
        base,
        [(REF, instruction("não propor acordo")), (REF, instruction("sem acordo", "lawsuit.settlement"))],
    )
    parsed = ConstraintsBlock.model_validate({**block, "text": ""})
    assert [i.text for i in parsed.instructions] == ["não propor acordo", "sem acordo"]
    [hard] = parsed.hard
    assert (hard.attr, hard.op, hard.values, hard.scope, hard.object, hard.relax) == (
        "lawsuit.settlement", "ne", [True], "object", REF, "never"
    )  # fmt: skip
    assert parsed.instructions[1].hard == hard.id
    assert block["version"] != base["version"]
    text = block_text(block, {}, "pt") or ""
    assert "exigido: não propor acordo (lawsuit case-1)" in text
    assert text.count("acordo") == 2  # the hard entry of an instruction is said once, as the instruction


def test_no_instruction_leaves_the_block_as_it_was() -> None:
    base = build(Body(at=NOW), for_="self", session=None, now=NOW, catalog=Catalog())
    assert with_instructions(base, []) is base


def test_a_correction_withdraws_an_instruction_and_only_a_later_saying_brings_it_back() -> None:
    said = {"kind": "preference", "attr": "", "op": "eq", "values": [], "strength": "must_not",
            "scope": "object", "object": REF, "text": "não propor acordo", "audience": "shared"}  # fmt: skip
    body = fold(None, [Entry("x:1", NOW, said)], NOW, Catalog())
    [listed] = [i for i in inferred(body, Catalog()) if i.kind == "instruction"]
    assert (listed.token, listed.ref, listed.value) == (stated_token("x:1"), REF, "não propor acordo")
    body = suppress(body, listed.token, NOW)
    assert body.stated == []
    assert fold(body, [Entry("x:1", NOW, said)], NOW, Catalog()).stated == []
    later = NOW.replace(hour=13)
    assert len(fold(body, [Entry("x:1", later, said)], later, Catalog()).stated) == 1


def test_past_the_cap_the_block_keeps_the_newest_instructions() -> None:
    """The cap kept the 50 oldest: an instruction just given to an account with 50 older ones was cut."""
    from dataclasses import replace
    from datetime import timedelta

    from niadra.domain.compile.constraints import MAX_INSTRUCTIONS

    base = build(Body(at=NOW), for_="self", session=None, now=NOW, catalog=Catalog())
    rows = [
        (REF, replace(instruction(f"regra {n}"), row_id=f"r-{n}", said_at=NOW + timedelta(minutes=n)))
        for n in range(MAX_INSTRUCTIONS + 1)
    ]
    texts = [i["text"] for i in with_instructions(base, rows)["instructions"]]
    assert len(texts) == MAX_INSTRUCTIONS
    assert texts[-1] == f"regra {MAX_INSTRUCTIONS}"
    assert "regra 0" not in texts


def test_past_its_sizes_the_block_keeps_every_restriction_and_says_what_it_left_out() -> None:
    # Past 50 instructions the block kept the newest 50 and said nothing; an old restriction
    # ("não falar de valores com a esposa", held with one person) was the first to go.
    wife = replace(instruction("não falar de valores com a esposa"), row_id="wife", only_with="Clara")
    others = [
        (
            REF,
            replace(
                instruction(f"instrução {n}"), row_id=f"n{n}", strength="must", said_at=NOW.replace(minute=n)
            ),
        )
        for n in range(60)
    ]
    base = build(Body(at=NOW), for_="self", session=None, now=NOW, catalog=Catalog())
    block = with_instructions(base, others, [(UUID(int=9), wife)])
    assert len(block["instructions"]) == 61  # 50 was the cap: all 61 now
    assert "omitted" not in block
    small = constraints.MAX_INSTRUCTIONS
    try:
        constraints.MAX_INSTRUCTIONS = 10
        capped = with_instructions(base, others, [(UUID(int=9), wife)])
    finally:
        constraints.MAX_INSTRUCTIONS = small
    texts = [i["text"] for i in capped["instructions"]]
    assert "não falar de valores com a esposa" in texts  # the oldest, kept: a restriction
    assert len(texts) == 10
    assert capped["omitted"] == {"instructions": 51}
    parsed = ConstraintsBlock.model_validate({**capped, "text": ""})
    assert parsed.omitted == {"instructions": 51}
    assert "fora deste bloco por tamanho: 51 instruções" in (block_text(capped, {}, "pt") or "")


def test_past_its_hard_size_the_block_keeps_the_restrictions_first_and_counts_the_rest() -> None:
    # Past 100 hard entries the block kept the oldest 100 and said nothing of the rest.
    def said(n: int, strength: str) -> Stated:
        return Stated(
            key=f"k{n}", attr="order.color", op="eq", values=[f"c{n}"], strength=strength, scope="persistent",
            source="stated", said_at=NOW.replace(second=n % 60, minute=n // 60),
        )  # fmt: skip

    entries = [said(n, "must") for n in range(1, 120)] + [said(0, "must_not")]
    block = build(Body(at=NOW, stated=entries), for_="self", session=None, now=NOW, catalog=Catalog())
    assert len(block["hard"]) == 100
    assert any(h["op"] == "ne" and h["values"] == ["c0"] for h in block["hard"])  # the restriction, oldest
    assert block["omitted"] == {"hard": 20}
    assert "fora deste bloco por tamanho: 20 exigências" in (block_text(block, {}, "pt") or "")
