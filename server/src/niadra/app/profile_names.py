"""The name and contact the Console shows a customer by (5.22), for one reader and a page of customers.

A fixed number of statements whatever the page holds: the handles (unless the caller has them), the name facts
and slots, and the subjects' own records. A company's name reaches every reader; a person's contact leaves in
full for the roles that see contacts and masked for the rest; a person's name only for a reader of customer
memory (`reads_memory`), through the policy of the reader's role, as the customer's memory screen shows it:
a name the policy masks or denies is not shown, and the contact stands in for it.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from niadra.app.compile import slot_rules
from niadra.app.profile_view import ConsoleReading
from niadra.config.models import SpaceConfig
from niadra.domain.common.space import Principal
from niadra.domain.compile import MemorySnapshot
from niadra.domain.identity.display import (
    NAME_PREDICATES,
    RECORD_TYPES,
    contact_handle,
    is_subject_record,
    name_sources,
    shown_contact,
    system_ids,
)
from niadra.domain.identity.masking import reveals_contacts
from niadra.domain.identity.provenance import governance_view
from niadra.domain.memory.profile import surviving
from niadra.domain.policy import Effect
from niadra.domain.policy.console import reads_memory
from niadra.domain.records import BusinessObject, Fact, HandleRecord, ProfileAttr, ProfileRecord
from niadra.ports.uow import UnitOfWork


@dataclass(frozen=True, slots=True)
class Shown:
    """How one reader sees a customer named: `name` None when none is known or the reader may not see it,
    `contact` None when the customer has no phone or e-mail."""

    name: str | None = None
    contact: str | None = None


async def shown_profiles(
    uow: UnitOfWork,
    principal: Principal,
    config: SpaceConfig,
    profiles: Iterable[ProfileRecord],
    handles: Sequence[HandleRecord] | None = None,
) -> dict[UUID, Shown]:
    """Each profile's name and contact as `principal` sees them, by profile id. `handles` saves their read
    when the caller holds every handle of these profiles already."""
    views = [governance_view(principal, p) for p in profiles]
    ids = [h for v in views for h in v.handle_ids]
    if not ids:
        return {v.profile_id: Shown() for v in views}
    found = list(handles) if handles is not None else await uow.identity.get_handles(ids)
    by_id = {h.handle_id: h for h in found}
    facts, attrs = await uow.memory.name_rows(ids, NAME_PREDICATES)
    records = await uow.objects.subject_records(ids, types=RECORD_TYPES, keys=system_ids(found))
    names = _Names(principal, config)
    reveal = reveals_contacts(principal)
    shown: dict[UUID, Shown] = {}
    for view in views:
        own = set(view.handle_ids)
        mine = [by_id[h] for h in view.handle_ids if h in by_id]
        contact = contact_handle(mine)
        shown[view.profile_id] = Shown(
            names.of(
                view,
                [f for f in facts if f.origin_handle_id in own],
                [a for a in attrs if a.origin_handle_id in own],
                [o for o in records if o.origin_handle_id in own and is_subject_record(o, system_ids(mine))],
            ),
            shown_contact(contact, reveal=reveal) if contact is not None else None,
        )
    return shown


class _Names:
    def __init__(self, principal: Principal, config: SpaceConfig) -> None:
        self._reading = ConsoleReading.of(principal, config) if reads_memory(principal) else None
        self._slot_rules = self._reading.settings.slot_rules if self._reading else slot_rules(config)

    def of(
        self,
        view: ProfileRecord,
        facts: Sequence[Fact],
        attrs: Sequence[ProfileAttr],
        records: Sequence[BusinessObject],
    ) -> str | None:
        sources = name_sources(view.kind, surviving(attrs, self._slot_rules), facts, records)
        if not sources:
            return None
        if view.kind.is_organization:
            return sources[0].value  # a company's name is no personal data
        if self._reading is None:
            return None
        snapshot = MemorySnapshot(
            profile=view, facts=tuple(facts), attrs=tuple(attrs), objects=tuple(records)
        )
        policy = self._reading.view(snapshot)
        for source in sources:
            if source.item in policy.attrs and policy.effect(source.item) is Effect.ALLOW:
                return source.value
        return None
