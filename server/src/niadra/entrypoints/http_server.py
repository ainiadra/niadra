"""Uvicorn inside our own event loop, so HTTP and the background loops share one shutdown signal."""

import asyncio

import uvicorn
from fastapi import FastAPI

HTTP_PORT = 8000


async def serve_http(app: FastAPI, stop: asyncio.Event, port: int = HTTP_PORT) -> None:
    config = uvicorn.Config(
        app,
        host="0.0.0.0",  # noqa: S104 - inside the container; the proxy is the only way in
        port=port,
        log_config=None,
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips="*",
        timeout_graceful_shutdown=20,
    )
    server = uvicorn.Server(config)
    # Uvicorn captures SIGTERM while it serves and re-raises it on exit, which reaches the loop's
    # handler and sets `stop`; a `stop` set elsewhere (a failed loop) ends the server here.
    serving = asyncio.create_task(server.serve())
    stopping = asyncio.create_task(stop.wait())
    await asyncio.wait({serving, stopping}, return_when=asyncio.FIRST_COMPLETED)
    server.should_exit = True
    stopping.cancel()
    await serving
