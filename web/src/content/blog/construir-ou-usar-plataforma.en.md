---
id: construir-ou-usar-plataforma
lang: en
slug: build-or-buy-memory-for-ai-agents
title: Build or buy memory for AI agents: in-house or a platform?
description: What it takes to build memory for AI agents with Redis, PostgreSQL and pgvector, when building in-house pays off and when a platform makes more sense.
order: 5
kind: explainer
date: 2026-09-22T00:41:00-03:00
updated: 2026-10-01T10:00:00-03:00
category: Architecture
icon: sliders
cover: Build in-house or use a platform
keywords: build vs buy AI agent memory, build AI agent memory with Redis and pgvector, AI agent memory platform, in-house memory for AI agents, how much does it cost to build AI agent memory, AI agent memory architecture, pgvector for AI agent memory
---

Building memory for your AI agents in-house pays off in four cases: a single channel, a single agent vendor, a dedicated data team, or a requirement no platform meets. With Redis, PostgreSQL and pgvector, the prototype comes together fast, because storing and searching messages is the easy part. The work that never ends is everything else: identity across channels, permissions, audit, erasure and round-the-clock operations. With several channels and vendors, heavy compliance or a tight deadline, a neutral platform makes more sense.

## What do you need to build beyond Redis and pgvector?

The typical architecture has Redis for recent messages, PostgreSQL with pgvector for the history, a queue and a service of your own. To serve agents from several vendors, that service needs nine pieces:

1. **The record** keeps every message in the order it happened, without duplicating what a vendor resends.
2. **Identity** joins phone, email, ID number and app login into one customer. It also notices when a [phone number gets a new owner](/en/blog/how-to-recognize-the-same-customer-across-channels).
3. **Extraction** pulls facts, promises and open items out of every conversation. It rejects any fact without a source message.
4. **The context** is tailored to each channel, at the size and in the order the [provider's prompt cache can reuse](/en/blog/how-to-cut-token-costs-for-ai-agents).
5. **Search** returns every history result with its date, channel and outcome.
6. **Permissions** follow each agent's purpose and its vendor.
7. **The audit trail** logs every read in a tamper-proof way.
8. **Data subject rights** require [erasure across everything derived from the data](/en/blog/security-and-gdpr-for-customer-memory-in-ai).
9. **Operations** mean 24/7 on-call, alerts and model upgrades.

## How much of this is the prototype, and how much never ends?

The prototype covers three pieces on one channel: the record, the context and search. In the demo, the agent seems to remember everything.

Even those three hide traps. With an approximate index (HNSW), pgvector filters after the index scan: by default, if the filter matches 10% of rows, you get 4 results on average ([pgvector](https://github.com/pgvector/pgvector)). Filtered down to one customer among millions, the search tends to come back nearly empty. The same documentation suggests starting with an index on the filter column.

The rest never ends. A new channel brings a new identifier. A wrong profile merge has to be undone without losing history. Models change the rules: at Anthropic, the smallest prompt the cache will reuse went from 4,096 tokens on Claude Opus 4.5 to 512 on Claude Opus 5 ([documentation](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). Every model switch also calls for a fresh evaluation of the extraction. And someone answers the alert at 3 am: Google's [Site Reliability Engineering](https://sre.google/sre-book/being-on-call/) book puts the minimum for a 24/7 on-call rotation at eight engineers on a single site.

## When is building in-house the right call?

Building in-house is the right call in four situations.

- **A single channel.** The customer always arrives with the same identifier, so identity across channels drops off the list.
- **A single vendor.** That vendor's memory already sees every conversation, and whatever is missing fits in a small service.
- **A dedicated data team.** With people to run the system, evaluate extraction and answer the security review, building gives you full control. That counts most when memory is part of the product.
- **A requirement no platform meets.** If the data has to stay on servers your own team operates, a fully managed platform won't do. Niadra can't serve that case, because it runs all the infrastructure, including the dedicated environment on the Regulated plan.

The technology isn't the obstacle. OpenAI serves 800 million ChatGPT users from a single primary PostgreSQL server and about 50 read replicas ([OpenAI](https://openai.com/index/scaling-postgresql/)). The hard part is the work.

## When does a neutral platform make more sense?

A neutral platform makes more sense in three situations.

- **Several channels and vendors.** [Each vendor's memory stays locked inside that vendor](/en/blog/what-is-omnichannel-memory-for-ai-agents). A memory built in-house fixes that, because it belongs to the company. The cost shows up in integration: every vendor needs an SDK, an API, its own credential and purpose-based permissions. In-house, the company writes and maintains all of it.
- **Heavy compliance.** The security review asks for every standard by name, with evidence. Even field-level encryption becomes a project: the pgcrypto documentation asks you to trust the database administrator and recommends encrypting in the application when you can't ([PostgreSQL](https://www.postgresql.org/docs/current/pgcrypto.html)). Encrypting in the application takes a key per company, held in an HSM.
- **A tight deadline.** On a platform, the nine pieces already exist. The integration fits into the agent you already run.

## How do the two options compare, criterion by criterion?

Building in-house wins on control and on where the data lives. The platform wins on time, operations and evidence for the security review.

| Criterion | Build in-house | Use a neutral platform |
|---|---|---|
| First result | Fast prototype on one channel | Three calls in the agent you already run |
| Identity across channels | You write every rule | Ready, with the weight of each identifier |
| Context per channel | New measurement with every model | Layered, from what changes least to what changes most |
| History search | Index, filters and permissions are on you | Through the SDK, the HTTP API or MCP |
| Permissions and audit | Your own project | By purpose and vendor, with every read logged |
| Erasure | You trace everything derived | With a receipt |
| Operations and monitoring | Your own on-call rotation, 24/7 | The platform's |
| Control | Full | Within what the platform offers |
| Where the data lives | Wherever you decide, even your own servers | In a single region, run by Niadra |
| Cost | Infrastructure, model and team | Usage-based (per conversation or task at Niadra) |
| Exit | Nothing to migrate | Depends on export (open format at Niadra) |

## How do you decide between building and buying?

Four questions separate the two cases.

1. How many channels and vendors will read the same memory?
2. What evidence will the security review ask for?
3. Who runs the system overnight?
4. Is memory the product your company sells, or infrastructure for it?

With one channel, one vendor and memory as the product, build. With several channels, several vendors and memory as infrastructure, use a platform. With mixed answers, the second and third questions decide, because evidence and on-call become fixed costs.

Test with a real case. At 2:02 pm, Marina messages on WhatsApp that the technician never showed up, and vendor A's agent replies. At 2:05 pm, she disputes her August bill in the app. At 2:07 pm, she calls, and vendor B's voice agent picks up. The prototype passes if that agent knows about the missed visit and the dispute before it says hello. When she brings up the credit from last time, the agent has to find the March 12 record in the history, with the US$ 40.

## How Niadra solves it

Niadra is the shared memory for all of a company's AI agents, customer-facing and internal, across every channel and vendor. The nine pieces on the list are part of the service, and Niadra runs all the infrastructure, with continuous monitoring and automatic alerts. On your side, you add the SDK, in Python or TypeScript, with three calls: `context()` before replying, `search()` during the conversation and `track()` after replying.

`context()` delivers the context already compiled, with no AI model in the path, with the source of every fact, conversation and action. `search()` also works through the HTTP API and MCP, with any LLM. Data is encrypted with AES-256-GCM at rest and TLS 1.3 in transit, with an exclusive key per company, protected by a master key in an HSM validated to FIPS 140-3 Level 3. Every read goes into an audit trail chained by SHA-256. Pricing is per conversation, US$ 2 to 3 per thousand ([Pricing](/en/preco)). The Regulated plan comes with a dedicated environment, run by Niadra ([Enterprise](/en/enterprise)).

The cost of the part that never ends has been measured: in the [benchmark of September 30, 2026](/en/benchmark), Niadra's model spend is US$ 0.36 per thousand ten-exchange conversations, and that of the open-source systems measured by the same method runs from US$ 0.97 to 18.72, before servers and operations ([what memory should cost per conversation](/en/blog/what-memory-should-cost-per-conversation)). The [adapters](/en/integracoes) connect the agents you already have, without rewriting anything.

## Frequently asked questions

### Can you build AI agent memory with only Redis and pgvector?

You can build the prototype. Redis holds the recent messages. PostgreSQL with pgvector holds the history and searches by similarity. It lacks identity across channels, purpose-based permissions, audit and erasure, which the security review checks.

### How much does it cost to run AI agent memory in-house?

The bill has three parts: infrastructure, the model that extracts facts from each conversation, and the team that runs it all. The 24/7 on-call rotation alone takes at least eight engineers on a single site, by the math in Google's [Site Reliability Engineering](https://sre.google/sre-book/being-on-call/) book. At Niadra, [pricing is per conversation](/en/preco), US$ 2 to 3 per thousand.

### Can I start on a platform and build in-house later?

Yes, if the platform exports everything in an open format and doesn't control your agent. That is how Niadra works: the agent gets three calls, with no change to its prompt, model or vendor. To leave, you import the export into your own system and point the three calls at it.
