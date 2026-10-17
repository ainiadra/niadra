"""Where the cell's databases are and how each role reaches them.

Two endpoints for the same PostgreSQL instance: the pooled one (PgBouncer in transaction mode) for
every serving process, and the direct one for the schema owner, whose work cannot go through a
transaction pooler: CREATE and DROP DATABASE run outside a transaction, and migrations, dumps and
restores hold session state. Without a pooler both are the same address.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.engine import URL

from niadra.domain.spaces.directory import PRIMARY_INSTANCE

Role = Literal["app", "system", "owner"]
DRIVER = "postgresql+psycopg"


@dataclass(frozen=True, slots=True)
class Endpoint:
    host: str
    """A host name, or the directory of a local server's unix socket."""
    port: int
    sslmode: str | None = None


@dataclass(frozen=True, slots=True)
class Servers:
    pooled: Endpoint
    direct: Endpoint
    passwords: Mapping[str, str | None]
    cell_database: str = "niadra_cell"
    # Instances of their own (tiers T1 and T2) by name, reached directly and through their own pooler;
    # the control plane places a space on one by name (control_cli place-project --instance), and the
    # snapshot carries it to the directory row the registry connects by.
    instances: Mapping[str, tuple[Endpoint, Endpoint]] = field(default_factory=dict)
    # PgBouncer's admin console, when there is one: KILL, PAUSE and RESUME of one database.
    pooler_admin: Endpoint | None = None
    # A whole URL per role (local development, tests): its database is replaced per use.
    overrides: Mapping[str, URL] = field(default_factory=dict)
    # The name the application role logs in with. The workers and the loops log in as `niadra_worker`,
    # which becomes `niadra_app` at login (same privileges, same row-level security) but has a pool of its
    # own in PgBouncer, capped at one of a space database's two server connections: background work never
    # takes the connection the acknowledgement of a batch needs (10, 5.2).
    app_login: str = "niadra_app"

    def url(
        self, role: Role, database: str, *, direct: bool = False, instance: str = PRIMARY_INSTANCE
    ) -> URL:
        if role in self.overrides:
            return self.overrides[role].set(database=database)
        pooled, reached = self.pooled, self.direct
        if instance != PRIMARY_INSTANCE:
            try:
                pooled, reached = self.instances[instance]
            except KeyError:
                raise LookupError(f"unknown database instance {instance}") from None
        endpoint = reached if direct else pooled
        query = {"sslmode": endpoint.sslmode} if endpoint.sslmode else {}
        return URL.create(
            DRIVER,
            username=self.app_login if role == "app" else f"niadra_{role}",
            password=self.passwords.get(role),
            host=endpoint.host,
            port=endpoint.port,
            database=database,
            query=query,
        )

    def cell_url(self, role: Role = "system", *, direct: bool = False) -> URL:
        return self.url(role, self.cell_database, direct=direct)


def libpq_environment(url: URL) -> dict[str, str]:
    """The PG* variables that make `pg_dump` and `pg_restore` reach `url`; the password never goes on
    a command line, where any process of the machine could read it."""
    host = url.host or url.query.get("host")
    port = url.port or url.query.get("port")
    env = {"PGDATABASE": url.database or "", "PGUSER": url.username or ""}
    if host:
        env["PGHOST"] = str(host)
    if port:
        env["PGPORT"] = str(port)
    if url.password:
        env["PGPASSWORD"] = str(url.password)
    if sslmode := url.query.get("sslmode"):
        env["PGSSLMODE"] = str(sslmode)
    return env
