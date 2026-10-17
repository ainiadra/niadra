"""Every turn is read whole: a long turn or a long batch is read in windows, never shortened (memory never
cuts what it was told)."""

from uuid import uuid4

from niadra.domain.extract.prompt import TURN_CHARS_BUDGET, build_prompt, redactable, turns
from niadra.domain.extract.result import EpisodeDraft, ExtractionResult, OpenItemDraft
from niadra.domain.extract.windows import PART_MARK, WINDOW_TURNS, merged, windows
from niadra.domain.vocabulary import Speaker
from tests.unit.extract.helpers import event, material, rules

START = "Por favor, não falem de valores com a minha sócia."
MIDDLE = "Prometo enviar o contrato assinado até sexta-feira."
END = "O prazo para a resposta do recurso vence em 30/10."


def email(chars: int) -> str:
    """An e-mail of about `chars` characters with an instruction at its start, a promise in its middle and a
    deadline at its end."""
    filler = "Segue o relatório do mês com os números de cada filial e as observações da equipe. "
    half = filler * (chars // (2 * len(filler)))
    return f"{START} {half}{MIDDLE} {half}{END}"


def shown(batch_windows: list) -> str:
    return "\n".join(build_prompt(w, rules(), flagged=frozenset(), spans={}).user for w in batch_windows)


def test_a_29000_character_email_is_read_whole_in_one_call() -> None:
    text = email(29_000)
    m = material([event(Speaker.CUSTOMER, text, 1)])
    parts = windows(m)
    assert parts == [m]
    assert text in dict(redactable(m)).values()
    user = build_prompt(m, rules(), flagged=frozenset(), spans={}).user
    for said in (START, MIDDLE, END):
        assert said in user  # the cut at 2,000 characters lost the last two


def test_past_one_calls_budget_a_long_email_is_read_in_parts_that_together_hold_all_of_it() -> None:
    words = " ".join(f"palavra{n}" for n in range(2_500))
    text = f"{START} {words[: len(words) // 2]} {MIDDLE} {words[len(words) // 2 :]} {END}"
    m = material([event(Speaker.CUSTOMER, "Oi, tudo bem?", 1), event(Speaker.CUSTOMER, text, 2)])
    parts = windows(m, budget=8_000)
    assert len(parts) > 2
    user = shown(parts)
    for said in (START, MIDDLE, END):
        assert said in user
    pieces = [e.text or "" for w in parts for e in w.events if (e.text or "").startswith("[part")]
    n = len(pieces)
    assert pieces[0].startswith(PART_MARK.format(k=1, n=n))
    assert pieces[-1].startswith(PART_MARK.format(k=n, n=n))
    # Every character of the e-mail is in some part.
    body = [p.split("] ", 1)[1] for p in pieces]
    assert body[0].startswith(START)
    assert body[-1].endswith(END)
    rebuilt = body[0]
    for piece in body[1:]:
        overlap = next(k for k in range(min(len(piece), len(rebuilt)), 0, -1) if rebuilt.endswith(piece[:k]))
        rebuilt += piece[overlap:]
    assert rebuilt == text
    for w in parts:
        assert sum(len(e.text or "") for e in w.events) <= 8_000 + 64
        # Never two parts of one turn in a window: each part keeps its own PII spans (keyed by the event).
        ids = [e.event_id for e in w.events]
        assert len(ids) == len(set(ids))
        keys = [k for k, _ in redactable(w)]
        assert len(keys) == len(set(keys))


def test_a_session_of_a_thousand_turns_is_read_in_windows_and_no_turn_is_left_out() -> None:
    said = [event(Speaker.CUSTOMER if n % 2 else Speaker.AI_AGENT, f"turno {n}", n) for n in range(1_000)]
    m = material(said)
    assert len(turns(m.sessions[0].events)) == 1_000  # the old reading kept the last 400
    parts = windows(m)
    assert len(parts) >= 3
    assert all(len(w.events) <= WINDOW_TURNS for w in parts)
    read = {e.event_id for w in parts for e in w.events}
    assert read == {e.event_id for e in said}


def test_a_batch_within_one_calls_budget_is_one_call() -> None:
    m = material([event(Speaker.CUSTOMER, "Oi", 1), event(Speaker.AI_AGENT, "Olá", 2)])
    assert windows(m) == [m]
    assert TURN_CHARS_BUDGET >= 100_000


def test_the_windows_of_one_session_merge_into_one_result_each_entry_once() -> None:
    first, second, shared = uuid4(), uuid4(), uuid4()
    promise = OpenItemDraft("Enviar o contrato assinado", "company", (shared,))
    one = ExtractionResult(
        EpisodeDraft("Pediu sigilo de valores.", "request", "contract", "pending", matter="contract_terms"),
        open_items=(promise, OpenItemDraft("Ligar amanhã", "company", (first,))),
        dropped=1,
    )
    two = ExtractionResult(
        EpisodeDraft("Prometeu o contrato; prazo do recurso em 30/10.", "request", "contract", "resolved"),
        open_items=(OpenItemDraft("Enviar o contrato assinado", "company", (second,)),),
        dropped=2,
    )
    whole = merged([one, two])
    assert whole.episode is not None
    assert whole.episode.summary == "Pediu sigilo de valores. Prometeu o contrato; prazo do recurso em 30/10."
    assert whole.episode.outcome == "resolved"
    assert whole.episode.matter == "contract_terms"
    assert [i.description for i in whole.open_items] == ["Enviar o contrato assinado", "Ligar amanhã"]
    assert whole.open_items[0].evidence_event_ids == (shared, second)
    assert whole.dropped == 3
