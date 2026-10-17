"""Receipts that missed the 20 ms budget wait here until the queue takes them (5.11).

Past `capacity` the process stops serving sensitive categories (privacy fails closed, L10);
at twice the capacity it stops serving at all, because a read without a receipt never leaves.
"""

from collections.abc import Sequence

from niadra.domain.receipts import ReceiptDraft


class ReceiptBuffer:
    def __init__(self, capacity: int = 10_000) -> None:
        self.capacity = capacity
        self._items: list[ReceiptDraft] = []

    def __len__(self) -> int:
        return len(self._items)

    @property
    def full(self) -> bool:
        return len(self._items) >= self.capacity

    @property
    def exhausted(self) -> bool:
        return len(self._items) >= 2 * self.capacity

    def add(self, drafts: Sequence[ReceiptDraft]) -> None:
        self._items.extend(drafts)

    def take(self) -> list[ReceiptDraft]:
        taken, self._items = self._items, []
        return taken
