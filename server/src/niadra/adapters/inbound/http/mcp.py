"""The remote MCP server of `niadra-read` (5.17): Streamable HTTP, JSON-RPC 2.0 at `/mcp`.

Requests are answered with a single JSON body (the transport allows it instead of an SSE stream);
there are no server-initiated messages, so `GET /mcp` is refused. The source key authenticates the
agent; the `Niadra-Subject-Token` header binds the connection to one customer, whom no tool takes
as an argument. Write tools forward to the ingest service; the read process never writes itself.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import orjson
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from niadra.adapters.inbound.http.auth import require_source
from niadra.adapters.inbound.http.denials import TOOL_SURFACES, record_denial
from niadra.app.serve import BlockAsk
from niadra.contracts.agent_memory import AgentMemorySearchRequest, CreateAgentNoteRequest, Evidence
from niadra.contracts.common import Handle, ObjectRef
from niadra.contracts.context import (
    ContextRequest,
    ContextResponse,
    HistoryFilters,
    SearchRequest,
    TimelineRequest,
)
from niadra.contracts.events import (
    ActionInfo,
    BatchRequest,
    Closes,
    Content,
    EventItem,
    FeedbackRequest,
    IdentifyItem,
    SpeakerRef,
)
from niadra.contracts.signals import ConstraintsBlock, ConstraintsRequest
from niadra.domain.common.errors import Forbidden, InvalidInput, NiadraError, RateLimited, Unauthenticated
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.serve.subject_token import SubjectClaims, SubjectRef
from niadra.domain.serve.tools import (
    GET_CONSTRAINTS,
    MCP_TOOLS,
    RESOLVE_IDENTITY,
    ToolSpec,
    agent_memory_tools,
    in_language,
)
from niadra.domain.vocabulary import AssertionMethod, Audience, EventKind, Feature, HandleType, Scope, Speaker

router = APIRouter(tags=["mcp"])

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26")
SUBJECT_HEADER = "niadra-subject-token"
CONTEXT_URI = "niadra://context"
_PARSE_ERROR, _INVALID_REQUEST, _NO_METHOD, _INVALID_PARAMS = -32700, -32600, -32601, -32602
_SERVER_ERROR = -32000

# One request, one count against the key's rate limit: a tool call is counted per tool in
# `_call_tool`, every other method once as /mcp in `mcp`.
Agent = Annotated[SourcePrincipal, Depends(require_source(rate_limited=False))]
Tool = Callable[["_Session", dict[str, Any]], Awaitable[dict[str, Any]]]

_TOOL_SCOPES = {
    "get_customer_context": Scope.CONTEXT,
    "track_event": Scope.TRACK,
    "record_action": Scope.ACT,
    "resolve_identity": Scope.IDENTIFY,
    "search_customer_history": Scope.SEARCH,
    "get_customer_timeline": Scope.SEARCH,
    "open_history_item": Scope.SEARCH,
    "correct_customer_memory": Scope.TRACK,
    "get_constraints": Scope.CONTEXT,
}

_PROMPT = {
    "name": "customer_context",
    "description": "Injects the bound customer's context at the start of the conversation, marked as data.",
    "arguments": [],
}


class _RpcError(Exception):
    def __init__(self, code: int, message: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class _Session:
    """One JSON-RPC request, with the caller, the services and the customer the token binds."""

    def __init__(self, request: Request, caller: SourcePrincipal) -> None:
        self.caller = caller
        self.container = request.app.state.container
        self._token = request.headers.get(SUBJECT_HEADER)
        self._claims: SubjectClaims | None = None

    @property
    def claims(self) -> SubjectClaims:
        if self._claims is None:
            if not self._token:
                raise Unauthenticated("this MCP connection has no subject token")
            self._claims = self.container.serve.subject_claims(self.caller, self._token)
        return self._claims

    @property
    def subject(self) -> Handle:
        return _handle(self.claims.subject)

    def about(self, asked: Handle | None) -> Handle | None:
        """The organization the token binds wins; the model may name one only when none is bound."""
        bound = _handle(self.claims.about) if self.claims.about else None
        if bound is not None and asked is not None and asked != bound:
            raise Forbidden("this connection is bound to another organization")
        return bound or asked

    def thread(self, task_id: str | None = None) -> dict[str, str | None]:
        """ContextRequest takes a conversation or a task, never both."""
        if self.claims.conversation_id:
            return {"conversation_id": self.claims.conversation_id, "task_id": None}
        return {"conversation_id": None, "task_id": self.claims.task_id or task_id}

    def offered(self) -> list[ToolSpec]:
        tools = [t for t in MCP_TOOLS if self.caller.allows(_TOOL_SCOPES[t.name])]
        if self.caller.audience is Audience.CUSTOMER_AGENT:
            # Linking identities is not in the default set of sources that talk to the end customer.
            tools = [t for t in tools if t is not RESOLVE_IDENTITY]
        tools = in_language(tools, self.container.serve.history_label(self.caller))
        config = self.container.config.space(self.caller.space.space_id)
        reads = self.caller.allows(Scope.AGENT_MEMORY) or self.caller.allows(Scope.CONTEXT)
        if config.agent_memory.enabled and reads:
            tools += agent_memory_tools(self.caller.allows(Scope.AGENT_MEMORY_WRITE))
        if Feature.SIGNALS in config.features.enabled and self.caller.allows(Scope.CONTEXT):
            tools.append(GET_CONSTRAINTS)
        return tools

    def evidence(self) -> Evidence | None:
        """The conversation or task the token names, when there is one: the agent's own tools do not need
        a customer, so a connection without a subject token still uses them."""
        if not self._token:
            return None
        claims = self.claims
        if claims.conversation_id:
            return Evidence(conversation_id=claims.conversation_id)
        return Evidence(task_id=claims.task_id) if claims.task_id else None


@router.post("/mcp")
async def mcp(request: Request, caller: Agent) -> Response:
    try:
        message = await request.json()
    except ValueError:
        return JSONResponse(_error(None, _PARSE_ERROR, "parse error"))
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
        return JSONResponse(
            _error(
                message.get("id") if isinstance(message, dict) else None, _INVALID_REQUEST, "invalid request"
            )
        )
    if "id" not in message:
        return Response(status_code=202)  # notifications, e.g. `notifications/initialized`
    session = _Session(request, caller)
    rpc_id = message["id"]
    headers = {}
    try:
        if message["method"] != "tools/call":
            await session.container.edge.check_rate(caller, "/mcp")
        result = await _dispatch(session, message["method"], message.get("params") or {})
        if message["method"] == "initialize":
            headers["Mcp-Session-Id"] = uuid.uuid4().hex
        return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result}, headers=headers)
    except _RpcError as exc:
        return JSONResponse(_error(rpc_id, exc.code, exc.message, exc.data))
    except RateLimited:
        raise  # an HTTP 429 with Retry-After, which MCP clients and the SDKs already back off on
    except NiadraError as exc:
        return JSONResponse(_error(rpc_id, _SERVER_ERROR, exc.code, {"code": exc.code}))


@router.get("/mcp", include_in_schema=False)
async def mcp_stream() -> Response:
    return Response(status_code=405, headers={"Allow": "POST"})


async def _dispatch(session: _Session, method: str, params: dict[str, Any]) -> dict[str, Any]:
    if method == "initialize":
        asked = params.get("protocolVersion")
        return {
            "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}, "resources": {}, "prompts": {}},
            "serverInfo": {"name": "niadra", "version": "1"},
            "instructions": "Customer memory shared by every agent of the company. Call get_customer_context "
            "once at the start; search the history only when the context does not answer.",
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [t.as_mcp() for t in session.offered()]}
    if method == "tools/call":
        return await _call_tool(session, params)
    if method == "resources/list":
        return {
            "resources": [
                {
                    "uri": CONTEXT_URI,
                    "name": "customer_context",
                    "mimeType": "text/plain",
                    "description": "The context pack of the customer this connection is bound to.",
                }
            ]
        }
    if method == "resources/read":
        if params.get("uri") != CONTEXT_URI:
            raise _RpcError(_INVALID_PARAMS, "unknown resource")
        text = _context_text(await _context(session, {}))
        return {"contents": [{"uri": CONTEXT_URI, "mimeType": "text/plain", "text": text}]}
    if method == "prompts/list":
        return {"prompts": [_PROMPT]}
    if method == "prompts/get":
        if params.get("name") != _PROMPT["name"]:
            raise _RpcError(_INVALID_PARAMS, "unknown prompt")
        text = _context_text(await _context(session, {}))
        return {
            "description": _PROMPT["description"],
            "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
        }
    raise _RpcError(_NO_METHOD, "method not found")


async def _call_tool(session: _Session, params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    offered = {t.name for t in session.offered()}
    if name not in _TOOLS:
        raise _RpcError(_INVALID_PARAMS, "unknown tool")
    surface = TOOL_SURFACES.get(str(name))
    if name not in offered:
        await record_denial(session.container, session.caller, surface, "forbidden")
        return _tool_error("forbidden")
    await session.container.edge.check_rate(session.caller, f"/mcp/{name}")
    args = dict(params.get("arguments") or {})
    # The model sees what it forgot, as a tool result it can act on, instead of a server error.
    spec = next(t for t in session.offered() if t.name == name)
    missing = [field for field in spec.parameters.get("required", []) if field not in args]
    if missing:
        return _tool_error("invalid_input", "missing argument: " + ", ".join(missing))
    try:
        return await _TOOLS[name](session, args)
    except (ValidationError, KeyError, TypeError, ValueError):
        return _tool_error("invalid_input")
    except (Unauthenticated, Forbidden) as exc:
        await record_denial(session.container, session.caller, surface, exc.code)
        raise
    except NiadraError as exc:
        await record_denial(session.container, session.caller, surface, exc.code)
        # Tool failures go back to the model as results it can read, not as protocol errors.
        return _tool_error(exc.code)


def _tool_error(code: str, detail: str | None = None) -> dict[str, Any]:
    text = f"error: {code}" + (f" ({detail})" if detail else "")
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _result(text: str, structured: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "structuredContent": structured, "isError": False}


async def _context(session: _Session, args: dict[str, Any]) -> ContextResponse:
    """Same signature as `context()` (5.18); the customer always comes from the subject token."""
    obj = ObjectRef.model_validate(args["object"]) if args.get("object") else None
    request = ContextRequest(
        subject=None if obj else session.subject,
        object=obj,
        about=session.about(Handle.model_validate(args["about"]) if args.get("about") else None),
        view=args.get("view") or "chat",
        verification=session.claims.level,
        query=args.get("query"),
        delta=bool(args.get("delta", False)),
        **session.thread(args.get("task_id")),
    )
    response: ContextResponse = await session.container.serve.context(
        session.caller, request, bound=session.subject
    )
    return response


ABOUT_UNLINKED = (
    "note: the organization named in `about` has no active link to this customer, so its memory is left out"
)


def _context_text(response: ContextResponse) -> str:
    """The pinned text, then the turn block in the order the SDKs build it: live turns, slots, delta."""
    parts = [response.text or ""]
    if response.live:
        parts.append(
            "\n".join(
                f"[{t.channel} {t.at.isoformat(timespec='minutes')}] {t.speaker}: {t.text}"
                for t in response.live
            )
        )
    if response.slots:
        parts.append(response.slots)
    if response.delta:
        parts.append(response.delta)
    if response.about_unlinked:
        parts.append(ABOUT_UNLINKED)
    return "\n".join(p for p in parts if p)


async def _get_context(session: _Session, args: dict[str, Any]) -> dict[str, Any]:
    response = await _context(session, args)
    return _result(_context_text(response), response.model_dump(mode="json", exclude={"text"}))


async def _get_constraints(session: _Session, args: dict[str, Any]) -> dict[str, Any]:
    """The constraints block of `POST /v1/constraints` for the bound customer: with `tool`, rendered through
    the binding the space's `tool-bindings` document gives that tool for this source."""
    wanted = ConstraintsRequest.model_validate(
        {"subject": session.subject, "for": args.get("for") or "self", "tool": args.get("tool")}
    )
    ask = BlockAsk(session.claims.conversation_id, None, "chat", wanted.for_)
    block: ConstraintsBlock = await session.container.serve.read_block(
        session.caller, "constraints", wanted.subject, ask
    )
    if wanted.tool is not None:
        block = session.container.signals.for_tool(session.caller, block, wanted.tool)
    lines = [block.text] if block.text else []
    if block.rendered is not None:
        rendered = block.rendered
        lines.append(f"{rendered.tool} arguments: {orjson.dumps(rendered.args).decode()}")
        if rendered.residual:
            residual = [_constraint_line(block, i) for i in rendered.residual]
            lines.append(f"{rendered.tool} cannot express: " + "; ".join(residual))
    text = "\n".join(lines) or "no constraints"
    return _result(text, block.model_dump(mode="json", by_alias=True, exclude_none=True))


def _constraint_line(block: ConstraintsBlock, constraint_id: str) -> str:
    found = next((h for h in block.hard if h.id == constraint_id), None)
    if found is None:
        return constraint_id
    return f"{found.attr} {found.op} {', '.join(str(v) for v in found.values)}"


async def _ingest(session: _Session, item: EventItem | IdentifyItem) -> dict[str, Any]:
    outcome = await session.container.ingest.ingest_batch(session.caller, BatchRequest(items=[item]))
    structured = outcome.model_dump(mode="json")
    if outcome.errors:
        return {
            "content": [{"type": "text", "text": f"rejected: {outcome.errors[0].code}"}],
            "structuredContent": structured,
            "isError": True,
        }
    return _result("recorded", structured)


def _now(session: _Session) -> datetime:
    now: datetime = session.container.clock.now()
    return now


async def _track_event(session: _Session, args: dict[str, Any]) -> dict[str, Any]:
    canonical = args.get("canonical_type")
    item = EventItem(
        kind=EventKind.SYSTEM_EVENT if canonical else EventKind.MESSAGE,
        idempotency_key=str(uuid7()),
        channel=args.get("channel") or "mcp",
        handles=[session.subject],
        speaker=SpeakerRef(role=Speaker(args.get("speaker") or Speaker.AI_AGENT.value)),
        content=Content(text=args["text"]),
        canonical_type=canonical,
        occurred_at=_now(session),
        **session.thread(),
    )
    return await _ingest(session, item)


async def _record_action(session: _Session, args: dict[str, Any]) -> dict[str, Any]:
    closes = args.get("closes")
    item = EventItem(
        kind=EventKind.ACTION,
        idempotency_key=str(uuid7()),
        channel=args.get("channel") or "mcp",
        handles=[session.subject],
        object_refs=[ObjectRef(**args["object"])] if args.get("object") else [],
        speaker=SpeakerRef(role=Speaker.AI_AGENT),
        action=ActionInfo(
            operation=args["operation"],
            result=args.get("result"),
            purpose=args.get("purpose"),
            closes=Closes(**closes) if closes else None,
        ),
        occurred_at=_now(session),
        **session.thread(),
    )
    return await _ingest(session, item)


async def _resolve_identity(session: _Session, args: dict[str, Any]) -> dict[str, Any]:
    others = [
        Handle(type=HandleType(h["type"]), value=h["value"], scope=h.get("scope")) for h in args["handles"]
    ]
    # What a model passes on is what the customer typed: a declaration, never proof (5.3).
    item = IdentifyItem(
        idempotency_key=str(uuid7()),
        handles=[session.subject, *others],
        method=AssertionMethod.DECLARED,
        conversation_id=session.claims.conversation_id,
        occurred_at=_now(session),
    )
    return await _ingest(session, item)


async def _search(session: _Session, args: dict[str, Any]) -> dict[str, Any]:
    thread = session.thread()
    request = SearchRequest(
        subject=session.subject,
        # The organization the token binds: the kit reaches what the pack's account block shows (21.4).
        about=session.about(None),
        query=args["query"],
        filters=HistoryFilters(**(args.get("filters") or {})),
        verification=session.claims.level,
        **({"max_tokens": args["max_tokens"]} if "max_tokens" in args else {}),
        **thread,
    )
    response = await session.container.serve.search(session.caller, request)
    lines = [f"{i.id} · {i.at.date().isoformat()} · {i.text}" for i in response.items]
    if response.recurrence:
        r = response.recurrence
        lines.append(f"recurrence: {r.category} {r.occurrences}x in {r.window_days} days")
    return _result("\n".join(lines) or "no results", response.model_dump(mode="json"))


async def _timeline(session: _Session, args: dict[str, Any]) -> dict[str, Any]:
    request = TimelineRequest(
        subject=session.subject,
        about=session.about(None),
        cursor=args.get("cursor"),
        limit=int(args.get("limit", 20)),
        filters=HistoryFilters(**(args.get("filters") or {})),
        verification=session.claims.level,
        conversation_id=session.claims.conversation_id,
    )
    response = await session.container.serve.timeline(session.caller, request)
    lines = [f"{i.id} · {i.at.date().isoformat()} · {i.text}" for i in response.items]
    return _result("\n".join(lines) or "no items", response.model_dump(mode="json"))


async def _open(session: _Session, args: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(args.get("id"), str):
        raise InvalidInput("id is required")
    opened = await session.container.serve.open_item(
        session.caller,
        args["id"],
        verification=session.claims.level,
        conversation_id=session.claims.conversation_id,
        subject=session.subject,
        about=session.about(None),
    )
    return _result(opened.summary, opened.model_dump(mode="json"))


async def _correct_memory(session: _Session, args: dict[str, Any]) -> dict[str, Any]:
    """A customer's correction, as the same `feedback.*` event `/v1/feedback` makes, for a fact this agent
    may read at the conversation's level only: the model never writes to a row it could not have seen."""
    fact_id = args.get("fact_id")
    action = args.get("action")
    value = args.get("value")
    if not isinstance(fact_id, str) or action not in ("correct", "retract"):
        raise InvalidInput("fact_id and an action of correct or retract are required")
    if action == "correct" and not (isinstance(value, str) and value.strip()):
        raise InvalidInput("correct needs the new value")
    try:
        fact = UUID(fact_id)
    except ValueError:
        return _tool_error("not_found", "no fact with this id for this customer")
    thread = session.thread()
    readable = await session.container.serve.can_read(
        session.caller, session.subject, f"fact:{fact}", verification=session.claims.level, **thread
    )
    if not readable:
        return _tool_error("not_found", "no fact with this id for this customer")
    request = FeedbackRequest(
        idempotency_key=str(uuid7()),
        subject=session.subject,
        action="correct_fact" if action == "correct" else "retract_fact",
        fact_id=fact,
        conversation_id=thread["conversation_id"],
        value=value.strip() if action == "correct" and isinstance(value, str) else None,
        reason=args.get("reason") if isinstance(args.get("reason"), str) else None,
    )
    outcome = await session.container.ingest.record_feedback(session.caller, request)
    structured = outcome.model_dump(mode="json")
    if outcome.errors:
        return {
            "content": [{"type": "text", "text": f"rejected: {outcome.errors[0].code}"}],
            "structuredContent": structured,
            "isError": True,
        }
    return _result("recorded; memory applies it in seconds", structured)


async def _search_agent_memory(session: _Session, args: dict[str, Any]) -> dict[str, Any]:
    evidence = session.evidence()
    request = AgentMemorySearchRequest(
        query=args["query"],
        tags=args.get("tags") or [],
        conversation_id=evidence.conversation_id if evidence else None,
        task_id=evidence.task_id if evidence else None,
    )
    response = await session.container.agent_memory.search(session.caller, request)
    lines = [f"[{n.kind.value}] {n.title}: {n.body}" for n in response.notes]
    return _result("\n".join(lines) or "no notes", response.model_dump(mode="json"))


async def _remember(session: _Session, args: dict[str, Any]) -> dict[str, Any]:
    request = CreateAgentNoteRequest(
        kind=args["kind"],
        title=args["title"],
        body=args["body"],
        tags=args.get("tags") or [],
        evidence=session.evidence(),
    )
    result = await session.container.agent_memory.create(session.caller, request)
    text = "saved" if result.note is not None else "saved for review by a person"
    return _result(text, result.model_dump(mode="json", exclude_none=True))


_TOOLS: dict[str, Tool] = {
    "get_customer_context": _get_context,
    "track_event": _track_event,
    "record_action": _record_action,
    "resolve_identity": _resolve_identity,
    "search_customer_history": _search,
    "get_customer_timeline": _timeline,
    "open_history_item": _open,
    "correct_customer_memory": _correct_memory,
    "search_agent_memory": _search_agent_memory,
    "remember": _remember,
    "get_constraints": _get_constraints,
}


def _handle(ref: SubjectRef) -> Handle:
    return Handle(type=ref.type, value=ref.value, scope=ref.scope, subject_kind=ref.subject_kind)


def _error(rpc_id: Any, code: int, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": rpc_id, "error": error}
