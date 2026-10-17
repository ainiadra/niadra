---
id: aproveitamento-do-contexto
lang: en
slug: how-to-know-whether-your-ai-agent-used-the-context
title: How to know whether your AI agent used the context it received
description: An AI agent can receive the customer's history and ignore it. How to measure, per agent and per vendor, what was used, what was asked again and what was contradicted.
order: 10
kind: explainer
date: 2026-09-22T14:20:00-03:00
updated: 2026-10-01T10:00:00-03:00
category: Governance
icon: eye
cover: Did the agent use what it got?
keywords: how to know whether an AI agent used the context, measure context use per AI agent, AI agent repeats questions, evaluating customer-facing AI agents, memory metrics for AI agents, 72-hour recontact rate, context use, governance of AI agents from several vendors
---

An AI agent can receive the customer's entire history and still ask "tell me what happened". Whoever delivers the memory needs to prove it was used, and only one party can prove it: the one holding both sides, the context that was delivered and the conversation that came next. Crossing the two, you can measure, per agent and per vendor, four things: what was put to use, what was asked again, what was said against what the memory already knew, and who transferred the conversation without the destination reading the context.

## Why is "we delivered the context" not enough?

Because delivery is not use. The agent receives a block of context before its first reply, and its prompt decides what to do with it. Some prompts say to confirm everything. Others ignore whatever is not in the expected format. Others use the context in the first reply and forget it by the fifth. On the customer's side, the result is the usual one: telling the story again.

Companies usually find out late, through recontact: the customer comes back within 72 hours because the problem was not solved, or because the second conversation started from scratch. Recontact is the number that matters to the business, but it does not explain anything. It says something failed. It does not say which agent, from which vendor, at which point.

## What can be measured, and how

Shared memory delivers the context with a receipt: the list of what was delivered, item by item (the open issue, the earlier promise, another agent's action, the identity fact), at the verification level it was delivered at. When the conversation ends, the same process that extracts facts from it also returns two lists: what the agent asked and what the agent stated. Crossing those lists with the receipt is deterministic, with no additional AI model, and yields four signals.

| Signal | What it is | What does not count |
|---|---|---|
| **Use** | Delivered items the agent cited or acted on: it mentioned the missed visit, the credit applied, the earlier promise; the action it recorded depends on a delivered item | A delivered item irrelevant to the task: it counts as unused, with no penalty, and helps trim that agent's next deliveries |
| **Repeat** | The agent asked for a piece of data that was in the delivered context: name, ID number, order number, the reason for contact | A verification question required by policy (an SMS code, identity confirmation before sensitive data); explicit confirmation of a delivered value when the source is configured to confirm before acting |
| **Contradiction** | The agent stated something a delivered fact disproves: "the visit is booked for Friday" when the delivered fact says Thursday | A divergence from a fact that was not in the delivery: that is a selection failure, measured separately, never attributed to the agent |
| **Transfer without reading** | The conversation moved to a human or another agent, and the destination did not request the context within ten minutes | A transfer to a destination that is not integrated: that becomes a coverage counter, not a repeat |

Two counters accompany the signals: conversations in which the agent never requested the context, and conversations in which the context arrived after the first reply. Without coverage there is no use to measure, and the number shows as "not measured", never as zero.

## An example, minute by minute

At 2:02 pm the customer writes on WhatsApp that the technician never showed up. At 2:05 pm, in the app, she disputes the bill. At 2:06 pm an internal billing agent applies a credit in the ERP. At 2:07 pm she calls.

Before saying hello, the voice agent receives four items: her name, the missed visit, the credit applied and the March failure. It says: "Hi, Marina. I can see the technician didn't show up this morning, and that this already happened in March. I've rescheduled the visit with priority, and the credit on your August bill has already been applied." The extraction returns zero questions, three statements (the missed visit, the March failure and the credit applied) and one action, the rescheduled visit. The crossing yields four items used out of four, zero repeats, zero contradictions.

In the version of the same call without shared memory, the agent asked for her name, her ID number and "what happened": three repeats, and the customer told the story for the third time in five minutes. That difference is what the measurement shows, agent by agent.

## What the number does for the company

- **Shows where the context does not reach the customer.** Vendor B's voice agent uses the context in 91% of deliveries; vendor A's WhatsApp agent, in 74%. What the company does with that number is its decision.
- **Explains recontact.** Next to each recontact rate, the measurement of each conversation shows, delivery by delivery, what the agent repeated or contradicted. The number that matters to the business gets a cause.
- **Trims the context.** A kind of item an agent received in dozens of measured deliveries and never used stops being sent to that agent, and the context gets smaller and cheaper. Open items, promises and sensitive data never leave, and the item comes back at the next measurement when the agent asks for it, mentions it or acts on it.
- **Goes into a contract only with proof.** In a 90-day program, the goal written beforehand is 72-hour recontact against a control group, and the measurement shows where it starts. Context use per agent becomes a clause only after sample review shows 90% agreement or more with the company's team. See [the 90 days, week by week](/en/blog/the-90-day-rollout-of-shared-memory-week-by-week).

## What this measurement is not

It is not a quality score for the agent. It measures memory use: it does not judge tone, does not rate friendliness, does not suggest prompts and blocks nothing. False positives exist: an agent may repeat a question because its prompt says to confirm everything. The answer is per-source configuration (marking the agent as "confirms before acting") and a sample review, in which the company's team marks each signal as valid or not. Each verdict goes into that environment's agreement rate, which says how far the measurement can be trusted.

It also never compares vendors in public. The number belongs to the company, and what it does with it is its decision.

## Why can't the agent vendor give you this number?

Because it holds only one side. An agent platform audits its own agents against its own rules, inside its own product. It does not see what another vendor delivered, nor what happened on the call handled by a competitor. A memory tool for a single agent cannot see the next conversation either, when it happens on another channel, with another vendor.

Only a neutral memory, one that delivers to every agent and receives from every agent, holds the delivery receipt and the conversation that came next. For the difference between the two architectures, see [memory inside the agent platform or neutral memory](/en/blog/memory-inside-the-agent-platform-or-neutral-memory).

## How Niadra solves it

Niadra delivers the context to each agent with a receipt of what was delivered, and receives the conversation that came next through the same SDK. At the end of each conversation it crosses the two and records, per delivery, what was used, what was asked again, what was contradicted and whether there was a transfer without reading. The result is ready together with the derived memory, with no extra AI model call.

In the Console, the company sees the numbers per agent, per vendor, per channel and per week, next to 72-hour recontact, with what each agent stopped receiving and how many tokens that saved, and, in the detail of each conversation, sees delivery by delivery what was used. Vendor A never sees vendor B's numbers. The company's team reviews a sample of conversations, and each verdict goes into the agreement rate, which says how far the measurement can be trusted. When an agent starts ignoring the context, a rule of the company's fires a signed webhook, and the company's system decides what to do ([Alerts](/en/produtos/avisos)).

The measurement follows the same privacy rule as the reads: the result is about the agent, never about the customer. Erasing a customer also erases the measurement rows derived from them, with a receipt ([security and GDPR](/en/blog/security-and-gdpr-for-customer-memory-in-ai)).

The number this measurement produces is the one missing from the repeat-contact calculation: [what it costs when the customer tells the same story on every channel](/en/blog/what-it-costs-when-the-customer-tells-the-same-story-on-every-channel) runs the calculation with the industry's numbers, and [Measurement](/en/produtos/medicao) is where it appears per agent and per vendor.

## Frequently asked questions

### Does this work with agents from different vendors?

Yes, and that is the case where it matters most. Every agent, from any vendor, receives the context through the same SDK and returns the conversation through the same path. The measurement is per agent and per vendor, and each vendor sees only its own numbers.

### What if the agent has to confirm the ID number as a security rule?

A verification question required by policy does not count as a repeat. What counts is asking again for data already delivered at the verification level the policy allowed it to be used at.

### Does it cost more per conversation?

No. The measurement uses the lists the conversation extraction already produces and the delivery receipt. It is processing over data already in the memory. Niadra's price is per conversation or task, and the measurement is included ([Pricing](/en/preco)).
