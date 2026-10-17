import pytest

from niadra.domain.common.errors import InvalidInput
from niadra.domain.identity.blocked import is_fixed_blocked, is_tenant_blocked
from niadra.domain.identity.documents import is_valid_cnpj, is_valid_cpf
from niadra.domain.identity.handles import HandleKey, classify, gmail_canonical, is_partial, normalize_handle
from niadra.domain.identity.masking import mask_value
from niadra.domain.identity.phones import normalize_phone
from niadra.domain.vocabulary import HandleType, SubjectKind

T = HandleType


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+55 (11) 98765-4321", "+5511987654321"),
        ("(11) 98765-4321", "+5511987654321"),
        ("11 8765-4321", "+5511987654321"),  # legacy mobile gains the ninth digit
        ("+55 11 3333-4444", "+551133334444"),  # a landline never does
        ("0055 21 2555 1234", "+552125551234"),
        ("+1 (415) 555-2671", "+14155552671"),
        ("0 21 11 98765-4321", "+5511987654321"),  # a carrier code before the area code
        ("021 11 8765 4321", "+5511987654321"),
        ("14155550123", "+14155550123"),  # eleven digits without a 9 are not a Brazilian mobile
        ("2025550123", "+2025550123"),  # 20 is no Brazilian area code
        ("+44 (0) 20 7946 0958", "+442079460958"),  # the trunk zero is never dialed
        ("\u202a+55 11 98765-4321\u202c", "+5511987654321"),  # as WhatsApp copies a number
        (
            "\uff0b\uff15\uff15 \uff11\uff11 \uff19\uff18\uff17\uff16\uff15 \uff14\uff13\uff12\uff11",
            "+5511987654321",
        ),
        ("tel:+55-11-98765-4321", "+5511987654321"),
        ("+52 1 55 1234 5678", "+525512345678"),  # Mexico's old mobile 1, still in WhatsApp ids
        ("+52 55 1234 5678", "+525512345678"),
        ("+54 9 11 2345-6789", "+541123456789"),  # Argentina's mobile 9
        ("+54 11 2345-6789", "+541123456789"),
    ],
)
def test_phones_normalize_to_e164(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    ("wa_id", "phone"),
    [
        ("5215512345678", "+525512345678"),
        ("5491123456789", "+541123456789"),
        ("551187654321", "+5511987654321"),
    ],
)
def test_a_whatsapp_id_and_the_number_a_call_shows_are_one_phone(wa_id: str, phone: str) -> None:
    """WhatsApp keeps mobile prefixes a phone call does not show: both reach the same phone handle."""
    derived = normalize_handle(T.WA_ID, wa_id).derived[0].key
    assert derived == normalize_handle(T.PHONE_E164, phone).key


@pytest.mark.parametrize("raw", ["12", "+55 11 abc", "+0 11 1234 5678"])
def test_garbage_is_not_a_phone(raw: str) -> None:
    with pytest.raises(InvalidInput):
        normalize_phone(raw)


def test_legacy_wa_id_implies_the_modern_phone() -> None:
    normalized = normalize_handle(T.WA_ID, "551187654321")
    assert normalized.key == HandleKey(T.WA_ID, "", "5511987654321")
    assert [d.key for d in normalized.derived] == [HandleKey(T.PHONE_E164, "", "+5511987654321")]


def test_landline_wa_id_keeps_its_digits() -> None:
    assert normalize_handle(T.WA_ID, "551133334444").derived[0].key.value == "+551133334444"


@pytest.mark.parametrize(
    "jid",
    ["120363012345678901@g.us", "5511987654321-1600000000@g.us", "status@broadcast", "1203630@newsletter"],
)
@pytest.mark.parametrize("declared", [T.WA_JID, T.EMAIL, T.PHONE_E164])
def test_a_whatsapp_group_or_list_is_never_a_handle(declared: HandleType, jid: str) -> None:
    """A group's id used to read as an e-mail address: every member's events would join through it."""
    assert classify(jid) is None
    with pytest.raises(InvalidInput, match="names no person"):
        normalize_handle(declared, jid)


def test_lid_drops_the_device() -> None:
    assert normalize_handle(T.WA_LID, "123456789012345:12@lid").key.value == "123456789012345@lid"


@pytest.mark.parametrize(
    "raw",
    ["mailto:Ana@Acme.com.br", "Ana Silva <ana@acme.com.br>", "<ANA@acme.com.br>", "\u200bana@acme.com.br"],
)
def test_an_email_as_copied_from_a_header_or_link(raw: str) -> None:
    assert normalize_handle(T.EMAIL, raw).key.value == "ana@acme.com.br"


def test_opaque_ids_are_never_rewritten() -> None:
    assert normalize_handle(T.SYSTEM_ID, "<x-1>", "crm").key.value == "<x-1>"


def test_wa_jid_drops_the_device_and_implies_the_phone() -> None:
    normalized = normalize_handle(T.WA_JID, "551187654321:12@s.whatsapp.net")
    assert normalized.key.value == "5511987654321@s.whatsapp.net"
    assert normalized.derived[0].key.value == "+5511987654321"


@pytest.mark.parametrize(
    ("declared", "value", "expected"),
    [
        (T.PHONE_E164, "Ana@Example.org", T.EMAIL),
        (T.EMAIL, "+55 11 98765-4321", T.PHONE_E164),
        (T.WA_ID, "+55 11 98765-4321", T.WA_ID),
        (T.EMAIL, "1234567@lid", T.WA_LID),
        (T.SYSTEM_ID, "ana@example.org", T.SYSTEM_ID),  # opaque ids keep the declared type
    ],
)
def test_contact_values_are_classified_by_format(
    declared: HandleType, value: str, expected: HandleType
) -> None:
    assert normalize_handle(declared, value, "crm" if declared is T.SYSTEM_ID else None).type is expected


def test_classify_leaves_opaque_values_alone() -> None:
    assert classify("cust-0042") is None


def test_email_is_lowercased_and_gmail_rules_are_only_a_hint() -> None:
    normalized = normalize_handle(T.EMAIL, " Ana.Silva+promo@GMAIL.com ")
    assert normalized.key.value == "ana.silva+promo@gmail.com"
    assert gmail_canonical(normalized.key.value) == "anasilva@gmail.com"
    assert gmail_canonical("ana.silva@outlook.com") is None


def test_cpf_check_digits() -> None:
    assert is_valid_cpf("52998224725")
    assert not is_valid_cpf("52998224724")
    assert normalize_handle(T.GOV_ID_HMAC, "529.982.247-25").key == HandleKey(
        T.GOV_ID_HMAC, "BR", "52998224725"
    )


def test_a_document_that_lost_its_leading_zeros_in_a_spreadsheet() -> None:
    assert normalize_handle(T.GOV_ID_HMAC, "1234567890").key == HandleKey(T.GOV_ID_HMAC, "BR", "01234567890")
    assert normalize_handle(T.GOV_ID_HMAC, "1234567890", "BR").key.value == "01234567890"
    assert normalize_handle(T.ORG_REGISTRY_HMAC, "6990404000156").key.value == "06990404000156"
    with pytest.raises(InvalidInput):
        normalize_handle(T.GOV_ID_HMAC, "1234567891", "BR")  # the check digits still decide


def test_cnpj_check_digits_including_the_alphanumeric_format() -> None:
    assert is_valid_cnpj("11222333000181")
    assert is_valid_cnpj("12ABC34501DE35")
    assert not is_valid_cnpj("11222333000182")
    key = normalize_handle(T.ORG_REGISTRY_HMAC, "12.ABC.345/01DE-35").key
    assert key == HandleKey(T.ORG_REGISTRY_HMAC, "BR", "12ABC34501DE35")


def test_other_countries_get_a_format_check_and_need_a_scope() -> None:
    assert normalize_handle(T.GOV_ID_HMAC, "123-45-6789", "us").key == HandleKey(
        T.GOV_ID_HMAC, "US", "123456789"
    )
    with pytest.raises(InvalidInput):
        normalize_handle(T.GOV_ID_HMAC, "123-45-6789")


def test_scoped_types_need_their_scope() -> None:
    for type in (T.WA_BSUID, T.SYSTEM_ID):
        with pytest.raises(InvalidInput):
            normalize_handle(type, "abc")
    assert normalize_handle(T.WA_BSUID, "BR.abc", "waba-1").key.scope == "waba-1"


def test_subject_kind_per_handle() -> None:
    assert normalize_handle(T.ORG_REGISTRY_HMAC, "11222333000181").subject_kind is SubjectKind.ACCOUNT
    assert normalize_handle(T.EMAIL_DOMAIN, "acme.com").subject_kind is SubjectKind.ACCOUNT
    partner = normalize_handle(T.SYSTEM_ID, "t-9", "tms", SubjectKind.PARTNER)
    assert partner.subject_kind is SubjectKind.PARTNER
    with pytest.raises(InvalidInput):
        normalize_handle(T.GOV_ID_HMAC, "52998224725", subject_kind=SubjectKind.ACCOUNT)
    with pytest.raises(InvalidInput):
        normalize_handle(T.ORG_REGISTRY_HMAC, "11222333000181", subject_kind=SubjectKind.PERSON)


def test_error_messages_never_carry_the_value() -> None:
    with pytest.raises(InvalidInput) as raised:
        normalize_handle(T.GOV_ID_HMAC, "529.982.247-24")
    assert "529" not in str(raised.value)


@pytest.mark.parametrize(
    "key",
    [
        HandleKey(T.PHONE_E164, "", "+5511999999999"),
        HandleKey(T.PHONE_E164, "", "+551112345678"),
        HandleKey(T.GOV_ID_HMAC, "BR", "11111111111"),
        HandleKey(T.GOV_ID_HMAC, "BR", "12345678909"),
        HandleKey(T.EMAIL, "", "teste@empresa.com.br"),
        HandleKey(T.EMAIL, "", "ana@example.com"),
        HandleKey(T.EMAIL, "", "ana@qa.test"),
        HandleKey(T.EMAIL_DOMAIN, "", "gmail.com"),
        HandleKey(T.SYSTEM_ID, "crm", "0000"),
        HandleKey(T.SYSTEM_ID, "crm", "null"),
    ],
)
def test_placeholders_are_blocked(key: HandleKey) -> None:
    assert is_fixed_blocked(key)


def test_real_values_are_not_blocked() -> None:
    assert not is_fixed_blocked(HandleKey(T.PHONE_E164, "", "+5511987654321"))
    assert not is_fixed_blocked(HandleKey(T.EMAIL, "", "ana.silva@gmail.com"))
    assert not is_fixed_blocked(HandleKey(T.EMAIL_DOMAIN, "", "acme.com.br"))


def test_tenant_list_is_normalized_before_comparing() -> None:
    key = normalize_handle(T.PHONE_E164, "+55 11 98765-4321").key
    assert is_tenant_blocked(key, {"phone_e164": ["(11) 98765-4321"]})
    assert not is_tenant_blocked(key, {"email": ["(11) 98765-4321"]})


def test_partial_profiles_have_only_channel_private_ids() -> None:
    assert is_partial({T.WA_BSUID})
    assert is_partial({T.WA_LID, T.ANON_ID})
    assert not is_partial({T.WA_BSUID, T.PHONE_E164})
    assert not is_partial(set())


@pytest.mark.parametrize(
    ("type", "value", "reveal", "shown"),
    [
        (T.PHONE_E164, "+5511987654321", False, "+55*******4321"),
        (T.PHONE_E164, "+5511987654321", True, "+5511987654321"),
        (T.EMAIL, "ana@acme.com", False, "a***@acme.com"),
        (T.GOV_ID_HMAC, "52998224725", True, "*********25"),
        (T.SYSTEM_ID, "cust-42", False, "cust-42"),
    ],
)
def test_masking_by_role(type: HandleType, value: str, reveal: bool, shown: str) -> None:
    assert mask_value(type, value, reveal_contacts=reveal) == shown
