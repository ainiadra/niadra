"""Agent memory routes (10, C.6): the agent's own working notes, the block for its prompt, the two tools
and the Console's review of proposals. Served by `niadra-admin` (the proxy's default group).

A source key reads with `agent_memory` (or `context`, which every agent key has) and writes with
`agent_memory:write`; people in the Console act with the integration role, and erase or approve as admins.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from niadra.adapters.inbound.http.auth import authenticate
from niadra.adapters.inbound.http.deps import service
from niadra.contracts.agent_memory import (
    AgentMemoryBlock,
    AgentMemoryExport,
    AgentMemorySearchRequest,
    AgentMemorySearchResponse,
    AgentNote,
    AgentNotePage,
    AgentNoteProposal,
    AgentNoteProposalPage,
    AgentNoteStatus,
    AgentNoteVisibility,
    ApproveProposalRequest,
    CreateAgentNoteRequest,
    DistillRequest,
    ErasedAgentNotes,
    ProposalStatus,
    RememberResult,
    Tag,
    UpdateAgentNoteRequest,
)
from niadra.contracts.context import ToolDefinition
from niadra.domain.common.errors import Forbidden, ScopeMissing
from niadra.domain.common.space import Principal, SourcePrincipal
from niadra.domain.control.records import Role
from niadra.domain.vocabulary import Scope

router = APIRouter(prefix="/v1/agent-memory", tags=["agent-memory"])

AgentMemory = Annotated[Any, Depends(service("agent_memory"))]


def _caller(*, source_scopes: tuple[Scope, ...], roles: tuple[Role, ...], any_of: bool = False) -> Any:
    """A source key with the scopes (one of them when `any_of`), or a person with one of the roles."""

    async def dependency(request: Request) -> Principal:
        principal = await authenticate(request)
        if isinstance(principal, SourcePrincipal):
            granted = [s for s in source_scopes if principal.allows(s)]
            if (any_of and not granted) or (not any_of and len(granted) != len(source_scopes)):
                raise ScopeMissing("the key lacks the scope " + " or ".join(source_scopes))
            await request.app.state.container.edge.check_rate(principal, request.url.path)
            return principal
        # Admins may do everything here; an empty `roles` leaves the route to them.
        if "admin" not in principal.roles and not (set(roles) & principal.roles):
            raise Forbidden("the role cannot do this")
        return principal

    return Depends(dependency)


Reader = Annotated[
    Principal,
    _caller(source_scopes=(Scope.AGENT_MEMORY, Scope.CONTEXT), roles=(Role.INTEGRATION,), any_of=True),
]
Writer = Annotated[Principal, _caller(source_scopes=(Scope.AGENT_MEMORY_WRITE,), roles=(Role.INTEGRATION,))]
Admin = Annotated[Principal, _caller(source_scopes=(Scope.ADMIN,), roles=())]


@router.get(
    "/block",
    response_model=AgentMemoryBlock,
    responses={304: {"description": "The block behind `If-None-Match` is still current."}},
)
async def block(
    caller: Reader,
    agent_memory: AgentMemory,
    response: Response,
    max_tokens: Annotated[int, Query(ge=50, le=2000)] = 300,
    tags: Annotated[list[Tag] | None, Query(max_length=8)] = None,
    view: str | None = None,
    source_id: UUID | None = None,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Any:
    """The notes this agent may read, as text for its prompt: after its instructions, before the customer's
    context. A key gets its own; a person previews an agent's with `source_id`."""
    result: AgentMemoryBlock = await agent_memory.block(
        caller, max_tokens=max_tokens, tags=tags or [], view=view, source_id=source_id
    )
    if if_none_match and if_none_match.strip('"') == result.etag:
        return Response(status_code=304, headers={"ETag": f'"{result.etag}"'})
    response.headers["ETag"] = f'"{result.etag}"'
    return result


@router.post("/search", response_model=AgentMemorySearchResponse)
async def search(body: AgentMemorySearchRequest, caller: Reader, agent_memory: AgentMemory) -> Any:
    return await agent_memory.search(caller, body)


@router.get("/tools", response_model=list[ToolDefinition])
async def tools(caller: Reader, agent_memory: AgentMemory) -> Any:
    """`search_agent_memory`, and `remember` when the key may write."""
    return agent_memory.tools_definitions(caller)


@router.post("/notes", response_model=RememberResult, status_code=201, response_model_exclude_none=True)
async def create_note(body: CreateAgentNoteRequest, caller: Writer, agent_memory: AgentMemory) -> Any:
    """422 `personal_data_in_agent_memory` when the title, body or tags carry personal data."""
    return await agent_memory.create(caller, body)


@router.get("/notes", response_model=AgentNotePage)
async def list_notes(
    caller: Reader,
    agent_memory: AgentMemory,
    source_id: UUID | None = None,
    status: AgentNoteStatus | None = AgentNoteStatus.ACTIVE,
    visibility: AgentNoteVisibility | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Any:
    return await agent_memory.list_notes(
        caller, source_id=source_id, status=status, visibility=visibility, cursor=cursor, limit=limit
    )


@router.get("/notes/{note_id}", response_model=AgentNote)
async def get_note(note_id: UUID, caller: Reader, agent_memory: AgentMemory) -> Any:
    return await agent_memory.get(caller, note_id)


@router.get("/notes/{note_id}/versions", response_model=list[AgentNote])
async def note_versions(note_id: UUID, caller: Reader, agent_memory: AgentMemory) -> Any:
    """Every version of the note, oldest first."""
    return await agent_memory.versions(caller, note_id)


@router.patch("/notes/{note_id}", response_model=AgentNote)
async def update_note(
    note_id: UUID, body: UpdateAgentNoteRequest, caller: Writer, agent_memory: AgentMemory
) -> Any:
    """A new version; the one it replaces is retired."""
    return await agent_memory.update(caller, note_id, body)


@router.post("/notes/{note_id}/retire", response_model=AgentNote)
async def retire_note(note_id: UUID, caller: Writer, agent_memory: AgentMemory) -> Any:
    return await agent_memory.retire(caller, note_id)


@router.delete("/notes", response_model=ErasedAgentNotes)
async def erase_notes(source_id: UUID, caller: Admin, agent_memory: AgentMemory) -> Any:
    """Deletes every note and proposal of one agent, every version, with an `admin` receipt."""
    return await agent_memory.erase_source(caller, source_id)


@router.get("/export", response_model=AgentMemoryExport)
async def export_notes(source_id: UUID, caller: Reader, agent_memory: AgentMemory) -> Any:
    return await agent_memory.export_source(caller, source_id)


@router.post("/distill", response_model=AgentNoteProposal, status_code=201)
async def distill(body: DistillRequest, caller: Writer, agent_memory: AgentMemory) -> Any:
    """Proposes one note from a conversation or task, read already masked; a person approves it."""
    return await agent_memory.distill(caller, body)


@router.get("/proposals", response_model=AgentNoteProposalPage)
async def list_proposals(
    caller: Reader,
    agent_memory: AgentMemory,
    source_id: UUID | None = None,
    status: ProposalStatus | None = ProposalStatus.PENDING,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Any:
    return await agent_memory.list_proposals(
        caller, source_id=source_id, status=status, cursor=cursor, limit=limit
    )


@router.post("/proposals/{proposal_id}/approve", response_model=AgentNote)
async def approve_proposal(
    proposal_id: UUID, caller: Admin, agent_memory: AgentMemory, body: ApproveProposalRequest | None = None
) -> Any:
    return await agent_memory.approve(caller, proposal_id, body or ApproveProposalRequest())


@router.post("/proposals/{proposal_id}/reject", response_model=AgentNoteProposal)
async def reject_proposal(proposal_id: UUID, caller: Admin, agent_memory: AgentMemory) -> Any:
    return await agent_memory.reject(caller, proposal_id)
