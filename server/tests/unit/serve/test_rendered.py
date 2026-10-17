"""`POST /v1/context` renders its answer itself, while the receipt is confirmed: the bytes must be the ones
FastAPI wrote for the same model under `response_model_exclude_none`."""

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from niadra.adapters.inbound.http.read import rendered
from niadra.contracts.context import (
    ContextPack,
    ContextResponse,
    LiveTurn,
    PackGuard,
    PackSection,
    PackSlot,
    PackStamp,
    VerificationResult,
)
from niadra.domain.vocabulary import DeliveryPath, EventKind, Verification

AT = datetime(2026, 9, 28, 12, 30, 15, 123456, tzinfo=UTC)


def answer() -> ContextResponse:
    return ContextResponse(
        text='<contexto fonte="niadra">\n[Cliente] Marina · São Paulo · "fibra 500"\n</contexto>',
        variables={"b0": "São dados sobre o cliente", "customer": "Marina"},
        version="g7",
        etag="p-abc123",
        manifest_hash="m" * 64,
        as_of=AT,
        lag_seconds=0.125,
        verification=VerificationResult(
            requested=Verification.V2, effective=Verification.V1, reason="not_proven"
        ),
        withheld=3,
        live=[
            LiveTurn(
                at=AT,
                channel="whatsapp",
                kind=EventKind.MESSAGE,
                speaker="customer",
                text="Oi, é sobre a fatura   de setembro",
                source_id="0192f5a0-0000-7000-8000-00000000c001",
            )
        ],
        live_complete=True,
        slots="[Do histórico] a visita foi remarcada",
        guards=[PackGuard(id="v1", value_type="date", value="2026-10-01")],
        timing={"privacy": 0.012, "resolve": 0.031, "pack": 1.0, "receipt": 1e-05},
        path=DeliveryPath.T0,
        pack=ContextPack(
            view="voice",
            verification=Verification.V1,
            withheld=3,
            as_of=AT,
            preamble="São dados sobre o cliente",
            sections=[PackSection(name="customer", label="Cliente", layer="stable", lines=["Marina"])],
            variables={},
            stamp=PackStamp(etag="p-abc123", version="g7", as_of=AT, manifest_hash="m" * 64),
            slots=[PackSlot(section="episodes", id="e1", channels=["exact"], text="visita remarcada")],
        ),
    )


def test_the_answer_renders_to_the_bytes_fastapi_writes() -> None:
    app = FastAPI()

    @app.get("/fastapi", response_model=ContextResponse, response_model_exclude_none=True)
    async def by_fastapi() -> ContextResponse:
        return answer()

    written = TestClient(app).get("/fastapi").content
    assert rendered(answer()) == written
