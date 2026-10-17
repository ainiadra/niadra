"""National documents, validated by the check digit of their scheme before they become handles (5.3).

A document is stored only as a keyed HMAC plus ciphertext: the space of a CPF is 10^9 values,
so a plain hash would be reversed in minutes.
"""

import re

from niadra.domain.common.errors import InvalidInput

_NOT_ALNUM = re.compile(r"[^0-9A-Za-z]")
_GENERIC = re.compile(r"^[0-9A-Z]{4,24}$")
_CNPJ = re.compile(r"^[0-9A-Z]{12}[0-9]{2}$")


def normalize_country(scope: str | None) -> str:
    country = (scope or "").split(":", 1)[0].strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", country):
        raise InvalidInput("a national document needs its country as scope")
    return country


def normalize_person_document(country: str, value: str) -> str:
    """Validates a person's national id (CPF in Brazil); other countries get a format check only."""
    compact = _NOT_ALNUM.sub("", value).upper()
    if country == "BR":
        compact = _restore_zeros(compact, 11)
        if not is_valid_cpf(compact):
            raise InvalidInput("the CPF failed its check digits")
        return compact
    return _generic(compact)


def normalize_org_registry(country: str, value: str) -> str:
    """Validates a company registry (CNPJ in Brazil, alphanumeric since 2026)."""
    compact = _NOT_ALNUM.sub("", value).upper()
    if country == "BR":
        compact = _restore_zeros(compact, 14)
        if not is_valid_cnpj(compact):
            raise InvalidInput("the CNPJ failed its check digits")
        return compact
    return _generic(compact)


def is_valid_cpf(digits: str) -> bool:
    if not re.fullmatch(r"\d{11}", digits):
        return False
    numbers = [int(d) for d in digits]
    for size in (9, 10):
        total = sum(n * w for n, w in zip(numbers[:size], range(size + 1, 1, -1), strict=True))
        if numbers[size] != (total * 10 % 11) % 10:
            return False
    return True


def is_valid_cnpj(value: str) -> bool:
    # Each character counts as its ASCII code minus 48, which keeps digits as they are (IN RFB 2229/2024).
    if not _CNPJ.fullmatch(value):
        return False
    numbers = [ord(c) - 48 for c in value]
    for size in (12, 13):
        weights = [*range(size - 7, 1, -1), *range(9, 1, -1)]
        remainder = sum(n * w for n, w in zip(numbers[:size], weights, strict=True)) % 11
        if numbers[size] != (0 if remainder < 2 else 11 - remainder):
            return False
    return True


def looks_like_cpf(value: str) -> bool:
    return is_valid_cpf(_restore_zeros(_NOT_ALNUM.sub("", value), 11))


def _restore_zeros(compact: str, size: int) -> str:
    """A spreadsheet stores a document as a number and drops its leading zeros (`012.345.678-90` becomes
    `1234567890`): up to two come back, and the check digits still decide."""
    if compact.isdigit() and size - 2 <= len(compact) < size:
        return compact.zfill(size)
    return compact


def _generic(compact: str) -> str:
    if not _GENERIC.fullmatch(compact):
        raise InvalidInput("the document has an invalid format")
    return compact
