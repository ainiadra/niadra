"""A person named only in the text of a record kept for someone else (5.14.0): an erasure finds it by the
index of raw content, and the record keeps, with the erased person's phone, e-mail and document replaced by
`ERASED`; a payload kept under no handle still goes whole."""

import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from niadra.config.models import HandleExpr, Mapping, MappingRule, WebhookAuth
from niadra.domain.privacy.raw_index import ERASED, body_tokens, handle_tokens, redact_body, redact_text
from niadra.domain.vocabulary import HandleType
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, Cell
from tests.unit.ingest.fakes import build

from .test_raw_index import SECRET, TypedIdentity, customer, forget, unmapped
from .world import World

PHONE = "+5511987654321"
EMAIL = "clara@exemplo.com"
CPF = "12345678909"
OTHER = "+5511900000001"


def webhook(cell: Cell) -> Callable[[dict[str, Any]], Awaitable[None]]:
    """A `paid` payload is kept under the handle its `customer` names; any other type under no handle."""
    config = cell.config.space(SPACE_ID)
    auth = WebhookAuth(mode="url_token", secret_ref=SECRET)
    rule = MappingRule(
        match="type == 'paid'",
        canonical_type="invoice.paid",
        idempotency_key="id",
        occurred_at="at",
        handles=[HandleExpr(type=HandleType.PHONE_E164, value="customer")],
        fields={"note": "note"},
    )
    mapping = Mapping(source_id=BILLING_SOURCE, version="m1", auth=auth, rules=[rule])
    cell.config.put(config.model_copy(update={"mappings": [mapping]}))
    cell.secrets.values[SECRET] = "t0k"
    ingest, _, _ = build(cell)

    async def send(body: dict[str, Any]) -> None:
        await ingest.ingest_webhook(BILLING_SOURCE, {}, json.dumps(body).encode(), {"token": "t0k"})

    return send


def owned_by_others(cell: Cell) -> list[str]:
    return sorted(
        k
        for k in cell.objects.objects
        if k.startswith(f"raw/{SPACE_ID}/") and not k.startswith(f"raw/{SPACE_ID}/_/")
    )


async def test_a_payload_kept_for_someone_else_keeps_with_the_erased_persons_identifiers_redacted(
    cell: Cell, world: World, make_privacy: Any
) -> None:
    profile, (_, email, cpf) = customer(world, PHONE, EMAIL, CPF)
    types = {email: HandleType.EMAIL, cpf: HandleType.GOV_ID_HMAC}
    privacy = make_privacy(identity=TypedIdentity(world, types))
    send = webhook(cell)
    note = "ligar para a Clara no (11) 98765-4321, e-mail Clara@Exemplo.com, CPF 123.456.789-09"
    await send({"type": "paid", "id": "i-1", "at": "2026-09-22T16:59:00Z", "customer": OTHER, "note": note})
    await send({"type": "refunded", "phone": PHONE})
    (kept,) = owned_by_others(cell)
    assert len(unmapped(cell)) == 1

    counts = await forget(cell, privacy, profile, "k-named")

    assert owned_by_others(cell) == [kept], "the payload is someone else's: it stays"
    body = json.loads(cell.objects.objects[kept])
    assert body["customer"] == OTHER
    assert body["note"] == f"ligar para a Clara no {ERASED}, e-mail {ERASED}, CPF {ERASED}"
    assert unmapped(cell) == [], "a payload under no handle that names her still goes whole"
    assert counts["named_payloads_redacted"] == 1
    assert counts["unmapped_payloads"] == 1
    # Indexed by what it names now: a second erasure of the same person finds nothing to redact.
    again = await forget(cell, privacy, profile, "k-named-again")
    assert "named_payloads_redacted" not in again


def test_redaction_matches_the_identifiers_as_the_index_reads_them() -> None:
    target = handle_tokens(
        [(HandleType.PHONE_E164, PHONE), (HandleType.EMAIL, EMAIL), (HandleType.GOV_ID_HMAC, CPF)]
    )
    text, found = redact_text(
        "+55 (11) 98765-4321 / 11987654321 / CLARA@exemplo.com / 123.456.789-09", target
    )
    assert (text, found) == (f"{ERASED} / {ERASED} / {ERASED} / {ERASED}", 4)
    untouched, none = redact_text(f"pedido 734211, telefone {OTHER}, {uuid4()}", target)
    assert none == 0
    assert OTHER in untouched
    body, n = redact_body(json.dumps({"tel": 5511987654321, "deep": [{"mail": EMAIL}]}).encode(), target)
    assert n == 2
    assert json.loads(body) == {"tel": ERASED, "deep": [{"mail": ERASED}]}
    same, zero = redact_body(b"not json, " + OTHER.encode(), target)
    assert (same, zero) == (b"not json, " + OTHER.encode(), 0)


def test_a_phone_is_matched_in_every_form_that_names_its_line() -> None:
    # The handle keeps a Brazilian mobile with its ninth digit; people still write it without, and WhatsApp
    # reports some accounts the old way: each side finds the other, and another line keeps.
    for kept in ((HandleType.PHONE_E164, "+5511998765432"), (HandleType.WA_ID, "551198765432")):
        target = handle_tokens([kept])
        text, found = redact_text("ligar no +55 11 9876-5432 ou (11) 99876-5432; fixo 11 3876-5432", target)
        assert (text, found) == (f"ligar no {ERASED} ou {ERASED}; fixo 11 3876-5432", 2)
        assert body_tokens(b"+55 11 9876-5432") & target
        assert body_tokens(b"+5511998765432") & target
