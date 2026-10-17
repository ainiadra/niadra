"""What a client's agent reads of a case, from the blocks the attendant got in juridico-sandbox (05/10/2026):
a promise as a promise with its date, every promise and instruction of the case, the case's fields by
their labels and dates, and the case by the number people write instead of an opaque id."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from niadra.config.object_types import ObjectTypeRegistry
from niadra.domain.compile import CompileInput, MemorySnapshot, Variant, compile_pack
from niadra.domain.compile.candidates import object_text
from niadra.domain.compile.compiler import _Compilation
from niadra.domain.compile.state_view import ObjectLine, bookkeeping, render, summarized
from niadra.domain.compile.templates import Formatter
from niadra.domain.memory.bound import MAX_ROWS, BoundRow, ObjectMemory, in_text
from niadra.domain.records import BusinessObject, ObjectKey
from niadra.domain.vocabulary import Audience, MemoryAudience, OpenItemKind, Speaker, Verification
from niadra.testing.cell import BILLING_SOURCE
from tests.unit.compile.factories import PHONE, T0, marina, settings, uid

CASE = ObjectKey("lawsuit", "cases", "49facdcd-6cd6-4fa6-a38c-9db59c95f614")
NOW = datetime(2026, 10, 5, 22, 0, tzinfo=UTC)
LAWYER = UUID("0192f5a0-0000-7000-8000-0000000000aa")


def item(
    text: str, at: int, due: datetime | None = None, kind: OpenItemKind = OpenItemKind.PROMISE
) -> BoundRow:
    return BoundRow(
        kind="item",
        row_id=str(uuid4()),
        text=text,
        origin_handle_id=LAWYER,
        said_at=datetime(2026, 10, 5, 21, at, tzinfo=UTC),
        audience=MemoryAudience.SHARED,
        item_kind=kind,
        due_at=due,
    )


def test_a_request_said_twice_keeps_the_date_one_of_them_gave() -> None:
    # Q2: the lawyer's request had "até 06/10"; the attendant relayed it without the date, newer, and the line
    # said "pedido: Enviar extratos do sistema de vendas e histórico do SAC" with no date at all.
    due = datetime(2026, 10, 6, 3, tzinfo=UTC)
    text = "Enviar extratos do sistema de vendas e histórico do SAC"
    memory = ObjectMemory(
        CASE,
        uuid4(),
        rows=(item(text, 48, kind=OpenItemKind.REQUEST), item(text, 46, due, kind=OpenItemKind.REQUEST)),
    )
    [said] = in_text(memory, NOW).rows
    assert said.due_at == due
    line = render([ObjectLine(ref="lawsuit cases:c-1", bound=[said])], "pt-BR", "America/Sao_Paulo") or ""
    assert f"pedido até 06/10/2026: {text}" in line


def test_every_promise_of_a_case_is_on_its_line() -> None:
    # Three of each kind was the cap: a fourth promise to the client was one the agent never read.
    rows = tuple(item(f"Promessa número {n} ao cliente", n) for n in range(6))
    said = in_text(ObjectMemory(CASE, uuid4(), rows=rows), NOW).rows
    assert len(said) == 6 < MAX_ROWS
    line = render([ObjectLine(ref="lawsuit cases:c-1", bound=said)], "pt-BR", "UTC") or ""
    assert line.count("promessa: Promessa número") == 6


def test_a_case_on_the_pack_goes_by_its_number_and_its_fields_by_their_labels() -> None:
    # Q12's turn line: "legal case 49facdcd-... · last_movement_on: 2026-10-05 · responsible_lawyer_name:
    # Diego Assis · claim_amount: 56000.0 · cnj: 1022345-26.2026.8.26.0100".
    obj = BusinessObject(
        object_id=uuid4(),
        key=CASE,
        state={
            "last_movement_on": "2026-10-05",
            "responsible_lawyer_name": "Diego Assis",
            "claim_amount": 56000.0,
            "cnj": "1022345-26.2026.8.26.0100",
            "assigned_lawyer": "7b1c2d3e-0000-4000-8000-000000000001",
        },
        as_of=NOW,
        source_id=uuid4(),
    )
    labels = {
        "last_movement_on": "Última movimentação",
        "responsible_lawyer_name": "Advogado responsável",
        "claim_amount": "Valor da causa",
        "cnj": "Número CNJ",
    }
    text = object_text(
        obj,
        Formatter("America/Sao_Paulo", "pt-BR", current_year=2026),
        order=("last_movement_on", "responsible_lawyer_name", "claim_amount", "cnj"),
        labels=labels,
        dates={"last_movement_on"},
        names=(("cnj", "responsible_lawyer_name"), frozenset(), frozenset()),
    )
    assert text == (
        "lawsuit [Número CNJ 1022345-26.2026.8.26.0100] · Última movimentação: 05/10 · "
        "Advogado responsável: Diego Assis · Valor da causa: 56000.00"
    )


def test_a_case_with_no_number_to_go_by_keeps_its_id_on_the_pack() -> None:
    obj = BusinessObject(object_id=uuid4(), key=CASE, state={"court": "TJSP"}, as_of=NOW, source_id=uuid4())
    text = object_text(
        obj,
        Formatter("UTC", "pt-BR"),
        names=(("court",), frozenset(), frozenset()),
        declared=frozenset({"court"}),
    )
    assert text == f"lawsuit {CASE.external_id} · court: TJSP"


def test_a_child_that_says_only_bookkeeping_is_a_count_on_its_parent() -> None:
    case = ObjectLine(ref="lawsuit cases:c-1", state="ativo", name="Número CNJ 0801234-67.2026.8.26.0100")
    files = [
        ObjectLine(
            ref=f"case_document cases:d-{n}",
            fields=[("Verificação de segurança", "clean"), ("Método de extração", "texto_nativo")],
            parents=[("lawsuit cases:c-1", "Número CNJ 0801234-67.2026.8.26.0100")],
        )
        for n in range(3)
    ]
    deadline = ObjectLine(
        ref="deadline cases:p-1",
        state="cumprido",
        dates=[(None, "2026-10-27")],
        parents=[("lawsuit cases:c-1", "Número CNJ 0801234-67.2026.8.26.0100")],
    )
    lines = [case, *files, deadline]
    kept = summarized(lines, [bookkeeping(line, measured=False) for line in lines])
    text = render(kept, "pt-BR", "America/Sao_Paulo") or ""
    body = text.splitlines()[1:-1]
    assert body == [
        "- lawsuit cases:c-1 [Número CNJ 0801234-67.2026.8.26.0100]: situação ativo; case document: 3",
        "- deadline cases:p-1 (de Número CNJ 0801234-67.2026.8.26.0100): "
        "situação cumprido; vence em 27/10/2026",
    ]
    # A child with an amount or a date of its own is something a person recognises: it keeps its line.
    assert not bookkeeping(files[0], measured=True)


def test_a_promise_says_by_when_before_its_words_and_apart_from_what_was_done() -> None:
    # Round 8 blocks, gpt-4.1-mini, 10 tries: "promessa: Avisar o cliente da intimação para contestar, até
    # 06/10/2026; cumprido em 06/10/2026: Protocolar a contestação" gave Q17 2 right (the notice read as done,
    # its date as the deadline to contest); the date first and done items apart gave 7; Q4 from 0 to 6.
    said = item("Enviar ao cliente a cópia da sentença.", 26, datetime(2026, 10, 6, 3, tzinfo=UTC))
    done = replace(item("Protocolar a contestação", 27, None), kept_at=NOW)
    line = render([ObjectLine(ref="lawsuit cases:c-1", bound=[said, done])], "pt-BR", "America/Sao_Paulo")
    assert (line or "").splitlines()[1:-1] == [
        "- lawsuit cases:c-1",
        "  - pendências abertas: promessa até 06/10/2026: Enviar ao cliente a cópia da sentença",
        "  - já feitas: Protocolar a contestação (feito em 05/10/2026)",
    ]


def test_a_hearing_on_the_account_goes_by_its_case_with_its_state_and_no_event_bookkeeping() -> None:
    # The r5 block: "[Conta] 05/10 · hearing 3dedea7b-... · Tipo de audiência: conciliation · Relatório
    # pronto: True · last_event_type: hearing.report_prepared · operation: confirm_hearing ·
    # state: realizada".
    hearing = BusinessObject(
        object_id=uuid4(),
        key=ObjectKey("hearing", "cases", "3dedea7b-c67b-49d0-9f11-aa7d1babb016"),
        state={
            "case_id": CASE.external_id,
            "hearing_type": "conciliation",
            "report_ready": True,
            "last_event_type": "hearing.report_prepared",
            "operation": "confirm_hearing",
            "state": "realizada",
        },
        as_of=NOW,
        source_id=uuid4(),
    )
    text = object_text(
        hearing,
        Formatter("UTC", "pt-BR"),
        order=("hearing_type", "report_ready", "case_id"),
        labels={"hearing_type": "Tipo de audiência", "report_ready": "Relatório pronto"},
        parent="Número CNJ 1021234-23.2026.8.26.0224",
        declared=frozenset({"case_id", "hearing_type", "report_ready"}),
    )
    # What the system last recorded reads in words after the name, never as its raw keys.
    assert text == (
        "hearing de Número CNJ 1021234-23.2026.8.26.0224 · report prepared · "
        "Tipo de audiência: conciliation · Relatório pronto: sim · situação: realizada"
    )
    # An operation the type declares as a field is the type's to say.
    declared = object_text(
        hearing,
        Formatter("UTC", "en"),
        order=("operation",),
        parent="CNJ 1",
        declared=frozenset({"case_id", "hearing_type", "report_ready", "operation"}),
    )
    assert "operation: confirm_hearing" in declared
    assert "last_event_type" not in declared


def test_an_objects_line_says_what_its_system_last_recorded() -> None:
    """Benchmark promise_action (09/10/2026, 81% on 30/09 to 20%): with the event's bookkeeping left out, the
    ERP's refund read "dispute [Recibo 23058] · Valor: 112.64" and no agent said the refund went through."""
    dispute = BusinessObject(
        object_id=uuid4(),
        key=ObjectKey("dispute", "erp", "23805"),
        state={"amount": "112.64", "receipt": "23058", "last_event_type": "fee.refunded"},
        as_of=NOW,
        source_id=uuid4(),
    )
    text = object_text(dispute, Formatter("UTC", "pt-BR"), labels={"amount": "Valor"}, declared={"amount"})
    assert text.startswith("dispute 23805 · fee refunded · Valor: 112.64")
    # The object's own type goes, and a mere change says nothing.
    reimbursed = replace(
        dispute, key=ObjectKey("claim", "erp", "1"), state={"last_event_type": "claim.reimbursed"}
    )
    assert object_text(reimbursed, Formatter("UTC", "en")) == "claim 1 · reimbursed"
    updated = replace(dispute, key=ObjectKey("claim", "erp", "1"), state={"last_event_type": "claim.updated"})
    assert object_text(updated, Formatter("UTC", "en")) == "claim 1"


def test_a_deadlines_computed_due_date_reads_as_its_due_date_on_the_pack() -> None:
    # Round 10, Q14: "[Conta] 06/10 · deadline de Número CNJ 1022345-... · due_on: 2026-10-13 · situação:
    # cumprido". The date a timer counts from is a date of the type (`date_fields`), and with no label it is
    # the object's due date in the space's words.
    registry = ObjectTypeRegistry.model_validate(
        {
            "types": [
                {
                    "type": "deadline",
                    "ownership": "subject",
                    "mirror_of": {"system": "cases"},
                    "fields": {"published_on": {"type": "date"}, "days": {"type": "number"}},
                    "values": {"due_on": {"rule": "cpc_deadline@v1", "inputs": ["published_on", "days"]}},
                    "timers": {"notice_48h": {"due": "due_on - 48h", "on_fire": ["notify"]}},
                }
            ]
        }
    )
    dates = registry.date_fields()["deadline"]
    assert dates == {"published_on", "due_on"}
    deadline = BusinessObject(
        object_id=uuid4(),
        key=ObjectKey("deadline", "cases", "22ac5722-bfdb-4450-aa40-d1a1566e0194"),
        state={"due_on": "2026-10-13", "days": 5, "state": "cumprido"},
        as_of=NOW,
        source_id=uuid4(),
    )
    named = registry.declared_names()["deadline"]
    text = object_text(
        deadline, Formatter("UTC", "pt-BR", 2026), dates=dates, parent="Número CNJ 1022345", declared=named
    )
    assert "vence em: 13/10" in text
    assert "due_on" not in text
    english = object_text(deadline, Formatter("UTC", "en", 2026), dates=dates, parent="CNJ 1", declared=named)
    assert "due: " in english
    assert "due_on" not in english


def test_the_firms_own_word_reads_as_the_teams_and_one_held_with_one_person_names_them() -> None:
    # The r5 block: "instrução: Não propor acordo; a diretoria decidiu levar o caso a julgamento, pois o laudo
    # culpa o autor (cliente)", said by the lawyer at the firm's assistant; models read the client as the
    # plaintiff the report blames.
    said = replace(
        item("Não propor acordo; a diretoria decidiu levar o caso a julgamento", 40),
        kind="instruction",
        item_kind=None,
        role="owner",
        speaker=Speaker.HUMAN_AGENT,
    )
    clara = replace(
        said, row_id=str(uuid4()), text="Não falar de valores com Clara", speaker=None, only_with="Clara"
    )
    line = render([ObjectLine(ref="lawsuit cases:c-1", bound=[said, clara])], "pt-BR", "UTC") or ""
    assert "instrução: Não propor acordo; a diretoria decidiu levar o caso a julgamento (equipe)" in line
    assert "(cliente)" not in line.split("Clara")[0]
    assert (
        "instrução se quem fala for Clara: Não falar de valores com Clara "
        "(Com qualquer outro contato, não se aplica.)"
    ) in line


def test_a_case_and_its_hearing_on_the_pack_go_by_the_cases_number() -> None:
    """The type's to-one relation names the hearing's case, and the case's written key names both lines: the
    hearing's opaque id says nothing."""
    registry = ObjectTypeRegistry.model_validate(
        {
            "types": [
                {
                    "type": "lawsuit",
                    "ownership": "subject",
                    "mirror_of": {"system": "court"},
                    "fields": {"cnj": {"type": "string", "label": "Número CNJ"}},
                },
                {
                    "type": "hearing",
                    "ownership": "subject",
                    "mirror_of": {"system": "court"},
                    "fields": {"case_id": {"type": "string"}, "report_ready": {"type": "bool"}},
                    "relations": {"case": {"type": "lawsuit", "via": "case_id"}},
                },
            ]
        }
    )
    assert registry.parent_fields() == {"hearing": (("case_id", "lawsuit"),)}
    case = BusinessObject(
        uid(0x801), CASE, {"cnj": "1021234-23.2026.8.26.0224"}, T0 - timedelta(days=1), BILLING_SOURCE, PHONE
    )
    hearing_key = ObjectKey("hearing", "cases", "3dedea7b-c67b-49d0-9f11-aa7d1babb016")
    hearing = BusinessObject(
        uid(0x802),
        hearing_key,
        {"case_id": CASE.external_id, "report_ready": True},
        T0 - timedelta(days=1),
        BILLING_SOURCE,
        PHONE,
    )
    snapshot = replace(marina(), objects=(case, hearing))
    pack = compile_pack(
        CompileInput(
            snapshot,
            Variant(verification=Verification.V2),
            settings(
                name_fields=registry.name_fields(),
                field_labels=registry.field_labels(),
                parent_fields=registry.parent_fields(),
            ),
        )
    )
    assert "hearing de Número CNJ 1021234-23.2026.8.26.0224" in pack.text
    assert hearing_key.external_id not in pack.text


def test_an_item_never_repeats_its_own_due_day_inside_its_words() -> None:
    # Q15, round 6: "cumprido em 06/10/2026: Juntar a procuração atualizada até 07/10/2026" was told as a
    # filing still to come. Its own due day is said beside it; another day in its words stays as said.
    due = datetime(2026, 10, 7, 3, tzinfo=UTC)
    kept = replace(item("Juntar a procuração atualizada até 07/10/2026", 40, due), kept_at=NOW)
    pending = item("Enviar cópia da decisão até o dia 7/10.", 41, due)
    other = item("Protocolar a réplica até 09/10/2026", 42, due)
    lines = [ObjectLine(ref="lawsuit cases:c-1", bound=[kept, pending, other])]
    line = render(lines, "pt-BR", "America/Sao_Paulo") or ""
    assert (
        "  - já feitas: Juntar a procuração atualizada (feito em 05/10/2026; data combinada 07/10/2026)"
        in line
    )
    assert "Juntar a procuração atualizada até" not in line
    assert "promessa até 07/10/2026: Enviar cópia da decisão" in line
    assert "promessa até 07/10/2026: Protocolar a réplica até 09/10/2026" in line


def test_a_kept_item_keeps_the_day_it_was_due_by() -> None:
    # Q2, round 9: the request was relayed and kept, and its line lost "até 06/10"; "até quando?" got "não há
    # prazo". A kept item with a due says it beside the day it was kept, in the state block and the pack.
    due = datetime(2026, 10, 6, 3, tzinfo=UTC)
    asked = replace(item("Pedir extratos do sistema de vendas e histórico do SAC", 43, due), kept_at=NOW)
    plain = replace(item("Protocolar a contestação", 44, None), kept_at=NOW)
    lines = [ObjectLine(ref="lawsuit cases:c-1", bound=[asked, plain])]
    line = render(lines, "pt-BR", "America/Sao_Paulo")
    assert (
        "  - já feitas: Pedir extratos do sistema de vendas e histórico do SAC (feito em 05/10/2026; "
        "data combinada 06/10/2026); Protocolar a contestação (feito em 05/10/2026)"
    ) in (line or "")
    english = render([ObjectLine(ref="lawsuit cases:c-1", bound=[asked])], "en-US", "America/Sao_Paulo")
    assert "(done on 2026-10-05; agreed date 2026-10-06)" in (english or "")


def test_a_due_day_is_the_day_in_the_spaces_timezone() -> None:
    fmt = Formatter("America/Sao_Paulo", "pt-BR", 2026)
    assert fmt.local_date(datetime(2026, 10, 7, 2, tzinfo=UTC)).isoformat() == "2026-10-06"


def test_an_open_item_past_its_due_says_it_is_overdue_after_its_due_date() -> None:
    # juridico-zero (07/10/2026): "promessa até 06/10/2026: Pedir extratos ..." read on the 7th was taken as
    # still in time, and "promessa atrasada desde 06/10/2026" was not read as the date it was due by ("até
    # quando?": 4 of 10, round 3). The due date stays the due date, and the line says it is late after it:
    # "até 06/10/2026 (atrasada)", 10 of 10. The same on the pack.
    late = replace(
        item("Pedir extratos do sistema de vendas ao cliente", 26, datetime(2026, 10, 6, 3, tzinfo=UTC)),
        overdue=True,
    )
    line = render([ObjectLine(ref="lawsuit cases:c-1", bound=[late])], "pt-BR", "America/Sao_Paulo")
    assert (
        "  - pendências abertas: promessa até 06/10/2026 (atrasada): Pedir extratos do sistema de vendas ao "
        "cliente" in (line or "").splitlines()
    )
    assert "desde" not in (line or "")
    english = render([ObjectLine(ref="lawsuit cases:c-1", bound=[late])], "en-US", "America/Sao_Paulo")
    assert "promise by 2026-10-06 (overdue): Pedir extratos" in (english or "")
    pack = _Compilation(CompileInput(marina(), Variant(verification=Verification.V2), settings()))
    assert (
        pack._bound_text(late)
        == "promessa até 06/10 (atrasada): Pedir extratos do sistema de vendas ao cliente"
    )


def test_a_date_the_type_does_not_declare_is_written_as_the_space_writes_dates_with_its_inferred_word() -> (
    None
):
    # juridico-zero: "Disponibilizado em: 2026-10-05" on a pack line, the field worded but not declared.
    deadline = BusinessObject(
        object_id=uuid4(),
        key=ObjectKey("deadline", "cases", "d-1"),
        state={"available_on": "2026-10-05", "addressee_is_client": True},
        as_of=NOW,
        source_id=uuid4(),
    )
    text = object_text(
        deadline,
        Formatter("UTC", "pt-BR"),
        labels={"available_on": "Disponibilizado em"},
        declared=frozenset(),
    )
    assert "Disponibilizado em: 05/10" in text
    assert "2026-10-05" not in text


def _cases_with_an_instruction(n: int) -> MemorySnapshot:
    """`n` cases of one customer, the oldest holding the team's "Não propor acordo neste processo"."""
    cases = tuple(
        BusinessObject(
            uid(0x900 + i),
            ObjectKey("lawsuit", "cases", f"c-{i}"),
            {
                "cnj": f"100{i}234-23.2026.8.26.0224",
                "court": "TJSP",
                "claim_amount": 1000.0 * i,
                "subject_matter": "Indenização por dano moral " * 2,
            },
            T0 - timedelta(days=i),
            BILLING_SOURCE,
            PHONE,
        )
        for i in range(n)
    )
    told = BoundRow(
        kind="instruction",
        row_id=str(uuid4()),
        text="Não propor acordo neste processo",
        origin_handle_id=LAWYER,
        said_at=T0 - timedelta(days=20),
        audience=MemoryAudience.SHARED,
        speaker=Speaker.HUMAN_AGENT,
        strength="must_not",
    )
    memory = ObjectMemory(cases[-1].key, cases[-1].object_id, rows=(told,))
    return replace(marina(), objects=cases, object_memory=(memory,))


def test_an_instruction_about_a_case_is_never_left_out_by_the_budget() -> None:
    """A must-not instruction rode on its case's line, and a line the knapsack left out took it along: with
    eight cases in the chat view, "Não propor acordo neste processo" was nowhere in the pack, and out of
    focus the constraints block does not carry it either. It is a line of its own now, said whatever the
    budget, once, by the case's number."""
    for view in ("chat", "voice"):
        pack = compile_pack(
            CompileInput(
                _cases_with_an_instruction(8),
                Variant(view=view, verification=Verification.V2, audience=Audience.CUSTOMER_AGENT),
                settings(name_fields={"lawsuit": (("cnj",), frozenset(), frozenset())}),
            )
        )
        assert pack.text.count("Não propor acordo neste processo") == 1, view
        assert (
            "lawsuit [cnj 1007234-23.2026.8.26.0224] · instrução: Não propor acordo neste processo (equipe)"
            in (pack.text)
        )
        # Before the cases it is about: what must not be done opens the block.
        assert "Não propor acordo neste processo" in pack.text.splitlines()[2], view
        # The measurement still finds what the pack carried.
        [entry] = [i for i in pack.manifest["items"] if i["kind"] == "instruction"]
        assert entry["bound"][0]["kind"] == "instruction"
        assert entry["object_ref"]["id"] == "c-7"


def test_an_item_reads_the_same_on_the_pack_and_in_the_state_block() -> None:
    """The pack said a kept item "Protocolar a contestação., feita em 07/10, data combinada 06/10" and the
    state block "Protocolar a contestação (feito em 07/10/2026; data combinada 06/10/2026)": two wordings of
    one item, a stray period on the pack. One wording now, each view writing dates as it writes them."""
    due = datetime(2026, 10, 6, 3, tzinfo=UTC)
    kept = replace(item("Protocolar a contestação.", 40, due), kept_at=datetime(2026, 10, 7, 15, tzinfo=UTC))
    loose = item("Enviar comprovante.", 41)
    pack = _Compilation(CompileInput(marina(), Variant(verification=Verification.V2), settings()))
    assert pack._bound_text(kept) == "Protocolar a contestação (feito em 07/10; data combinada 06/10)"
    assert pack._bound_text(loose) == "promessa: Enviar comprovante"
    block = render([ObjectLine(ref="lawsuit cases:c-1", bound=[kept, loose])], "pt-BR", "America/Sao_Paulo")
    assert "Protocolar a contestação (feito em 07/10/2026; data combinada 06/10/2026)" in (block or "")
    assert "promessa: Enviar comprovante\n" in f"{block}\n"
    assert ".," not in pack._bound_text(kept)


def test_a_space_in_a_language_no_pack_is_written_in_reads_every_block_in_english() -> None:
    """A Spanish space read its pack in English and its state block in Spanish: one rule for both now."""
    line = [ObjectLine(ref="lawsuit cases:c-1", state="open", bound=[item("Send the copy", 40)])]
    assert render(line, "es-MX", "UTC") == render(line, "en-US", "UTC")


def test_the_same_words_held_with_two_people_are_two_instructions() -> None:
    def told(who: str) -> BoundRow:
        return BoundRow(
            kind="instruction",
            row_id=f"i-{who}",
            text="Não falar de valores dos processos",
            origin_handle_id=LAWYER,
            said_at=NOW,
            audience=MemoryAudience.SHARED,
            only_with=who,
        )

    said = in_text(ObjectMemory(CASE, uuid4(), rows=(told("Clara"), told("Paulo"))), NOW).rows
    assert sorted(r.only_with or "" for r in said) == ["Clara", "Paulo"]


def test_a_line_and_the_block_say_how_much_memory_they_leave_out() -> None:
    # A line said three observations and the block twenty objects, and neither said
    # there was more, so an agent took them for everything there was.
    kept_on = datetime(2026, 10, 4, 12, tzinfo=UTC)
    done = replace(item("Protocolar a contestação", 1), kept_at=kept_on)
    line = ObjectLine(ref="lawsuit cases:c-1", bound=[done], more={"said": 4, "done": 5})
    text = render([line], "pt-BR", "UTC", more=81) or ""
    assert "mais 4 registros (consulte o histórico)" in text
    assert "já feitas: Protocolar a contestação (feito em 04/10/2026); mais 5" in text
    assert text.splitlines()[-2] == "- mais 81 objetos deste cliente fora desta lista (consulte o histórico)"
    english = render([line], "en", "UTC", more=2) or ""
    assert "4 more records (see the history)" in english
    assert "- 2 more objects of this customer off this list (see the history)" in english
