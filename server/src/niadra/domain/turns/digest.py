"""The digest of a recorded value (Turn Record 6.2.1): SHA-256 over its canonical JSON (RFC 8785), so the
same value has the same digest whichever producer computed it."""

import hashlib
import json
from decimal import Decimal
from typing import Any


def canonical_json(value: Any) -> bytes:
    """RFC 8785: keys sorted by UTF-16 code units at every depth, no whitespace, strings escaped only where
    JSON must, numbers as ECMAScript writes them. Raises `ValueError` on a value JSON cannot hold."""
    return _canonical(value).encode()


def digest(value: Any) -> tuple[str, int]:
    """`sha256:<hex>` and the size of the canonical form, in bytes."""
    body = canonical_json(value)
    return "sha256:" + hashlib.sha256(body).hexdigest(), len(body)


def _canonical(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return json.dumps(value)
    if isinstance(value, int | float):
        return _number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list | tuple):
        return "[" + ",".join(_canonical(item) for item in value) + "]"
    if isinstance(value, dict):
        keys = sorted(value, key=_utf16)
        return (
            "{" + ",".join(f"{json.dumps(k, ensure_ascii=False)}:{_canonical(value[k])}" for k in keys) + "}"
        )
    raise ValueError(f"not a JSON value: {type(value).__name__}")


def _utf16(key: Any) -> bytes:
    if not isinstance(key, str):
        raise ValueError("object keys are strings")
    return key.encode("utf-16-be", errors="surrogatepass")


def _number(x: float) -> str:
    """The shortest digits that read back the same double, in exponent form from 1e21 up and below 1e-6."""
    number = float(x)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError("NaN and infinities are not JSON")
    if number == 0:
        return "0"
    if number < 0:
        return "-" + _number(-number)
    _, raw, exponent = Decimal(repr(number)).as_tuple()
    assert isinstance(exponent, int)
    digits = [str(d) for d in raw]
    while len(digits) > 1 and digits[-1] == "0":
        digits.pop()
        exponent += 1
    s, k = "".join(digits), len(digits)
    n = exponent + k
    if k <= n <= 21:
        return s + "0" * (n - k)
    if 0 < n <= 21:
        return s[:n] + "." + s[n:]
    if -6 < n <= 0:
        return "0." + "0" * -n + s
    tail = f"e{'+' if n - 1 >= 0 else '-'}{abs(n - 1)}"
    return s + tail if k == 1 else f"{s[0]}.{s[1:]}{tail}"
