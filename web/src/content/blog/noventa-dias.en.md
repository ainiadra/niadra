---
id: noventa-dias
lang: en
slug: the-90-day-rollout-of-shared-memory-week-by-week
title: The 90-day rollout of shared memory, week by week
description: Preparation before day 1, four weeks in shadow mode, eight weeks on one journey with a control group and the decision in week 13. Who does what and how each goal is measured.
order: 11
kind: guide
date: 2026-09-22T14:40:00-03:00
updated: 2026-10-01T10:00:00-03:00
category: Rollout
icon: clock
cover: From the first call to memory in production
keywords: rolling out memory for AI agents, how long it takes to integrate memory for AI agents, 90-day plan for AI agents, control group for AI agents, shadow mode for AI agents, goals for customer-facing AI agents, how to measure 72-hour recontact, enterprise AI rollout
---

Giving every AI agent in a company one memory takes 90 days when the work is a mission with a scope, a goal and a date, not an open-ended project. Two to four weeks of preparation come before day 1. Then come four weeks in shadow mode, in which the memory is built and the context is prepared and audited without reaching the agents. In weeks 5 to 12, one journey receives the memory for a share of customers, drawn customer by customer, and the rest form the control group. Week 13 brings the result, and the decision happens on the date set at the kickoff meeting. A Niadra engineer stays on it from diagnosis to production, and the client's team neither installs nor runs infrastructure.

## Before day 1: diagnosis and preparation

No mission starts without three numbers from the client itself, per channel: conversation volume per month, 72-hour recontact and cost per contact. Nobody estimates on the client's behalf. With those numbers, the team picks one journey (the most frequent one, and the one where customers repeat their story the most: a billing dispute, a technician visit, a delivery change) and writes the goals.

The three goals, with the measurement method next to each:

1. **72-hour recontact** on the chosen journey, measured against the control group.
2. **Correct context**, measured by the client team's sample review: the share of extracted facts the reviewer confirms.
3. **Incident-free operation**: no read outside the policy, no sensitive data delivered without the required verification, no outage that delays an agent.

Context use, per agent and per vendor, sits next to the goals and explains recontact: it shows where the context did not reach the customer ([how to know whether your agent used the context](/en/blog/how-to-know-whether-your-ai-agent-used-the-context)). It becomes a contract clause only when sample review shows 90% agreement or more between the client's team and the measurement.

Preparation takes two to four weeks and runs in parallel:

- **Security and contract.** The security questionnaire, the threat model and the data processing agreement, with the providers named, reach the security team right away, together with the annex naming the region's law. On the Regulated plan, a penetration test by an independent firm is part of the contract. The security review is what delays an enterprise purchase the most, so it starts before day 1.
- **Test environment.** Niadra opens the client's test environment in the region stated in the contract. The sample data the diagnosis uses is delivered under the data processing agreement.
- **Three calls in the agent that already exists.** The Niadra engineer sits with the client's team and adds `context()` before the first reply, `search()` for the history and `track()` at the end of the conversation. Nothing in the agent is rewritten.
- **The first system.** The CRM, the ERP or the help desk sends the events it already emits, by webhook, through the API or in batches. The configuration assistant proposes the mapping and tests it on up to a hundred events already received. A person approves it.

## Weeks 1 to 4: shadow mode

The memory is now built for real, with every conversation and every event, and each agent's context is prepared and audited, but not delivered. The agents keep working as before. This gives the baseline: Niadra measures how often each agent asked for data the context would have carried, without any customer receiving anything different.

This is also when the sample review starts. Between 1% and 10% of conversations go to a queue in which the client's team marks each extracted fact as right, wrong or missing, and checks the measurement's signals. The verdicts go into that environment's agreement rate, and extraction is tuned per source: the billing agent keeps the dispute reason and the payment promise; the orders agent, the delivery change. Goal 2 starts being measured here.

The control group is drawn in this phase, customer by customer, with the draw recorded. Between 5% and 20% of the journey's customers will receive the memory from week 5; the rest stay without it until the end of the 90 days.

## Weeks 5 to 12: one journey, with a control group

The memory now reaches the agents, only for the customers who were drawn. Recontact for both groups is measured side by side, customer by customer, across channels.

### Weeks 5 and 6: the second channel or vendor

The value of a shared memory shows when the second channel comes in: the call starts already knowing what was said on WhatsApp. At this stage the second agent joins, preferably from another vendor. Cross-channel identity is checked against real cases: a phone number that changed owners, a customer with two emails, two people merged by mistake and separated without losing history.

The first alerts go in here. A promise past due or a third complaint on the same subject becomes a signed webhook to the client's system, and the agent or the system decides what to do.

### Weeks 7 and 8: the internal agent closes the loop

The internal agent (billing, orders, tickets) starts receiving the context for its task and recording what it did. The recorded action closes the open item: the credit applied at 2:06 pm reaches the voice agent at 2:07 pm. This is the stage where the client sees the company's working memory, not just the support memory ([memory for internal agents](/en/blog/memory-for-internal-ai-agents)).

### Weeks 9 to 12: the reading against the control group

From week 9, recontact for the group receiving the memory is read against the control group, with the confidence interval alongside. The engineer and the sponsor read the numbers together, per agent and per vendor, and fix what the measurement points at: the agent that repeats because its own prompt says so, the context item that is never used, the source that only integrated recording and never asks for the context.

At this stage the read log reaches the security team's SIEM.

## Week 13: the result and the decision

The final report compares the three goals with what was written before day 1, with the control group alongside and context use per agent as the explanation. The decision meeting has had a date since the kickoff meeting. What comes out of it is the expansion plan: the next journeys, agents and systems.

## After production: who looks after the memory

A shared memory changes every time a new agent joins, a vendor is replaced or a system starts emitting a new event. The Niadra engineer stays on it from diagnosis to production. After that, support follows the contracted plan, and each new agent comes in as a short mission with the same design: integration, one week in shadow mode, measurement. When a source stops sending events, Niadra sends a webhook.

## Who does what

| Role | Responsibility |
|---|---|
| **Client sponsor** | Picks the journey, approves the goals and the decision date, reads the numbers from week 9 |
| **Client technical owner** | Integrates the three calls into the agents and points the systems' events, during preparation |
| **Client security** | Reviews the security documents before day 1, approves the policy per source, receives the read log in the SIEM |
| **Client reviewers** | Check the conversation sample from week 1, a few hours a week |
| **Niadra engineer** | From diagnosis to production: mapping, extraction, policy, measurement, sample review and reading the numbers. After that, support according to the plan |
| **Agent vendors** | Integrate the SDK into their own agents and see only their own numbers |

## How Niadra solves it

Niadra is fully managed: infrastructure, keys, backups and monitoring are run by Niadra, in a single region stated in the contract ([Enterprise](/en/enterprise)). The 90-day mission comes with the Niadra engineer, goals written before day 1, shadow mode for the baseline and a control group drawn customer by customer. The price is per conversation or task, US$ 2 to 3 per thousand, and system events are not charged separately ([Pricing](/en/preco)).

Before day 1, the [ten tests](/en/blog/ten-tests-before-ai-agents-from-several-vendors-go-live) say whether the memory behaves the way the contract promises; the calculation that justifies the mission, with the industry's numbers, is in [what it costs when the customer tells the same story on every channel](/en/blog/what-it-costs-when-the-customer-tells-the-same-story-on-every-channel).

## Frequently asked questions

### Do I need to switch agent vendors to start?

No. The agents that already exist get three calls during preparation and stay where they are. The second vendor joins in weeks 5 and 6, through the same path.

### Why four weeks in shadow mode?

Because without a baseline there is nothing to compare against. In shadow mode, the memory is built and the context is prepared and audited without changing anything for the end customer. With sample review, the Niadra engineer tunes the extraction before any customer receives the context.

### What if the goal is not met?

The report says why, per agent and per vendor, with the conversations and the receipts. The decision to continue belongs to the client, on the date set from the start.

### How much of my team's time does this take?

The technical owner spends most of their time during preparation, integrating the calls and the events. Sample review takes a few hours a week from someone who knows the operation. The rest belongs to the Niadra engineer.
