"""Loads the key-encryption key (KEK) of the local key vault.

`env:NAME` reads an environment variable; anything else is a file path (a mounted secret).
The value is 32 raw bytes, or their base64 or hex encoding.
"""

import base64
import binascii
import os
from pathlib import Path

KEK_BYTES = 32


def load_kek(path_or_env: str) -> bytes:
    if path_or_env.startswith("env:"):
        raw = os.environ.get(path_or_env[4:], "").encode()
    else:
        raw = Path(path_or_env).read_bytes()
    kek = _decode(raw.strip()) if len(raw) != KEK_BYTES else raw
    if len(kek) != KEK_BYTES:
        raise ValueError("the KEK must be 32 bytes, raw, base64 or hex")
    return kek


def _decode(text: bytes) -> bytes:
    if len(text) == 2 * KEK_BYTES:
        try:
            return bytes.fromhex(text.decode())
        except ValueError:
            pass
    try:
        return base64.b64decode(text, validate=True)
    except binascii.Error:
        return text
