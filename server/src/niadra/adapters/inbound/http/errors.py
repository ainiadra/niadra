"""Maps domain errors to RFC 9457 problem responses. Error messages never carry personal data."""

from collections.abc import Mapping
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from niadra.adapters.inbound.http.denials import read_surface, record_denial
from niadra.domain.common.errors import NiadraError, RateLimited, Unavailable

STATUS_BY_CODE = {
    "invalid_input": 422,
    "verification_not_allowed": 422,
    "about_without_link": 422,
    "unknown_object": 422,
    "not_found": 404,
    "method_not_allowed": 405,
    "conflict": 409,
    "forbidden": 403,
    "scope_missing": 403,
    "operation_not_allowed": 403,
    "unauthenticated": 401,
    "rate_limited": 429,
    "unavailable": 503,
    "wrong_cell": 421,
    "lease_lost": 409,
    "personal_data_in_agent_memory": 422,
    # The agent features' codes (7.7).
    "turn_too_large": 413,
    "pin_mismatch": 422,
    "not_replayable": 422,
    "replay_expired": 410,
    "object_type_unknown": 422,
    "agent_state_conflict": 412,
    "no_fingerprint": 422,
    "lease_held": 409,
    "task_locked": 409,
    "effect_in_flight": 409,
    "coordination_unavailable": 503,
    "invalid_handle": 422,
    "unsupported_type": 422,
}


def problem(
    status: int,
    code: str,
    title: str,
    detail: str | None,
    request_id: str | None,
    members: Mapping[str, Any] | None = None,
) -> JSONResponse:
    """`members` are the error's own extension members (RFC 9457, 3.2), e.g. the pins of `pin_mismatch`."""
    body: dict[str, Any] = {
        "type": f"https://docs.niadra.com/errors/{code}",
        "title": title,
        "status": status,
        "code": code,
    }
    if detail:
        body["detail"] = detail
    if request_id:
        body["request_id"] = request_id
    body.update(members or {})
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


async def handle_niadra_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, NiadraError)
    status = STATUS_BY_CODE.get(exc.code, 500)
    await record_denial(
        request.app.state.container,
        getattr(request.state, "principal", None),
        read_surface(request.url.path),
        exc.code,
    )
    response = problem(
        status,
        exc.code,
        exc.code.replace("_", " "),
        exc.detail or None,
        _request_id(request),
        getattr(exc, "members", None),
    )
    if isinstance(exc, RateLimited | Unavailable) and exc.retry_after_s is not None:
        response.headers["Retry-After"] = str(exc.retry_after_s)
    elif status == 503:
        response.headers["Retry-After"] = "2"
    return response


async def handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Only field paths and error types: echoing input values could leak personal data.
    fields = [".".join(str(p) for p in err.get("loc", ())) + f": {err.get('type')}" for err in exc.errors()]
    return problem(422, "invalid_input", "invalid input", "; ".join(fields)[:1000], _request_id(request))


# The router's own answers (no route, wrong method) and any other HTTP error of the framework.
_CODE_BY_STATUS = {
    404: "not_found",
    405: "method_not_allowed",
    401: "unauthenticated",
    403: "forbidden",
    409: "conflict",
    429: "rate_limited",
    503: "unavailable",
}


async def handle_http_error(request: Request, exc: Exception) -> JSONResponse:
    """A path with no route or a method the route does not take answers like every other error, as
    problem+json with the request id; the framework's text (`Not Found`) never becomes the detail."""
    assert isinstance(exc, StarletteHTTPException)
    status = exc.status_code
    code = _CODE_BY_STATUS.get(status) or ("internal_error" if status >= 500 else "invalid_input")
    response = problem(status, code, code.replace("_", " "), None, _request_id(request))
    for name, value in (exc.headers or {}).items():
        response.headers[name] = value  # `Allow` on a 405
    return response


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)
