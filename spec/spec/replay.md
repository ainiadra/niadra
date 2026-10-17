# Niadra Replay, version 0

- **Status:** draft
- **Date:** 2026-09-29
- **Schemas:** [`schemas/replay-case.v0.json`](../schemas/replay-case.v0.json) (the case),
  [`schemas/replay-results.v0.json`](../schemas/replay-results.v0.json) (what a run reports),
  [`schemas/scenario-run.v0.json`](../schemas/scenario-run.v0.json) (the verdict)
- **Vectors:** [`vectors/regression-stats.v0.json`](../vectors/regression-stats.v0.json)
- **License:** Apache 2.0

## 1. Purpose

A team that changes a prompt, a model or the data an agent consults needs to know, before the change reaches
customers, whether the agent still does what it did in the conversations that mattered. Running one real turn
once says little: models are not deterministic, and a test that fails one time in five is noise unless it
failed less before.

This spec turns recorded turns ([`turn-record.md`](turn-record.md)) into **scenarios**, runs them again inside
the company's own boundary, and turns N executions into a statistical **verdict**. The recorder orchestrates:
it keeps the scenarios, serves each turn as a **case** with the build it ran on, and decides the verdict.
The company's CI executes: the **runner** fetches the recorded values from wherever they live, runs the
agent with its tools intercepted, and evaluates structural **assertions** on the new record.

## 2. Conventions

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, MAY and
OPTIONAL in this document are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174) when, and
only when, they appear in all capitals.

- The **recorder** keeps turn records, scenarios and runs, and computes verdicts.
- The **runner** runs cases inside the company's boundary, usually an SDK command in the company's CI.
- A **pin** is one of the build's pins of Turn Record section 5.2: `prompts`, `corpus_digest`, `model`,
  `assembler` or `tool_schemas`.
- The routes below are under `/v1` and need a key with the `replay` scope, or a person with the
  `integration` or `security` role.

## 3. Scenarios

3.1. A scenario is up to 50 recorded turns and up to 50 assertions they must keep passing. Its turns are
kept while it exists: the recorder copies them out of their tier when the scenario is created, and keeps them
after the scenario last ran for the recording document's `scenario_days`, 180 by default (7 to 365; section
7.4). A turn that left storage answers `replay_expired`.

3.2. The routes:

| Route | What it does |
|---|---|
| `POST /v1/scenarios` | Creates one from `{name, turn_ids, assertions?}`. Without assertions, the recorder suggests them (3.4). `Idempotency-Key` is REQUIRED. |
| `POST /v1/scenarios/from-report` | Creates one from a bug report: `{conversation_id}` or `{turn_ids}`, with an optional `name` and `description`. The recorder keeps the turns and suggests assertions from them and from the description's words. It reads the description and never stores it. |
| `GET /v1/scenarios` | The active scenarios, newest first, by `cursor`; or `?ids=a,b` for up to 50 by id; `?status=retired` lists the retired ones. |
| `GET /v1/scenarios/{id}` | One scenario. |
| `PATCH /v1/scenarios/{id}` | Changes `name`, `assertions` or `status` (`active`, `retired`). Each change adds one to `version`. |

A conversation id travels in a body, never in a URL.

3.3. An **assertion** is `{id, kind, args, turn_id?, suggested}`:
- `id` is unique in the scenario: 1 to 64 lowercase letters, digits, `_`, `.` or `-`, starting with a
  letter or a digit;
- `kind` and `args` are in section 5;
- `turn_id`, when present, limits the assertion to that turn's case; absent, it applies to every turn;
- `suggested` is true for the ones the recorder suggested.

3.4. The recorder suggests assertions by rule, from each turn's frame (never from its values). For the
turn at position `i` of the scenario, starting at 1, each id starts with `t<i>.`:

| When the turn | It suggests |
|---|---|
| called a tool that answered `ok` | `tool_called` with that `tool`, id `t<i>.tool_called.<tool>` |
| reported `applied` constraints on a call | `hard_respected` |
| had a tool result that showed observations | `no_denial_with_results` |
| recorded claims | `claims_traced` and `claims_match_state` |
| recorded effects | `effect_once` |
| created a handoff | `handoff_when` with `expected: true` |
| made tool calls | `budget` with `max_tool_calls` twice the recorded count |

From a report's description, matched without regard to case in Portuguese, English and Spanish, it suggests
one assertion for every turn, with id `report.<kind>`, when no turn already suggested that kind:

| Words about | It suggests |
|---|---|
| a denial, something unavailable or out of stock | `no_denial_with_results` |
| a price, a value, an amount or a deadline | `claims_match_state` |
| a promise | `no_promise_without_action` |
| something done twice, a duplicate | `effect_once` |
| a human, an attendant, a transfer | `handoff_when` with `expected: true` |

## 4. The case

4.1. `POST /v1/replay/cases` with:

| Field | Required | Meaning |
|---|---|---|
| `turn_id` | yes | The turn to run again. |
| `scenario_id` | no | The scenario it belongs to; its assertions for the turn come with the case. |
| `mode` | no | `hermetic_turn` (default), `hermetic_conversation` or `era_memory` (4.4). |
| `build` | yes | The build the runner will run, as the Turn Record's `build`. |
| `vary` | no | The pins the run changes on purpose, e.g. `["prompts"]` to test a new prompt. |

4.2. The **pin check**. For every pin not in `vary`, the running build and the recorded one MUST be equal
when the recording requires the pin, and when both carry it. A pin the recording does not require and one
side lacks is not compared. On any difference the recorder answers 422 `pin_mismatch`, with
`pins: [{name, recorded, running}]` in the problem body, and serves no case.

4.3. The recorder also answers:
- 422 `not_replayable`, with `reasons: [{reason, value}]`, for a turn that cannot be run again: `reason` is
  `content_mode` (value `hash_only`), `fidelity` (`bronze`, `silver`), `completeness` (`partial`,
  `incomplete`, `unknown`) or `missing_pin` (the pin's name). The turn viewer shows the same list as
  `replay_blockers`.
- 410 `replay_expired` for a turn of the scenario that left storage;
- 404 for a turn it does not know, or one outside the scenario named.

4.4. The case (`replay-case.v0.json`):

| Field | Meaning |
|---|---|
| `case_id` | This case, for the results. |
| `turn_id`, `scenario_id`, `mode`, `vary` | As asked. |
| `record` | The turn record. In `stored` mode each blob carries its kept, masked `content`; in `pointer` mode the runner reads each `pointer` with the company's own credentials. |
| `required_pins` | The pins the recording requires. |
| `input` | `{kind, text?}`: for a `message` turn, the customer's message that opened it, masked. |
| `history` | The conversation before the turn, masked, as `[{role, text, at}]` with `role` one of `customer`, `agent` or `human`: at most the last 50 public messages of the 7 days before the turn, ending with the turn's own input. |
| `assertions` | The scenario's assertions for this turn. |
| `expires_at` | When the turn leaves storage. |

The pack of the time is the blob named by the record's read with `surface: pack`. When the record kept only
the pack's etag, there is no pack content to replay with.

The modes:
- `hermetic_turn`: the turn alone, with the recorded pack and every tool answered from the record;
- `hermetic_conversation`: the scenario's turns in order, until the first strong divergence;
- `era_memory`: the pack of the time, with the current state of the objects the tools read.

## 5. Assertions

The runner evaluates each assertion on the record of the replayed turn and on the text it emitted. An
outcome is `pass`, `fail` or `not_checked`. A kind or an argument the runner cannot evaluate, or one whose
evidence the turn did not produce, yields `not_checked`, which is never a failure. Text matches ignore case.

| Kind | `args` | It passes when |
|---|---|---|
| `tool_called` | `tool` (REQUIRED), `min` (default 1) | At least `min` calls of `tool` answered `ok`. |
| `tool_not_called` | `tool` (REQUIRED) | No call of `tool`. |
| `hard_respected` | none | Every call that reported `applied` has `violations: 0`. No such call: `not_checked`. |
| `no_denial_with_results` | `lang` | No tool result showed observations or items, or the text has no denial phrase of `lang` (Appendix A). |
| `claims_traced` | none | Every claim has `evidence`. |
| `claims_match_state` | none | Every claim's verdict is `matched`, `quoted_found` or `anchored`. |
| `no_promise_without_action` | `lang` | The text has no promise phrase of `lang` (Appendix A), or the turn recorded an effect or a handoff. |
| `expected_in_topk` | `ref`, `k` (REQUIRED), `list_id` | A `presented` interaction (of `list_id`, when given) shows `ref` at a position of at most `k`. |
| `handoff_when` | `expected` (default true) | The turn created a handoff exactly when `expected` says so. |
| `effect_once` | `key` | No effect key reaches `done` twice; with `key`, that key reaches `done` exactly once. |
| `budget` | at least one of `max_tool_calls`, `max_model_calls`, `max_tokens`, `max_cost_usd`, `max_latency_ms` | Every count given is within its limit; `max_tokens` counts input and output tokens. |
| `lexicon` | at least one of `must_include`, `must_not_include`; `lang` | The text holds every phrase of `must_include` and none of `must_not_include`. |
| `tools_offered_match` | `tools` (REQUIRED) | The tools offered to the model in the turn are exactly `tools`. |

`lang` is `pt`, `en` or `es`; without it, the runner uses the conversation's language.

## 6. Running a case

6.1. The runner MUST fetch each blob it uses and check its digest (Turn Record 6.2.1). A blob that cannot be
fetched or does not match its digest makes the execution an infrastructure error.

6.2. The runner MUST make the pin check of 4.2 itself, against the build it runs. A pin that does not match
stops the case: its executions are reported as `pin_mismatch`.

6.3. A tool call whose normalized arguments match a recorded call's `args_hash` answers with the recorded
result, the recorded calls of one tool taken in order. A call that matches none runs live only when the tool
is marked as safe to run dry; otherwise it answers empty and counts as divergent.

6.4. The replayed record stays with the runner, marked `synthetic`. It is never sent as a turn.

## 7. Reporting a run

7.1. `POST /v1/scenario-runs` (`replay-results.v0.json`), with `Idempotency-Key`:

| Field | Required | Meaning |
|---|---|---|
| `scenario_ids` | yes | The scenarios run. |
| `build` | yes | The build that ran. |
| `mode`, `runs`, `vary` | no | As the cases were asked. |
| `results` | yes | One entry per execution of one case. |

Each result is `{scenario_id, turn_id, case_id?, run, status, paraphrase, assertions, divergent_calls,
latency_ms?, error?}`:
- `status` is `completed`, `pin_mismatch` or `infrastructure_error`. An infrastructure error (the agent
  raised, a blob could not be fetched or did not match its digest, a timeout) is counted apart and never
  fails an assertion.
- `paraphrase` says the execution's input was a paraphrase of the recorded one.
- `assertions` is `[{id, kind, outcome, detail?}]`.
- `error` is a short description with no personal data.

7.2. The recorder makes the pin check of 4.2 against each scenario's turns before recording anything, and
answers 422 `pin_mismatch` like a case does.

7.3. The answer is 201 with the run (`scenario-run.v0.json`), its verdict already decided: `status` is
`done`.

7.4. Each run renews its scenarios' turns for `scenario_days`: to at least that long from the run, so a
shorter setting takes effect as the turns come due.

7.5. **A failure attributed to data.** When an execution fails an assertion that reads what the tools
showed (`claims_match_state`, `claims_traced`, `no_denial_with_results`, `expected_in_topk`,
`hard_respected`), the recorder reads the recorded turn's tool observations against the space's declared
types. Each of these opens a data issue for the data's owner, or counts one more occurrence of the open one:
- a declared field that came back null: `null_field`;
- a `state` value that is neither a declared lifecycle state nor a code a source's vocabulary maps to one:
  `out_of_vocabulary`;
- a declared field older at the source (`provenance.source_observed_at`) than its type's freshness allows
  when the turn ran: `stale_source`.

An issue is keyed by its class, type, field and tool, holds up to 50 object references, and carries no
value. A run opens or counts at most 20. Types the space did not declare are not attributed here.

## 8. The verdict

8.1. **Per assertion**, over the executions of the scenario that `completed` and in which the assertion
applied, `not_checked` left out:
- `passed` and `failed` count the outcomes;
- the **baseline** is (`baseline_passed`, `baseline_failed`) of the same assertion in the baseline run (8.2).
  Without one, the baseline is the recording, which passed the assertion as many times as the run checked it:
  (`passed + failed`, 0);
- `drop` is the baseline's pass rate minus the run's, a rate with no outcome counting as 1;
- `p_value` is the one-sided Fisher exact test that the run fails more often than the baseline: for the
  2x2 table with `N = baseline_passed + baseline_failed + passed + failed`,
  `K = baseline_failed + failed` and `n = passed + failed`, the probability that a hypergeometric draw of
  `n` from `N` with `K` failures holds at least `failed` failures. It is 1 when `K` or `n` is 0;
- `regression` is `failed >= 2` and `drop >= 0.2` and `p_value < 0.05`;
- `flaky` is not `regression` and `failed >= 1`.

`drop` and `p_value` are rounded to 6 decimal places, half to even, before the comparisons.

8.2. The baseline run of a scenario is the latest earlier run of it whose verdict for it was `pass` or
`flaky`.

8.3. **Per scenario**, the verdict is:
1. `pin_mismatch` when any execution is `pin_mismatch`;
2. otherwise `infrastructure_error` when no execution completed;
3. otherwise `regression` when any assertion is a regression;
4. otherwise `flaky` when any assertion is flaky;
5. otherwise `pass`.

It also counts `completed`, `infrastructure_errors` and `pin_mismatches`, and says `needs_paraphrase` when an
assertion both passed and failed: an intermittent result MUST be run again with paraphrases of the input
before it is trusted.

8.4. **The run's verdict** is the worst of its scenarios' in the order `pass`, `flaky`,
`infrastructure_error`, `pin_mismatch`, `regression`. Flaky results are listed and never block.

8.5. `vectors/regression-stats.v0.json` holds executions and baselines with the expected verdicts. Each case
has an `id`, a `description`, `executions` (each `{status, paraphrase, outcomes}` with `outcomes` mapping an
assertion id to `pass`, `fail` or `not_checked`), a `baseline` (an assertion id mapped to
`[passed, failed]`, or `null` for the recording) and `expect`, the scenario's verdict with every field of
8.1 and 8.3. An implementation MUST reproduce every field exactly.

## 9. The change log

`GET /v1/changes` lists what changed between two builds of an agent, newest first, by `cursor`, so a
regression can be bisected: `{kind, at, agent, name?, before, after}` where `kind` is `prompt` (one prompt's
version, named by `name`), `model`, `code` (the assembler, named `assembler`, or a tool's schema, named by
the tool), `data` (the corpus digest) or `compiler` (the context compiler's version). `at` is when the later
build was first seen, and `?agent=` keeps one agent's.

The kind `config` is a change of the space's configuration: a section (`name`, such as `recording`,
`object_types` or `policy`) whose digest changed between two applied configurations, with `before` and
`after` as `sha256:<hex>` digests, never the values, and no `agent`. Every agent reads the configuration, so
`?agent=` keeps these entries too. A section's first recorded digest is where its history starts, not a
change. Only a space that records turns keeps this history.

## 10. Privacy

- The case carries the conversation's history masked, and in `pointer` mode no recorded value: the runner
  reads them inside the company's boundary.
- A scenario's turns are personal data with the purpose `quality`; they are kept `scenario_days` after the
  scenario last ran, and erased with the rest of a person's turns.
- A report's description is read to suggest assertions and never stored.
- A run's results hold assertion outcomes and counts; `error` and `detail` MUST NOT carry personal data.

## 11. Versioning

This is a draft. A new field means a new schema file, and a published file never changes meaning.

## Appendix A. Phrases

Matched as substrings after lowercasing.

| `lang` | Denial phrases | Promise phrases |
|---|---|---|
| `pt` | `não temos`, `não encontrei`, `não há`, `indisponível`, `esgotado`, `não está disponível` | `vou enviar`, `vou verificar`, `te retorno`, `vou providenciar`, `vou encaminhar` |
| `en` | `we don't have`, `couldn't find`, `not available`, `out of stock`, `unavailable`, `no results` | `i will send`, `i'll send`, `i will check`, `i'll check`, `we will get back` |
| `es` | `no tenemos`, `no encontré`, `no hay`, `no disponible`, `agotado` | `le enviaré`, `voy a verificar`, `le aviso`, `voy a enviar` |
