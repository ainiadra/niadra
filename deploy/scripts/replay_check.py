# /// script
# requires-python = ">=3.11"
# dependencies = ["niadra @ git+https://github.com/ainiadra/niadra-sdk-python@main"]
# ///
"""The replay's end-to-end check from the company's side: the Python SDK records two turns of a deterministic
agent, the turns become scenarios, and the SDK's executor runs them with the build pinned. Its tool answers
from the record and never runs, and the cell answers with the statistical verdict: pass.

- `hermetic_turn`, five runs of one turn; `hermetic_conversation`, the two turns in order; `era_memory`, where
  the tool is safe to run and runs live.
- The tool counterfactual: a stylist's turn searched with the customer's hard constraint (a preference an
  earlier turn stated), and the runner runs the search again with and without it, dry, and posts positions
  and overlaps to the cell.

    uv run scripts/replay_check.py --api http://127.0.0.1:19709 --control http://127.0.0.1:19700 \\
        --bootstrap "$LOCAL_E2E_DIR/bootstrap.json" [--runs 5]

Against a cell that scripts/local-e2e.sh left running (--keep): the api at LOCAL_E2E_PORT+9, the control
plane at LOCAL_E2E_PORT. The SDK comes from its main branch; to check a checkout instead:

    uv run --no-project --with-editable ../niadra-sdk-python python scripts/replay_check.py ...

The sandbox's keys predate the `replay` scope, so the check logs in to the control plane as the sandbox's
admin (bootstrap.json), turns the space's `turns`, `signals` and `measurement` features on when they are off,
and mints a key of the `whatsapp` source with the scopes `track`, `context` and `replay`. The key is never
printed. Each step prints PASS or FAIL; the exit code is the number of failed steps.
"""

import argparse
import json
import sys
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from niadra import Niadra, phone
from niadra.options import CacheOptions, TurnOptions
from niadra.replay import Counterfactual, Replayer, ReplayInput
from sdk_session import Run, Sandbox, bearer, expect

# Synthetic customer: a number in the range Anatel reserves for fiction and tests.
CUSTOMER = phone("+5511900005678")
SHOPPER = phone("+5511900005680")
# A turn is applied by the turns worker after the route takes it.
TURN_TIMEOUT_S = 120

BUILD = Niadra.build(prompts={"quote": "v1"}, model="demo-model-1")
PLAN = {"plan": "ouro", "lives": 2}
quoted: list[dict[str, Any]] = []


@Niadra.tool("quote", dry_run=True)
def quote(plan: str, lives: int) -> dict[str, Any]:
    quoted.append({"plan": plan, "lives": lives})
    return {"plan": plan, "lives": lives, "price_full": 511.06}


def build_agent() -> Callable[[ReplayInput | None], str]:
    """A deterministic agent: no model and nothing random, so every replay of its turn passes."""

    def agent(_given: ReplayInput | None = None) -> str:
        found = quote(**PLAN)
        return f"O plano {found['plan']} para {found['lives']} vidas sai por R$ {found['price_full']:.2f}."

    return agent


BUILD_STYLIST = Niadra.build(prompts={"stylist": "v1"}, model="demo-model-1")
CATALOG = [
    {"variant_id": f"e2e-cf-{n}", "color": color, "price": price}
    for n, (color, price) in enumerate(
        [("preto", 120), ("azul", 90), ("preto", 80), ("bege", 150), ("azul", 60), ("verde", 70), ("preto", 40)]
    )
]
SEARCH = {
    "tool": "search_products",
    "args": [{"attr": "item_variant.color", "param": "color", "negation": {"param": "not_color"}}],
    "results": [
        {
            "path": "cards[*]",
            "type": "item_variant",
            "namespace": "loja",
            "id": "variant_id",
            "fields": {"color": "color"},
        }
    ],
    "capabilities": {"overfetch": False},
}
searched: list[dict[str, Any]] = []


@Niadra.tool("search_products", binding=SEARCH, dry_run=True)
def search_products(not_color: list[str] | None = None, color: str | None = None) -> dict[str, Any]:
    """A read-only search of a synthetic catalog, cheapest first."""
    searched.append({"not_color": not_color, "color": color})
    refused = set(not_color or ())
    cards = [c for c in CATALOG if c["color"] not in refused and (color is None or c["color"] == color)]
    return {"cards": sorted(cards, key=lambda c: c["price"])}


def presented(frame: Any, cards: list[dict[str, Any]], engaged: str | None = None) -> None:
    """What the interface showed of a result, and the item the person opened."""
    exposure = str(uuid.uuid4())
    items = [{"pos": i + 1, "ref": f"item_variant:loja:{c['variant_id']}"} for i, c in enumerate(cards[:3])]
    frame.interact(
        {"kind": "presented", "exposure_id": exposure, "list_id": "results", "list_kind": "search_products",
         "delivered_at": datetime.now(timezone.utc).isoformat(), "visible_k": 3, "items": items}
    )  # fmt: skip
    if engaged is not None:
        frame.interact({"kind": "engaged", "exposure_id": exposure, "ref": engaged, "how": "detail"})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", required=True, help="the data API's address")
    parser.add_argument("--control", required=True, help="the control plane's address")
    parser.add_argument("--bootstrap", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    sandbox = Sandbox(args.api, args.control, json.loads(args.bootstrap.read_text()), "replay check")
    api = sandbox.api
    held: dict[str, Any] = {}
    run = Run()

    def replay_key() -> str:
        held["key"] = sandbox.key("whatsapp", "track", "context", "replay", ready="/v1/scenarios?limit=1")
        return "a key with the replay scope, served by the cell"

    def stored(turn_id: str) -> dict[str, Any]:
        """The turn once the turns worker applied it."""
        deadline = time.monotonic() + TURN_TIMEOUT_S
        while True:
            response = api.get(f"/v1/turns/{turn_id}", headers=bearer(held["key"]))
            if response.status_code != 404 or time.monotonic() > deadline:
                return expect(response, 200)
            time.sleep(3)

    def recorded() -> str:
        niadra = sdk(held["key"])
        conversation_id = f"e2e-replay-{uuid.uuid4().hex[:8]}"
        turn_ids = []
        with niadra.conversation(conversation_id, subject=CUSTOMER, agent_id="sales") as conversation:
            for question in ("Quanto sai o plano ouro para duas vidas?", "Pode confirmar o valor do plano ouro?"):
                conversation.customer(question)
                with conversation.turn(build=BUILD) as frame:
                    conversation.agent(build_agent()())
                turn_ids.append(frame.turn_id)
        assert niadra.flush(30), "the SDK did not send the turns within 30 s"
        niadra.close()
        for turn_id in turn_ids:
            turn = stored(turn_id)
            assert turn.get("replayable") is not False, f"{turn_id} is not replayable: {turn.get('not_replayable')}"
        held["turn_ids"] = turn_ids
        return f"turns {', '.join(turn_ids)} with the quote tool, stored"

    def scenario() -> str:
        made = []
        for key, name, turns in (
            ("scenario_id", "replay check: quote", held["turn_ids"][:1]),
            ("conversation_id", "replay check: a conversation", held["turn_ids"]),
        ):
            headers = {**bearer(held["key"]), "idempotency-key": str(uuid.uuid4())}
            body = {"name": name, "turn_ids": turns}
            created = expect(api.post("/v1/scenarios", json=body, headers=headers), 201)
            held[key] = created["scenario_id"]
            kinds = sorted({a["kind"] for a in created.get("assertions") or ()})
            assert kinds, f"no assertions suggested for the scenario: {created}"
            made.append(f"{created['scenario_id']} with {', '.join(kinds)}")
        return "; ".join(made)

    def replay(scenario_id: str, runs: int, mode: str) -> tuple[Any, dict[str, Any]]:
        niadra = sdk(held["key"])
        quoted.clear()
        try:
            result = Replayer(niadra, build_agent, build=BUILD).run([scenario_id], runs=runs, mode=mode)
        finally:
            niadra.close()
        (summary,) = result.scenarios
        assert result.verdict == "pass", f"verdict {result.verdict}: {summary}"
        return result, summary

    def replayed() -> str:
        result, summary = replay(held["scenario_id"], args.runs, "hermetic_turn")
        assert quoted == [], f"the tool ran {len(quoted)} times in the replay"
        assert summary["completed"] == args.runs, summary
        return f"run {result.run_id}: {summary['completed']} of {args.runs} completed, verdict {result.verdict}"

    def conversation() -> str:
        result, summary = replay(held["conversation_id"], 2, "hermetic_conversation")
        assert quoted == [], f"the tool ran {len(quoted)} times in the replay"
        assert summary["completed"] == 4, summary
        return f"run {result.run_id}: two turns in order, twice, verdict {result.verdict}"

    def era() -> str:
        result, summary = replay(held["scenario_id"], 2, "era_memory")
        assert quoted == [PLAN, PLAN], f"the safe tool did not run live: {quoted}"
        return f"run {result.run_id}: the safe tool ran live, verdict {result.verdict}"

    def constrained() -> str:
        niadra = sdk(held["key"])
        conversation_id = f"e2e-cf-{uuid.uuid4().hex[:8]}"
        try:
            with niadra.conversation(conversation_id, subject=SHOPPER, agent_id="stylist") as talk:
                # The blocks beside a pack pass what the pack passes: an unproven conversation gets them empty.
                talk.verify("network_attestation", "V1")
                talk.customer("Quero um vestido, nada de preto.")
                with talk.turn(build=BUILD_STYLIST) as frame:
                    frame.interact(
                        {"kind": "preference", "attr": "item_variant.color", "op": "not_in", "values": ["preto"],
                         "strength": "must", "scope": "persistent", "source": "tool_args"}
                    )  # fmt: skip
                    presented(frame, search_products(not_color=["preto"])["cards"])
                    talk.agent("Separei opções que não são pretas.")
                assert niadra.flush(30), "the SDK did not send the turn within 30 s"
                deadline = time.monotonic() + TURN_TIMEOUT_S
                while not getattr(talk.context(include=["constraints"]).constraints, "hard", None):
                    if time.monotonic() > deadline:
                        raise AssertionError("the stated preference did not reach the constraints block")
                    time.sleep(3)
                talk.customer("Tem alguma mais barata?")
                with talk.turn(build=BUILD_STYLIST) as frame:
                    block = talk.context(include=["constraints"]).constraints
                    hard = block.hard if block is not None else []
                    refused = [str(v) for h in hard if h.op == "not_in" for v in h.values]
                    cards = search_products(not_color=refused)["cards"]
                    presented(frame, cards, engaged=f"item_variant:loja:{cards[1]['variant_id']}")
                    talk.agent("A mais barata sai por R$ 60,00.")
            assert niadra.flush(30), "the SDK did not send the turn within 30 s"
            stored(frame.turn_id)
        finally:
            niadra.close()
        held["searched_turn"] = frame.turn_id
        return f"turn {frame.turn_id} searched with the hard constraint from the turn before"

    def counterfactual() -> str:
        niadra = sdk(held["key"])
        searched.clear()
        try:
            run_ = Counterfactual(niadra, {"search_products": search_products}).run(
                [held["searched_turn"]], tool="search_products", element="hard", label="e2e"
            )
        finally:
            niadra.close()
        assert len(run_.cases) == 1 and run_.cases[0]["status"] == "completed", run_.cases
        assert searched == [{"not_color": ["preto"], "color": None}] * 2 + [{"not_color": None, "color": None}]
        assert "preto" not in json.dumps(run_.cases), "positions and overlaps only"
        report = run_.report or {}
        assert report.get("run_id") and report.get("completed") == 1, report
        return (
            f"run {report['run_id']}: overlap {run_.cases[0]['overlap']:.2f} against noise {run_.cases[0]['noise']:.2f}"
        )

    def sdk(key: str) -> Niadra:
        return Niadra(
            key,
            base_url=args.api,
            channel="whatsapp",
            cache=CacheOptions(ttl=0, stale_while_revalidate=0),
            turns=TurnOptions(content_mode="stored"),
        )

    steps: tuple[tuple[str, Callable[[], str]], ...] = (
        ("log in to the control plane", sandbox.login),
        ("the space records turns", lambda: sandbox.features_on("turns", "signals", "measurement")),
        ("a key for the replay", replay_key),
        ("the SDK records two turns", recorded),
        ("the turns become scenarios", scenario),
        (f"the SDK replays a turn {args.runs} times", replayed),
        ("the SDK replays the conversation", conversation),
        ("the SDK replays a turn in its era", era),
        ("a search under a hard constraint", constrained),
        ("the tool counterfactual", counterfactual),
    )
    for name, fn in steps:
        if not run.step(name, fn) and name in ("log in to the control plane", "a key for the replay"):
            break
    return run.failed


if __name__ == "__main__":
    sys.exit(main())
