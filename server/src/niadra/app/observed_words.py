"""The space's words for its systems' field names and codes, written by the generation model
(`domain.objects.words`).

Called by the observed configuration once per run with what is still unworded; never fails the run: a spent
budget, a model error or an answer that does not parse leave the names as they are, and the next run asks
again.
"""

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from niadra.domain.extract.errors import BudgetExceeded
from niadra.domain.objects.words import (
    NAMES_PER_CALL,
    WORDS_SCHEMA,
    WORDS_SYSTEM,
    Words,
    answered,
    words_request,
)
from niadra.ports.infra import LlmClient

log = logging.getLogger(__name__)

PURPOSE = "extraction"


class ObservedWordsService:
    def __init__(self, llm: LlmClient | None) -> None:
        self._llm = llm

    async def write(
        self,
        space_id: UUID,
        locale: str,
        names: Sequence[str],
        codes: Sequence[str],
        context: Sequence[Mapping[str, Any]] = (),
    ) -> Words:
        """The words for at most `NAMES_PER_CALL` of `names` and of `codes`, in `locale`, with the record
        types they come from as `context` (names only); none on any failure."""
        names, codes = list(names)[:NAMES_PER_CALL], list(codes)[:NAMES_PER_CALL]
        if self._llm is None or not (names or codes):
            return Words()
        try:
            result = await self._llm.complete_json(
                system=WORDS_SYSTEM,
                user=words_request(locale, names, codes, context),
                json_schema=WORDS_SCHEMA,
                reasoning="minimal",
                max_output_tokens=1200,
                space_id=space_id,
                purpose=PURPOSE,
            )
            answer = json.loads(result.content)
        except BudgetExceeded:
            return Words()
        except Exception:
            log.warning("observed words not written", extra={"space_id": str(space_id)}, exc_info=True)
            return Words()
        return Words(answered(answer, names, "fields"), answered(answer, codes, "values"))
