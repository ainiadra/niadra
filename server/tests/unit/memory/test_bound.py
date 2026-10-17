"""Memory bound to an object: the audience rule, what each reader gets, the caps and
the rule that counts an agent's message against an instruction."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from niadra.adapters.memory.objects import InMemoryObjectRepository
from niadra.adapters.memory.store import MemoryStore
from niadra.domain.memory.bound import (
    FINAL_WINDOW,
    MAX_ROWS,
    BoundRow,
    ObjectMemory,
    audience_of,
    for_reader,
    in_text,
    kept,
    reads,
    violates,
)
from niadra.domain.records import ObjectKey
from niadra.domain.vocabulary import Audience, MemoryAudience, OpenItemKind, Sensitivity, Visibility

T0 = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
LAWYER = UUID("0192f5a0-0000-7000-8000-00000000a001")
CASE = ObjectKey("lawsuit", "court", "0001234-56.2026.8.26.0100")
SHARED, INTERNAL = MemoryAudience.SHARED, MemoryAudience.INTERNAL


def row(kind: str, text: str, audience: MemoryAudience, minutes: int = 0, **extra: object) -> BoundRow:
    fields: dict[str, object] = {"row_id": f"{kind}-{text}", **extra}
    return BoundRow(
        kind=kind,  # type: ignore[arg-type]
        text=text,
        origin_handle_id=LAWYER,
        said_at=T0 + timedelta(minutes=minutes),
        audience=audience,
        **fields,  # type: ignore[arg-type]
    )


def test_what_a_customer_facing_source_heard_in_public_is_shared() -> None:
    assert audience_of(Audience.CUSTOMER_AGENT, Visibility.PUBLIC, "observation") is SHARED
    assert audience_of(Audience.HUMAN, Visibility.PUBLIC, "observation") is SHARED
    assert audience_of(Audience.CUSTOMER_AGENT, Visibility.INTERNAL, "observation") is INTERNAL


def test_an_internal_remark_stays_internal_and_a_promise_or_an_instruction_is_shared_by_kind() -> None:
    assert audience_of(Audience.INTERNAL_AGENT, Visibility.PUBLIC, "observation") is INTERNAL
    assert audience_of(Audience.INTERNAL_AGENT, Visibility.INTERNAL, "item") is SHARED
    assert audience_of(Audience.INTERNAL_AGENT, Visibility.PUBLIC, "instruction") is SHARED
    assert audience_of(None, Visibility.PUBLIC, "observation") is INTERNAL


def test_a_customer_facing_agent_reads_only_shared_rows_and_internal_readers_read_both() -> None:
    assert reads(SHARED, Audience.CUSTOMER_AGENT)
    assert not reads(INTERNAL, Audience.CUSTOMER_AGENT)
    for reader in (Audience.INTERNAL_AGENT, Audience.HUMAN, Audience.REVIEWER, Audience.ANALYST):
        assert reads(INTERNAL, reader)
        assert reads(SHARED, reader)
    assert not reads(None, Audience.INTERNAL_AGENT)


def test_for_reader_takes_the_internal_and_the_sensitive_rows_away_and_the_policy_only_takes() -> None:
    memory = ObjectMemory(
        CASE,
        UUID(int=7),
        (
            row("observation", "a tese é fraca", INTERNAL),
            row("instruction", "não propor acordo", SHARED, strength="must_not"),
            row("item", "enviar os extratos ao cliente", SHARED, item_kind=OpenItemKind.PROMISE),
            row("observation", "tratamento em curso", SHARED, sensitivity=Sensitivity.SENSITIVE),
        ),
    )
    [client] = for_reader([memory], Audience.CUSTOMER_AGENT)
    assert {r.text for r in client.rows} == {"não propor acordo", "enviar os extratos ao cliente"}
    [internal] = for_reader([memory], Audience.INTERNAL_AGENT)
    assert {r.text for r in internal.rows} == {
        "a tese é fraca",
        "não propor acordo",
        "enviar os extratos ao cliente",
    }
    [denied] = for_reader([memory], Audience.INTERNAL_AGENT, admits=lambda r: r.kind == "instruction")
    assert [r.text for r in denied.rows] == ["não propor acordo"]


def test_kept_holds_the_newest_twenty_of_each_kind_once() -> None:
    rows = [row("observation", f"o{i}", SHARED, i) for i in range(30)]
    rows += [row("item", "i", SHARED), row("item", "i", SHARED)]
    out = kept(rows)
    said = [r for r in out if r.kind == "observation"]
    assert len(said) == MAX_ROWS
    assert said[0].text == "o29"
    assert len([r for r in out if r.kind == "item"]) == 1


def test_an_instruction_leaves_the_reads_a_week_after_its_object_is_final_or_when_it_expires() -> None:
    expiring = row("instruction", "ligar só de manhã", SHARED, expires_at=T0 + timedelta(days=1))
    lasting = row("instruction", "não propor acordo", SHARED)
    memory = ObjectMemory(CASE, UUID(int=7), (expiring, lasting))
    assert len(memory.instructions(T0)) == 2
    assert memory.instructions(T0 + timedelta(days=2)) == [lasting]
    final = ObjectMemory(CASE, UUID(int=7), (lasting,), final_at=T0)
    assert final.instructions(T0 + FINAL_WINDOW - timedelta(minutes=1)) == [lasting]
    assert final.instructions(T0 + FINAL_WINDOW + timedelta(minutes=1)) == []


def test_the_said_is_compared_to_the_recorded_and_never_replaces_it() -> None:
    said = row("observation", "02/10", SHARED, observes="due_date", recorded="05/10")
    assert said.diverges
    assert not row("observation", "05/10", SHARED, observes="due_date", recorded=" 05/10 ").diverges
    assert not row("observation", "02/10", SHARED, observes="due_date").diverges


def test_an_object_memory_reads_back_the_same_from_its_json() -> None:
    memory = ObjectMemory(
        CASE,
        UUID(int=7),
        (
            row("item", "enviar os extratos", SHARED, item_kind=OpenItemKind.DISPUTE, due_at=T0),
            row("instruction", "não propor acordo", SHARED, strength="must_not", role="lawyer"),
        ),
        focus=True,
        final_at=T0,
    )
    assert ObjectMemory.from_json(memory.to_json()) == memory
    assert memory.ref == "lawsuit:court:0001234-56.2026.8.26.0100"


def test_a_message_that_does_what_the_instruction_forbids_counts_and_one_that_refuses_it_does_not() -> None:
    assert violates("Neste processo, não propor acordo", "must_not", "Podemos propor um acordo de 5 mil?")
    assert not violates("Neste processo, não propor acordo", "must_not", "Não vamos propor acordo agora.")
    assert not violates("Neste processo, não propor acordo", "must_not", "O prazo vence na sexta.")
    assert not violates("enviar os extratos", "must", "Vou enviar os extratos")
    # A contraction negates too: "I don't" is "I do not".
    assert not violates(
        "Do not propose a discount", "must_not", "I don't propose a discount, we keep the price"
    )
    assert violates("Do not propose a discount", "must_not", "We can propose a discount of 10%")


def test_a_line_says_open_items_then_kept_ones_and_the_same_promise_once() -> None:
    # 05/10/2026: the attendant echoed a lawyer's promise and the case's line said it three times, while the
    # lawyer's promise kept the day before was not said at all (Q10).
    rows = (
        row("item", "Enviar comprovante ao financeiro", SHARED, 3),
        row("item", "Enviar comprovante ao financeiro.", SHARED, 2, row_id="echo"),
        row("item", "enviar  comprovante ao financeiro", SHARED, 1, row_id="echo-2"),
        row("item", "Recolher as custas", SHARED, 0, kept_at=T0),
        row("instruction", "não propor acordo", SHARED),
    )
    memory = ObjectMemory(CASE, UUID(int=7), rows)
    said = in_text(memory, T0).rows
    assert [(r.kind, r.text, r.kept_at) for r in said] == [
        ("instruction", "não propor acordo", None),
        ("item", "Enviar comprovante ao financeiro", None),
        ("item", "Recolher as custas", T0),
    ]
    assert memory.open_items() == list(rows[:3])
    # The reader's own items are lines of its pack already.
    assert [r.kind for r in in_text(memory, T0, own={LAWYER}).rows] == ["instruction"]
    assert ObjectMemory.from_json(memory.to_json()) == memory


def test_a_case_keeps_every_open_item_and_every_instruction_and_says_the_overdue_first() -> None:
    # 20 newest of each kind was the cap. A case with 40 open items lost its 20 oldest, the
    # most overdue, and a case with more than 20 instructions lost its oldest restriction; nothing said so.
    due = [T0 + timedelta(days=n - 10) for n in range(40)]
    items = [
        row(
            "item",
            f"Promessa {n}",
            SHARED,
            n,
            due_at=due[n],
            overdue=due[n] < T0,
            item_kind=OpenItemKind.PROMISE,
        )
        for n in range(40)
    ]
    rule = row("instruction", "não falar de valores com a esposa", SHARED, -1, strength="must_not")
    others = [row("instruction", f"instrução {n}", SHARED, n) for n in range(25)]
    memory = ObjectMemory(CASE, UUID(int=7), kept([*items, rule, *others]))
    assert len(memory.open_items()) == 40
    assert rule in memory.of("instruction")
    told = in_text(memory, T0 + timedelta(days=1))
    said = [r for r in told.rows if r.kind == "item"]
    assert len(said) == 40
    # The most overdue first, then by the date each is due.
    assert [r.text for r in said[:3]] == ["Promessa 0", "Promessa 1", "Promessa 2"]
    assert all(r.overdue for r in said[:10])
    assert not said[10].overdue
    assert sum(1 for r in told.rows if r.kind == "instruction") == 26
    assert told.more == {}


def test_a_line_says_how_many_observations_and_kept_items_it_leaves_out() -> None:
    # Three observations on a line, and nothing told the agent there were more.
    said = [row("observation", f"fato {n}", SHARED, n) for n in range(7)]
    done = [
        row("item", f"Feito {n}", SHARED, n, kept_at=T0, item_kind=OpenItemKind.PROMISE) for n in range(25)
    ]
    memory = ObjectMemory(CASE, UUID(int=7), kept([*said, *done]))
    assert len(memory.of("observation")) == 7
    told = in_text(memory, T0)
    assert [r.text for r in told.rows if r.kind == "observation"] == ["fato 6", "fato 5", "fato 4"]
    assert told.more == {"said": 4, "done": 5}
    assert len([r for r in told.rows if r.kind == "item"]) == MAX_ROWS


async def test_an_objects_cap_keeps_its_owner_and_its_people_with_a_role_first_and_counts_the_rest() -> None:
    # The 20 handles an object kept were the first by id, so a lawyer whose words a case is
    # about could be left out by 20 bare participants, and nothing counted them.
    objects = InMemoryObjectRepository(MemoryStore())
    case = uuid4()
    lawyer = UUID("ffffffff-ffff-7fff-bfff-ffffffffffff")  # last by id
    bare = [(UUID(int=n + 1), "participant") for n in range(60)]
    await objects.add_participants(case, [*bare, (lawyer, "lawyer")])
    parts = await objects.participants_of([case], per_object=50)
    assert parts[case][0] == (lawyer, "lawyer")
    assert len(parts[case]) == 50
    assert await objects.participant_count(case) == 61
