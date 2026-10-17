# Niadra open specifications

The open formats of Niadra, the omnichannel memory layer for a company's AI agents: how an event
from any channel, platform or system enters the memory, how the memory reaches any agent, from any
vendor, and how a conversation proves who is there. Three small formats, usable by anyone:

| Spec | Schema | What it is |
|---|---|---|
| [Event Envelope](spec/event-envelope.md) | [`event-envelope.v0.json`](schemas/event-envelope.v0.json) | One message, system event or agent action, from any channel or system, with the context an agent's turn was built on. |
| [Context Pack](spec/context-pack.md) | [`context-pack.v1.json`](schemas/context-pack.v1.json) | What an agent should know about one subject now: text for the model, metadata for the program, what the customer's last turn needs from memory, and the blocks a read asks for by `include`. |
| [Verification Assertion](spec/verification-assertion.md) | [`verification-assertion.v0.json`](schemas/verification-assertion.v0.json) | That a conversation reached a verification level, by which method, until when. |

And the documents that travel around them:

| Spec | Schemas | What it is |
|---|---|---|
| [Media Upload](spec/media-upload.md) | [`media-upload-request.v0.json`](schemas/media-upload-request.v0.json), [`media-upload-response.v0.json`](schemas/media-upload-response.v0.json) | How a file reaches storage so an event can carry only its reference and digest. |
| [Feedback](spec/feedback.md) | [`feedback.v0.json`](schemas/feedback.v0.json) | A correction of what a memory derived, recorded as an event. |
| [Business Object](spec/business-object.md) | [`object-timeline.v0.json`](schemas/object-timeline.v0.json) | What happened to an invoice, order or ticket. |
| [Object Type](spec/object-type.md) | [`object-type.v0.json`](schemas/object-type.v0.json) | One kind of state a company's agents work on (a sale, a lawsuit, a proposal, an item), declared or derived from the company's schema: fields with four logical values and their observers, sources with precedence and known defects, freshness, lifecycle, timers and readings, niadra-expr, the language of its conditions, with its vectors in `vectors/niadra-expr.v0.json`, and derivation from a PostgreSQL catalog with its fingerprint and drift report, with its vectors in `vectors/type-derive.v0.json`, and the space's tool bindings (`tool-binding.v0.json`, section 9). |
| [Object State](spec/object-state.md) | [`object-state.v1.json`](schemas/object-state.v1.json) | One object of a declared type as a read serves it: each value with its logical value, stamps, source and freshness at the read, what may not be claimed with it, and how a system of record pushes state. |
| [Turn Record](spec/turn-record.md) | [`turn-record.v0.json`](schemas/turn-record.v0.json) | What an AI agent read, called, showed, claimed and decided in one turn, with its build pinned for replay; the values may stay in the company's storage. |
| [Replay](spec/replay.md) | [`replay-case.v0.json`](schemas/replay-case.v0.json), [`replay-results.v0.json`](schemas/replay-results.v0.json), [`scenario-run.v0.json`](schemas/scenario-run.v0.json) | Recorded turns kept as scenarios, run again N times inside the company's boundary with the build pinned, and a statistical verdict per assertion, with its vectors in `vectors/regression-stats.v0.json`. |
| [OpenTelemetry conventions](spec/otel-conventions.md) | (text) | The `gen_ai` attributes a memory reads and the `niadra.*` attributes for the turn, the build, the exposure, the channel and the customer. |
| [Interaction](spec/interaction.md) | [`interaction.v0.json`](schemas/interaction.v0.json) | What a person was shown, looked at, engaged with, wants, refuses and is; the canonical moment of an exposure. |
| [Constraints Block](spec/constraints.md) | [`constraints.v0.json`](schemas/constraints.v0.json) | One person's constraints for the tools of a turn, how they render through a tool's arguments, and how conformance is measured. |
| [Claim Contract](spec/claim-contract.md) | [`claim-contract.v0.json`](schemas/claim-contract.v0.json) | What an agent may state and the evidence each claim needs, checked without a model; with its negative corpus. |
| [Tool Counterfactual](spec/counterfactual.md) | [`counterfactual-results.v0.json`](schemas/counterfactual-results.v0.json), [`counterfactual-run.v0.json`](schemas/counterfactual-run.v0.json) | Whether one element of the constraints block changes what a tool returns, beyond the tool's own noise: recorded calls run live with and without it in the company's CI, reported as overlaps and positions, with its vectors in `vectors/counterfactual-overlap.v0.json`. |

And the formats that coordinate agents, vendors and people around one customer, in draft:

| Spec | Schemas | What it is |
|---|---|---|
| [Coordination](spec/coordination.md) | [`coordination-decision.v0.json`](schemas/coordination-decision.v0.json), [`handoff-package.v0.json`](schemas/handoff-package.v0.json) | Whether an agent may act on a customer or an object now, who holds it, what was already done, and what a handoff carries. |
| [Contact Token](spec/contact-token.md) | [`contact-token.v0.json`](schemas/contact-token.v0.json) | A short-lived signed permission for one outbound contact, which a gateway checks offline. |
| [Suppression List](spec/suppression-list.md) | [`suppression-list.v0.json`](schemas/suppression-list.v0.json) | Who not to contact, per purpose and channel, keyed per reader so no vendor learns about anyone else. |
| [Exposure Token](spec/exposure-token.md) | (a string) | A 33-character token a store copies from an agent's card to the cart line, to attribute the order to the card. |
| [Agent State](spec/agent-state.md) | [`agent-state.v0.json`](schemas/agent-state.v0.json) | An agent's working memory under a declared schema: compare-and-swap or merge by key, kept whole. |

## Layout

| Path | What it holds |
|---|---|
| `spec/` | The prose of each format. It is normative, written with RFC 2119 keywords. |
| `schemas/` | JSON Schema 2020-12 for every document, `<name>.<version>.json`, with `$id` under `https://specs.niadra.com/schemas/`. Generated from the contract models of the Niadra server ([`../server`](../server)), so the published format and the running API cannot drift apart. |
| `examples/` | Conforming documents, one directory per schema, and in `examples/invalid/` one violation per file. |
| `vectors/` | The test vectors every implementation runs, one file per behavior (`<name>.v0.json`): the cases and their expected results. |
| `tests/` | The conformance tests of this directory: examples against schemas, the shape of every vector file, and the rules the prose states. |
| `scripts/` | `generate.py` (writes `schemas/` from the server) and `audit.py` (pip-audit over `uv.lock`). |

## Validating a document

Any JSON Schema 2020-12 validator works. With Python:

```python
import json
from jsonschema import Draft202012Validator, FormatChecker

schema = json.load(open("schemas/event-envelope.v0.json"))
Draft202012Validator(schema, format_checker=FormatChecker()).validate(my_event)
```

## Test vectors

Each file in `vectors/` has the same layout: `vectors` (its name), `version`, `spec` (the prose it belongs
to, which links back to it) and `cases`, each case with an `id`, its inputs and `expect`.

| Vectors | Spec | What an implementation computes |
|---|---|---|
| `claim-parser`, `claim-detect`, `claim-evidence`, `claim-anchor` | [Claim Contract](spec/claim-contract.md) | Numbers and their roles in a text, the claims a contract detects, the evidence verdicts, the text anchor score |
| `constraint-render` | [Constraints Block](spec/constraints.md) | How a constraints block renders through a tool binding |
| `contact-token` | [Contact Token](spec/contact-token.md) | Issuing a contact token and verifying it offline |
| `contact-window`, `suppression-key` | [Suppression List](spec/suppression-list.md) | Whether an instant falls in a contact window, and the per-reader suppression key of a destination |
| `counterfactual-overlap` | [Tool Counterfactual](spec/counterfactual.md) | The overlap of two ranked result lists |
| `exposure-token` | [Exposure Token](spec/exposure-token.md) | Building and parsing the 33-character token |
| `niadra-expr`, `type-derive` | [Object Type](spec/object-type.md) | The niadra-expr language and type derivation from a PostgreSQL catalog |
| `regression-stats` | [Replay](spec/replay.md) | The statistical verdict per assertion |
| `turn-record-digest` | [Turn Record](spec/turn-record.md) | The canonical JSON digest of a turn record |

The tests here check that the vectors are well formed and consistent with the schemas. Whether an
implementation gets the expected results is for that implementation to prove by running the same files:
the server keeps copies of the ones it implements in [`../server/tests/spec`](../server/tests/spec), and the
SDKs ([Python](https://github.com/ainiadra/niadra-sdk-python), [TypeScript](https://github.com/ainiadra/niadra-sdk-ts))
run the ones they implement.

## Running the checks

The same steps CI runs ([`../.github/workflows/spec.yml`](../.github/workflows/spec.yml)), from this directory:

```bash
uv sync --frozen
uv run scripts/audit.py   # known vulnerabilities in every locked package (pip-audit)
uv run ruff check .
uv run pytest -q
```

## Regenerating the schemas

The schemas come from the server's Pydantic models. From the monorepo root, with the server's environment:

```bash
cd server && uv run python ../spec/scripts/generate.py .
```

Then run the checks above. The generator also adds the cross-field rules that the server's models enforce
in code (for example, "an action event needs the `action` block"), so the schemas reject what the API
rejects, and drops section citations from the server's docstrings, which point at text outside the specs.

## Versioning

These are drafts, one version per document: `v1` for the Context Pack and the Object State, `v0` for the
others. Until the first stable version, a draft may change in place, and its schema is regenerated from
the server with it.

## License

Apache 2.0. See [LICENSE](LICENSE).
