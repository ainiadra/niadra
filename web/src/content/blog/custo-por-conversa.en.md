---
id: custo-por-conversa
lang: en
slug: what-memory-should-cost-per-conversation
title: What an AI agent's memory should cost per conversation
description: What a memory spends on models per thousand conversations, measured in the benchmark (US$ 0.36), what it costs at list price (US$ 2 to 3) and how much that is against the call it improves, under 1% of a five-minute call.
order: 5
kind: measured
date: 2026-09-30T22:30:00-03:00
category: Pricing
icon: target
cover: US$ 0.003 per conversation
keywords: cost of memory for AI agents, price per conversation AI agent, how much does agent memory cost, cost per thousand conversations, omnichannel memory pricing, voice agent cost per minute, billing per conversation not per token
---

An agent's memory should cost a small fraction of the conversation it improves, and the bill should be per conversation, not per token, read or search. Measured in the [benchmark of September 30, 2026](https://github.com/ainiadra/niadra-sdk-python/tree/main/benchmarks/results/2026-09-30-6e6d07), Niadra's model spend per thousand ten-turn conversations is US$ 0.36; the list price is US$ 2 to 3 per thousand conversations or tasks, with reads, searches and system events included. A five-minute call on a voice platform costs US$ 0.39 to 0.75; that call's memory, at US$ 0.003, costs under 1% of it.

## What a memory spends, measured

The cost of a memory has three parts: the model that extracts the memory from the conversations, the infrastructure that stores and serves it, and the read, which in a well-built memory calls no model at all. The benchmark measures the first by the same method for every system: the model calls from the write until the memory settled, at the harness's prices, per thousand ten-turn conversations.

| Measure | Per thousand conversations | Where it comes from |
|---|---|---|
| Niadra's model spend, extraction only | US$ 0.36 | median of three repetitions in the region |
| Model spend of the open-source systems measured | US$ 0.97 to 18.72 | the same method, servers not priced |
| The agent's own model, in chat | US$ 0.095 | the agent's prompt with the context, at the same prices |

The [benchmark page](/en/benchmark) has the table with the names. What Niadra does to stay at US$ 0.36: one model call per session, not per message, over already masked text; system events go in without a model; and the read calls no model, because the context is compiled when the memory changes.

## Why bill per conversation, not per token

Three billing models show up in the memory market: by volume of text (tokens, bytes or characters), by operation (every write, every search, every read) and by conversation. The first two punish what the products' own documentation recommends.

Whoever bills per operation bills every search before every answer: from the light conversation (three reads) to the standard one (ten reads), the price triples. Whoever bills per text bills the long conversation: from the standard conversation to the heavy one (thirty turns), it triples again. And whoever bills per token has the incentive backwards: it earns more by sending bigger contexts, which is the opposite of what the voice agent needs.

Per conversation or task, the arithmetic closes the right way. A conversation with the customer, or an internal agent's task, in which some agent read the memory, counts once. Ten reads and five searches inside it count as one. Several agents in the same conversation count once, and the charge goes to the source that opened the session. Control and shadow groups never count. A system event, which only writes, is never billed. The rule is in the server's code, not in a sales text, and the monthly statement says, per space, what was used.

## How much that is against the conversation

The right frame is what the buyer already pays per conversation to the platforms the agents run on. List prices read on September 30, 2026, for a five-minute call and a twenty-message WhatsApp conversation:

| Platform | What the conversation costs | Niadra's memory, at US$ 3 per thousand, as a share |
|---|---|---|
| [Vapi](https://vapi.ai/pricing), with transcription, model, voice and telephony | US$ 0.45 to 0.71 per call | 0.4% to 0.7% |
| [Retell](https://www.retellai.com/pricing), with model, voice and telephony | US$ 0.745 per call | 0.4% |
| [ElevenLabs Agents](https://elevenlabs.io/pricing/agents), additional minute, model apart | US$ 0.40 per call, plus the model | under 0.75% |
| [Twilio Voice](https://www.twilio.com/en-us/voice/pricing/us) with [ConversationRelay](https://www.twilio.com/en-us/products/conversational-ai/pricing), voice and model apart | US$ 0.39 per call | 0.8% |
| [WhatsApp through Twilio](https://www.twilio.com/en-us/messaging/pricing/whatsapp), Twilio's fee only, without Meta's fees or the model | US$ 0.10 per conversation | 3% |

The memory costs under 1% of a call and about 3% of a WhatsApp conversation's fee. The price does not need to be the lowest on the table; it needs to be irrelevant next to the conversation, and visible in what it avoids: a contact center that recontacts 30% of its customers within 72 hours pays for the memory of a hundred calls by avoiding one repeat contact.

## The list price and what it covers

| Volume per month | Per thousand conversations or tasks |
|---|---|
| Up to 1 million | US$ 3.00 |
| 1 to 10 million | US$ 2.50 |
| Above 10 million | US$ 2.00 |

A monthly minimum of US$ 500 on the Production plan, which covers 166,000 conversations or tasks. The Regulated plan, with a dedicated environment, SSO, a contractual SLA and a security review, is by contract. The tier assumes an average conversation of up to twenty turns; a customer whose average goes past that is priced by contract, and the statement already reports the extraction tokens per space, so nobody finds that out on the invoice. The [pricing page](/en/preco) has the whole table and the questions.

## How Niadra solves it

Niadra bills per conversation or task in which an agent read the memory, with reads, searches and system events included, because it is the unit the buyer already uses to pay the voice and messaging platforms. The model spend of every system, Niadra included, is measured and published in the benchmark, with the script and the files to reproduce it. The post on [cutting token costs for AI agents without losing context](/en/blog/how-to-cut-token-costs-for-ai-agents) covers the other side of the bill: what the compact context saves on the agent's own model.

## Frequently asked questions

### Is the US$ 0.36 model spend the price?

No. It is what Niadra spends on models per thousand ten-turn conversations, measured in the benchmark by the same method as every system. The list price, US$ 2 to 3 per thousand, covers the model, the infrastructure and the operation, and it is what the customer pays.

### What happens in a conversation with fifty turns?

It counts once, like any other. The price tier assumes an average conversation of up to twenty turns; an operation whose average goes past that is priced by contract, and the monthly statement shows the extraction tokens before that becomes a surprise.

### Do you bill for the human agent's or the internal agent's read?

Any agent's read counts, once per conversation or task, and goes to the source that opened the session. A ticket, e-mail or ERP source that only writes is never billed.
