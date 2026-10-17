# Niadra Coordination, version 0

- **Status:** draft
- **Date:** 2026-09-29
- **Schemas:** [`schemas/coordination-decision.v0.json`](../schemas/coordination-decision.v0.json),
  [`schemas/handoff-package.v0.json`](../schemas/handoff-package.v0.json)
- **License:** Apache 2.0

## 1. Purpose

A company's customer is served by several agents from several vendors, by people in panels no agent sees,
and by timers and middleware that call nobody. Each of them can contact the same person, act on the same
object or repeat what another already did. Coordination answers, before an agent acts, whether it may act
now, who holds the subject or the object, what was already done, and why; and it records what happened
after the fact.

The memory is a mirror that fails early with the right message: it never sends a message, never routes
one and is never the only barrier. The barrier that applies is the company's dispatch point (a gateway
or a middleware), which can check a contact token offline ([`contact-token.md`](contact-token.md)).

## 2. Conventions

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, MAY
and OPTIONAL in this document are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174)
when, and only when, they appear in all capitals.

- The **subject** is a customer, named by a handle. An **object** is a business object, named by
  `{type, namespace, id}` as in [`business-object.md`](business-object.md).
- A **holder** is whoever has a subject or an object now: an agent, a person, a queue or a system.
- A **purpose** is why an outbound contact happens: `transactional`, `service`, `marketing`, `retention`
  and `collection` are standard, and a space MAY declare others.
- An **effect** is an external act caused by a business fact: a message delivered, a document filed, a
  notice sent, a paid call made.
- The **producer** answers checks and records declarations. A **caller** is the agent or its SDK.

## 3. The check

A caller asks before it acts. The request:

| Field | Required | Meaning |
|---|---|---|
| `subject` | no | The customer. Without it there is no budget, suppression or holder to consult. |
| `object` | no | The object acted on. |
| `agent` | yes | The agent asking. |
| `intent` | yes | What it is about to do, as a name (`farewell`, `proposal_followup`). |
| `channel` | no | The channel of a contact. |
| `direction` | yes | `inbound` (answering a message) or `outbound` (starting a contact). |
| `purpose` | yes | The purpose of a contact. |
| `task` | no | The task type, when the act works on an object (`hearing_summary`). |
| `effect_key` | no | The key of the business fact the act is an effect of (section 7). |
| `effect_kind` | no | The kind of that effect; `delivery` when absent. |
| `gateway_id` | no | The dispatch point the contact will leave through. |
| `destination_hash` | no | The token's `rcpt` when the destination is not the subject's handle. |

The answer is the **decision**, described by `coordination-decision.v0.json`:

| Field | Meaning |
|---|---|
| `decision` | `allow`, `defer`, `deny` or `handoff_to`. |
| `reasons` | Codes, section 4. Empty for a plain `allow`. |
| `holder` | With `handoff_to`: who holds the subject and takes over. |
| `owner` | Who holds the subject or the object: the holder, the claim's kind and level, the source it came from, how it came (`declaration`, `mapping`, `worker`), since when, until when, and its epoch (section 6). |
| `locks` | The task locks on the object (section 6.7). |
| `effect` | With `effect_key`: `none`, `in_flight`, `done` or `unknown_outcome`, and the attempt (section 7). |
| `channel` | The channel's state: until when free-form messages go out, whether a paid template is required, until when the quiet hours last (section 9). |
| `contact_budget` | Per purpose with a budget: the units left and when the next one is allowed (section 8). |
| `suppressions` | The purposes the subject is suppressed for at every hour. Never the reason, never the handle. |
| `contact_window_until` | With `contact_window`: when the subject's contact window on the channel ends, the first moment the contact may go. |
| `commitments_active` | Offers, discounts or proposals another agent made that still hold. |
| `promises_open` | Promises made to the subject and not kept yet. |
| `decision_id` | The decision's id, which a declaration cites. |
| `valid_for_s` | How long the decision holds. A caller that acts later MUST check again. |
| `contact_token` | Only with an `allow` of an outbound contact whose purpose needs a token. |

3.1. `allow` means the caller may act now. With `effect.state: none`, the check also reserved the effect
for the caller, which MUST then declare how it ended (section 5).

3.2. `defer` means not now: `owner.valid_until`, `channel.quiet_until` or the budget's `next_allowed_at`
says when to ask again. A deferred contact is rescheduled, never dropped.

3.3. `deny` means not at all for this purpose now: the subject is suppressed, the budget is spent, or the
effect already happened. A denial is recorded with its reason.

3.4. `handoff_to` means the subject is held by someone else who must answer instead: the caller SHOULD
route the conversation to `holder` or create a handoff (section 11).

3.5. A message the customer sent is never denied: an `inbound` check answers `allow` or `handoff_to`,
never `deny` or `defer`.

3.6. The producer evaluates in this order and stops at the first that decides: the effect (a `done`,
`in_flight` or `unknown_outcome` effect denies); a suppression of the purpose (denies); a holder whose
claim does not allow the intent (defers an outbound contact until the claim ends, hands off an inbound
one); another holder's lock on the object's task (defers); an exhausted budget or slice of the intent
(denies); a spread slice whose next unit is not free yet (defers); a contact window of the subject that
`now` falls in, on the channel and for the purpose or `any` (defers until the window ends,
[`suppression-list.md`](suppression-list.md), section 6.4); the quiet hours (defer). Only then is the budget
spent and the token issued. The budget, the suppression and the holder are read and the
budget spent in one atomic step, so two callers never both get the last unit.

3.7. A check without `effect_key` MUST NOT reserve anything but budget. A batch of checks
(`/check/batch`, up to 500) answers each one as if asked alone, in order.

3.8. **Tokens.** An allowed outbound contact of a purpose that needs a token carries one for the gateway
the check names in `gateway_id`, or else for the only gateway of the space that serves the check's
channel. A channel no gateway serves gets no token: nothing there checks one. A check that names an
unknown gateway, or none while several serve the channel, is refused as invalid. The token's `jti` is the
decision's `decision_id`, and its `rcpt` is `destination_hash` when the caller gives it, or else the
subject's handle keyed with the gateway's key, so the subject is then a phone number or an e-mail
address.

3.9. **The contact log.** Every allowed outbound contact of a subject on a channel is recorded under its
`decision_id` before the answer, and a `contact.made` of the same `jti` or `decision_id` counts as a use
of it (section 5.2).

3.10. **Advice without a check.** A context read MAY add the `coordination` block
([`context-pack.md`](context-pack.md), 10): who holds the subject, the purposes it is suppressed for, the
contacts each purpose with a budget has left, and the commitments that hold. It decides nothing, reserves
nothing and issues no token; an act still asks the check.

## 4. Reason codes

| Code | Decision | Meaning |
|---|---|---|
| `effect_done` | `deny` | The effect of this key already happened. |
| `effect_in_flight` | `deny` | Another attempt holds the key now. |
| `effect_unknown_outcome` | `deny` | An attempt may have gone out and nobody confirmed it (section 7.3). |
| `suppressed` | `deny` | The subject asked not to be contacted for this purpose, or a rule suppressed it. |
| `budget_exhausted` | `deny` | The purpose's budget for the subject, or the intent's slice of it, is spent in the window. |
| `budget_paced` | `defer` | The intent's slice spreads its units over days, and the next one is not free yet. |
| `owner_active` | `defer`, `handoff_to` | Another holder's claim does not allow the intent. |
| `lock_held` | `defer` | Another holder locked the task on the object. |
| `contact_window` | `defer` | The subject's own contact hours on the channel: the contact waits until `contact_window_until`. |
| `quiet_hours` | `defer` | The channel's quiet hours. |
| `template_required` | `allow` | Outside the service window: only a paid template may go out. |
| `rebuilding` | `defer` | The producer is rebuilding its state and the purpose fails closed (section 10). |
| `unavailable` | `defer` | A dependency of the producer is down and the purpose, or the effect's key, fails closed (section 10). |
| `unchecked` | `allow` | The producer could not decide and the purpose fails open (section 10). |

A reader MUST treat an unknown code as opaque and act on `decision` alone.

## 5. Declarations

After it acts, the caller declares what happened (`/declare`, with an `Idempotency-Key`). `detail`
carries the fields of each kind, and no other:

| `kind` | `detail` | Effect |
|---|---|---|
| `case.opened` | `level`, `lease_s`, `intents` | A case claim on the subject (section 6). |
| `case.closed` | `claim_id`, `epoch` | Releases the case. |
| `lease` | `level`, `lease_s`, `intents` | An owner lease, as `/claims` with kind `owner`. |
| `task_lock` | `task`, `lease_s` | A task lock on the declaration's `object`. |
| `contact.made` | `decision_id`, `jti`, `purpose`, `channel`, `gateway_id`, `unchecked` | The contact left. `jti` is the contact token's. |
| `effect` | `effect_key`, `state` (`done`, `failed`, `unknown_outcome`), `attempt` | How the reserved attempt ended. |
| `commitment.made` | `commitment_id`, `type`, `terms`, `valid_until` | An offer, a discount or a proposal. |
| `commitment.withdrawn` | `commitment_id` | It no longer holds. |
| `commitment.decided` | `commitment_id`, `outcome` (`accepted`, `declined`) | The person answered it, maybe in a later conversation; it no longer holds. |
| `handoff` | `handoff_id`, `status` (`accepted`, `closed`), `outcome` | The handoff moved; a handoff is created with its package (section 11). |
| `suppression.added` | `purpose`, `channel`, `reason`, `until`, `window` | The subject may not be contacted for the purpose (`any`: every purpose), on the channel or on every channel (null). `until` defaults to the reason's own interval, or to no end. With `window`, only in those local hours ([`suppression-list.md`](suppression-list.md), section 6.4). |
| `suppression.lifted` | `purpose`, `channel` | Lifts what the same source added for the purpose and channel. |

5.1. A declaration never raises an error for the order it arrives in: a `contact.made` for a decision the
producer no longer holds is still recorded.

5.2. Two `contact.made` citing the same `jti` are both recorded, and the second counts as a token used
twice. A `contact.made` with neither `jti` nor `decision_id` left without a decision: it still counts in
its purpose's budget for the subject, over the limit or not.

5.3. A commitment names its subject, and holds by its type's compatibility with those that hold for the
subject: of a `first_holds` type, the first holds and a later one does not; of a `best_wins` type, the one
with the higher `score` holds and the other is superseded; an `exclusive` one holds alone, over every
type, and does not hold while another does. `accepted` in the answer says whether the new one holds. A
commitment also becomes the agent's action (operation `commitment.<type>`) on the object
`{type: commitment, namespace: coordination, id: commitment_id}`, so another agent's context shows it as
done by someone else; accepting or declining it in a later conversation changes its state.

5.4. A suppression names its subject by a phone number or an e-mail address, the destinations of the
suppression list ([`suppression-list.md`](suppression-list.md), section 3). A source lifts only what it
added; a suppression another source added stays until that source or a person lifts it, or its `until`
passes.

5.5. The reason `stated_preference` is always known: the producer adds it itself for the hours a customer
said they may be contacted in ("don't call before 9 am"), as a suppression of `any` purpose with a `window`,
under a source of its own, so the customer's next word on the same channel replaces it and no vendor lifts
it.

## 6. Ownership

6.1. **Claims.** A claim says that a holder has a subject or an object, of a kind (`owner`, `case` or
`task_lock`), at a level, from a source, since a time, until a time, allowing some intents. Claims come in
three ways: a **declaration** through the API; an **observation mapped** from a webhook (a service panel
opened a human session); an **observation** the company's worker read and sent (a middleware's pause
flag).

6.2. **Levels.** The space declares its levels from the most restrictive to the least, for example
`closed`, `human_active`, `transfer_pending`, `soft_pause`, `agent_active`.

6.3. **The most restrictive wins.** The holder of a target is the claim that has not expired with the
most restrictive level; among equals, the latest; among claims of the same time, the one whose source
name sorts first. A claim at a softer level MUST NOT demote a stricter one still valid.

6.4. **Validity per source.** Each source has a maximum validity the producer applies to what it states,
including a state that never expires where it comes from: a claim lasts until the earlier of what it
says and its source's cap.

6.5. **Declarations may be refused, observations never.** A declaration is refused (`409 lease_held`, with
the holder and until when) while another holder's valid claim is at least as restrictive, and nothing is
recorded. The same holder renews its own claim. An observation is a fact a system reported: it always
replaces what the same source said before, and the resolution in 6.3 still decides who holds. An
observation older than what its source said last is ignored, so events delivered out of order never bring
back a claim that ended.

6.5.1. **Mapped observations.** The space maps a source's system events to ownership, per canonical type:
an event holds its subject (or each object it names) at a level, by a holder, from the event's time and for
at most the source's validity; or it ends what the source said before, at the event's time. A service
panel's `session_opened` and `session_closed` are the usual pair.

6.5.2. **The worker's observations.** The company's worker reports what it read (a middleware's pause flag)
through `/claims` naming the ownership `source` and, optionally, `observed_at`. It is capped by the
source's validity and never refused; one older than the source's last report answers `409`.

6.6. **Epochs.** Every accepted claim gets an epoch, larger than any before it on the target. A release
names the epoch it got; a release with another epoch is refused, so a holder whose claim was replaced
cannot release its successor's.

6.7. **Task locks.** A `task_lock` on an object names the task type. Whoever asks for the same task on the
same object sees who holds it (`lock_held`), whether an agent or a person. A lock is its holder's alone:
another holder's lock of the same task is refused (`409 task_locked`) until it is released or expires, and
the same holder renews it. A lock holds its task, never the object or the subject.

## 7. Effects exactly once

7.1. **The key.** The caller names the business fact an effect belongs to: one farewell per
conversation (`farewell:<conversation id>`), one filing per notice, one notice per fact. The producer
keeps the key only as a keyed hash, and names the effect in paths by that hash (`effect_id`), since a key
may carry a conversation id. A key lasts for its kind's window, or as long as the fact exists when the
space says so.

7.2. **States and transitions.**

| From | To | By |
|---|---|---|
| (none) | `reserved`, attempt 1 | a check or a reservation |
| `reserved` | `done`, `failed`, `unknown_outcome` | the caller holding the attempt |
| `reserved` | `done`, `failed` | an observation from the system of record |
| `reserved`, lease lapsed | `unknown_outcome` | nobody: at the next touch |
| `unknown_outcome` | `done`, `failed` | an observation, a person, or the caller of that attempt |
| `failed` | `done` | an observation that confirms it late |
| `failed` | `reserved`, next attempt | a check or a reservation |
| `done` | (none) | |

7.3. **An ambiguous effect is never sent again on its own.** An attempt whose outcome the caller does not
know (a request that timed out after it was sent) is `unknown_outcome`, and so is an attempt whose
reservation lapsed without a settlement. A reservation on an `unknown_outcome` key answers
`unknown_outcome` and reserves nothing. Only a settlement to `failed`, by an observation, a person, or
the caller of that attempt, opens a new attempt.

7.4. A settlement by the caller names the attempt it holds; a settlement of an older attempt is refused.

7.5. When the producer cannot answer, the caller MUST NOT send again what may have gone out: it records
`unknown_outcome`.

7.6. **A turn's report.** A turn record's `effects` report what the agent saw of each key, after the fact.
The producer takes a `done` from any state short of `done`, since the effect went out; an
`unknown_outcome` only turns an attempt in flight ambiguous; a key it never saw (the check had no answer)
starts at the reported state, as attempt 1. A report never opens an attempt and never moves a key back.

## 8. Budgets and admission

8.1. **Contact budgets.** A purpose MAY have a budget: at most `limit` contacts per subject in a rolling
window, summed over every agent and every vendor of the space. A contact at time `t` counts from `t`
until just before `t` plus the window.

8.2. **Slices.** A budget MAY be sliced by funnel stage, named by the check's `intent`: a slice is spent
until it runs out, or spread evenly over a number of days, one unit every `spread_days` divided by its
limit (a unit asked for sooner is deferred with `budget_paced`). A slice counts in the budget's window,
and the slices never add up to more than the budget; an intent without a slice spends from the budget
alone.

8.3. **Paid budgets.** A paid operation (a search, a refresh, an expensive call) MAY have a budget in the
company's units, never in money, per object, subject or space and window, and a ceiling per call.

8.4. **Admission before the act.** The units are reserved before the act, atomically. An operation of
unknown cost reserves the highest cost measured for it, or its ceiling per call before any was measured
(one unit without a ceiling). An attempt over the ceiling is refused with `over_call_ceiling`. After the
act the reservation is settled: `done` with the real cost, which raises the measured maximum; `failed`,
which keeps what was reserved, since a paid call that fails still counts; or `skipped`, which gives the
units back. A reservation that the producer could not record durably is undone, and the operation is not
admitted (`unavailable`): admission fails closed.

8.5. Without units left, the decision is `deny` with `budget_exhausted` and the time the next unit frees,
never a deferral.

8.6. **Durability.** Every unit a contact or paid budget spends is recorded before the answer. One the
producer cannot record is given back, and a purpose that fails closed gets `defer` with `unavailable`.
When the producer's state store comes back empty, the budgets are reloaded from that record (section
10.1).

## 9. Channels

9.1. The channel's state is computed at the check: until when a free-form message may go out (on
WhatsApp, 24 hours after the customer's last message), whether a paid template is required outside that
window, and the quiet hours. The customer's last message is the latest the producer received on the
channel from any source that sends the conversation, whether or not a check saw it. When the producer
cannot tell, the window counts as closed, and a template is required where the channel says so.

9.2. A contact inside the quiet hours is deferred to their end, never dropped.

## 10. When the producer cannot decide

Every purpose declares which way it fails:

| Purpose | Producer unavailable, or the check over the caller's time budget |
|---|---|
| A message the customer sent | Never denied: the agent answers and declares after. |
| `transactional` | Allowed, without a token. |
| `service` | Allowed and declared with `unchecked`. |
| `marketing`, `retention` | Deferred and asked again; the gateway refuses without a token. |
| `collection` | Deferred; the gateway refuses without a token. |
| Any purpose the subject opted out of | Refused by the caller's local copy of the suppression list ([`suppression-list.md`](suppression-list.md)). |
| An effect with a key | Deferred with `unavailable`: nothing is reserved, and what may have gone out is never sent again on its own (section 7.5). |

A message the customer sent comes before the rest: it is answered `allow` with `unchecked` whatever its
purpose or key. When the producer answers itself, a purpose that fails closed gets `defer` with
`unavailable`, and one that fails open gets `allow` with `unchecked`, with no reservation and no token.

10.1. The producer's state store keeps no copy on disk. When it comes back empty, the purposes that fail
closed get `defer` with `rebuilding` for 60 seconds while the durable record is replayed into it; the
purposes that fail open go on. Until the replay ends, a subject, an object or an effect's key is loaded
from the durable record the first time a check or a declaration touches it. During the 60 seconds, a
check whose effect key is in neither the state store nor the durable record gets `defer` with
`rebuilding`: its reservation may still be on its way to the record.

## 11. Handoffs

11.1. A handoff moves a subject to another holder with a package, described by
`handoff-package.v0.json`:

| Field | Meaning |
|---|---|
| `spec` | Always `handoff-package.v0`. |
| `handoff_id`, `created_at` | The handoff. |
| `from_agent`, `target` | Who hands off, to whom. |
| `level` | The verification level the package was compiled at: the receiving side's. |
| `reason` | Why, in the agent's words. |
| `context` | The compiled view for the receiver: its text, its etag and the policy version it was built under. |
| `owner` | Who held the subject when it was handed off. |
| `open_objects` | The subject's objects whose lifecycle has not reached a final outcome, as the subject's view reads them at the display purpose, with their state and since when (at most 50). Empty when the space keeps no typed state. |
| `commitments_active`, `promises_open` | What was offered or promised and still holds. A promise is named by its id, its due date and its owner, never by its text. |
| `effects` | The effects already done, in flight or ambiguous for the subject. |
| `suppressions` | The purposes the subject is suppressed for. |
| `outcome` | How to report back: the outcome vocabulary and by when. |

11.2. The package is compiled under the receiving side's policy and verification level: it MUST NOT carry
what that side could not read. The request names that level (`V0` when absent), never above the caller's
own. The package names its `subject`.

11.3. The outcome comes back through the API, or through a system event of the receiving side that the
space maps to an outcome (`handoffs.reported`: the source, the event's canonical type and the outcome; the
event names the handoff by an object reference of type `handoff` or by a field, `handoff_id` by default).
It is one of the space's outcome vocabulary, and feeds the memory (the receiving side's action `handoff.outcome` on the object `{type: handoff, namespace:
coordination, id: handoff_id}`, which the next agent's context shows as done by someone else), the
suppressions and the signals the space maps the outcome to. A reported event that names no open handoff,
or one already closed, changes nothing. A handoff without an outcome by `expected_by` reads as `expired`,
and is closed as such.

11.4. While the outcome is due, the subject is held for the receiving side at the space's handoff level
(`transfer_pending` by default), so other agents' outbound contacts wait; a stricter claim that already
holds the subject keeps it. The outcome ends the hold.

## 12. Levels of adoption

Each level is useful alone:

| Level | The company does | Coordination gives |
|---|---|---|
| Shadow | Nothing new: its outbound messages already reach the memory | Conflicts per 1,000 customers a month, with a sample for review |
| Suppression | Reads the list every 60 seconds or follows its changes | The floor every vendor honors |
| Check and declare | The agent asks before and declares after | Holder, locks, budget, channel, effects, commitments and promises in one read |
| Dispatch | The gateway lets out only outbound messages with a valid token | Enforcement, checked offline |

Where the dispatch point is third-party software that checks no token, coordination is shadow plus
suppression, and a producer SHOULD say so.

12.1. **The shadow report** counts, over the days asked for, what a check would have changed among the
outbound messages already received, with nothing enforced: `concurrent_agents` (a message while another
agent had messaged the subject in the last 24 hours), `outside_window` (a message after the channel's
service window closed), `quiet_hours`, `over_budget` (a contact started beyond the space's tightest contact
budget) and `tokens_used_twice`; each also per 1,000 customers a month, with a sample for review.

## 13. Examples

See [`examples/coordination-decision/`](../examples/coordination-decision/): a marketing contact allowed
with a token and the budget left, an outbound contact deferred while a person holds the subject, a
farewell whose effect was already done, and a customer's message handed to the attendant who holds it.
[`examples/handoff-package/`](../examples/handoff-package/) holds a sales agent's handoff to the closing
queue.
