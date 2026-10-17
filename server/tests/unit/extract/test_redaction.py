from niadra.domain.extract.redaction import Redactor
from niadra.ports.infra import PiiSpan


def test_same_value_gets_the_same_placeholder_across_texts() -> None:
    r = Redactor()
    a = r.redact("mail ana@x.com now", [PiiSpan(5, 14, "email")])
    b = r.redact("again ana@x.com and bo@y.com", [PiiSpan(6, 15, "email"), PiiSpan(20, 28, "email")])
    assert a == "mail [email_1] now"
    assert b == "again [email_1] and [email_2]"
    assert r.restore("changed to [email_2]") == "changed to bo@y.com"


def test_overlapping_spans_widen_the_redaction() -> None:
    r = Redactor()
    text = "cpf 123.456.789-00 ok"
    out = r.redact(text, [PiiSpan(4, 12, "cpf"), PiiSpan(8, 18, "phone"), PiiSpan(50, 60, "phone")])
    assert out == "cpf [cpf_1] ok"
    assert r.restore(out) == text


def test_unknown_placeholders_are_left_alone() -> None:
    assert Redactor().restore("see [email_9]") == "see [email_9]"
