"""No customer data in logs: a canary value goes through ingestion, extraction, reads, history
search and the failure paths, and never appears in a log line, an error's traceback included."""

import io
import logging
from collections.abc import Iterator
from typing import Any

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.adapters.observability.logging import JsonFormatter
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest, SearchRequest
from niadra.domain.common.errors import NiadraError
from niadra.domain.vocabulary import HandleType, Verification
from niadra.testing.cell import VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow

CANARY = "canario7731"
PHONE = "+5511977317731"
EMAIL = f"{CANARY}@example.org"


@pytest.fixture
def logs() -> Iterator[io.StringIO]:
    """Every record, at every level, through the production formatter."""
    out = io.StringIO()
    handler = logging.StreamHandler(out)
    handler.setFormatter(JsonFormatter())
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield out
    finally:
        root.removeHandler(handler)
        root.setLevel(level)


def remembered() -> dict[str, Any]:
    return {
        "episode": {
            "summary": f"Cliente {CANARY} pediu segunda via",
            "intent": "request",
            "category": "invoice",
            "outcome": "resolved",
            "resolution": f"Enviada para {EMAIL}",
            "sentiment": 0.1,
        },
        "facts": [
            {
                "op": "add",
                "predicate": "preferred_name",
                "value": CANARY,
                "target": None,
                "evidence": ["e1"],
                "valid_at": None,
                "certainty": 0.9,
                "contradicts": [],
                "about": None,
                "object": None,
            }
        ],
        "open_items": [],
        "resolve": [],
        "profile": [],
        "agent_questions": [],
        "agent_claims": [],
    }


class LeakyLlm(ScriptedLlm):
    """A provider whose error message quotes the prompt, as some do."""

    async def complete_json(self, **kwargs: Any) -> Any:
        if not self.answers:
            raise RuntimeError(f"provider refused the prompt: {kwargs.get('user', '')[:200]}")
        return await super().complete_json(**kwargs)


async def test_a_canary_never_reaches_the_logs(cell: Cell, logs: io.StringIO) -> None:
    flow = Flow.build(cell, LeakyLlm(remembered()))
    now = cell.clock.now().isoformat()

    def said(conversation: str, n: int) -> dict[str, Any]:
        return {
            "type": "event",
            "idempotency_key": f"{conversation}-{n}",
            "channel": "whatsapp",
            "conversation_id": f"{conversation}-{CANARY}",
            "handles": [{"type": "phone_e164", "value": PHONE}, {"type": "email", "value": EMAIL}],
            "speaker": {"role": "customer"},
            "content": {"type": "text", "text": f"Me chama de {CANARY}, meu email é {EMAIL}"},
            "occurred_at": now,
        }

    def ended(conversation: str) -> dict[str, Any]:
        name = f"{conversation}-{CANARY}"
        return {"type": "conversation.ended", "idempotency_key": f"{name}-end", "conversation_id": name}

    # Ingestion, with an item the batch refuses because of the value it carries.
    bad = {**said("wa", 9), "handles": [{"type": "phone_e164", "value": f"not-a-phone-{CANARY}"}]}
    await flow.send(WHATSAPP_SOURCE, said("wa", 1), bad, {**ended("wa"), "occurred_at": now})
    await flow.drain()
    # A second conversation whose extraction fails with the prompt in the provider's error.
    await flow.send(WHATSAPP_SOURCE, said("wa2", 1), {**ended("wa2"), "occurred_at": now})
    try:
        await flow.drain()
    except (RuntimeError, NiadraError):
        logging.getLogger("niadra.test").exception("a task failed")

    caller = principal(VOICE_SOURCE, cell.config.spaces()[0])
    subject = Handle(type=HandleType.PHONE_E164, value=PHONE)
    await flow.serve.context(
        caller, ContextRequest(subject=subject, conversation_id=f"call-{CANARY}", query=CANARY, delta=True)
    )
    await flow.serve.search(
        caller,
        SearchRequest(
            subject=subject, query=f"{CANARY} {EMAIL}", verification=Verification.V1, conversation_id="c"
        ),
    )
    text = logs.getvalue()
    assert text  # something was logged, so the check means something
    assert CANARY not in text
    assert "77317731" not in text


def test_a_logged_error_keeps_where_it_failed_and_never_its_message(logs: io.StringIO) -> None:
    def fail() -> None:
        try:
            raise KeyError(CANARY)
        except KeyError as exc:
            raise ValueError(f"duplicate key value ({EMAIL})") from exc

    try:
        fail()
    except ValueError:
        logging.getLogger("niadra.test").exception("insert failed")
    line = logs.getvalue()
    assert "insert failed" in line
    assert "builtins.ValueError" in line
    assert "builtins.KeyError" in line
    assert "in fail" in line
    assert CANARY not in line
