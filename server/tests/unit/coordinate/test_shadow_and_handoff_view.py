"""The pure parts of the shadow run and of the `handoff` view (24.3.4): conflicts counted the way a check
would have found them, and the handoff's lines rendered in the space's language, the same bytes every time."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from niadra.domain.compile.handoff import HandoffLines, Holder, render
from niadra.domain.compile.templates import Formatter
from niadra.domain.coordinate.shadow import Message, Policy, run
from niadra.domain.privacy.erasure import ErasureScope, TargetKind

T0 = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)
CUSTOMER = UUID(int=1)


def _sent(n: int, minutes: int, agent: str, conversation: str | None = None) -> Message:
    return Message(
        UUID(int=100 + n), CUSTOMER, "whatsapp", T0 + timedelta(minutes=minutes), True, agent, conversation
    )


def test_the_shadow_run_finds_what_a_check_would_have_deferred_or_refused() -> None:
    heard = Message(UUID(int=99), CUSTOMER, "whatsapp", T0 - timedelta(hours=30), False, "customer", "c0")
    messages = [
        heard,
        _sent(1, 0, "agent:a", "c1"),
        _sent(2, 60, "agent:b", "c2"),  # another agent within a day
        _sent(3, 7 * 60, "agent:a", "c3"),  # 22:00: quiet hours
        _sent(4, 8 * 60, "agent:a", "c3"),  # the same conversation: not a new contact
    ]
    policy = Policy(
        windows={"whatsapp": 24},
        quiet=lambda channel, at: at if at.hour >= 21 else None,
        budget=(2, timedelta(days=7)),
    )
    report = run(messages, policy, days=30, sample=2)
    assert report.conflicts == {
        "concurrent_agents": 3,
        "outside_window": 4,
        "quiet_hours": 2,
        "over_budget": 1,
    }
    assert (report.customers, report.messages, len(report.sample)) == (1, 4, 2)
    assert report.per_thousand_customers_a_month["over_budget"] == 1000.0


def test_the_handoff_view_puts_what_coordination_knows_before_the_customer() -> None:
    lines = HandoffLines(
        source="agent:sales",
        target="queue:closing",
        reason="quer fechar hoje",
        holder=Holder("agent:sales", "agent_active", T0 + timedelta(hours=1)),
        commitments=[("discount", T0 + timedelta(days=3))],
        effects=[("delivery", "done")],
        suppressed=["marketing"],
        vocabulary=["sold", "declined"],
        expected_by=T0 + timedelta(days=1),
    )
    text = render(lines, "[Cliente] Marina\n", Formatter("America/Sao_Paulo", "pt-BR"))
    assert text.split("\n") == [
        "Transbordo de agent:sales para queue:closing.",
        "Motivo: quer fechar hoje.",
        "Com: agent:sales (agent active) até 29/09/2026 13:00.",
        "Em vigor: discount até 02/10/2026.",
        "Já feito: delivery (feito).",
        "Não contatar para: marketing.",
        "Responda com um de: sold, declined, até 30/09/2026 12:00.",
        "",
        "[Cliente] Marina",
    ]
    english = render(lines, "", Formatter("UTC", "en-US"))
    assert english.startswith("Handoff from agent:sales to queue:closing.\nReason: quer fechar hoje.")


def test_the_subjects_an_erasure_found_travel_with_its_scope() -> None:
    scope = ErasureScope(UUID(int=7), TargetKind.HANDLE, (UUID(int=8),), None, None, T0)
    found = scope.with_subject_hashes((b"b" * 32, b"a" * 32)).with_subject_hashes((b"a" * 32,))
    assert found.subject_hashes == (b"a" * 32, b"b" * 32)
    assert ErasureScope.from_json(found.to_json()) == found
