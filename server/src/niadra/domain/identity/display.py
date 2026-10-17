"""How the Console names a customer (5.22): a company by the name its own systems sent, a person by the name
the memory holds or, without one, by a phone or an e-mail. The pseudonym stays a reference for receipts and
support, never anyone's name on a screen.

A company's name is not personal data, so every reader sees it. A person's name and contact are: the contact
leaves in full only for the roles that see contacts (`masking.CONTACT_REVEAL_ROLES`), masked for the rest, and
the name only for a reader of the customer's memory whose policy lets the item that holds it through (the app
decides that with the same view the customer's memory screen reads under).
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from niadra.domain.identity.masking import mask_value
from niadra.domain.records import BusinessObject, Fact, HandleRecord, ProfileAttr
from niadra.domain.vocabulary import FactStatus, HandleStatus, HandleType, SubjectKind

NAME_PREDICATES = (
    "social_name",
    "nome_social",
    "name",
    "nome",
    "full_name",
    "nome_completo",
    "preferred_name",
    "nome_preferido",
    "nickname",
    "apelido",
)
"""Fact predicates and profile slots that say how a customer is called, the one to show first: a social name
before the registered one (Decreto 8.727/2016), a full name before a nickname."""

COMPANY_FIELDS = (
    "trade_name",
    "nome_fantasia",
    "fantasia",
    "display_name",
    "name",
    "nome",
    "company_name",
    "nome_empresa",
    "legal_name",
    "razao_social",
)
"""The fields of a company's own record that name it, the one people call it by first."""

PERSON_FIELDS = ("social_name", "nome_social", "full_name", "nome_completo", "name", "nome", "display_name")
"""The fields of a person's own record that name them."""

RECORD_TYPES = frozenset(
    {
        "account",
        "conta",
        "company",
        "empresa",
        "organization",
        "organizacao",
        "customer",
        "cliente",
        "client",
        "contact",
        "contato",
        "person",
        "pessoa",
        "partner",
        "parceiro",
        "lead",
        "member",
        "membro",
        "patient",
        "paciente",
        "beneficiary",
        "beneficiario",
    }
)
"""Object types that are the record of the subject itself (a CRM account, an ERP customer), not something the
subject has: a field `name` of one of them names the subject, where an order's or a plan's names a product."""

NAME_CHARS = 200
"""A longer value is a text, not a name: it never becomes a label."""

_CONTACT_ORDER = {HandleType.PHONE_E164: 0, HandleType.WA_ID: 0, HandleType.WA_JID: 0, HandleType.EMAIL: 1}
_DIGITS = re.compile(r"\d+")


@dataclass(frozen=True, slots=True)
class NameSource:
    """A value that may name the customer, and the item of their memory that holds it: `attr:0:<slot>`,
    `fact:<id>` or `object:<id>`, the keys the policy decides by."""

    item: str
    value: str


def name_sources(
    kind: SubjectKind,
    slots: Mapping[str, ProfileAttr],
    facts: Iterable[Fact],
    records: Iterable[BusinessObject],
) -> list[NameSource]:
    """Every value that may name the customer, the best first. A company goes by its own record first, as its
    systems sent it; a person by what the memory holds first (a profile slot before a fact of the same rank,
    then the latest), then by their own record. `slots` are each slot's surviving value; `records` the
    subject's own records (`is_subject_record`)."""
    said: list[tuple[tuple[int, int, float], NameSource]] = []
    for slot, attr in slots.items():
        if slot in NAME_PREDICATES and (value := _clean(attr.value)):
            rank = (NAME_PREDICATES.index(slot), 0, -attr.observed_at.timestamp())
            said.append((rank, NameSource(f"attr:0:{slot}", value)))
    for fact in facts:
        if fact.status is not FactStatus.ACTIVE or fact.object_ref is not None or fact.about_handle_id:
            continue  # a name said about an object or another subject is not this customer's
        if fact.predicate in NAME_PREDICATES and (value := _clean(fact.value)):
            rank = (NAME_PREDICATES.index(fact.predicate), 1, -fact.last_confirmed_at.timestamp())
            said.append((rank, NameSource(f"fact:{fact.fact_id}", value)))
    memory = [source for _, source in sorted(said, key=lambda pair: pair[0])]
    fields = COMPANY_FIELDS if kind.is_organization else PERSON_FIELDS
    sent: list[tuple[tuple[int, float], NameSource]] = []
    for obj in records:
        found = next(((i, v) for i, f in enumerate(fields) if (v := _clean(obj.state.get(f)))), None)
        if found is not None:
            sent.append(((found[0], -obj.as_of.timestamp()), NameSource(f"object:{obj.object_id}", found[1])))
    own = [source for _, source in sorted(sent, key=lambda pair: pair[0])]
    return own + memory if kind.is_organization else memory + own


def is_subject_record(obj: BusinessObject, system_ids: Iterable[tuple[str, str]]) -> bool:
    """An object of a record type, or one whose key is a system id of the subject in the same system: the CRM
    account `ACC-2201` of the subject known as `system_id` `ACC-2201` in `crm`."""
    return obj.key.type.lower() in RECORD_TYPES or (obj.key.namespace, obj.key.external_id) in set(system_ids)


def system_ids(handles: Iterable[HandleRecord]) -> list[tuple[str, str]]:
    """(scope, value) of each system id: the keys a subject's own record may carry."""
    return [(h.scope, h.value) for h in handles if h.type is HandleType.SYSTEM_ID and h.scope]


def contact_handle(handles: Sequence[HandleRecord]) -> HandleRecord | None:
    """The contact a person is shown by when no name is known: a phone (WhatsApp included) before an e-mail,
    the latest active one of each. A blocked, ambiguous or recycled number names nobody."""
    usable = [h for h in handles if h.type in _CONTACT_ORDER and h.status is HandleStatus.ACTIVE and h.value]
    if not usable:
        return None
    return min(usable, key=lambda h: (_CONTACT_ORDER[h.type], -h.last_seen.timestamp(), str(h.handle_id)))


def shown_contact(handle: HandleRecord, *, reveal: bool) -> str:
    """The contact as a person reads it: `+55 11 98117-2201`, or `ana@acme.com.br`; masked as the profile
    page masks it for a role that does not see contacts (`+55 ** *****-2201`, `a***@acme.com.br`)."""
    if handle.type is HandleType.EMAIL:
        return mask_value(handle.type, handle.value, reveal_contacts=reveal)
    digits = "".join(_DIGITS.findall(handle.value.partition("@")[0].split(":", 1)[0]))
    return format_phone(digits, reveal=reveal)


def format_phone(digits: str, *, reveal: bool) -> str:
    """An E.164 number by its digits, grouped as the country writes it: Brazil (`+55 11 98117-2201`, a
    landline `+55 11 3456-7890`) and North America (`+1 415 555-0123`); any other country keeps E.164. Masked,
    it keeps what the mask of an identifier keeps: the country code and the last four digits."""
    if digits.startswith("55") and len(digits) in (12, 13):
        area, number = digits[2:4], digits[4:]
        head, tail = number[:-4], number[-4:]
        return f"+55 {area} {head}-{tail}" if reveal else f"+55 ** {'*' * len(head)}-{tail}"
    if digits.startswith("1") and len(digits) == 11:
        area, head, tail = digits[1:4], digits[4:7], digits[7:]
        return f"+1 {area} {head}-{tail}" if reveal else f"+1 *** ***-{tail}"
    return mask_value(HandleType.PHONE_E164, "+" + digits, reveal_contacts=reveal)


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text if 0 < len(text) <= NAME_CHARS else None
