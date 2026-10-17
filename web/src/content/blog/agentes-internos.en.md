---
id: agentes-internos
lang: en
slug: memory-for-internal-ai-agents
title: Memory for internal AI agents: CRM, ERP, tickets, billing and orders
description: How AI agents that work inside the company, in the CRM, the ERP, tickets, billing and orders, share the same memory as the customer-facing agents.
order: 9
kind: explainer
date: 2026-09-22T10:10:00-03:00
updated: 2026-10-01T10:00:00-03:00
category: Architecture
icon: hub
cover: The agents working behind the scenes
keywords: memory for internal AI agents, AI agents in the ERP, AI agents in the CRM, AI billing agent, AI agent for ticket triage, shared memory between AI agents, context for back-office AI agents, how to connect AI agents to ERP and CRM
---

An internal AI agent works inside the company without talking to the customer: it applies a credit in billing, changes a delivery in the ERP, triages a ticket. To act without mistakes, it needs the same memory the customer-facing agents use, and they need to know what it did. A shared memory ties every conversation, every system event (order, ticket, bill) and every agent action to the right customer. The systems remain the official source: the memory keeps what the agents need to remember and points back to where it came from.

## What is an internal AI agent?

It is an agent that reads and writes in the company's systems instead of talking to the customer. Some examples:

- **Billing:** reviews a dispute and applies a credit to the bill.
- **Orders:** changes the delivery address or reschedules an installation in the ERP.
- **Tickets:** classifies, prioritizes and routes each request in the help desk.
- **Agent copilot:** prepares the case before a human agent picks up.
- **Finance and operations:** reconciles payments and finds stalled orders.

Each one works in a different system, and often comes from a different vendor than the agent serving the customer.

## Why does it need the same memory as the customer-facing agents?

Because both sides act on the same customer. At 2:02 pm, Marina tells the WhatsApp agent that the technician never showed up. At 2:05 pm, she disputes her August bill in the app. At 2:06 pm, the billing agent reviews the dispute and applies a $40 credit in the ERP. At 2:07 pm, she calls.

Without shared memory, each agent gets it wrong in its own way:

- **The internal agent acts without knowing what was said to the customer.** It does not know the technician visit failed for the second time this year, and treats the dispute as an isolated case.
- **The customer-facing agent promises what another agent already did.** The voice agent cannot see the credit applied at 2:06 pm, so it offers another one, or says the dispute is still under review.

With shared memory, the voice agent picks up knowing both things: the visit that never happened and the credit that is already applied. It is the same problem as the [handoff between channels](/en/blog/how-to-keep-context-from-voice-call-to-whatsapp), now between customer service and what happens inside the systems.

## How do the CRM, ERP, help desk, billing and orders get into the memory?

Through three paths, and none of them requires replacing a system.

1. **The events the systems already emit.** Order created, bill disputed, ticket reopened. The system sends the event through a webhook, and a versioned mapping turns it into the memory's format. The original event is kept as it arrived, for 90 days.
2. **The API.** For systems without webhooks, events come in through an integration or in batches.
3. **The agents themselves.** Each agent records what it did in each system, with the result and the source.

Each system has its own customer identifier: the CRM record, the ERP contract, the help desk requester. The memory ties those identifiers to the same customer with the same rules used for phone and email: every link has a source and a weight, and can be undone without losing history ([how identity works](/en/blog/how-to-recognize-the-same-customer-across-channels)).

The system remains the official source. The ERP keeps the amount on the bill; the memory keeps that the bill was disputed and that the credit was applied, with the date, the source and the record's ID in the ERP. A system event is structured data, so it goes in through mapping rules, with no AI model along the way.

## What is an agent action, and why record it?

An agent action is the record of what an agent did in a system: what, where, on which record and with what result. It serves two purposes.

- **Closing what was left open.** The dispute opened at 2:05 pm becomes resolved when the billing agent records the credit at 2:06 pm.
- **Telling the next agent.** The voice agent gets the credit in its context, with the source next to it, before it picks up.

Without that record, a promise made on one channel and fulfilled in another system keeps showing up as pending, and someone will fulfill it again.

## How do you control what each internal agent can read?

Internal agents tend to have broad access to the systems, so the rule in the memory is the same as for customer-facing agents: their own credential, purpose-based reads and a log of every read ([how it works across vendors](/en/blog/how-ai-agents-from-different-vendors-share-customer-history)). The billing agent reads bills and disputes, and does not read technical issues or health data.

This matters because agent governance is still the exception: only 1 in 5 companies say they have mature governance for autonomous AI agents ([Deloitte, State of AI in the Enterprise 2026](https://www.deloitte.com/us/en/what-we-do/capabilities/applied-artificial-intelligence/content/state-of-ai-in-the-enterprise.html)).

## How Niadra solves it

Niadra is the shared memory for all of a company's AI agents: the ones serving customers on any channel and the ones working behind the scenes, in the CRM, the ERP, tickets, billing and orders. Agents use three SDK calls, in Python or TypeScript: `context()` before acting, with the customer context for the task; `search()` while working, across the entire history; and `track()` afterwards, to record the conversation or the action. Systems come in through webhooks or the API, and each system's identifier stays tied to the right customer.

Data is encrypted with AES-256-GCM at rest and TLS 1.3 in transit, with an exclusive key per company, protected by a master key in a FIPS 140-3 Level 3 HSM, and every read goes into an audit trail chained by SHA-256. Niadra runs all the infrastructure. Pricing is per conversation or task, US$ 2 to 3 per thousand, and events from your systems are not billed separately ([Pricing](/en/preco)). The Regulated plan comes with a dedicated environment ([Enterprise](/en/enterprise)).

Systems come in by webhook, batch or file ([Systems](/en/produtos/sistemas)), and the [n8n node](/en/integracoes/n8n) connects the flows that already exist. The internal agent's action reaches the customer-facing agent as declared, confirmed by the system event or divergent; it is test 9 of [ten tests to run before AI agents from several vendors go live](/en/blog/ten-tests-before-ai-agents-from-several-vendors-go-live).

## Frequently asked questions

### Does the memory replace the CRM or the ERP?

No. The CRM, the ERP and the help desk remain the official source of the data. The memory ties orders, tickets and bills to the right customer, keeps what the agents need to remember and points back to the source record.

### Do I need a connector for every system?

No. The systems send the events they already emit through webhooks, or through the API when they have no webhooks, and a mapping transforms each event. Agents also record what they did. To decide what to build in-house and what to use off the shelf, see [build or buy memory for AI agents](/en/blog/build-or-buy-memory-for-ai-agents).

### How is an internal agent task billed?

At Niadra, each internal agent task counts as one conversation: the reads and searches inside it count once. The events your systems send are not billed separately.
