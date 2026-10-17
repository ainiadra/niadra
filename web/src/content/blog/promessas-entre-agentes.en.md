---
id: promessas-entre-agentes
lang: en
slug: how-to-stop-your-ai-agent-promising-and-forgetting
title: How to stop your AI agent from promising and forgetting
description: The WhatsApp agent says the invoice copy goes out by Friday, and on Monday the voice agent knows nothing about it. How a promise with a due date travels from one agent to the next, who sees what, and why the same promise said twice must not become two.
order: 2
kind: guide
date: 2026-10-08T09:00:00-03:00
category: Operations
icon: clock
cover: The promise reaches the next agent
keywords: AI agent promises and forgets, AI agent promises with due dates, follow up between AI agents, customer pending items across channels, voice agent does not know what WhatsApp promised, commitment memory for AI agents, promised deadline to customer
---

An AI agent stops promising and forgetting when every promise becomes a pending item with an owner, a due date and an object, stored outside the agent that made it, and delivered to every agent that serves the same customer or works on the same order, invoice or case. If the promise lives only in the conversation where it was said, it disappears when the conversation ends. If it lives in shared memory, the next agent sees it before answering, with the due date.

This guide shows how it works in practice, on both sides: the agent that serves the customer and the internal agent that works in the system.

## Why does the promise get lost?

The scene is common. On Friday, a customer asks on WhatsApp for a copy of an invoice, and the agent answers: "I'll send it by Monday". On Monday, the customer calls. The voice agent is from another vendor, reads the history of the last call (none) and the account record (nothing about the copy), and asks again what the customer needs. The customer tells the story a second time and hangs up thinking nobody remembers them.

Three things failed:

- **The promise never left the conversation.** It existed as a sentence inside the WhatsApp history, not as something with a due date and an owner.
- **It was not tied to the object.** "The copy" belonged to invoice 7731; without that link, no agent opening the invoice would know about the promise.
- **Nobody on the inside knew.** The billing agent in the ERP, which could have generated the copy, never learned it had been promised.

## What does a promise need to keep?

To cross agents and channels, a promise needs four pieces of information:

1. **What was promised**, in the words of whoever promised it: "send the invoice copy".
2. **By when.** A date, when one was said. "By Monday" becomes the right day, worked out from the day the sentence was said, not the day someone read it.
3. **About what.** The object: invoice 7731, order 48213, the case named by its number. When the customer writes the number, the promise is tied to it.
4. **Of what kind.** A promise by the company ("I'll send it"), a request to the customer ("send me the receipt") or a dispute ("the charge is wrong"). Each kind shows with its own name, so the agent does not confuse what the company owes with what the customer owes.

## Who sees the promise?

Every agent working with the same customer or the same object, with the right audience filter:

- **Monday's voice agent** sees "promise by 13/10: send the invoice copy" and opens the call with what is pending.
- **The billing agent in the ERP** sees the same item on the invoice's line and can generate it without anyone asking.
- **The staff assistant** sees the item when a human agent opens the case.

What a lawyer, manager or analyst notes internally about the case does not reach the customer. What was promised to the customer does, so that any agent can keep it.

## What about a promise said twice?

It happens all the time. The lawyer promises to send a copy of the ruling; hours later, the service agent repeats it to the customer: "the lawyer will send you the copy". If each sentence becomes a pending item, the case has two promises and the next agent does not know whether they are two things.

The rule is to treat the repetition as the same promise. The same thing about the same object, with compatible due dates, is one item, even in other words ("copy of the judgment" and "copy of the ruling"). When the words are not enough to decide, a decision model checks whether the new sentence repeats the open one. A different thing, such as "the receipt for finance" and "the receipt for the court file", stays a separate item.

## What happens when the due date passes?

The item does not disappear. It shows as "by 06/10 (overdue)", with the date. The form matters: in tests with the question "until when was it?", the word "overdue" without the date made the agent answer that there was no deadline. With the date first, it answers the date.

## How Niadra solves it

With Niadra, every promise, request and dispute an agent or a person makes becomes a pending item with a due date, a kind and an object, stored under whoever said it and delivered in the [context](/en/produtos/contexto) of every agent working with that customer or object, by audience. The item closes on what was done, as the post on [why "I'll send it" is not "I sent it"](/en/blog/when-to-mark-a-pending-item-as-done) explains, and an overdue item can [alert whoever needs to know](/en/produtos/avisos). The [history](/en/produtos/historico) shows when each one opened, who promised it and how it closed. The documentation describes [object memory](https://docs.niadra.com/en/concepts/object-memory) in detail.

## Frequently asked questions

### Does the agent need to call a tool to record the promise?

No. The promise is extracted from the conversation itself, from what the agent or the person said. The agent only needs to record its turns, which the SDK does.

### What if the agent promises a date that does not exist?

A date only the agent said, with nothing in memory backing it, does not become the item's due date. The promise is recorded without a date instead of carrying an invented deadline to the next agents.

### Do agents from other vendors see the promise?

Yes. Memory is neutral: one vendor's WhatsApp agent and another vendor's voice agent read the same item, as the post on [agents from different vendors](/en/blog/how-ai-agents-from-different-vendors-share-customer-history) shows.
