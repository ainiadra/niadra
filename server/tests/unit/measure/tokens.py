"""Exposure tokens as an SDK builds them (exposure token spec, section 3): the server only parses them."""

import base64
import hashlib
from uuid import UUID

from niadra.domain.measure.attribution import MAX_POSITION, TOKEN_VERSION, TokenError


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def build_token(exposure_id: UUID, position: int) -> str:
    """The token of the card at `position` of the exposure."""
    if not 1 <= position <= MAX_POSITION:
        raise TokenError("bad_position")
    prefix = f"{TOKEN_VERSION}.{_b64(exposure_id.bytes)}.{position}"
    return f"{prefix}.{_b64(hashlib.sha256(prefix.encode()).digest())[:4]}"
