"""The control plane's own secret sealing, for the few secrets it must use itself (an OIDC client secret).

AES-256-GCM with a random 96-bit nonce; the stored bytes are `version || nonce || ciphertext`. The key is
NIADRA_CONTROL_SECRETS_KEY when the deployment sets it (32 bytes, raw, base64 or hex); otherwise it is
derived from the key pepper with HKDF under a label of its own, so no two uses of the pepper share a key.
"""

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from niadra.adapters.outbound.keys.kek import load_kek
from niadra.domain.common.errors import Unavailable

_VERSION = b"\x01"
_NONCE_BYTES = 12
_LABEL = b"niadra-control-secrets"


class AesGcmSealer:
    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("the control secrets key must have 32 bytes")
        self._aead = AESGCM(key)

    @classmethod
    def from_pepper(cls, pepper: bytes) -> "AesGcmSealer":
        return cls(HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_LABEL).derive(pepper))

    @classmethod
    def from_env(cls, pepper: bytes) -> "AesGcmSealer":
        if os.environ.get("NIADRA_CONTROL_SECRETS_KEY"):
            return cls(load_kek("env:NIADRA_CONTROL_SECRETS_KEY"))
        return cls.from_pepper(pepper)

    def seal(self, value: str, aad: bytes) -> bytes:
        nonce = os.urandom(_NONCE_BYTES)
        return _VERSION + nonce + self._aead.encrypt(nonce, value.encode(), aad)

    def unseal(self, sealed: bytes, aad: bytes) -> str:
        if not sealed.startswith(_VERSION) or len(sealed) < 1 + _NONCE_BYTES + 16:
            raise Unavailable("a sealed secret has an unknown format")
        nonce = sealed[1 : 1 + _NONCE_BYTES]
        try:
            return self._aead.decrypt(nonce, sealed[1 + _NONCE_BYTES :], aad).decode()
        except InvalidTag:
            # A key change or a value moved to another row: never a reason to fall back to anything.
            raise Unavailable("a sealed secret could not be opened") from None
