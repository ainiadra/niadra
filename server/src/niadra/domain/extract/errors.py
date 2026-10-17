"""Errors of the extraction path that other layers react to."""

from niadra.domain.common.errors import RateLimited


class BudgetExceeded(RateLimited):
    """The space spent its daily LLM cap for this purpose; the task waits for the next day (5.9)."""

    code = "llm_budget_exceeded"

    def __init__(self, retry_after_s: int, detail: str = "", cap_micros: int | None = None) -> None:
        super().__init__(retry_after_s, detail)
        self.cap_micros = cap_micros
