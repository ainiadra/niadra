from datetime import datetime, timedelta
from itertools import count
from typing import Any
from uuid import UUID

from niadra.domain.common.ids import uuid7
from niadra.domain.receipts import ReceiptDraft
from niadra.domain.vocabulary import Audience, ReceiptKind, ReceiptSurface
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE

_seq = count(1)
BASE = datetime.fromisoformat("2026-09-22T12:00:00+00:00")


def draft(
    *,
    producer: str = "read-1",
    seq: int | None = None,
    at: datetime | None = None,
    space_id: UUID = SPACE_ID,
    **fields: Any,
) -> ReceiptDraft:
    seq = next(_seq) if seq is None else seq
    read_at = at or BASE + timedelta(milliseconds=seq)
    values: dict[str, Any] = {
        "receipt_id": uuid7(int(read_at.timestamp() * 1000)),
        "space_id": space_id,
        "producer_id": producer,
        "producer_seq": seq,
        "kind": ReceiptKind.READ,
        "surface": ReceiptSurface.PACK,
        "audience": Audience.CUSTOMER_AGENT,
        "read_at": read_at,
        "source_id": WHATSAPP_SOURCE,
    }
    values.update(fields)
    return ReceiptDraft(**values)
