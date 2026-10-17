"""`IntakeLog` as an ordinary transaction of the unit of work, for unit tests: the same claim and row as the
single statement PostgreSQL runs."""

from typing import cast

from niadra.adapters.memory.ingest import InMemoryEventRepository
from niadra.domain.ingest.intake import NewIntake
from niadra.ports.uow import UnitOfWorkFactory


class TransactionIntakeLog:
    def __init__(self, uow: UnitOfWorkFactory) -> None:
        self._uow = uow

    async def accept(self, intake: NewIntake) -> set[str]:
        async with self._uow(intake.space_id) as uow:
            return await cast(InMemoryEventRepository, uow.ingest).accept_intake(intake)
