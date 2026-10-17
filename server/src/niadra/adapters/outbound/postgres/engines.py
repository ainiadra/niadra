"""SQLAlchemy async engines on psycopg, shaped for PgBouncer in transaction mode."""

from typing import Any, NoReturn

from sqlalchemy import exc
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from niadra.adapters.outbound.postgres.servers import DRIVER
from niadra.domain.common.errors import Unavailable

UNREACHABLE_RETRY_S = 2


def make_engine(
    url: str | URL,
    *,
    pool_size: int = 5,
    max_overflow: int = 5,
    pool_timeout: float = 30.0,
    application_name: str = "niadra",
    pre_ping: bool = True,
    **options: Any,
) -> AsyncEngine:
    """`pre_ping=False` saves the round trip of a `SELECT 1` on every checkout; whoever uses such an engine
    retries a first statement that finds its connection dead (`uow`, `intake`)."""
    # No server-side prepared statements: PgBouncer in transaction mode would break them.
    parsed = make_url(url)
    if parsed.drivername == "postgresql":
        parsed = parsed.set(drivername=DRIVER)
    return create_async_engine(
        parsed,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_pre_ping=pre_ping,
        pool_recycle=1800,
        connect_args={"prepare_threshold": None, "application_name": application_name},
        # A failed statement's error never carries its parameters, which hold customer data (5.20).
        hide_parameters=options.pop("hide_parameters", True),
        **options,
    )


def raise_unreachable(error: exc.DBAPIError) -> NoReturn:
    """A connection the server or the pooler refused or dropped is a database out of reach: 503 with a
    retry delay, not 500. Any other database error is raised as it is."""
    if isinstance(error, exc.OperationalError):
        raise Unavailable("the database did not answer", retry_after_s=UNREACHABLE_RETRY_S) from error
    raise error
