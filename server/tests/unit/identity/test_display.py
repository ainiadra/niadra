"""How the Console names a customer: the name sources and their order, the contact and its mask."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from niadra.domain.common.ids import uuid7
from niadra.domain.identity.display import (
    NameSource,
    contact_handle,
    format_phone,
    is_subject_record,
    name_sources,
    shown_contact,
    system_ids,
)
from niadra.domain.identity.search import is_name_query
from niadra.domain.records import BusinessObject, Fact, HandleRecord, ObjectKey, ProfileAttr
from niadra.domain.vocabulary import (
    FactStatus,
    HandleStatus,
    HandleType,
    Sensitivity,
    Speaker,
    SubjectKind,
    Verification,
)
from niadra.testing.cell import BILLING_SOURCE, WHATSAPP_SOURCE

T0 = datetime(2026, 10, 1, 12, tzinfo=UTC)
T = HandleType
HANDLE = uuid7()


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def fact(predicate: str, value: str, minutes: int = 0, **extra: Any) -> Fact:
    found = Fact(
        uuid7(), HANDLE, predicate, value, "contact", Sensitivity.NORMAL, Speaker.CUSTOMER, 0.9, at(minutes),
        at(minutes), FactStatus.ACTIVE, at(minutes),
    )  # fmt: skip
    return replace(found, **extra)


def attr(slot: str, value: str, minutes: int = 0) -> ProfileAttr:
    return ProfileAttr(HANDLE, slot, value, WHATSAPP_SOURCE, at(minutes))


def record(
    type: str, state: dict[str, object], *, namespace: str = "crm", id: str = "ACC-1"
) -> BusinessObject:
    return BusinessObject(uuid7(), ObjectKey(type, namespace, id), state, T0, BILLING_SOURCE, HANDLE)


def handle(
    type: HandleType,
    value: str,
    minutes: int = 0,
    *,
    scope: str = "",
    status: HandleStatus = HandleStatus.ACTIVE,
) -> HandleRecord:
    return HandleRecord(
        uuid7(), type, scope, SubjectKind.PERSON, value, status, Verification.V0, T0, at(minutes)
    )


def values(found: list[NameSource]) -> list[str]:
    return [s.value for s in found]


def test_a_person_goes_by_the_fullest_name_the_memory_holds_a_slot_before_a_fact() -> None:
    nickname = fact("nickname", "Mari", 9)
    preferred = fact("preferred_name", "Marina", 1)
    full = attr("name", "Marina Souza Lima")
    social = fact("nome_social", "Marina Souza", 0)

    assert values(name_sources(SubjectKind.PERSON, {}, [nickname, preferred], [])) == ["Marina", "Mari"]
    assert values(name_sources(SubjectKind.PERSON, {"name": full}, [preferred], [])) == [
        "Marina Souza Lima",
        "Marina",
    ]
    assert (
        values(name_sources(SubjectKind.PERSON, {"name": full}, [social, preferred], []))[0] == "Marina Souza"
    )
    [slot] = name_sources(SubjectKind.PERSON, {"name": full}, [], [])
    assert slot.item == "attr:0:name"
    [said] = name_sources(SubjectKind.PERSON, {}, [preferred], [])
    assert said.item == f"fact:{preferred.fact_id}"


def test_the_latest_of_one_kind_of_name_comes_first() -> None:
    old, new = fact("preferred_name", "Mari", 0), fact("preferred_name", "Marina", 30)
    assert values(name_sources(SubjectKind.PERSON, {}, [old, new], [])) == ["Marina", "Mari"]


def test_a_name_about_an_object_or_another_subject_or_not_active_names_nobody() -> None:
    rows = [
        fact("name", "Dipirona 500 mg", object_ref=ObjectKey("order", "erp", "1")),
        fact("name", "Ana", about_handle_id=uuid7()),
        fact("name", "Velho", status=FactStatus.RETRACTED),
        fact("name", "   "),
        fact("name", "x" * 201),
        fact("product_or_service_used", "Plano Ouro"),
    ]
    assert name_sources(SubjectKind.PERSON, {}, rows, []) == []


def test_a_company_goes_by_its_own_record_first_its_trade_name_before_its_legal_name() -> None:
    account = record(
        "account", {"razao_social": "Horizonte Hotéis Ltda.", "nome_fantasia": "Horizonte Hotéis"}
    )
    legal = record("empresa", {"legal_name": "Pátio Verde Restaurantes S.A."}, id="EMP-2")
    said = fact("name", "Horizonte")

    found = name_sources(SubjectKind.ACCOUNT, {}, [said], [account])
    assert values(found) == ["Horizonte Hotéis", "Horizonte"]
    assert found[0].item == f"object:{account.object_id}"
    assert values(name_sources(SubjectKind.PARTNER, {}, [], [legal])) == ["Pátio Verde Restaurantes S.A."]


def test_a_person_goes_by_the_memory_before_their_own_record() -> None:
    contact = record("contact", {"name": "Marina S. Lima", "company_name": "Acme"})
    assert values(name_sources(SubjectKind.PERSON, {}, [fact("preferred_name", "Marina")], [contact])) == [
        "Marina",
        "Marina S. Lima",
    ]
    assert (
        values(name_sources(SubjectKind.PERSON, {}, [], [record("cliente", {"nome_fantasia": "Loja"})])) == []
    )


def test_a_subject_record_is_of_a_record_type_or_keyed_by_one_of_its_system_ids() -> None:
    crm = handle(T.SYSTEM_ID, "ACC-2201", scope="crm")
    keys = system_ids([crm, handle(T.PHONE_E164, "+5511981172201")])

    assert keys == [("crm", "ACC-2201")]
    assert is_subject_record(record("Account", {}), [])
    assert is_subject_record(record("registro", {}, namespace="crm", id="ACC-2201"), keys)
    assert not is_subject_record(record("order", {"name": "Toalha"}, namespace="erp", id="ACC-2201"), keys)
    assert not is_subject_record(record("plan", {"name": "Plano Ouro"}), keys)


@pytest.mark.parametrize(
    ("digits", "full", "masked"),
    [
        ("5511981172201", "+55 11 98117-2201", "+55 ** *****-2201"),
        ("551134567890", "+55 11 3456-7890", "+55 ** ****-7890"),
        ("14155550123", "+1 415 555-0123", "+1 *** ***-0123"),
        ("447911123456", "+447911123456", "+44******3456"),
    ],
)
def test_a_phone_reads_as_its_country_writes_it_and_its_mask_keeps_what_an_identifier_mask_keeps(
    digits: str, full: str, masked: str
) -> None:
    assert format_phone(digits, reveal=True) == full
    assert format_phone(digits, reveal=False) == masked


def test_the_contact_is_the_latest_active_phone_whatsapp_included_before_an_e_mail() -> None:
    email = handle(T.EMAIL, "marina.lima@acme.com.br", 50)
    old_phone = handle(T.PHONE_E164, "+5511900000001", 1)
    wa = handle(T.WA_JID, "5511981172201@s.whatsapp.net", 40)
    recycled = handle(T.PHONE_E164, "+5511900000009", 60, status=HandleStatus.SUSPECTED_RECYCLED)
    crm = handle(T.SYSTEM_ID, "CLI-1", 70, scope="crm")

    found = contact_handle([email, old_phone, wa, recycled, crm])
    assert found is wa
    assert shown_contact(wa, reveal=True) == "+55 11 98117-2201"
    assert shown_contact(wa, reveal=False) == "+55 ** *****-2201"
    assert contact_handle([email, crm]) is email
    assert shown_contact(email, reveal=True) == "marina.lima@acme.com.br"
    assert shown_contact(email, reveal=False) == "m***@acme.com.br"
    assert shown_contact(handle(T.WA_ID, "5511981172201"), reveal=True) == "+55 11 98117-2201"
    assert contact_handle([crm, recycled]) is None


@pytest.mark.parametrize(
    ("query", "name"),
    [
        ("Marina Souza", True),
        ("joão", True),
        ("+55 11 98117-2201", False),
        ("ana@acme.com.br", False),
        ("p_ejv2aimtm3ud2iyw", False),
        ("529.982.247-25", False),
        ("12345", False),
        ("x" * 201, False),
    ],
)
def test_a_name_query_is_a_value_no_identifier_format_claims(query: str, name: bool) -> None:
    assert is_name_query(query) is name
