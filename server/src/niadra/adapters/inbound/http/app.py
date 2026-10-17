"""Builds the FastAPI app of one deployable from its route groups (7.1, the "served by" column)."""

import asyncio
import logging
import time
import uuid
from collections.abc import Iterable
from typing import Any

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from niadra.adapters.inbound.http.deprecations import deprecation_headers
from niadra.adapters.inbound.http.errors import (
    handle_http_error,
    handle_niadra_error,
    handle_validation_error,
    problem,
)
from niadra.adapters.observability import metrics
from niadra.adapters.observability.logging import request_id_var
from niadra.domain.common.errors import NiadraError, NotFound

log = logging.getLogger(__name__)

# Headers the edge proxy adds to every request it forwards (Traefik sets the X-Forwarded family).
_FORWARDED = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip")
COMPRESS_FROM = 1400
"""Answers longer than one TCP segment go compressed to a client that accepts gzip. A server sends at most
about 14 KB before the client's first acknowledgement (ten segments, again after an idle pause), so a
longer answer costs one more round trip: a context read with the `state` block (15 KB) took two round trips
from Sao Paulo, 340 ms instead of 190, while gzip makes it 2.6 KB (B22, 03/10/2026)."""
COMPRESS_LEVEL = 6
SLOW_AFTER_S = {metrics.CONTEXT_ROUTE: 0.05, "/v1/batch": 0.08}
"""Past this a request gets a log line, as does every 5xx and 429; other routes after
`SLOW_DEFAULT_S`. A line per request would be millions a day for nothing."""
SLOW_DEFAULT_S = 0.25
ROBOTS = "User-agent: *\nDisallow: /\n"
"""Every API host asks crawlers to stay out, and every answer says `noindex` (X-Robots-Tag)."""


def create_app(
    title: str,
    container: Any,
    routers: Iterable[APIRouter],
    allowed_origins: Iterable[str] = (),
    *,
    compress: bool = False,
    ready: asyncio.Event | None = None,
) -> FastAPI:
    """`compress` gzips the long answers (`COMPRESS_FROM`). Only for APIs whose callers authenticate with a
    bearer key, as the cell's do: a compressed answer that mixes a secret with what a caller wrote leaks
    through its length only when a browser adds the credential by itself (a cookie). `ready` is set once the
    process warmed up (`entrypoints.warmup`): until then `/readyz`, the readiness probe, answers 503 while
    `/healthz`, the liveness probe, answers from the start. Without it the app is ready at once."""
    # The OpenAPI document is exported at build time for the docs site, never served (7.0).
    app = FastAPI(title=title, version="1", openapi_url=None, docs_url=None, redoc_url=None)
    app.state.container = container
    app.add_exception_handler(NiadraError, handle_niadra_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_error)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> Response:
        if ready is not None and not ready.is_set():
            return Response('{"status":"warming"}', status_code=503, media_type="application/json")
        return Response('{"status":"ready"}', media_type="application/json")

    @app.get("/robots.txt", include_in_schema=False)
    async def robots() -> Response:
        # An API host is no page to index: search engines found the documented hosts and crawled them.
        return Response(ROBOTS, media_type="text/plain")

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics(request: Request) -> Response:
        # For the in-cluster scraper, which calls the pod directly: a request that came through the
        # public edge carries its forwarding headers and finds nothing here.
        if any(header in request.headers for header in _FORWARDED):
            raise NotFound("not found")
        body, content_type = metrics.render()
        return Response(body, media_type=content_type)

    for router in routers:
        app.include_router(router)
    if compress:
        # Inside `RequestContext`, which then sees the compressed answer's headers and times it whole.
        app.add_middleware(GZipMiddleware, minimum_size=COMPRESS_FROM, compresslevel=COMPRESS_LEVEL)
    app.add_middleware(RequestContext)
    if origins := list(allowed_origins):
        # The Console runs on its own host and calls the cell and the control plane from the browser.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["authorization", "content-type", "idempotency-key", "x-request-id"],
            # Retry-After is not a CORS-safelisted header: without it here the Console cannot read how long
            # to wait on a 429 or a 503 and falls back to its own guess.
            expose_headers=["x-request-id", "etag", "retry-after", "deprecation", "sunset", "link"],
            max_age=600,
        )
    return app


class RequestContext:
    """Every request's id, timing and error shape, as a plain ASGI middleware: Starlette's
    `BaseHTTPMiddleware` runs each request through a task group and memory streams, a cost the read path
    paid on every call."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        request_id = headers.get("x-request-id") or uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        traceparent = headers.get("traceparent")
        token = request_id_var.set(request_id)
        stats = metrics.start_request()
        started = time.perf_counter()
        answered: list[int] = []

        async def respond(message: Message) -> None:
            if message["type"] == "http.response.start":
                elapsed = time.perf_counter() - started
                stats.open = False
                status: int = message["status"]
                answered.append(status)
                route = _route_template(scope)
                state = scope.get("state", {})
                read: metrics.ReadSummary | None = state.get("read")
                space = _space_of(state)
                timing = f"app;dur={elapsed * 1000:.1f}"
                if route not in ("/metrics", "/healthz", "/readyz"):
                    metrics.HTTP_REQUESTS.labels(route, scope["method"], str(status)).inc()
                    metrics.HTTP_LATENCY.labels(route, scope["method"]).observe(elapsed)
                    if stats.statements:
                        metrics.READ_DB_STATEMENTS.labels(route, metrics.read_kind(read)).inc(
                            stats.statements
                        )
                    if read is not None:
                        metrics.context_read(route, space, read, elapsed)
                        timing += "".join(f", {stage};dur={ms:.1f}" for stage, ms in read.stages.items())
                    if status >= 500 or status == 429 or elapsed > SLOW_AFTER_S.get(route, SLOW_DEFAULT_S):
                        _log_request(scope["method"], route, status, elapsed, space, read, stats)
                extra = MutableHeaders(scope=message)
                extra["x-request-id"] = request_id
                extra["x-robots-tag"] = "noindex, nofollow"
                for name, value in deprecation_headers(scope["method"], route).items():
                    extra[name] = value
                extra["server-timing"] = timing
                if traceparent:
                    extra["traceparent"] = traceparent
            await send(message)

        try:
            await self.app(scope, receive, respond)
        except Exception:
            # An unexpected failure answers like every other error, as problem+json with the request id
            # (and the X-Request-Id and traceparent headers), never a plain-text 500. The traceback goes to
            # the log only: the body carries no detail.
            log.exception("unhandled error on %s %s", scope["method"], _route_template(scope))
            if answered:
                raise
            await problem(500, "internal_error", "internal error", None, request_id)(scope, receive, respond)
        finally:
            request_id_var.reset(token)


def _space_of(state: dict[str, Any]) -> str | None:
    """The internal id of the caller's space, once authentication put the caller in the request's state."""
    space = getattr(state.get("principal"), "space", None)
    space_id = getattr(space, "space_id", None)
    return str(space_id) if space_id is not None else None


def _log_request(
    method: str,
    route: str,
    status: int,
    elapsed: float,
    space: str | None,
    read: metrics.ReadSummary | None,
    stats: metrics.RequestStats,
) -> None:
    """One line for a slow or failed request: ids, the route's template, times and counts, never the
    subject or the body."""
    extra: dict[str, Any] = {
        "route": route,
        "method": method,
        "status": status,
        "duration_ms": round(elapsed * 1000, 1),
        "db_statements": stats.statements,
    }
    if space is not None:
        extra["space_id"] = space
    if read is not None:
        extra |= {"stages": dict(read.stages), "read_path": read.path}
    log.warning("slow request" if status < 400 else "failed request", extra=extra)


def _route_template(scope: Scope) -> str:
    """The path template the router matched, not the raw path: raw paths carry ids and would explode
    label cardinality. Read after the call, from the scope the router filled; FastAPI wraps included
    routers, so walking `app.router.routes` from here finds the wrapper, not the route."""
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"
