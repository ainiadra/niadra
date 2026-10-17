"""What the Console keeps for a person across browsers: a few small JSON values by key (5.22).

The store is for the Console's own state (the tutorials done, the welcome seen), so it is capped small:
nothing here is customer data, and nothing here is read by the cells.
"""

import json
import re
from typing import Any

from niadra.domain.common.errors import InvalidInput

MAX_KEYS = 16
MAX_VALUE_BYTES = 4096
_KEY = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


def check_preference(key: str, value: Any, existing: frozenset[str]) -> None:
    if not _KEY.match(key):
        raise InvalidInput(
            "a preference key is 1 to 64 of a-z, 0-9, '_', '.' and '-', starting with a letter"
        )
    if value is None:
        raise InvalidInput("a preference needs a value")
    if len(json.dumps(value, separators=(",", ":")).encode()) > MAX_VALUE_BYTES:
        raise InvalidInput(f"a preference value is at most {MAX_VALUE_BYTES} bytes of JSON")
    if key not in existing and len(existing) >= MAX_KEYS:
        raise InvalidInput(f"a person keeps at most {MAX_KEYS} preferences")
