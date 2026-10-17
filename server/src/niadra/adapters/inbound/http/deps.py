"""FastAPI dependencies shared by every router: the service container and the caller."""

from typing import Any

from fastapi import Request


def container(request: Request) -> Any:
    return request.app.state.container


def service(name: str) -> Any:
    """`Depends(service("ingest"))` resolves `container.ingest`, the module's application service."""

    def resolve(request: Request) -> Any:
        return getattr(request.app.state.container, name)

    return resolve
