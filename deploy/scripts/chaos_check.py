# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx"]
# ///
"""The chaos test of both SDKs against a cell: Niadra goes down in the middle of a conversation (its process
killed, its network silent, 503s, answers past the deadline), and the agent goes on with the last good pack and
the opt-out holding; once Niadra is back, the turn records, events and declarations arrive, each stored once.

    uv run scripts/chaos_check.py --api http://127.0.0.1:21109 --control http://127.0.0.1:21100 \\
        --bootstrap "$LOCAL_E2E_DIR/bootstrap.json" --python-sdk ../niadra-sdk-python --ts-sdk ../niadra-sdk-ts \\
        [--runs 3]

Against a cell that scripts/local-e2e.sh left running (--keep): the api at LOCAL_E2E_PORT+9, the control plane at
LOCAL_E2E_PORT. The tests are the SDKs' own (`tests/test_chaos.py` in Python, `test/chaos.test.ts` in
TypeScript), which without a cell run against a stand-in; here they run against the cell through their proxy,
each with its checkout's own dependencies (`uv sync` and `pnpm install` done).

The check logs in to the control plane as the sandbox's admin (bootstrap.json), turns the space's `turns` and
`coordination` features on when they are off, and mints a key of the `whatsapp` source with the scopes `track`,
`context` and `coordinate`. The subject is the e2e's customer, whose memory makes the pack worth keeping. The key
is never printed. Each run of each SDK prints PASS or FAIL, then the measures of every run: the turn latency
during the outage and the seconds to drain once Niadra is back. The exit code is the number of failed runs.
"""

import argparse
import json
import os
import statistics
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from sdk_session import Run, Sandbox

# The e2e's customer (k8s/charts/niadra/files/e2e_sandbox.py, PHONE): extraction gave them memory.
CUSTOMER = "+5511900001234"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", required=True, help="the data API's address")
    parser.add_argument("--control", required=True, help="the control plane's address")
    parser.add_argument("--bootstrap", type=Path, required=True)
    parser.add_argument("--python-sdk", type=Path, required=True, help="a niadra-sdk-python checkout")
    parser.add_argument("--ts-sdk", type=Path, required=True, help="a niadra-sdk-ts checkout, installed")
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    sandbox = Sandbox(args.api, args.control, json.loads(args.bootstrap.read_text()), "chaos check")
    run = Run()
    held: dict[str, Any] = {}

    def key() -> str:
        sandbox.login()
        sandbox.features_on("turns", "coordination")
        held["key"] = sandbox.key("whatsapp", "track", "context", "coordinate", ready="/v1/suppressions/salt")
        return "a key with the coordinate scope, served by the cell"

    if not run.step("the sandbox's source key", key):
        return run.failed
    report = Path(tempfile.mkdtemp(prefix="niadra-chaos-")) / "measures.jsonl"
    env = {
        **os.environ,
        "NIADRA_CHAOS_API": args.api,
        "NIADRA_CHAOS_KEY": held["key"],
        "NIADRA_CHAOS_SUBJECT": CUSTOMER,
        "NIADRA_CHAOS_REPORT": str(report),
    }
    commands = {
        "python": (["uv", "run", "pytest", "tests/test_chaos.py", "-q", "-o", "addopts="], args.python_sdk),
        "typescript": (["npx", "vitest", "run", "test/chaos.test.ts"], args.ts_sdk),
    }
    for n in range(1, args.runs + 1):
        for sdk, (command, cwd) in commands.items():

            def chaos(command: list[str] = command, cwd: Path = cwd) -> str:
                done = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=False)
                if done.returncode != 0:
                    tail = (done.stdout + done.stderr).strip().splitlines()[-15:]
                    raise AssertionError("\n".join(tail))
                return "killed, blackhole, 503 and slow"

            run.step(f"{sdk} chaos, run {n}", chaos)
    measures = [json.loads(line) for line in report.read_text().splitlines()] if report.exists() else []
    for sdk in ("python", "typescript"):
        for mode in ("killed", "blackhole", "503", "slow"):
            rows = [m for m in measures if m["sdk"] == sdk and m["mode"] == mode]
            if not rows:
                continue
            turns = [t for m in rows for t in m["turn_ms"]]
            drains = [m["drain_s"] for m in rows]
            print(
                f"      {sdk:10} {mode:9} turn during the outage: median {statistics.median(turns):.0f} ms, "
                f"max {max(turns):.0f} ms; drain after recovery: median {statistics.median(drains):.2f} s, "
                f"max {max(drains):.2f} s; resent and deduplicated: {sum(m['resent_duplicates'] for m in rows)} "
                f"({len(rows)} results)"
            )
    return run.failed


if __name__ == "__main__":
    raise SystemExit(main())
