/**
 * English descriptions of the turn record, typed state, working memory, signals, coordination and
 * measurement routes where the contract has none.
 */
const IDEM =
  'Required. Kept 24 hours with the hash of the body: a retry with the same key returns the first answer, and the same key with a different body returns 409 `conflict`.'

export const EN: Record<string, string> = {
  // Coordination
  'op:post /v1/coordination/check':
    'Asks whether the agent may act now, before it acts. The producer evaluates in order and stops at the first that decides: the effect key (done, in flight or ambiguous denies), a suppression of the purpose, a holder whose claim does not allow the intent, another holder\'s lock on the task, an exhausted or paced budget, the quiet hours. Only then the budget is spent and, for a purpose that needs one, the contact token is issued. A message the customer sent is never denied. With `effect_key`, an `allow` whose `effect.state` is `none` also reserved the effect: act, then declare how it ended.',
  'res:post /v1/coordination/check:200': 'The decision, valid for `valid_for_s` seconds; a caller that acts later asks again.',
  'op:post /v1/coordination/check/batch': 'Up to 500 checks in one request, each answered as if asked alone, in order.',
  'res:post /v1/coordination/check/batch:200': 'One decision per check, in the order asked.',
  'op:post /v1/coordination/declare':
    'What happened after the fact: a case opened or closed, an owner lease, a task lock, a contact that left (`contact.made`, with the token\'s `jti`), how a reserved effect ended, a commitment made, withdrawn or decided, a handoff that moved, a suppression added or lifted. A declaration never fails for the order it arrives in; a `contact.made` for a decision no longer held is still recorded.',
  'param:post /v1/coordination/declare:Idempotency-Key': IDEM,
  'res:post /v1/coordination/declare:200': 'The declaration was recorded; `accepted` says whether a commitment holds.',
  'op:post /v1/coordination/claims':
    'Declares a claim on the customer (`owner` or `case`, at a level of the space, for a lease) or a task lock on a business object. The most restrictive valid claim holds the target; a declaration is refused while another holder\'s claim is at least as restrictive (409 `lease_held`), and the same holder renews its own. With `source`, it is an observation the company\'s worker read from one of the space\'s ownership sources: never refused, it replaces what that source said before, capped by the source\'s validity.',
  'param:post /v1/coordination/claims:Idempotency-Key': IDEM,
  'res:post /v1/coordination/claims:201': 'The claim, with the epoch a release must name.',
  'op:post /v1/coordination/claims/{claim_id}/release': 'Ends the claim. The release names the epoch the claim got when it was accepted; a release with another epoch is refused, so a holder whose claim was replaced cannot release its successor\'s.',
  'res:post /v1/coordination/claims/{claim_id}/release:200': 'The released claim.',
  'op:post /v1/coordination/effects':
    'Reserves the next attempt of an effect, named by the key of the business fact it belongs to (one farewell per conversation, one filing per notice). A check with `effect_key` does the same; this route serves a caller that already decided. A key already `done` or `unknown_outcome` reserves nothing, and the answer says so; a key `reserved` by another attempt answers 409.',
  'param:post /v1/coordination/effects:Idempotency-Key': IDEM,
  'res:post /v1/coordination/effects:201': 'The attempt is reserved for the caller, which must settle it.',
  'op:post /v1/coordination/effects/{effect_id}/settle':
    'How the attempt the caller holds ended: `done`, `failed` or `unknown_outcome` (the request was sent and nobody confirmed it). Only `failed` opens a new attempt; an ambiguous effect is never sent again on its own. A settlement of an older attempt is refused.',
  'res:post /v1/coordination/effects/{effect_id}/settle:200': 'The effect as it stands after the settlement.',
  'op:post /v1/handoffs':
    'Moves the customer to another holder with a package: the compiled view for the receiver at its verification level and policy, who held the customer, the open objects, the commitments and promises that hold, the effects done or in flight, the suppressions, and how to report the outcome. While the outcome is due, the customer is held at the space\'s handoff level, so other agents\' outbound contacts wait.',
  'param:post /v1/handoffs:Idempotency-Key': IDEM,
  'res:post /v1/handoffs:201': 'The handoff, with its package.',
  'op:get /v1/handoffs/{handoff_id}': 'One handoff and its package, as compiled for the receiving side.',
  'res:get /v1/handoffs/{handoff_id}:200': 'The handoff.',
  'op:post /v1/handoffs/{handoff_id}/accept': 'The receiving side accepts the handoff.',
  'param:post /v1/handoffs/{handoff_id}/accept:Idempotency-Key': IDEM,
  'res:post /v1/handoffs/{handoff_id}/accept:200': 'The handoff, accepted.',
  'op:post /v1/handoffs/{handoff_id}/outcome':
    'The outcome, one of the space\'s outcome vocabulary. It ends the hold, becomes the receiving side\'s action `handoff.outcome` on the handoff object, which the next agent\'s context shows as done by someone else, and feeds the suppressions and signals the space maps the outcome to. A handoff without an outcome by `expected_by` reads as expired.',
  'res:post /v1/handoffs/{handoff_id}/outcome:200': 'The closed handoff.',
  'op:get /v1/suppressions':
    'The suppression list for this key: a page of changes, oldest first, from `cursor`; without a cursor, every entry in force. Each entry carries the destination keyed with this key\'s salt (HMAC-SHA256 over its canonical form), the purpose, the channel (null for every channel), `since`, `until` and `removed`. Never a handle, a name or the reason. Read it again every 60 seconds, apply pages in order and keep the latest state per `id`; with `reset`, drop the copy first.',
  'res:get /v1/suppressions:200': 'A page of the list.',
  'op:get /v1/suppressions/salt': 'The 32-byte salt of the calling key, base64url, with which it computes the key of each destination. Two readers of one space get different keys for the same person. Keep it as a secret and never send it or the keys anywhere else.',
  'res:get /v1/suppressions/salt:200': 'The salt, its id and since when it applies.',
  'op:get /.well-known/niadra-contact-keys.json':
    'The public keys a gateway checks a contact token with, offline: a JWK set (RFC 8037, `OKP` and `Ed25519`) of one space, named by `space`. A key is `active` or `retiring`: a retiring key signs nothing new and still verifies what it signed until `not_after`. Cache it, refresh it at least hourly, and on an unknown `kid` refresh at most once a minute before refusing.',
  'res:get /.well-known/niadra-contact-keys.json:200': 'The key set of the space.',
  'param:get /.well-known/niadra-contact-keys.json:space': 'The space whose keys to list. No caller may list a cell\'s spaces.',
  'op:get /v1/coordination/reports': 'The shadow reports of the space, newest first: for each run, the days covered and the conflicts a check would have pointed out among the outbound messages already received (`concurrent_agents`, `outside_window`, `quiet_hours`, `over_budget`, `tokens_used_twice`), each also per 1,000 customers a month, with a sample for review and nobody named.',
  'res:get /v1/coordination/reports:200': 'A page of reports.',
  'op:post /v1/coordination/shadow': 'Asks for a shadow report over a period: the space\'s policy applied to the outbound messages already ingested, with nothing enforced. The run reads the period in the background, and the report is listed when it is done.',
  'res:post /v1/coordination/shadow:202': 'The run was accepted; the report appears in the list when it finishes.',
  // Turn records and replay
  'op:get /v1/scenarios/{scenario_id}': 'One scenario: its turns, its assertions and its version.',
  'res:get /v1/scenarios/{scenario_id}:200': 'The scenario.',
  'op:patch /v1/scenarios/{scenario_id}': 'Changes the name, the assertions or the status (`active`, `retired`). Each change adds one to `version`.',
  'res:patch /v1/scenarios/{scenario_id}:200': 'The scenario after the change.',
  'op:post /v1/scenarios/from-report':
    'A bug report becomes a scenario: `conversation_id` or `turn_ids`, with an optional name and description. The turns are kept, and the assertions are suggested by rule from the turns and from the words of the description, which is read and never stored.',
  'param:post /v1/scenarios/from-report:Idempotency-Key': IDEM,
  'res:post /v1/scenarios/from-report:201': 'The scenario, with the suggested assertions.',
  'param:post /v1/scenarios:Idempotency-Key': IDEM,
  'res:post /v1/scenarios:201': 'The scenario.',
  'op:get /v1/scenario-runs/{run_id}': 'One run: the verdict of each scenario, the statistics of each assertion against its baseline, and how many executions completed, hit an infrastructure error or stopped at the pin check.',
  'res:get /v1/scenario-runs/{run_id}:200': 'The run.',
  'param:post /v1/scenario-runs:Idempotency-Key': IDEM,
  'res:post /v1/scenario-runs:201': 'The run, with its verdict decided.',
  'param:post /v1/turns/promote:Idempotency-Key': IDEM,
  // Typed state
  'res:get /v1/objects/coverage:200': 'The coverage per type.',
  'res:post /v1/types/fingerprint:200': 'Whether the type drifted, and the data issue opened or counted in when the type asks for an alert.',
  'res:post /v1/state/refresh-requests/{request_id}/release:200': 'The released request and the reason it carried.',
  'res:get /v1/coordination/overview:200': 'The counts of the window.',
  'param:post /v1/content/{ref}/release:Idempotency-Key': IDEM,
  'param:post /v1/content/{ref}/release:ref': 'The object\'s id, the field and the content\'s digest, as the `content` marker of the field names them.',
  'res:post /v1/content/{ref}/release:200': 'Whether something was released; false when the content was already clean.',
  // Signals
  'param:post /v1/profiles/{profile_id}/review-requests:Idempotency-Key': IDEM,
  'res:post /v1/profiles/{profile_id}/review-requests:201': 'The request, with the explanation built from receipts.',
  'op:get /v1/review-requests': 'The review requests of the space, newest first, with the status and the resolution of each.',
  'res:get /v1/review-requests:200': 'A page of requests.',
  'param:post /v1/legal-holds:Idempotency-Key': IDEM,
  'res:post /v1/legal-holds:201': 'The hold.',
  // Measurement
  'op:get /v1/measure/counterfactual-runs': 'The counterfactual reports of the space, newest first.',
  'res:get /v1/measure/counterfactual-runs:200': 'A page of reports.',
  'op:get /v1/measure/counterfactual-runs/{run_id}': 'One report: the overlap, the noise floor, the effect, the sign test, where the engaged items went, the cases skipped and the limits the report declares itself.',
  'res:get /v1/measure/counterfactual-runs/{run_id}:200': 'The report.',
  'res:post /v1/measure/counterfactual-runs:201': 'The report, computed over the completed cases.',
}
