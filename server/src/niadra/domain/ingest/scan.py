"""The scan of a third party's content on arrival (L27; 24.3.10): deterministic rules, no model.

No content is an instruction. Text that talks to the model (asks it to ignore what it was told, speaks as the
system or the assistant, tries to close the envelope the pack wraps content in) is `flagged` and never reaches
a model's read. Text the rules find nothing in is `clean`, unless its type or source asks for a full scan
(`scan: required`): then it stays `pending`, which blocks a model's read as `flagged` does, until the decision
model's batch or a person clears it. Content Niadra does not hold (a pointer) cannot be read here.
"""

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum


class ScanState(StrEnum):
    PENDING = "pending"
    CLEAN = "clean"
    FLAGGED = "flagged"


@dataclass(frozen=True, slots=True)
class ScanResult:
    state: ScanState
    reason: str | None = None


_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "override",
        re.compile(
            r"\b(ignore|disregard|forget|override|ignora|ignorar|desconsidere|desconsidera|esqueca|esquece|olvida)"
            r"\b.{0,60}?\b(instructions?|rules|prompts?|instrucoes|regras|orientacoes|instrucciones|reglas)\b"
        ),
    ),
    (
        "persona",
        re.compile(r"\b(you are now|from now on you are|voce agora e|a partir de agora voce e|ahora eres)\b"),
    ),
    ("prompt", re.compile(r"\b(system prompt|prompt do sistema|prompt del sistema|developer message)\b")),
    ("role", re.compile(r"(^|\n)\s*(system|assistant|developer|sistema|assistente|asistente)\s*:")),
    ("markup", re.compile(r"</?\s*(system|instructions?|niadra-data)\b")),
)


def scan(text: str, *, required: bool) -> ScanResult:
    """The rules' verdict on `text`; `required` keeps what they pass pending for the full scan."""
    folded = _fold(text)
    for name, rule in _RULES:
        if rule.search(folded):
            return ScanResult(ScanState.FLAGGED, f"rule:{name}")
    return ScanResult(ScanState.PENDING if required else ScanState.CLEAN)


def _fold(text: str) -> str:
    """Lowercase without accents, so `instruções` and `instrucoes` read alike."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))
