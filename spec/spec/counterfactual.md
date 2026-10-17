# Niadra Tool Counterfactual, version 0

- **Status:** draft
- **Date:** 2026-09-29
- **Schemas:** [`schemas/counterfactual-results.v0.json`](../schemas/counterfactual-results.v0.json) (what a
  runner reports), [`schemas/counterfactual-run.v0.json`](../schemas/counterfactual-run.v0.json) (the report)
- **Vectors:** [`vectors/counterfactual-overlap.v0.json`](../vectors/counterfactual-overlap.v0.json)
- **License:** Apache 2.0

## 1. Purpose

A memory hands an agent's tools a constraints block ([constraints spec](constraints.md)) that renders into
the tools' arguments. Before anyone runs an experiment on it, a team wants to know something smaller: does
the element change what the tool returns at all? A search that ignores a filter, or a filter that removes
nothing, makes every later measurement of that element a measurement of nothing.

This spec answers that question from recorded turns ([turn record spec](turn-record.md)). The company's CI
runs each recorded tool call live, with and without the element, at the same moment, and runs the call with
the element a second time to measure the tool's own noise. It reports positions and overlaps, never items.
The recorder adds the cases up.

It does not say whether the result got better: a hard constraint changes a list by construction, and a
change is expected. It does not measure how the model reacts to either list. Every report says both
(section 7).

## 2. Conventions

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, MAY and
OPTIONAL in this document are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174) when, and
only when, they appear in all capitals.

- The **recorder** keeps the reports and adds the cases up.
- The **runner** runs the calls inside the company's boundary, usually an SDK command in the company's CI.
  It is the same runner that runs replay cases ([replay spec](replay.md)).
- The **element** is what the variant goes without: `constraints` (the whole block), `hard` (its hard
  constraints), `size` (its attributes) or `exclude` (its exclusions, which the SDK filters after the call).
- The **base** is the call with the element, rendered as the constraints spec renders it; the **variant**
  is the same call rendered from the block without the element.
- The route is under `/v1` and needs a key with the `replay` scope, or a person with the `analysis` role.

## 3. Which calls

3.1. A case is one recorded tool call whose rendered arguments or post filter carried the element. The
runner MUST NOT run a call the element did not touch: its variant is its base.

3.2. The runner MUST run the three calls (the base twice, the variant once) at the same moment, one right
after the other, so the data behind the tool is the same for all three.

3.3. A tool that writes state MUST run as its dry run. A tool that writes state and has no dry run is never
called: the case is reported with `status` `no_dry_run`.

3.4. The runner compares the list each call returns after the SDK's post filter, in the order the tool
returned it.

## 4. overlap@k

4.1. `k` is the recorded list's `visible_k` when the call's result was presented to the person (interaction
spec, 5.4), otherwise the run's `k` (10 by default). It is at most 100.

4.2. For two lists `a` and `b` of object references, where an item repeated within a list counts once, at
its first position, and `D` is the smaller of `k` and the length of the longer list:

    overlap@k(a, b) = sum over d = 1..D of w(d) * |a[1..d] ∩ b[1..d]| / d
                      ---------------------------------------------------
                      sum over d = 1..D of w(d)

with `w(d) = 1 / log2(d + 1)`, the exposure weight of the interaction spec. Two equal lists overlap 1 however
short they are, and so do two empty lists (`D` is 0); a list against an empty one overlaps 0.

4.3. Per case, `overlap` is overlap@k of the base's first list and the variant's list, and `noise` is
overlap@k of the base's two lists.

4.4. `vectors/counterfactual-overlap.v0.json` holds lists and their expected overlap. Each case has an `id`,
a `description`, `a`, `b`, `k` and `expect`: `{"overlap": <number>}`. An implementation MUST match every
expected value within 0.000001.

## 5. Engaged items

For each item the person engaged with in the recorded turn (an `engaged` interaction of the exposure that
presented the call's result), the case reports `{base, variant}`: the item's 1-based position in the base's
first list and in the variant's list, over the whole list, not only the first `k`; absent where the list
does not hold it.

## 6. Reporting a run

6.1. `POST /v1/measure/counterfactual-runs` (`counterfactual-results.v0.json`):

| Field | Required | Meaning |
|---|---|---|
| `tool` | yes | The tool's name, as the turn record names it. |
| `element` | yes | `constraints`, `hard`, `size` or `exclude`. |
| `label` | no | A name for the run, such as the commit it ran on. |
| `k` | no | The positions compared when a recorded list has no `visible_k`; 10 by default. |
| `cases` | yes | Up to 5,000 cases. |

Each case is `{turn_id, call_id, status, dry_run, k?, overlap?, noise?, base_count?, variant_count?,
engaged}`:
- `status` is `completed`, `no_dry_run`, `tool_error` (a call failed) or `infrastructure_error` (the runner
  could not run the case). Only a completed case carries `k`, `overlap`, `noise`, `base_count` and
  `variant_count`, and all of them; only a completed case carries `engaged`.
- `dry_run` says the calls ran as the tool's dry run.
- `base_count` and `variant_count` are the lengths of the base's first list and of the variant's list.

6.2. A case MUST NOT carry an item, an argument, a result or personal data: positions and overlaps only.

6.3. The answer is 201 with the report (`counterfactual-run.v0.json`). `GET /v1/measure/counterfactual-runs`
lists the reports, newest first, by `cursor`, and `GET /v1/measure/counterfactual-runs/{run_id}` reads one.

## 7. The report

7.1. Over the completed cases:
- `overlap` is the mean `overlap` and `noise_floor` the mean `noise`, each rounded to 6 decimal places;
- `effect` is `noise_floor - overlap`: how much of the first positions the element moves beyond the tool's
  own noise;
- `below_noise`, `above_noise` and `ties` count the cases whose `overlap` is below, above or equal to their
  own `noise`;
- `p_value` is the two-sided sign test of `below_noise` against `above_noise`: when the element changes
  nothing, the variant is one more draw of the base, and either side is as likely. It is exact up to 1,000
  decided cases and uses the normal approximation above, rounded to 6 decimal places; absent when no case
  is decided;
- `engaged` counts the engaged items: `items`; `shown`, within the base's first `k`; `kept`, within both
  lists' first `k`; `lost`, within the base's first `k` and not the variant's; `gained`, within the
  variant's first `k` and not the base's; and `mean_shift`, the variant's position minus the base's,
  averaged over the items both lists hold.

7.2. `skipped` counts the cases that did not complete, by `status`.

7.3. `limits` lists what holds the answer back, by code:

| Code | When | Meaning |
|---|---|---|
| `not_quality` | always | The report says whether the element changes the list, not whether the list got better. |
| `model_reaction_not_measured` | always | How the model reacts to either list is not measured. |
| `trivial_for_hard` | `element` is `constraints` or `hard` | A hard constraint changes a list by construction. |
| `few_cases` | fewer than 30 completed cases | The number is fragile. |
| `noisy_tool` | `noise_floor` below 0.9 | The tool returns different lists for the same call; changes of that size are not the element's. |
| `cases_skipped` | any case did not complete | The report covers only the completed cases. |
| `dry_run` | any completed case ran as a dry run | A dry run may return what a real call would not. |

## 8. Privacy

A report holds counts, positions and overlaps. The recorder keeps the report, never the cases' turn or call
ids.

## 9. Versioning

This is a draft. A new field means a new schema file, and a published file never changes meaning.
