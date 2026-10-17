import asyncio
import json
import time
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from niadra.adapters.outbound.models.http import PER_TEXT_S, QUERY_TIMEOUT_S, HttpModelsClient
from niadra.domain.common.errors import ModelsRefused, Unavailable
from niadra.ports.infra import ModelsClient, PiiSpan
from tests.latency import within
from tests.unit.models_client.pod import EmulatedPod

BASE = "http://models.cell.internal:8080"


@pytest.fixture
async def client() -> AsyncIterator[HttpModelsClient]:
    c = HttpModelsClient(BASE, timeout_s=0.2)
    yield c
    await c.aclose()


async def test_implements_the_port(client: HttpModelsClient) -> None:
    port: ModelsClient = client
    assert port is client


@respx.mock
async def test_embed(client: HttpModelsClient) -> None:
    route = respx.post(f"{BASE}/v1/embed").respond(
        json={"vectors": [[0.6, 0.8], [1, 0]], "dim": 2, "model_version": "hash-char35-384@1"}
    )
    vectors, version = await client.embed(["a", "b"])
    assert vectors == [[0.6, 0.8], [1.0, 0.0]]
    assert version == "hash-char35-384@1"
    assert json.loads(route.calls.last.request.content) == {"texts": ["a", "b"]}


@respx.mock
async def test_a_question_is_encoded_under_its_own_short_deadline(client: HttpModelsClient) -> None:
    route = respx.post(f"{BASE}/v1/embed").respond(
        json={"vectors": [[0.6, 0.8]], "dim": 2, "model_version": "onnx:m-r1"}
    )
    assert await client.embed_query("fatura") == ([0.6, 0.8], "onnx:m-r1")
    # In the pod's question lane, never behind the write side's batches.
    assert json.loads(route.calls.last.request.content) == {"texts": ["fatura"], "query": True}
    assert route.calls.last.request.extensions["timeout"]["read"] == pytest.approx(QUERY_TIMEOUT_S)


@respx.mock
async def test_a_busy_encoder_fails_a_question_in_about_a_tenth_of_a_second(client: HttpModelsClient) -> None:
    async def busy(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2)
        return httpx.Response(200, json={"vectors": [[1.0]], "dim": 1, "model_version": "v"})

    respx.post(f"{BASE}/v1/embed").mock(side_effect=busy)
    started = time.monotonic()
    with pytest.raises(Unavailable, match="timed out"):
        await client.embed_query("fatura")
    # The query's 100 ms timeout: even ten times that is below the busy encoder's two seconds.
    within("a question to a busy encoder", time.monotonic() - started, QUERY_TIMEOUT_S)


@respx.mock
async def test_pii(client: HttpModelsClient) -> None:
    respx.post(f"{BASE}/v1/pii").respond(
        json={"spans": [[{"start": 4, "end": 18, "kind": "cpf"}], []], "model_version": "pii-rules@1"}
    )
    assert await client.pii(["CPF 529.982.247-25", "nada"]) == [[PiiSpan(4, 18, "cpf")], []]


@respx.mock
async def test_empty_inputs_do_not_call_the_server(client: HttpModelsClient) -> None:
    assert await client.embed([]) == ([], "")
    assert await client.pii([]) == []
    assert not respx.calls


@respx.mock
async def test_batch_timeouts_grow_with_the_texts(client: HttpModelsClient) -> None:
    # PII shares the pod's threads with the embedding batches: a flat deadline failed an extraction's PII
    # batch while a burst of embeddings kept the pod busy (release 2026.09.28-1750-da68f12).
    embed = respx.post(f"{BASE}/v1/embed").respond(
        json={"vectors": [[1.0]] * 3, "dim": 1, "model_version": "onnx:m-r1"}
    )
    pii = respx.post(f"{BASE}/v1/pii").respond(json={"spans": [[]] * 4, "model_version": "rules-1"})
    await client.embed(["a", "b", "c"])
    await client.pii(["a", "b", "c", "d"])
    assert embed.calls.last.request.extensions["timeout"]["read"] == pytest.approx(0.2 + 3 * PER_TEXT_S)
    assert pii.calls.last.request.extensions["timeout"]["read"] == pytest.approx(0.2 + 4 * PER_TEXT_S)


@respx.mock
async def test_timeout_becomes_unavailable(client: HttpModelsClient) -> None:
    respx.post(f"{BASE}/v1/pii").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(Unavailable, match="timed out"):
        await client.pii(["x"])


@respx.mock
async def test_connection_error_becomes_unavailable(client: HttpModelsClient) -> None:
    respx.post(f"{BASE}/v1/pii").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(Unavailable, match="ConnectError"):
        await client.pii(["x"])


@respx.mock
@pytest.mark.parametrize("status", [404, 500, 503])
async def test_error_status_becomes_unavailable(client: HttpModelsClient, status: int) -> None:
    respx.post(f"{BASE}/v1/embed").respond(status_code=status, json={"detail": "x"})
    with pytest.raises(Unavailable, match=str(status)):
        await client.embed(["x"])


@respx.mock
@pytest.mark.parametrize("status", [413, 422])
async def test_a_refused_input_is_final_not_unavailable(client: HttpModelsClient, status: int) -> None:
    # A retry sends the same texts, and the pod refuses them again: the caller must know it is final.
    respx.post(f"{BASE}/v1/pii").respond(status_code=status, json={"detail": "x"})
    with pytest.raises(
        ModelsRefused, match=f"refused 2 texts, the longest of 11 characters \\({status}\\)"
    ) as err:
        await client.pii(["ana@x.com", "cpf 1234567"])
    assert not isinstance(err.value, Unavailable)
    assert "ana@x.com" not in str(err.value)


@respx.mock
@pytest.mark.parametrize(
    "body",
    [
        {"spans": [], "model_version": "v"},  # fewer results than inputs
        {"spans": [[{"start": 0, "end": 1}]], "model_version": "v"},  # no kind
        {"spans": "none", "model_version": "v"},
    ],
)
async def test_malformed_span_responses_become_unavailable(client: HttpModelsClient, body: object) -> None:
    respx.post(f"{BASE}/v1/pii").respond(json=body)
    with pytest.raises(Unavailable):
        await client.pii(["x"])


@respx.mock
async def test_non_json_and_malformed_bodies_become_unavailable(client: HttpModelsClient) -> None:
    respx.post(f"{BASE}/v1/embed").respond(text="<html>bad gateway</html>")
    respx.post(f"{BASE}/v1/pii").respond(json=["not", "an", "object"])
    with pytest.raises(Unavailable):
        await client.embed(["x"])
    with pytest.raises(Unavailable):
        await client.pii(["x"])


@respx.mock
async def test_errors_never_echo_the_text(client: HttpModelsClient) -> None:
    respx.post(f"{BASE}/v1/pii").respond(status_code=500)
    with pytest.raises(Unavailable) as caught:
        await client.pii(["CPF 529.982.247-25"])
    assert "529" not in str(caught.value)


# The pod's limits (niadra_models.settings): the release of 106d3f6 sent one PII call of 424 texts and the
# pod answered 413 to every attempt of that extraction.


def _long_text(size: int, planted: dict[int, str]) -> str:
    """Sentences of filler up to `size` characters, with each planted value starting near its offset."""
    parts: list[str] = []
    length = 0
    for at, value in sorted(planted.items()):
        while length < at:
            sentence = (
                f"Frase numero {len(parts)} sem nada pessoal.\n"
                if len(parts) % 5 == 4
                else (f"Frase numero {len(parts)} sem nada pessoal. ")
            )
            parts.append(sentence)
            length += len(sentence)
        parts.append(value + " ")
        length += len(value) + 1
    while length < size:
        parts.append("Mais uma frase comum. ")
        length += 22
    return "".join(parts)


@respx.mock
async def test_pii_of_a_text_over_the_limit_finds_what_lies_beyond_it() -> None:
    pod = EmulatedPod().install()
    client = HttpModelsClient(BASE)
    text = _long_text(65_000, {31_000: "cliente2@exemplo.com", 52_000: "4111 1111 1111 1111"})
    [spans] = await client.pii([text])
    assert {(text[s.start : s.end], s.kind) for s in spans} == {
        ("cliente2@exemplo.com", "email"),
        ("4111 1111 1111 1111", "card"),
    }
    assert pod.requests
    assert not pod.refused()
    assert all(r.longest <= 20_000 for r in pod.requests)
    await client.aclose()


@respx.mock
@pytest.mark.parametrize("offset", range(19_000, 20_600, 37))
async def test_a_value_across_a_window_cut_is_found_whole(offset: int) -> None:
    # A card with spaces spans the cut wherever it falls: some window holds it whole.
    EmulatedPod().install()
    client = HttpModelsClient(BASE)
    card = "5500 0000 0000 0004"
    text = "x" * offset + " " + card + " " + "y" * 25_000
    [spans] = await client.pii([text])
    assert [(text[s.start : s.end], s.kind) for s in spans] == [(card, "card")]
    await client.aclose()


@respx.mock
async def test_more_texts_than_a_request_takes_go_in_several_and_come_back_in_order() -> None:
    pod = EmulatedPod().install()
    client = HttpModelsClient(BASE)
    texts = [f"item {i}" + (f" contato{i}@x.com" if i % 100 == 0 else "") for i in range(600)]
    spans = await client.pii(texts)
    assert [i for i, found in enumerate(spans) if found] == [0, 100, 200, 300, 400, 500]
    assert all(texts[i][s.start : s.end] == f"contato{i}@x.com" for i in (0, 300, 500) for s in spans[i])
    assert sorted(r.texts for r in pod.requests) == [88, 256, 256]
    vectors, version = await client.embed(texts)
    assert (len(vectors), version) == (600, "embed-test")
    assert not pod.refused()
    await client.aclose()


@respx.mock
async def test_a_long_text_to_embed_is_cut_to_the_limit() -> None:
    pod = EmulatedPod().install()
    client = HttpModelsClient(BASE)
    vectors, _ = await client.embed(["a" * 50_000])
    assert len(vectors) == 1
    assert [r.longest for r in pod.requests] == [20_000]
    await client.aclose()


@respx.mock
async def test_the_first_refusal_of_several_requests_is_raised() -> None:
    EmulatedPod(max_texts=100).install()
    client = HttpModelsClient(BASE)
    with pytest.raises(ModelsRefused, match="refused 256 texts"):
        await client.pii(["x"] * 300)
    await client.aclose()
