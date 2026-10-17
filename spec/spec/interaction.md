# Niadra Interaction, version 0

- **Status:** draft
- **Date:** 2026-09-29
- **Schema:** [`schemas/interaction.v0.json`](../schemas/interaction.v0.json)
- **License:** Apache 2.0

## 1. Purpose

What a person was shown, how far they looked, what they engaged with, what they said they want or
refuse, and what they are. A memory derives the subject's signals from these interactions (affinity,
interests, stated constraints, attributes) and hands them back to the agent's tools as a constraints
block ([constraints spec](constraints.md)). This document fixes the eight kinds of interaction and the
canonical moment of an exposure, so that "shown", "seen" and "bought after seeing" mean the same thing
for every agent and every vendor.

## 2. Conventions

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, MAY
and OPTIONAL in this document are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174)
when, and only when, they appear in all capitals.

- An object is written `type:namespace:id` (`item_variant:loja:123`), the short form of the business
  object reference: the type is a declared object type, the namespace the system of record, and the id,
  which MAY contain colons, everything after the second colon.
- A field of a type is written `type.field` (`item_variant.color`); a subject attribute is a dotted name
  (`size.pants`, `network.preferred`, `diet`).
- Every interaction carries `for`: `self` (the default), `beneficiary:<id>` (a person under the subject's
  profile, such as a dependent) or `gift`.

## 3. Where interactions travel

3.1. A producer that records turns sends interactions inside the turn record, in `interactions`
(turn record spec). Each entry is one interaction of this document.

3.2. A producer that records no turns sends each interaction as one item of the event batch, of type
`interaction`, with the subject and the context the turn record would give:

| Field | Required | Meaning |
|---|---|---|
| `type` | yes | `interaction`. |
| `idempotency_key` | yes | The producer's unique id for this item; every retry reuses it. |
| `handle` | yes | The subject. |
| `conversation_id` | no | The conversation it happened in. |
| `agent` | no | The agent that showed the list or heard the preference. |
| `occurred_at` | yes | When it happened. |
| `interaction` | yes | The interaction. |

3.3. A span named `niadra.exposure` in OpenTelemetry becomes a `presented` interaction with `method`
`otel` (OpenTelemetry conventions spec).

3.4. A producer MUST NOT send an interaction item to a consumer that does not announce the feature: the
batch refuses unknown item types.

## 4. Kinds

| `kind` | Fields | What it is |
|---|---|---|
| `presented` | `exposure_id`, `list_id`, `list_kind`, `page`, `offset`, `parent_list_id`, `items[]` (`pos`, `ref`, `shown`, `team`), `visible_k`, `delivered_at`, `method` | A list delivered to the person (section 5). |
| `seen` | `exposure_id`, `max_index_seen` | How far the person saw a list, as the list component reports. |
| `engaged` | `exposure_id`, `ref`, `how` | A click, a detail opened, a mention ("tell me about the second one"), an item added to a cart, compared or shared. |
| `feedback` | `ref`, `polarity`, `reason_attrs` | The person liked or disliked an object, and optionally why, by field. |
| `preference` | `attr`, `op`, `values`, `strength`, `scope`, `category`, `expires_at`, `source` | What the person wants or refuses (section 6). |
| `attribute` | `name`, `system`, `value`, `source`, `category` | What the person is: a size, a preferred network, a diet (section 6). |
| `watch` | `ref`, `condition`, `consent_event_key`, `expires_at` | "Let me know when" (section 7). |
| `unwatch` | `ref` | Stop a watch. |

4.1. `kind` is REQUIRED; a consumer MUST reject an interaction without it or with another value.

4.2. Purchases, deliveries, returns and "kept it" are not interactions: they come from the lifecycle
outcomes of the objects that record them, never from what an agent says.

## 5. The exposure

5.1. **The canonical moment.** An exposure is a list the interface bridge delivered to the person, and
it happens at `delivered_at`: when the list reached the client. It is not when a tool returned the
results, nor when the model named them. A producer MUST stamp `delivered_at` at delivery.

5.2. **The id.** `exposure_id` is a UUIDv7 the producer mints at delivery. The exposure token (exposure
token spec) carries it with a position, so an app attributes a cart line by copying one string.

5.3. **Positions.** `pos` is the 1-based position in the whole list. "See more" is the same `list_id`
with `page + 1` and `offset` equal to the items already shown; every `pos` of that page is greater than
`offset`. Within one exposure each position and each object appears once. `parent_list_id` links a list
opened from another (a carousel inside a look).

5.4. **What was seen.** An item counts as exposed when its `pos` is at most `offset + visible_k`, or at
most the highest `max_index_seen` of any `seen` of the same exposure. Without `visible_k` and without a
`seen`, every item counts as exposed and the consumer corrects by position.

5.5. **Method.** `method` says how the producer knew: `bridge` from the interface bridge (the default),
`otel` from a span, `tool_result` inferred from what a tool returned. A `tool_result` exposure counts
more than the person saw; a consumer MUST keep it labeled and MUST NOT use it for attribution by line.

5.6. **What an item showed.** `shown` holds the values the item displayed for the fields its type tracks
or lets be claimed (a price, availability), so a later read can say what changed since the person saw
it. It MUST NOT hold personal data.

5.7. **Interleaving.** When two rankings are interleaved, `team` says which one contributed each item
(`a` or `b`); engagement and cart go to the list that contributed the item. An interleaved list's
`list_kind` MUST be the name of the tool whose rankings were interleaved, as the space's interleaving
experiment names it: a consumer counts one impression per such exposure, won by the ranking whose items the
person engaged with more, and a tie when neither was engaged with more (including none at all). Ranking
`a` is the one in production, `b` the candidate.

## 6. Preferences and attributes

6.1. A `preference` is about a field of a declared type (`attr`), with an operator and values: `in`,
`not_in`, `eq`, `ne`, `lt`, `lte`, `gt`, `gte` (one value each for the comparisons) and `between` (two
values, low and high).

6.2. `strength` `must` and `must_not` make a hard constraint; `prefer` and `avoid` a soft one. `scope`
is `turn`, `session` (the default) or `persistent`; `category` limits it to one category of the type
(a size in pants is not a size in shirts).

6.3. `source` is `stated` (the person said it), `tool_args` (the SDK captured it from the arguments of
a tool call) or `correction` (the person corrected an inference). A preference is never inferred: an
inference is a soft signal the consumer derives, never an interaction.

6.4. An `attribute` is what the person said about themselves (`stated`) or corrected (`correction`).
Attributes from outcomes (bought, kept, returned for size) are derived by the consumer and carry their
own source in the constraints block.

6.5. A consumer MUST NOT derive affinity or interest from an interaction with an object whose type or
field is sensitive; for those it keeps only the type and a count.

## 7. Watches

7.1. A watch exists only when the person asked for it: `consent_event_key` names the event where they
did. A consumer MUST NOT raise a notice from an inferred interest.

7.2. `condition` is an expression of the object type language over the object's fields
(`available == yes`); `expires_at` bounds it.

## 8. The beneficiary and the gift

8.1. Signals are kept apart for each `for`: what a person buys for a dependent never becomes the person's
own taste.

8.2. A `gift` goes to a disposable bucket and never becomes an attribute of the subject.

8.3. A delivery to another address is never taken as a beneficiary by itself: the constraints block asks
`for_whom` instead.

## 9. Processing

9.1. An interaction never carries message text; what was said travels in the events the turn record
points to.

9.2. A second item with a known `idempotency_key` MUST be treated as a duplicate.

9.3. A consumer keeps raw interactions for measurement for a bounded time (45 days by default) and then
only as aggregates.

## 10. Examples

See [`examples/interaction/`](../examples/interaction/), and the invalid ones in
[`examples/invalid/`](../examples/invalid/) whose names start with `interaction-`.
