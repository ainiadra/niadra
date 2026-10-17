"""Runs the site's copyable Python against the published SDK, so the code behind a Copy button never drifts.

The home snippet (src/components/Artifacts.tsx, SNIPPET) and every ```python block of the blog posts run
against `niadra` from PyPI and its in-memory emulator (niadra_mock, shipped in the same package), with
stand-ins for what the reader already has: the caller's number, the call id, the model, the prompt.

A snippet passes when it raises nothing, the SDK logs no warning (a call the emulator refused or could
not answer), every context comes back with its text block, and each track() turn reaches the emulator
(two per track() inside the exchange loop, one per bare track()).

Usage: pip install niadra && python scripts/python-snippets.py
"""

import logging
import os
import re
import sys
import threading
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

from niadra_mock import MOCK_KEY, MockApp

ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDERS = {"${before}": "before", "${during}": "during", "${after}": "after"}


class _Quiet(WSGIRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


class _Warnings(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.seen: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.seen.append(record.getMessage())


class _Model:
    """The reader's model client: takes the system prompt parts, the tool definitions and the history."""

    def chat(self, system: list[str], tools: list[dict], messages: list[dict]) -> str:
        assert all(isinstance(part, str) for part in system), "every system part must be text"
        assert tools and all(isinstance(t, dict) for t in tools), "tools must be the kit's definitions"
        return "The credit is on your next invoice."


def snippets() -> list[tuple[str, str]]:
    found = []
    artifacts = (ROOT / "src/components/Artifacts.tsx").read_text()
    home = re.search(r"const SNIPPET = \([^)]*\) => `(.*?)`", artifacts, re.S)
    if not home:
        sys.exit("SNIPPET not found in src/components/Artifacts.tsx")
    code = home.group(1)
    for key, word in PLACEHOLDERS.items():
        code = code.replace(key, word)
    found.append(("src/components/Artifacts.tsx", code))
    for post in sorted((ROOT / "src/content/blog").glob("*.md")):
        for i, block in enumerate(re.finditer(r"```python\n(.*?)```", post.read_text(), re.S)):
            found.append((f"{post.relative_to(ROOT)}#{i + 1}", block.group(1)))
    return found


def expected_turns(code: str) -> int:
    """A track() inside `for role, text in turns:` sends the two turns of the exchange; a bare track() sends one;
    a conversation's customer() or agent() sends one."""
    lines = code.splitlines()
    total = 0
    for i, line in enumerate(lines):
        if "niadra.track(" in line:
            previous = next((p for p in reversed(lines[:i]) if p.strip()), "")
            total += 2 if previous.strip().startswith("for ") else 1
        elif re.search(r"\b\w+\.(customer|agent)\(", line):
            total += 1
    return total


def main() -> int:
    mock = MockApp()
    server = make_server("127.0.0.1", 0, mock.wsgi, handler_class=_Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ["NIADRA_API_KEY"] = MOCK_KEY
    os.environ["NIADRA_BASE_URL"] = f"http://127.0.0.1:{server.server_port}"
    warnings = _Warnings()
    logging.getLogger("niadra").addHandler(warnings)

    failed = 0
    found = snippets()
    for name, code in found:
        warnings.seen.clear()
        before = len(mock.cell.events)
        scope = {
            "caller_id": "+5511987654321",
            "sender_phone": "+5511987654321",
            "call_id": "call-1",
            "thread_id": "wa-1",
            "question": "Where is the credit for the missed visit?",
            "reply": "It is on your next invoice.",
            "model": _Model(),
            "YOUR_PROMPT": "You help our customers.",
            "history": [],
        }
        try:
            exec(compile(code, name, "exec"), scope)
            client = scope["niadra"]
            client.flush()
            stored = len(mock.cell.events) - before
            expected = expected_turns(code)
            problems = list(warnings.seen)
            if stored != expected:
                problems.append(f"{stored} of {expected} tracked turns reached the emulator")
            ctx = scope.get("ctx")
            if ctx is not None and not isinstance(ctx.system_block, str):
                problems.append("the context has no text block")
            client.close()
        except Exception as exc:  # noqa: BLE001 - any error is the finding
            problems = [f"{type(exc).__name__}: {exc}"]
        failed += bool(problems)
        print(("FAIL " if problems else "ok   ") + name + ("".join(f"\n     {p}" for p in problems)))
    server.shutdown()
    print(f"{len(found)} snippets, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
