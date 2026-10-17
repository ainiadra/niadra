/**
 * English descriptions for the governance routes of the data API: privacy and audit,
 * sources, patterns, measurement and usage, review and assistant, triggers and webhooks,
 * and base-wide analysis. Only where the contract has no description of its own.
 */
export const EN: Record<string, string> = {
  // Privacy and audit
  'op:post /v1/forget':
    'Erases a profile, a handle or a conversation by lineage: event content, the facts left without evidence, episodes, open items, objects owned by the handle, the personal content of actions, patterns, links, measurement rows, review samples, trigger payloads, packs and files, plus tombstones for the continuous export. Forgetting a person never erases the account it is linked to. The erasure runs in the background; follow it with `GET /v1/forget/{request_id}`. When it completes, the answer carries the erasure receipt hash and the export runs that already held the data, and the `erasure.completed` webhook fires.',
  'res:post /v1/forget:202': 'Accepted. The erasure is queued; `status` moves from `pending` to `running` and `completed`.',
  'param:post /v1/forget:Idempotency-Key': 'Mandatory. Kept 24 hours with the body hash; the same key with a different body returns 409.',
  'op:get /v1/forget':
    'Lists the erasure requests of the space, newest first, filtered by `status`. Your privacy team uses it to show every data subject request and its evidence: what was erased, the receipt hash and the export runs your data lake must purge.',
  'res:get /v1/forget:200': 'One page of erasure requests.',
  'param:get /v1/forget:status': 'Only requests in this state: `pending`, `running`, `completed` or `failed`.',
  'op:get /v1/forget/{request_id}':
    'Reads one erasure request. `erased` counts the rows removed per kind of data, `receipt_hash` is the hash of the erasure receipt in the audit chain, and `export_run_ids` lists the continuous export runs that already contained the data, so you can purge your own copy.',
  'res:get /v1/forget/{request_id}:200': 'The erasure request and how far it went.',
  'op:post /v1/export':
    'Builds the access and portability package of one data subject, by `profile_id` or by handle: events, facts, episodes, open items, objects, actions, patterns with their evidence, and links. The answer is immediate and carries a signed download URL, the SHA-256 of the package and the list of files inside it. Every export is recorded in the audit trail.',
  'res:post /v1/export:201': 'The package is ready: download it before `download_expires_at`.',
  'op:get /v1/exports/{run_id}/download':
    'Downloads the file of a finished export. The body is the package itself, not JSON; check it against the `sha256` returned when the export was created. Each download is recorded in the audit trail.',
  'res:get /v1/exports/{run_id}/download:200': 'The package file.',
  'op:get /v1/exports/runs':
    'Lists the runs of the continuous export to your bucket, newest first: the change sequence range each run covered, its status and the manifest it wrote. Filter by `schedule_id` to follow one destination.',
  'res:get /v1/exports/runs:200': 'One page of export runs.',
  'param:get /v1/exports/runs:schedule_id': 'Only the runs of this export schedule.',
  'op:put /v1/exports/destinations/{schedule_id}/credential':
    'Writes the access key of your export destination (S3 or S3-compatible storage), a JSON with `access_key_id` and `secret_access_key`, straight into the vault of the cell. The key should only write to the destination prefix. The value is never returned and never reaches the control plane; the answer only says which version is now in use.',
  'res:put /v1/exports/destinations/{schedule_id}/credential:200': 'Stored. `version` is the credential version now in use.',
  'param:put /v1/exports/destinations/{schedule_id}/credential:schedule_id': 'The export schedule, as named in the `export-schedules` configuration.',
  'op:get /v1/receipts':
    'Lists the receipts of the space: every context read, history call, analytics query and administrative action, and every read the policy refused (`denied`, with the reason in `detail`), with the delivered item ids, how many items were withheld and under which policy version. Content is never stored in a receipt. Receipts are chained by SHA-256 per space, slice and day; filter by profile, source, conversation, kind or period. Prefer `POST /v1/receipts/search`, which takes the same filters in the body: a conversation id may be a phone number or an e-mail, and a query string reaches access logs.',
  'res:get /v1/receipts:200': 'One page of receipts.',
  'res:post /v1/receipts/search:200': 'One page of receipts, as `GET /v1/receipts` answers.',
  'op:get /v1/receipts/verify':
    'Recomputes the SHA-256 chain of one day of receipts and compares its root with the root anchored for that day. `valid` is true when no link is broken; `breaks` lists the slice and sequence of each break. `anchor_copy_matches` compares the recomputed root with the copy kept outside the database, and `anchor_locked` says whether that copy sits in write-once storage. Run it during an audit to prove that no receipt was changed or removed.',
  'res:get /v1/receipts/verify:200': 'The verification of that day: heads per slice, breaks and whether the anchored root matches.',
  'param:get /v1/receipts/verify:day': 'The day to verify, in UTC.',
  'op:get /v1/lineage/receipt/{receipt_id}':
    'Answers "why did the agent say that?": from one receipt to the items delivered, and from each item to the events it came from, with channel, source and time. `excluded` lists what the policy held back and why, and `manifest_found` says whether the provenance manifest is still available.',
  'res:get /v1/lineage/receipt/{receipt_id}:200': 'The receipt, its items and the source events behind each one.',
  'op:get /v1/lineage/fact/{fact_id}':
    'Answers "who saw this fact?": every receipt in which the fact was delivered, with the source, the audience and the time of each read.',
  'res:get /v1/lineage/fact/{fact_id}:200': 'The receipts that delivered the fact.',
  'op:post /v1/policy/simulate':
    'Shows what a source or an audience would receive for one profile, item by item, before you grant access: the effect of the policy on each item (`allow`, `mask` or `deny`), the rule that decided it and the text the agent would get. Nothing is delivered and no receipt of a read is written; the simulation itself is recorded as an administrative action.',
  'res:post /v1/policy/simulate:200': 'The simulated items, the withheld counts per reason and the resulting text.',
  'op:put /v1/secrets/{kind}/{secret_id}':
    'Writes a secret straight into the vault of the cell: a webhook signing secret (`webhook`) or the credential of a model provider you bring (`model`). Secrets are write only; the answer carries the new version, and during a webhook secret rotation the previous version stays valid until you retire it.',
  'res:put /v1/secrets/{kind}/{secret_id}:200': 'Stored. `version` is the secret version now in use.',
  'param:put /v1/secrets/{kind}/{secret_id}:kind': '`webhook` for a webhook signing secret, `model` for a model provider credential.',
  // Sources
  'res:get /v1/sources/arrived:200': 'Whether any event arrived, and since when the question holds.',
  'param:get /v1/sources/arrived:days': 'How many days to look at, from 1 to 90; the default is 90.',
  'op:get /v1/sources/coverage':
    'Shows, per source and per day, how many events the SDK says it sent, how many arrived, how many were rejected and how many had an unmapped type. A source whose last event is old is silent: the context marks it in `coverage`, and the `source.silent` trigger can notify you.',
  'res:get /v1/sources/coverage:200': 'Coverage per source, with one row per day.',
  'param:get /v1/sources/coverage:days': 'How many days to cover, from 1 to 90.',
  'res:post /v1/sources/{source_id}/revoke:200': 'The source is cut in this cell; `revoked_key_ids` lists the keys that stopped working.',
  // Patterns, measurement and usage
  'op:post /v1/traits/{trait_id}/retract':
    'Retracts a pattern that is wrong. The retraction leaves a tombstone, so the same evidence does not bring the pattern back. The pattern leaves the context of the next reads.',
  'res:post /v1/traits/{trait_id}/retract:200': 'The pattern, now with `retracted_at`.',
  'op:post /v1/traits/rules/dry-run':
    'Tests a pattern rule from the catalog, with your parameters, against the last 90 days before you turn it on: how many profiles were evaluated, how many would get the pattern, and a pseudonymous sample with the evidence and the expiry of each one. It counts as derivation does: a count below 2 counts as 2 outside the single-evidence rules, a rule over system events runs within the hot window of the space, and a profile that objected to profiling is neither counted nor shown. Nothing is written to the memory.',
  'res:post /v1/traits/rules/dry-run:200': 'How many profiles would match, with a sample.',
  'op:post /v1/profiles/{profile_id}/traits/opt-out':
    'Records the objection of a data subject to profiling: the memory stops deriving patterns for this profile and removes the active ones. Facts, history and context keep working.',
  'res:post /v1/profiles/{profile_id}/traits/opt-out:200': 'The opt-out is in force from `opted_out_at`.',
  'op:get /v1/context-use':
    'Measures whether each agent used the context it received: deliveries with at least one item used, repeated questions, contradictions, selection misses, handoffs the receiver did not read, late deliveries and sessions without context. Group with `group_by` by `day`, `source_id`, `vendor`, `channel`, `view` or `experiment_group` (default `source_id`). Rates come with a confidence interval, and a rate with nothing to measure is `null`, never zero. `review` says, per signal, how many of its marks your reviewers confirmed in the same period and sources. People with the vendor role only see their own sources. `slot_channels` says, per view and retrieval channel, what each channel brought to the per-turn slots and what agents did with it, and `retrieval_weights` carries the learned fusion weights, with their version and the last nightly runs.',
  'res:get /v1/context-use:200': 'One bucket per combination of the `group_by` dimensions.',
  'param:get /v1/context-use:group_by': 'Repeat the parameter to group by more than one of `day`, `source_id`, `vendor`, `channel`, `view` and `experiment_group`.',
  'param:get /v1/context-use:experiment_group': 'Only one group of an experiment, such as the control group, to compare it with the group that receives the memory.',
  'op:get /v1/context-use/{conversation_id}':
    'The detail of one conversation: one entry per delivery, with the manifest it came from, the items used, the questions repeated, the contradictions and whether a handoff was read. When a delivery could not be measured, `not_measured_reason` says why. Prefer `POST /v1/context-use/conversation`, which takes the conversation id in the body.',
  'res:get /v1/context-use/{conversation_id}:200': 'One entry per measured delivery of the conversation.',
  'res:post /v1/context-use/conversation:200': 'One entry per measured delivery of the conversation.',
  'res:get /v1/usage/budget:200': 'One line per purpose: `extraction` and `assistant`.',
  'op:get /v1/usage':
    'The usage of the space per hour or per day: billable sessions, context reads, history calls, ingested events and the other metrics, with their cost in micros. Pass `group_by=source` to split each metric by source, or `source_id` to see one source.',
  'res:get /v1/usage:200': 'One item per metric and period, and per source with `group_by=source`.',
  'param:get /v1/usage:metric': 'Only these metrics, such as `session_billable`, `context_read`, `history_call` or `event_ingested`. Up to 20.',
  'param:get /v1/usage:group_by': '`source` splits every metric by source.',
  // Review and assistant
  'op:get /v1/review/queue':
    'The next samples for your team to review: the facts extracted from a session, with the evidence behind each one, and the measurement signals found in it. The reviewer sees no more than the agent of that purpose would, and every sample opened is recorded. A session that left nothing a verdict can be recorded on is not sampled, and a sample left with nothing leaves the queue.',
  'res:get /v1/review/queue:200': 'The next samples to review.',
  'op:post /v1/review/{sample_id}/verdicts':
    'Records the verdicts of one reviewer on a sample: `correct`, `wrong` or `missing` for a fact, `holds` or `does_not_hold` for a measurement signal or a pattern. Only a person with the review role can call it, never a source key. A verdict does not change the memory by itself: a wrong fact leaves through `POST /v1/feedback`, a wrong pattern through its retraction.',
  'res:post /v1/review/{sample_id}/verdicts:200': 'The verdicts were recorded.',
  'op:get /v1/review/agreement':
    'How much reviewers agree with the system, and with each other, per kind of item and per signal of the measurement (`signal:repeated` and the others), over the last `days` days. It is the number that tells whether the extraction and the measurement can be trusted in your space. Each verdict names the source of its sample, so a person with the vendor role reads the agreement on its own sources only.',
  'res:get /v1/review/agreement:200': 'Agreement per kind of item and per signal, with confidence intervals.',
  'param:get /v1/review/agreement:days': 'Window of the report, from 1 to 365 days.',
  'param:get /v1/review/agreement:source_id': 'Only the verdicts on samples of this source.',
  'op:post /v1/assist':
    'Asks the configuration assistant for a proposal: a webhook mapping, an extraction schema, a source policy, or a trigger or pattern rule. It works on masked samples inside the cell and produces a versioned diff with its checks; a person approves it in the control API. The assistant never changes the configuration on its own.',
  'res:post /v1/assist:202': 'Queued. Follow the run until it reaches `proposed` or `delivered`.',
  'op:get /v1/assist':
    'Lists the proposals requested from the configuration assistant, newest first, with their status and the diff each one produced.',
  'res:get /v1/assist:200': 'One page of assistant runs.',
  'op:get /v1/assist/{run_id}':
    'Reads one assistant run: its status, the checks it ran against your samples, the problems it found and, once proposed, the diff sent to the control plane for approval.',
  'res:get /v1/assist/{run_id}:200': 'The assistant run.',
  // Triggers and webhooks
  'op:post /v1/triggers/dry-run':
    'Tests a trigger rule before you turn it on: for how many customers active in the last 30 days the condition holds now, how many were evaluated, and a sample with the evidence. It also checks that the target endpoint may receive what the rule sends; `target_problem` says why not. Nothing is sent.',
  'res:post /v1/triggers/dry-run:200': 'How many firings the rule would have had, with a sample.',
  'op:get /v1/triggers/firings':
    'Lists the firings of your trigger rules, with the rule version and the pointers to the evidence, never the content of a conversation. A firing whose evidence was split by an unmerge shows `retracted_at`.',
  'res:get /v1/triggers/firings:200': 'One page of firings.',
  'op:get /v1/webhooks/deliveries':
    'Lists the deliveries of outgoing webhooks, with attempts, the last status code and the next attempt. Failed deliveries retry with exponential backoff for 24 hours, then become `dead` and stay up to 7 days, counted from when the delivery was created, for you to redeliver.',
  'res:get /v1/webhooks/deliveries:200': 'One page of deliveries.',
  'op:post /v1/webhooks/deliveries/{delivery_id}/redeliver':
    'Sends a failed or dead delivery again, with the same `webhook-id`, so your consumer can recognize it as the same event.',
  'res:post /v1/webhooks/deliveries/{delivery_id}/redeliver:202': 'The delivery is queued again.',
  'res:post /v1/webhooks/endpoints/{endpoint_id}/test:202': 'The test delivery is queued; follow it in `GET /v1/webhooks/deliveries`.',
  'param:post /v1/webhooks/endpoints/{endpoint_id}/test:endpoint_id': 'The `endpoint_id` of the endpoint or SIEM stream, as in `webhook-endpoints`.',
  // Base-wide analysis
  'op:get /v1/insights/schema':
    'Describes what base-wide analysis accepts: the entities (episodes, open items, patterns, actions, objects and profiles), the dimensions and filters of each one, the metrics and the minimum group size.',
  'res:get /v1/insights/schema:200': 'The queryable entities, dimensions and metrics.',
  'op:get /v1/insights/overview':
    'The headline counts of the space memory, computed every night per entity and dimension. Buckets smaller than the minimum group size are suppressed and counted in `suppressed_buckets`.',
  'res:get /v1/insights/overview:200': 'Counts per dimension, as of the last nightly run.',
  'op:post /v1/insights/aggregate':
    'Counts and rates across all customers of the space: pick an entity, a period, filters, dimensions and metrics (`count`, `profiles`, `avg_sentiment`). Groups smaller than the minimum group size are suppressed, never shown, and every query leaves an analytics receipt.',
  'res:post /v1/insights/aggregate:200': 'One row per group, with the suppressed groups counted apart.',
  'op:post /v1/insights/profiles':
    'Finds the profiles that meet a criterion, such as three complaints about the same category in a period. Profiles come back by pseudonym, never by handle.',
  'res:post /v1/insights/profiles:200': 'Matching profiles, by pseudonym.',
  'op:post /v1/insights/search':
    'Semantic search over the episodes of the whole space, for questions a single profile cannot answer. Each episode is ranked by the vector stored with its current version; only the question and the episodes without a vector of its encoder are encoded at question time, and the receipt says how many were. Hits come back by pseudonym, with redacted snippets, and `truncated` says whether more episodes matched than were scanned.',
  'res:post /v1/insights/search:200': 'Matching episodes, by pseudonym, with redacted snippets.',
  'op:post /v1/insights/episodes/sample':
    'A redacted sample of the episodes that match a filter, for a person or an LLM to read what is behind a number.',
  'res:post /v1/insights/episodes/sample:200': 'The sampled episodes, by pseudonym.',
  'op:post /v1/insights/context':
    'The memory of one customer, by pseudonym, as the analyst audience may see it: facts, episodes, open items and patterns, of the confirmed handles only: what arrived through an unconfirmed link counts nowhere, and the pseudonym lists that handle apart in `hidden_handle_ids`. Sensitive categories stay out unless your policy opens them to analysts.',
  'res:post /v1/insights/context:200': 'The customer memory, by pseudonym.',
  'op:post /v1/insights/lineage':
    'Explains where one item of a pseudonymous customer came from, down to the events that produced it; an item that arrived through an unconfirmed link is not found.',
  'res:post /v1/insights/lineage:200': 'The item and the events behind it.',
  'op:post /v1/insights/reveal':
    'Swaps a pseudonym for the handles of the customer. It needs a reason of at least 10 characters and elevated access: a source key with the `analytics` and `admin` scopes, or a person with the security role. Every reveal is recorded with its reason. The handles of an unconfirmed link stay out.',
  'res:post /v1/insights/reveal:200': 'The handles behind the pseudonym.',
  'res:post /mcp/insights:200': 'The JSON-RPC 2.0 answer to the message.',
  // Restore, contradictions, review pairs
  "res:post /v1/facts/{fact_id}/restore:202": "Accepted: the restore is now a `feedback.restore_fact` event, reported as a batch answer; the memory applies it within seconds.",
  "param:post /v1/facts/{fact_id}/restore:fact_id": "The retracted or corrected fact.",
  "param:post /v1/facts/{fact_id}/restore:Idempotency-Key": "Required. Kept 24 hours with the body hash; a replayed key is a duplicate.",
  "FactRestoreRequest.profile_id": "The profile the fact belongs to.",
  "FactRestoreRequest.reason": "Why the retraction or correction is undone, up to 500 characters; kept in the lineage.",
  "FactContradictionOut": "A fact in conflict with the one read, and who found the conflict.",
  "ReviewContradiction": "A live pair of facts in conflict, one of them resting on the sampled conversation.",
  "ReviewContradiction.other_fact_id": "The older fact of the pair.",
  "ReviewContradiction.predicate": "The predicate both facts state.",
  "ReviewContradiction.value": "The value of the newer fact, as the purpose of the sample lets the reviewer see it.",
  "ReviewContradiction.other_value": "The value of the older fact.",
  // Knowledge gaps and the intent gate
  "res:get /v1/insights/gaps:200": "The gaps, the recurring questions and the proposals of the window, with the groups withheld.",
  "param:get /v1/insights/gaps:days": "The window, in days; the counts count each customer once inside it.",
  "res:post /v1/insights/gaps/review:200": "The decision as recorded.",
  "GapSource": "A source of the space that could record the missing subject.",
  "GapSource.name": "The source as the Console names it.",
  "GapSource.source_id": "The source.",
  "GapsResponse.gaps": "What customers asked for and memory had no record of, or agents had to ask returning customers for.",
  "GapsResponse.recurring": "The questions memory answers every time, by subject and category.",
  "GapsResponse.proposals": "Standing answers proposed by rule; nothing reaches the agents until the team approves the configuration.",
  "GapsResponse.since": "Start of the window.",
  "GapsResponse.until": "End of the window.",
  "RecurringOut": "A question customers ask again and again that memory answers.",
  "RecurringOut.key": "`<kind>:<subject>:<category>`; the key of a review.",
  "RecurringOut.subject": "A value type or a predicate.",
  "RecurringOut.topic": "The normalized category of the conversations; empty when none was measured.",
  "RecurringOut.turns": "Turns asking for it in the window.",
  "RecurringOut.customers": "Distinct customers in the window, estimated within about 3%.",
  "RecurringOut.review": "The decision of the team on it, when there is one.",
  "ProposalOut": "A standing line proposed by rule for the space.",
  "ProposalOut.key": "`<kind>:<subject>:<category>`; the key of a review.",
  "ProposalOut.subject": "A value type or a predicate.",
  "ProposalOut.topic": "The normalized category; empty when none.",
  "ProposalOut.review": "The decision of the team on it, when there is one.",
  "GapReviewRequest": "The decision of the team on a gap or a proposal.",
  "GapReviewRequest.key": "The `key` of the gap, recurring question or proposal.",
  "GapReviewRequest.decision": "`accepted` or `dismissed`.",
  "GapReviewRequest.note": "Why, up to 500 characters.",
  "GapReviewOut": "A decision as recorded.",
  "GapReviewOut.decision": "`accepted` or `dismissed`.",
  "GapReviewOut.note": "The note left with it.",
  "GapReviewOut.decided_at": "When it was recorded.",
  "GapReviewOut.decided_by": "Who recorded it: the person, or the key.",
  "GatedTurnsOut.view": "`voice` or `chat`.",
  // Learned views
  "LearnedViewOut.defaults": "Per section, the view's own budget in tokens.",
  "LearnedViewOut.source_id": "The reading source.",
  "LearnedViewOut.view": "`chat` or `voice`.",
  "ViewRunOut": "One nightly run of the learned views.",
  "ViewRunOut.computed_at": "When the run was recorded.",
  "ViewRunOut.day": "The last day of the window the run read.",
  "ViewRunOut.outcome": "`applied` (a view moved), `unchanged`, `no_verdicts` (a window with nothing to judge) or `vetoed` (a control group said it did worse).",
  "ViewRunOut.version": "The version the run wrote, or the one it kept.",
}
