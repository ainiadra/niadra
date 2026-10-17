---
id: memoria-sem-configuracao
lang: en
slug: memory-for-ai-agents-with-no-configuration
title: "Memory for AI agents with no configuration: connect the SDK and never open the dashboard again"
description: Agent memory that needs types, fields and operations declared before it works turns into a months-long project. What memory can learn from traffic on its own, what stays protected until a person decides, and how to check it works.
order: 1
kind: explainer
date: 2026-10-08T09:00:00-03:00
category: Rollout
icon: bolt
cover: Connect it and forget the dashboard
keywords: AI agent memory with no configuration, zero configuration agent memory, how to roll out AI agent memory fast, infer object types from systems, memory that learns CRM and ERP fields, deploy AI agents without an integration project, managed memory for AI agents
---

Memory for AI agents works without configuration when it learns, from what your systems and agents already send, which objects exist (order, invoice, case, ticket), which fields they have, which states they go through, when they are due and which operations agents perform on them. With Niadra, the only manual step is creating one key per agent. From there the SDK sends the events and memory learns the rest, without anyone opening the dashboard.

This post explains what can be learned from traffic, what has to stay protected until a person decides, and how to check, in your own case, that memory is delivering what it should.

## Why does configuration become the bottleneck?

Any agent memory needs to know what an order is before it can tell the voice agent that order 48213 is out for delivery. The traditional path is to declare it upfront: the "order" type, its fields, which are dates, which are amounts, which field identifies the order, which states are final, which operations close a pending item. In a company with a CRM, an ERP, a help desk and an in-house system, that is dozens of types and hundreds of fields.

The problem is not the one-time effort. It is what comes after:

- **Every new field stalls.** The system starts sending `delivery_date` and no agent sees it until someone declares it.
- **The limit stays hidden.** A rule such as "show at most eight fields" cuts the field that mattered, and nobody knows until an agent gets it wrong.
- **The people who configure are not the people who suffer.** The integration team declares once; the service team finds the gap when the customer complains.

Memory that needs configuration to show new data turns every system change into a ticket.

## What can memory learn on its own?

Almost everything that is declared today is already written in the traffic. Niadra learns, continuously:

1. **Object types.** The first event from a system carrying an object nobody declared creates the type, mirroring the system that sent it.
2. **Fields and what each one is.** A field's kind comes from its values: date, date and time, amount, number, yes or no, text. One odd value does not change a field's kind.
3. **The identifier written in a conversation.** When a customer writes "order 48213" or a case number, and that value identifies exactly one of their objects, the conversation is tied to it with no declared rule.
4. **States, and which ones are final.** The sequence of states shows which are final. A final state becomes a measured outcome.
5. **Due dates.** A date field named like a deadline, with future values, becomes the object's due date, with a reminder before it.
6. **Operations.** The names agents and systems use (notify the client, attach a document, pay court fees) become the vocabulary that ties a promise to the action that keeps it.
7. **Words in your language.** Field names and values reach the agent in the space's language ("Claim amount", "in progress") instead of `claim_amount` and `in_progress`.

None of this needs the company to say what is what. Declaring stays possible as an optional adjustment: a better label, an access rule, a field you want marked as sensitive. When something is declared, the declaration always wins over what was learned.

## What stays protected until a person decides?

Learning on its own cannot mean exposing on its own. The rule Niadra follows: what is learned never loosens privacy, it only tightens it.

- **Personal data is held.** An undeclared field whose value looks like an e-mail, a phone number or an ID number, or whose name says contact, address or document, does not reach agents until a person declares it. The hold shows up as a note in the dashboard, never silently.
- **Long free text is treated as content.** A court filing's text or a ticket comment does not enter the object's line as an ordinary field.
- **A staff member's name is not a customer's name.** The lawyer in charge or the account manager, on an object from the system of record, is shown; a customer's or patient's name is held.
- **Nobody becomes staff by saying so.** A conversation is internal when the person is identified by the company system's id; a phone or an e-mail always identifies a customer.
- **Loosening is still a person's call.** Tightening a protection needs no approval; loosening goes in as a proposed change and applies only after a person at the company approves it in the Console.

## How do you check that memory works?

Without configuration, the question changes from "did I declare everything?" to "did the agent get what it needed?". Three checks answer it:

- **Did the block have the answer?** For a real customer question, was the right information in the context handed to the agent? That is the metric that separates memory errors from model errors, and the post on [telling the two errors apart](/en/blog/did-the-memory-have-the-answer-memory-error-or-model-error) shows how to measure it.
- **What was learned is visible.** The dashboard shows every type, field and operation as "Niadra default", "Inferred by Niadra" or "Declared", with the history of each change and who made it.
- **What was held is explained.** Every held field, every limit reached and every refused name becomes a note with the reason. A note is never a required task.

## How Niadra solves it

With Niadra, a customer creates one key per agent, connects the SDK and does not need to come back to the dashboard. Memory learns the objects in the [company's systems](/en/produtos/sistemas), delivers the [current state](/en/produtos/estado) and what was said about each object in every agent's [context](/en/produtos/contexto), and measures outcomes from final states in [measurement](/en/produtos/medicao). Everything learned is recorded and visible in the [Console](/en/produtos/console), where each value shows as default, inferred or declared, with the optional adjustment next to it. The documentation explains [how undeclared fields are served](https://docs.niadra.com/en/concepts/object-types) and [how object memory ties conversations to orders, invoices and cases](https://docs.niadra.com/en/concepts/object-memory).

## Frequently asked questions

### Do I need to declare any object type to start?

No. The first event from each system creates the type, and fields appear as they arrive. Declaring is for adjustments: a better label, a sensitive field, an access rule.

### What if memory learns something wrong?

What is learned never loosens a protection, and every change shows in the Console with author and date. Your declaration always wins over what was learned, so fixing it means declaring the right value.

### Can a field with personal data reach an agent without me knowing?

No. An undeclared field whose value looks like an e-mail, a phone, an ID number or a customer's name is held until a person declares it, and the hold shows up as a note.

### Does this work for internal and customer-facing agents at the same time?

Yes. The same key serves a conversation with staff and a conversation with a customer; what each conversation can see is decided by who is on the other side, as the post on [memory for internal agents](/en/blog/memory-for-internal-ai-agents) explains.
