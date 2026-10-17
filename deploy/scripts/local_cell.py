"""
Helpers of scripts/local-e2e.sh, run with the server checkout's Python:

    python local_cell.py launch <port> <serve args...>   `niadra.entrypoints.serve` on <port>, not 8000
    python local_cell.py proxy <port> <upstreams.json>   the api host's ingress: each path to its deployable
    python local_cell.py blackhole <port>                a server that takes connections and never answers:
                                                         the decision model down, as when it is unreachable
    python local_cell.py burst <port> <bootstrap.json> <n>
                                                         n closed conversations of the e2e's customer, sent
                                                         at once through the api at <port>, and one long
                                                         one: a burst of one customer's work right before
                                                         the check

The proxy routes the way the chart's ingress does on Traefik (values.yaml `exactRoutes` and `routes`): a
whole path first, then the longest prefix as a string (`/v1/context` takes `/v1/context-use` unless a longer
prefix names it), everything else to admin.
"""

import asyncio
import json
import sys

# The chart's values.yaml, which tests/test_routing.py keeps these equal to.
EXACT_ROUTES = {"ingest": ("/v1/turns", "/v1/turns/promote")}
ROUTES = {
    "ingest": (
        "/v1/batch",
        "/v1/ingest",
        "/v1/otel",
        "/v1/media",
        "/v1/feedback",
        "/v1/objects/push",
        "/v1/objects/snapshot",
        "/v1/types",
    ),
    "read": (
        "/v1/context",
        "/v1/history",
        "/v1/objects",
        "/v1/subject-tokens",
        "/mcp",
        "/v1/state",
        "/v1/agent-state",
        "/v1/sdk",
        "/v1/constraints",
        "/v1/coordination",
        "/v1/handoffs",
        "/v1/suppressions",
        "/v1/notifications",
        "/.well-known/niadra-contact-keys.json",
    ),
    "insights": ("/v1/insights", "/mcp/insights"),
    "admin": (
        "/v1/context-use",
        "/v1/objects/coverage",
        "/v1/coordination/reports",
        "/v1/coordination/shadow",
        "/v1/coordination/overview",
    ),
}
DROPPED = {"content-length", "transfer-encoding", "content-encoding", "connection"}


def route(path: str) -> str:
    """The deployable of a path, as Traefik matches an Ingress: an `Exact` path first, then the longest
    `Prefix` as a plain string (Traefik's `PathPrefix`, so `/mcp` takes `/mcpx` too), then admin."""
    for name, paths in EXACT_ROUTES.items():
        if path in paths:
            return name
    best, found = 0, "admin"
    for name, prefixes in ROUTES.items():
        for prefix in prefixes:
            if path.startswith(prefix) and len(prefix) > best:
                best, found = len(prefix), name
    return found


def launch(port: int, args: list[str]) -> None:
    import niadra.entrypoints.http_server as http_server

    http_server.serve_http.__defaults__ = (port,)
    from niadra.entrypoints.serve import main

    main(args)


def proxy(port: int, upstreams: dict[str, str]) -> None:
    import httpx
    import uvicorn

    client = httpx.AsyncClient(timeout=60)

    async def app(scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] != "http":
            return
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        query = scope["query_string"].decode()
        url = upstreams[route(scope["path"])] + scope["path"] + (f"?{query}" if query else "")
        headers = [(k.decode(), v.decode()) for k, v in scope["headers"] if k.decode().lower() != "host"]
        response = await client.request(scope["method"], url, content=body, headers=headers)
        out = [(k.encode(), v.encode()) for k, v in response.headers.items() if k.lower() not in DROPPED]
        out.append((b"content-length", str(len(response.content)).encode()))
        await send({"type": "http.response.start", "status": response.status_code, "headers": out})
        await send({"type": "http.response.body", "body": response.content})

    asyncio.run(uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")).serve())


def blackhole(port: int) -> None:
    async def hold(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read()  # until the client gives up and closes
        writer.close()

    async def serve() -> None:
        server = await asyncio.start_server(hold, "127.0.0.1", port)
        await server.serve_forever()

    asyncio.run(serve())


def burst(port: int, bootstrap: str, count: int) -> None:
    """What an imported history or a re-extraction pass does to one customer: many sessions closing together,
    each one extracted, reconciled, embedded and compiled under the customer's lease, while the e2e asks for
    their invoice. Synthetic, the e2e's own number (the range Anatel reserves for fiction and tests).

    One more conversation is long: 280 turns, over 20,000 characters, with an e-mail past that mark. The
    models pod takes at most 256 texts a request and 20,000 characters a text (413 beyond); the release of
    106d3f6 sent one PII call of 424 texts on 28/09 and retried it until its last attempt."""
    import uuid
    from datetime import UTC, datetime

    import httpx

    with open(bootstrap) as f:
        key = json.load(f)["keys"]["whatsapp"]
    phone = {"type": "phone_e164", "value": "+5511900001234"}
    lines = [
        ("customer", "Oi, o pedido {n} chegou com a embalagem amassada."),
        ("ai_agent", "Sinto muito. Posso abrir a troca do pedido {n}?"),
        ("customer", "Pode, preciso receber ainda esta semana."),
        ("ai_agent", "Combinado: troca do pedido {n} aberta, entrega em 3 dias."),
    ]
    long_text = "Mensagem {k} da conversa longa sobre o pedido {n}, sem nenhum dado pessoal nesta linha."
    long_lines = [
        ("customer" if k % 2 == 0 else "ai_agent", long_text.replace("{k}", str(k))) for k in range(280)
    ]
    long_lines[270] = ("customer", "Para a nota fiscal do pedido {n}, meu email e cliente.longo@example.com")

    def conversation_items(turns: list[tuple[str, str]], n: int) -> list[dict[str, object]]:
        conversation = f"burst-{uuid.uuid4().hex[:8]}"
        now = datetime.now(UTC).isoformat()
        items: list[dict[str, object]] = [
            {
                "type": "event",
                "idempotency_key": f"{conversation}-{j}",
                "channel": "whatsapp",
                "conversation_id": conversation,
                "handles": [phone],
                "speaker": {"role": role},
                "direction": "inbound" if role == "customer" else "outbound",
                "content": {"type": "text", "text": text.format(n=n)},
                "occurred_at": now,
            }
            for j, (role, text) in enumerate(turns)
        ]
        items.append({"type": "conversation.ended", "idempotency_key": f"{conversation}-end",
                      "conversation_id": conversation, "occurred_at": now})
        return items

    async def send() -> None:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30) as api:
            conversations = [conversation_items(lines, 5000 + i) for i in range(count)]
            conversations.append(conversation_items(long_lines, 5000 + count))
            for items in conversations:
                # A new cell answers 401 until the keys reach it and 503 while the space's database is made,
                # as the check's first step waits for.
                for _ in range(150):
                    response = await api.post(
                        "/v1/batch", json={"items": items}, headers={"authorization": f"Bearer {key}"}
                    )
                    if response.status_code not in (401, 503):
                        break
                    await asyncio.sleep(2)
                response.raise_for_status()

    asyncio.run(send())
    print(f"==> a burst of {count} closed conversations of the e2e's customer and a long one")


def history(port: int, bootstrap: str, count: int) -> None:
    """What a long-lived sandbox holds of the e2e's customer: `count` orders the billing system reported over
    many runs, each its own object, so the check reads a customer with more objects than a graph shows."""
    import uuid
    from datetime import UTC, datetime, timedelta

    import httpx

    with open(bootstrap) as f:
        key = json.load(f)["keys"]["billing"]
    phone = {"type": "phone_e164", "value": "+5511900001234"}
    run = uuid.uuid4().hex[:8]
    start = datetime.now(UTC) - timedelta(days=30)
    items = [
        {
            "type": "event",
            "kind": "system_event",
            "idempotency_key": f"history-{run}-{n}",
            "channel": "erp",
            "canonical_type": "order.placed",
            "handles": [phone],
            "object_refs": [{"type": "order", "namespace": "erp", "id": f"HIST-{run}-{n:05d}"}],
            "speaker": {"role": "system"},
            "fields": {"status": "placed"},
            "occurred_at": (start + timedelta(minutes=n)).isoformat(),
        }
        for n in range(count)
    ]

    async def send() -> None:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30) as api:
            for at in range(0, len(items), 200):
                # A new cell answers 401 until the keys reach it and 503 while the space's database is made.
                for _ in range(150):
                    response = await api.post(
                        "/v1/batch", json={"items": items[at : at + 200]}, headers={"authorization": f"Bearer {key}"}
                    )
                    if response.status_code not in (401, 503):
                        break
                    await asyncio.sleep(2)
                response.raise_for_status()

    asyncio.run(send())
    print(f"==> a history of {count} orders of the e2e's customer")


if __name__ == "__main__":
    command, port = sys.argv[1], int(sys.argv[2])
    if command == "blackhole":
        blackhole(port)
    elif command == "burst":
        burst(port, sys.argv[3], int(sys.argv[4]))
    elif command == "history":
        history(port, sys.argv[3], int(sys.argv[4]))
    elif command == "launch":
        launch(port, sys.argv[3:])
    elif command == "proxy":
        with open(sys.argv[3]) as f:
            proxy(port, json.load(f))
    else:
        sys.exit(f"unknown command {command}")
