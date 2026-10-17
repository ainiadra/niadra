/**
 * English descriptions of the write, context, history, objects, identity and profiles
 * routes, where the contract has none. Some replace base texts (en.ts) that went
 * stale against the real contract.
 */
const IDEM =
  'Required. Kept 24 hours with the hash of the body: a retry with the same key returns the first answer, and the same key with a different body returns 409 `conflict`.'

export const EN: Record<string, string> = {
  // Write
  'op:post /v1/batch':
    'Up to 500 items of mixed types: messages, system events, actions, `identify`, `verify`, `handoff` and the end of a conversation or task. The raw event is stored before the answer and deduplicated by `idempotency_key`, so a retry is always safe. Items are validated one by one: when every item is accepted the answer is 200, and when any is rejected it is 207 with one entry per rejected item in `errors`, never a 422 for the whole batch. Scopes are checked per item: `track` for events, `act` for actions, `identify` for identification.',
  'res:post /v1/batch:200': 'Every item was accepted or recognized as a duplicate.',
  'op:post /v1/media/uploads':
    'Reserves a place for an audio file or attachment and returns a presigned `upload_url`. Upload the bytes with `PUT` sending exactly the headers in `upload_headers`: the store refuses any other bytes, which is how the SHA-256 you declared is enforced. Then send the event with `content.media_ref` and `content.media_sha256`. Pass `subject` so a `forget` reaches the file even if no event ever references it.',
  'res:post /v1/media/uploads:201': 'The upload slot, valid until `expires_at`.',
  'op:get /v1/ingest/files/{import_id}':
    'Follows a file import started with `POST /v1/ingest/files`: status, how many records were accepted, duplicated or rejected, and the first 100 rejected rows with the reason. Values from the file are never echoed back.',
  'res:get /v1/ingest/files/{import_id}:200': 'The import, as it stands now.',
  'param:get /v1/ingest/files/{import_id}:import_id': 'The `import_id` returned when the file was sent.',
  'res:post /v1/ingest/files:202': 'The file is stored and queued; follow it with `GET /v1/ingest/files/{import_id}`.',
  'res:post /v1/ingest/webhook/{source_id}:200': 'The payload after the mapping of the source, as a batch result: accepted, duplicates and one error per rejected item. The raw payload is stored in every case.',
  'res:get /v1/ingest/webhook/{source_id}:200': 'The challenge value, echoed as plain text.',
  'param:get /v1/ingest/webhook/{source_id}:source_id': 'The source configured for this system.',
  'res:post /v1/otel/v1/traces:200': 'The spans after the mapper, as a batch result: accepted, duplicates and one error per rejected span.',
  'op:post /v1/feedback':
    'Retracts or corrects a fact, resolves an open item or records the outcome of a conversation. Each action needs its own fields, or the answer is 422: `retract_fact` needs `fact_id`, `correct_fact` needs `fact_id` and `value`, `resolve_open_item` needs `open_item_id`, and `conversation_outcome` needs `conversation_id` and `value`. Every correction becomes an event, and the derived memory is recomputed from it.',
  'res:post /v1/feedback:200': 'The correction was stored as an event, reported as a batch result.',
  // Context
  'op:get /v1/context':
    'The same Context Pack as `POST /v1/context`, addressed by profile id instead of a handle, for tools that already hold the profile. Send the ETag you have in `If-None-Match` and the answer is 304 with no body while the pack is still current. Same policy, verification level and receipt as the `POST`. Prefer `POST /v1/context` with `profile_id`: this route takes the conversation id in the query, a conversation id may be a phone number or an e-mail, and a query string reaches access logs.',
  'res:get /v1/context:200': 'The pack.',
  'param:get /v1/context:profile_id': 'The profile, as returned by the identity and profile routes.',
  'param:get /v1/context:view': 'Channel, subject or task view, such as `voice`, `chat` or `task:billing`.',
  'param:get /v1/context:verification': 'The level proven in this conversation; the effective level is never above the ceiling of the source.',
  'param:get /v1/context:conversation_id': 'Pins the pack to the conversation, so every turn gets the same bytes.',
  'param:get /v1/context:task_id': 'For internal agents, in place of `conversation_id`.',
  'param:get /v1/context:if-none-match': 'The ETag you already hold. When it still matches, the answer is 304.',
  'op:post /mcp':
    'The remote MCP server of the space, over Streamable HTTP with JSON-RPC 2.0. Authenticate with the source key and bind the customer with the `Niadra-Subject-Token` header, minted by `POST /v1/subject-tokens`. It serves eight tools (`get_customer_context`, `track_event`, `record_action`, `resolve_identity`, `search_customer_history`, `get_customer_timeline`, `open_history_item`, `correct_customer_memory`), the `niadra://context` resource and one prompt, `customer_context`, with the pack ready for your model. With the subject signals on, it also serves `get_constraints`, the constraints block of the customer, rendered for a tool through the binding the space declares when the model names it. Each tool needs its own scope, and no tool takes the customer as an argument.',
  'res:post /mcp:200': 'The JSON-RPC answer. A tool failure comes back as a result with `isError`, not as an HTTP error.',
  // History
  'op:get /v1/history/timeline':
    'The same timeline as `POST /v1/history/timeline`, addressed by profile id in the query, for tools that already hold the profile: one conversation or agent action per line, newest first, paginated with `cursor`. Same policy, verification level and receipt. Prefer `POST /v1/history/timeline` with `profile_id`, which keeps the conversation id out of the URL.',
  'res:get /v1/history/timeline:200': 'One page.',
  'res:post /v1/history/open:200': 'The opened item, as `GET /v1/history/items/{item_id}` answers.',
  'param:get /v1/history/timeline:profile_id': 'The profile whose history to read.',
  'param:get /v1/history/timeline:cursor': 'Opaque cursor from `next_cursor`.',
  'param:get /v1/history/timeline:conversation_id': 'The conversation asking, so the verification level of that session applies.',
  'param:get /v1/objects/{object_type}/{namespace}/{external_id}:external_id': 'The id in the system of record. It may contain slashes.',
  'param:get /v1/objects/{object_type}/{namespace}/{external_id}/timeline:external_id': 'The id in the system of record. It may contain slashes.',
  // Identity
  'op:get /v1/identity/assertions':
    'The immutable identity log: which handles were stated to belong to the same subject, by which method, with which evidence, and whether the assertion was applied, is pending or was rejected. Filter by profile or by state.',
  'param:get /v1/identity/assertions:profile_id': 'Only the assertions of this profile.',
  'param:get /v1/identity/assertions:state': '`applied`, `pending` (waiting for the merge outside the request) or `rejected`.',
  'op:post /v1/identity/assertions':
    'States that two or more handles belong to the same subject, with the method that proves it. The assertion goes through the same protections as the ones the ingestion makes: blocked values, limits per handle type, and never an automatic merge of two profiles that already hold a handle at V3 or above. The merge itself runs outside the request, so the answer is 202 with the operation.',
  'res:post /v1/identity/assertions:202': 'The operation, queued or applied.',
  'param:post /v1/identity/assertions:Idempotency-Key': IDEM,
  'res:post /v1/identity/assertions/{assertion_id}/retract:202': 'The operation. The profile is recomputed and may split in two or more.',
  'param:post /v1/identity/assertions/{assertion_id}/retract:Idempotency-Key': IDEM,
  'op:post /v1/identity/merge':
    'Merges two or more profiles by hand, with the actor and the reason recorded. Without `force`, the same protections as automatic merges apply: two profiles with verified handles or two organizations are refused. `force` overrides them and should be rare. Memory is not copied: facts keep pointing to their handle of origin, so an unmerge later puts everything back.',
  'res:post /v1/identity/merge:202': 'The operation, queued or applied.',
  'param:post /v1/identity/merge:Idempotency-Key': IDEM,
  'op:post /v1/identity/unmerge':
    'Splits a profile by retracting the assertions that connect some of its handles: name the handles to move out, or the assertions to retract. The component is recomputed and may split into several profiles; facts, objects and patterns follow their handle of origin, and the affected packs are recompiled.',
  'res:post /v1/identity/unmerge:202': 'The operation, queued or applied.',
  'param:post /v1/identity/unmerge:Idempotency-Key': IDEM,
  'op:post /v1/identity/handles/block':
    'Marks a value that must never link profiles: a store phone, a shared inbox, a test e-mail. A blocked handle still reads, but it never creates a merge again.',
  'res:post /v1/identity/handles/block:202': 'The operation, with status `blocked`.',
  'param:post /v1/identity/handles/block:Idempotency-Key': IDEM,
  'param:post /v1/identity/links:Idempotency-Key': IDEM,
  'param:post /v1/identity/links/{link_id}/end:Idempotency-Key': IDEM,
  'op:get /v1/identity/suggestions':
    'Pairs of profiles that look like the same subject, found offline and never applied without a person. Each side comes with its profile and identifier, masked by the role of the reader.',
  'res:get /v1/identity/suggestions:200': 'One page of open suggestions.',
  'param:post /v1/identity/suggestions/{suggestion_id}/accept:Idempotency-Key': IDEM,
  'res:post /v1/identity/suggestions/{suggestion_id}/accept:202': 'The operation that merges the pair.',
  'op:post /v1/identity/suggestions/{suggestion_id}/dismiss': 'Dismisses a suggestion. The pair of profiles does not come back for review, whatever the evidence.',
  'res:post /v1/identity/suggestions/{suggestion_id}/dismiss:200': 'The suggestion, now `rejected`.',
  'param:post /v1/identity/suggestions/{suggestion_id}/dismiss:Idempotency-Key': IDEM,
  // Profiles
  'res:get /v1/profiles:200': 'A page of profiles, with the cursor of the next one.',
  'param:get /v1/profiles:order': 'The order: `last_seen_desc`, the latest activity first (the default), or `last_seen_asc`, the longest without activity first.',
  'param:get /v1/profiles:kind': 'Only profiles of this kind: `person`, `account` or `partner`.',
  'param:get /v1/profiles:cursor': 'The `next_cursor` of the page before, as it came.',
  'param:get /v1/profiles:limit': 'How many rows a page, from 1 to 100; the default is 50.',
  'res:post /v1/profiles/search:200': 'The profiles found, with handles masked by the role of the reader.',
  'op:get /v1/profiles/{profile_id}':
    'A profile: subject kind, pseudonym, handles with their status and verification level, and the links to accounts and partners. Contact values are masked unless the role of the reader may see them.',
  'res:get /v1/profiles/{profile_id}:200': 'The profile.',
  'res:get /v1/profiles/{profile_id}/memory:200': 'The memory of the profile.',
  'res:get /v1/profiles/{profile_id}/objects:200': 'The objects of the profile.',
}
