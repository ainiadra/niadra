# /// script
# requires-python = ">=3.11"
# dependencies = ["niadra[postgres] @ git+https://github.com/ainiadra/niadra-sdk-python@main"]
# ///
"""The object types' end-to-end check from the company's side, with the Python SDK:

- Deriving a type (study flow 13): `niadra types derive` reads a synthetic sample database's catalog and
  proposes the type, which the sandbox declares. `niadra types derive --check` then finds no drift (exit 0).
  After a new value in the status column's CHECK, it finds drift (exit 1): Niadra gets only the fingerprint
  and the counts, opens one data issue of kind `drift` and gives the same issue to the same drift again.
- Field access at a tool's output (study flow 16): a type whose health declaration the voice agent may read
  masked and the WhatsApp agent not at all. A tool declared with `mask_output=True` returns the declaration,
  and each agent's model gets it as its key's SDK profile says: `[masked]`, or no field. The turn record holds
  what the model got.

    uv run scripts/types_check.py --api http://127.0.0.1:19809 --control http://127.0.0.1:19800 \\
        --bootstrap "$LOCAL_E2E_DIR/bootstrap.json" --sample-dsn postgresql://postgres@127.0.0.1:19810/postgres

Against a cell that scripts/local-e2e.sh left running (--keep): the api at LOCAL_E2E_PORT+9, the control
plane at LOCAL_E2E_PORT, and `--sample-dsn` any PostgreSQL where the check may create a database and drop it
(the e2e's own server at LOCAL_E2E_PORT+10 serves). The SDK comes from its main branch; to check a checkout
instead:

    uv run --no-project --with-editable '../niadra-sdk-python[postgres]' python scripts/types_check.py ...

The check logs in to the control plane as the sandbox's admin (bootstrap.json), turns on the features it
needs, declares its own two types (other types stay) and mints keys of the sandbox's sources (the WhatsApp
one with the `replay` scope, to read its turn back). No key is ever printed. Each step prints PASS or FAIL;
the exit code is the number of failed steps.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import psycopg
from niadra import Niadra, phone
from niadra.options import CacheOptions, TurnOptions
from niadra.turns.mask import MASKED
from sdk_session import READY_TIMEOUT_S, Run, Sandbox, bearer, expect

# Synthetic customer: a number in the range Anatel reserves for fiction and tests.
CUSTOMER = phone("+5511900005679")
# A turn is applied by the turns worker after the route takes it.
TURN_TIMEOUT_S = 120
CONTRACT_TYPE = "sdk_contract"
BUILD = Niadra.build(prompts={"proposal": "v1"}, model="demo-model-1")
SAMPLE_TABLE = """
CREATE TABLE contracts (
    id text PRIMARY KEY,
    status text NOT NULL CHECK (status IN ('draft', 'signed')),
    total numeric(12, 2) NOT NULL,
    signed_at timestamptz,
    notes jsonb
)"""
NEW_STATE = """
ALTER TABLE contracts DROP CONSTRAINT contracts_status_check;
ALTER TABLE contracts ADD CONSTRAINT contracts_status_check CHECK (status IN ('draft', 'signed', 'cancelled'))"""
# A synthetic value the WhatsApp agent's model must never receive, looked for in everything it is served.
CANARY = f"canary-{uuid.uuid4().hex[:12]}"
PROPOSAL = {"type": "sdk_proposal", "namespace": "crm", "id": f"p-{uuid.uuid4().hex[:8]}"}
PROPOSAL_TYPE = {
    "type": "sdk_proposal",
    "ownership": "subject",
    "mirror_of": {"system": "crm"},
    "fields": {
        "price_full": {"type": "money"},
        "health_declaration": {
            "type": "text",
            "sensitivity": "health",
            "pii": True,
            "access": [{"readers": ["agent:voice"], "effect": "mask"}],
        },
    },
    "sources": {"billing": {"kind": "push", "precedence": 1}},
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", required=True, help="the data API's address")
    parser.add_argument("--control", required=True, help="the control plane's address")
    parser.add_argument("--bootstrap", type=Path, required=True)
    parser.add_argument("--sample-dsn", required=True, help="a PostgreSQL where a sample database may be created")
    args = parser.parse_args()
    sandbox = Sandbox(args.api, args.control, json.loads(args.bootstrap.read_text()), "types check")
    sample = f"sdk_sample_{uuid.uuid4().hex[:8]}"
    sample_dsn = psycopg.conninfo.make_conninfo(args.sample_dsn, dbname=sample)
    held: dict[str, Any] = {}
    run = Run()

    def admin(sql: str, dbname: str | None = None) -> None:
        dsn = psycopg.conninfo.make_conninfo(args.sample_dsn, dbname=dbname) if dbname else args.sample_dsn
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql)

    def niadra(key: str, *extra: str) -> subprocess.CompletedProcess[str]:
        """The SDK's command line, as a company's CI runs it: the key and the catalog's address from the
        environment, never on the command line."""
        env = {**os.environ, "NIADRA_API_KEY": key, "NIADRA_BASE_URL": args.api, "NIADRA_DERIVE_DSN": sample_dsn}
        command = [str(Path(sys.executable).parent / "niadra"), "types", "derive", "--table", "public.contracts"]
        return subprocess.run([*command, "--type", CONTRACT_TYPE, *extra], env=env, capture_output=True, text=True)

    def sample_database() -> str:
        admin(f"CREATE DATABASE {sample}")
        admin(SAMPLE_TABLE, sample)
        return f"{sample}, one synthetic table of contracts"

    def derived() -> str:
        held["billing"] = sandbox.key("billing", "track", "context", "state:push")
        done = niadra(held["billing"])
        assert done.returncode == 0, f"exit {done.returncode}: {done.stderr[-300:]}"
        proposed = json.loads(done.stdout)
        assert proposed["states"] == ["draft", "signed"], proposed
        assert proposed["mirror_of"]["drift"] == "alert", proposed["mirror_of"]
        held["fingerprint"] = proposed["mirror_of"]["fingerprint"]
        reviews = [line for line in done.stderr.splitlines() if line.startswith("review: ")]
        sandbox.features_on("turns", "state")
        sandbox.declare_types({**proposed, "sources": {"billing": {"kind": "push", "precedence": 1}}})
        return f"{len(proposed['fields'])} fields, states {proposed['states']}, {len(reviews)} to review; declared"

    def no_drift() -> str:
        deadline = time.monotonic() + READY_TIMEOUT_S
        while True:
            done = niadra(held["billing"], "--check")
            if done.returncode != 2 or time.monotonic() > deadline:
                break
            time.sleep(3)  # the type reaches the cell with the control plane's next snapshot
        assert done.returncode == 0, f"exit {done.returncode}: {done.stdout[-300:]} {done.stderr[-300:]}"
        report = json.loads(done.stdout)
        assert report["drift"] is False and report["fingerprint"] == held["fingerprint"], report
        return "the catalog matches the declared fingerprint: exit 0"

    def drift() -> str:
        admin(NEW_STATE, sample)
        done = niadra(held["billing"], "--check")
        assert done.returncode == 1, f"exit {done.returncode}: {done.stdout[-300:]} {done.stderr[-300:]}"
        report = json.loads(done.stdout)
        assert report["drift"] is True and report.get("issue_id"), report
        assert report["changes"]["states_added"] == 1 and report["changes"]["fields"] == ["status"], report
        assert "state added: cancelled" in done.stderr, done.stderr
        again = json.loads(niadra(held["billing"], "--check").stdout)
        assert again["issue_id"] == report["issue_id"], (again, report)
        held["issue_id"] = report["issue_id"]
        return f"a new CHECK value: exit 1, issue {report['issue_id'][:8]}, the same issue the second time"

    def listed() -> str:
        page = expect(sandbox.api.get("/v1/data-issues", params={"kind": "drift"}, headers=sandbox.person), 200)
        [issue] = [i for i in page["items"] if i.get("issue_id") == held["issue_id"]]
        assert issue["object_type"] == CONTRACT_TYPE, issue
        assert "cancelled" not in json.dumps(issue), "the issue carries the counts, never the value"
        return f"the operator lists issue {held['issue_id'][:8]} of {CONTRACT_TYPE}"

    def served(source: str, turn: bool) -> Any:
        """What the model of the agent behind `source` gets from the tool, and the turn's id when recorded."""
        client = Niadra(
            held[source],
            base_url=args.api,
            channel=source,
            cache=CacheOptions(ttl=0, stale_while_revalidate=0),
            turns=TurnOptions(content_mode="stored"),
        )

        def shown(result: dict[str, Any]) -> list[dict[str, Any]]:
            return [{"ref": f"{PROPOSAL['type']}:{PROPOSAL['namespace']}:{PROPOSAL['id']}", "fields": {}}]

        @client.tool("get_proposal", provenance=shown, mask_output=True)
        def get_proposal(proposal_id: str) -> dict[str, Any]:
            return {"proposal_id": proposal_id, "price_full": 812.4, "health_declaration": CANARY}

        try:
            assert client.profile() is not None, f"the {source} key read no SDK profile"
            if not turn:
                return get_proposal(PROPOSAL["id"]), None
            with client.conversation(f"e2e-mask-{uuid.uuid4().hex[:8]}", subject=CUSTOMER, agent_id="sales") as c:
                c.customer("Qual o valor da proposta?")
                with c.turn(build=BUILD) as frame:
                    got = get_proposal(PROPOSAL["id"])
                    c.agent(f"A proposta sai por R$ {got['price_full']:.2f}.")
            assert client.flush(30), "the SDK did not send the turn within 30 s"
            return got, frame.turn_id
        finally:
            client.close()

    def field_access() -> str:
        sandbox.features_on("turns", "state")
        sandbox.declare_types(PROPOSAL_TYPE)
        held["voice"] = sandbox.key("voice", "track", "context")
        held["whatsapp"] = sandbox.key("whatsapp", "track", "context", "replay")

        def declared() -> Any:
            profile = sandbox.api.get("/v1/sdk/profile", headers=bearer(held["whatsapp"]))
            types = {t["type"]: t for t in profile.json().get("types", [])} if profile.status_code == 200 else {}
            return types.get(PROPOSAL["type"], {}).get("field_access")

        deadline = time.monotonic() + READY_TIMEOUT_S
        while not declared() and time.monotonic() < deadline:
            time.sleep(3)
        assert declared() == {"health_declaration": "deny"}, declared()
        voice, _ = served("voice", turn=False)
        assert voice == {"proposal_id": PROPOSAL["id"], "price_full": 812.4, "health_declaration": MASKED}, voice
        whatsapp, turn_id = served("whatsapp", turn=True)
        assert whatsapp == {"proposal_id": PROPOSAL["id"], "price_full": 812.4}, whatsapp
        held["turn_id"] = turn_id
        return "the voice agent's model gets the declaration masked, the WhatsApp agent's never"

    def recorded() -> str:
        deadline = time.monotonic() + TURN_TIMEOUT_S
        while True:
            response = sandbox.api.get(f"/v1/turns/{held['turn_id']}", headers=bearer(held["whatsapp"]))
            if response.status_code != 404 or time.monotonic() > deadline:
                turn = expect(response, 200)
                break
            time.sleep(3)
        assert CANARY not in json.dumps(turn), "the record holds a value the model never got"
        return f"turn {held['turn_id']} holds what the model got"

    steps = (
        ("log in to the control plane", sandbox.login),
        ("a synthetic sample database", sample_database),
        ("niadra types derive proposes the type", derived),
        ("--check on the same catalog", no_drift),
        ("--check after a new state", drift),
        ("the drift is a data issue", listed),
        ("a masked tool output per agent", field_access),
        ("the turn record", recorded),
    )
    try:
        for name, fn in steps:
            if not run.step(name, fn) and name in ("log in to the control plane", "a synthetic sample database"):
                break
    finally:
        try:
            admin(f"DROP DATABASE IF EXISTS {sample} WITH (FORCE)")
        except psycopg.Error as error:
            print(f"the sample database {sample} was not dropped: {type(error).__name__}")
    return run.failed


if __name__ == "__main__":
    sys.exit(main())
