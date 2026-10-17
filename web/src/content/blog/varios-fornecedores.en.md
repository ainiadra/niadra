---
id: varios-fornecedores
lang: en
slug: how-ai-agents-from-different-vendors-share-customer-history
title: How AI agents from different vendors share customer history
description: Per-agent credentials, purpose-based reads, a log of every read and a memory the company keeps: how AI agents from different vendors share customer history.
order: 7
kind: guide
date: 2026-09-22T00:41:00-03:00
updated: 2026-10-01T10:00:00-03:00
category: Governance
icon: eye
cover: Each vendor sees only what it should
keywords: how AI agents from different vendors share customer history, multi-vendor AI agents, AI agent governance, purpose-based access for AI agents, how to audit which AI agent read customer data, how to switch AI agent vendors without losing history, how to avoid AI vendor lock-in
---

AI agents from different vendors share customer history when all of them read from and feed the same memory, one that sits outside every vendor. Each agent connects with its own credential, and the purpose declared on that credential decides what the agent receives. Every read leaves a record chained by SHA-256, which the company checks in its own SIEM. When a vendor leaves, the memory stays whole for its replacement.

## How does each vendor connect to the same memory?

Marina is a customer of a company with five AI agents: the WhatsApp agent, from vendor A; the voice agent, from vendor B; the in-app agent, from the in-house team; the orders agent, from vendor C; and the billing agent, an internal agent. The reasons the memory sits above all of them are covered in [omnichannel memory for AI agents](/en/blog/what-is-omnichannel-memory-for-ai-agents).

The company issues each agent its own credential, and no agent uses another's. On each credential, the company declares a purpose: "support" for the WhatsApp, voice and in-app agents, "orders" for vendor C's agent and "billing" for the internal agent. The vendor does not choose a purpose request by request: the credential says who is reading and why.

The integration is the same for everyone: the SDK in Python or TypeScript, the HTTP API or an [MCP](https://modelcontextprotocol.io/) tool. The agent makes three calls: `context()` before replying, `search()` during the conversation and `track()` afterwards. The SDK is open source, so every vendor can audit what it sends. The company makes this integration a requirement in every vendor contract.

## What can each agent read and write?

Access starts denied. Each agent reads what its purpose allows. The rule covers the context and every search through the history. At this company, the policy looks like this:

| Agent | Purpose | What it gets |
|---|---|---|
| WhatsApp, vendor A | support | chat context |
| Voice, vendor B | support | voice context |
| App, in-house team | support | full context |
| Orders, vendor C | orders | orders, deliveries and addresses |
| Billing, internal agent | billing | bills, payment plans and disputes |

The policy works item by item. The billing agent can see that the August bill is disputed, but it cannot see the technical open item. At 9:31 am, one of its reads was denied for exactly that reason. The same rule keeps health data out of its reach.

Writing follows the same logic. The company decides which agents feed the memory. It also decides who sees what each vendor recorded. Every agent adds its own conversations, and none rewrites what another recorded.

## What happens when two agents record facts that contradict each other?

At 2:02 pm, Marina asks vendor A's WhatsApp agent to have the technician call before coming over. At 2:07 pm, she tells vendor B's voice agent she would rather get the heads-up on WhatsApp. The memory keeps both facts, each with its channel, time and agent.

No vendor decides which one holds. The model that processes the conversation flags the contradiction, and a fixed rule picks the most recent fact. What counts is when the fact took effect, not the order it arrived in: a late transcript never overrides a newer fact. The next context carries the newer preference, and the memory keeps where each value came from: "Phone 2:07 pm". The 2:02 pm request stays in the history, marked as replaced.

In the conversation, what the customer says wins, and the correction becomes a new fact in the memory. The hard case is a different one: the customer states one thing and a system records another. That is why every fact in the context says whether the customer declared it or a system confirmed it, and every action says whether the agent only declared it or the system already confirmed it.

## How do you prove which agent read what?

Every read produces a record with the agent, the vendor, what was read, the purpose and the time. The record covers the context and every history search, denied reads included. Here are the reads about Marina that day:

| Time | Who read | Purpose | Result |
|---|---|---|---|
| 2:07 pm | Voice agent, vendor B | support | got the voice context |
| 2:05 pm | In-app agent, in-house team | support | got the full context |
| 2:02 pm | WhatsApp agent, vendor A | support | got the chat context |
| 9:31 am | Billing agent, internal | billing | denied: billing does not see technical open items |

Records are chained by [SHA-256](https://csrc.nist.gov/pubs/fips/180-4/upd1/final) hash. Each line carries the hash of the line before it, so changing any line breaks the chain. Each day's root is sealed in immutable storage. A copy streams to the company's SIEM in real time. That lets the company prove whether vendor B received the bill dispute, without relying on the vendor's own logs.

## What changes when the company switches vendors or cuts off access?

The company cuts off a vendor's access on the spot, without depending on that vendor. From then on, its agents get neither context nor search results.

The memory stays whole. Everything the old vendor recorded stays in place, with the source of every fact. The new vendor gets its own credential and reads the same memory from its first conversation. The company can also export everything in an open format, whenever it wants.

The part that needs care is what the old vendor already received. Those copies live on its side and fall under its contract. Under the GDPR, the processor must delete or return the personal data at the end of the service, at the controller's choice ([Art. 28(3)(g)](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679#art_28)). The LGPD requires deleting personal data once processing ends, with exceptions such as legal obligations ([Art. 16](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art16), official text in Portuguese). The read log shows what each vendor received, and that is the list the [exit clause in the DPA](/en/blog/security-and-gdpr-for-customer-memory-in-ai) has to cover.

## How do you roll it out, from the first channel to a new vendor?

The rollout happens in three steps.

1. **First channel.** The company connects one agent, such as vendor A's WhatsApp agent, with its own credential and the purpose "support". Before going live, the team simulates what that agent will see. The agent calls `context()` before replying and `track()` afterwards. The memory grows with every conversation.
2. **Second channel.** Vendor B's voice agent and the in-app agent join. The memory [recognizes Marina](/en/blog/how-to-recognize-the-same-customer-across-channels) by her phone number, and the voice agent gets what she said on WhatsApp before it says hello. This is the step where the customer stops repeating her story.
3. **New vendor.** Vendor C arrives with the purpose "orders". The team defines what that purpose can read and simulates the agent's view before granting access. From day one, its reads show up in the SIEM.

No step changes the prompt, the model or the vendor behind the agents already in service.

## How Niadra solves it

Niadra is the shared memory for all of a company's AI agents, customer-facing and internal, across every channel and vendor. It works with any vendor and any AI model. Every agent makes the same three calls, through the SDK, the HTTP API or as an MCP tool:

```python
from niadra import Niadra, phone

niadra = Niadra(channel="voice")
caller = phone(caller_id)

# while the phone rings: the carrier's full attestation proves level 2 for this call
niadra.verify("network_attestation", "V2", handle=caller, conversation_id=call_id)

# vendor B's voice agent: its credential and the call's level decide what comes back
ctx = niadra.context(caller, view="voice", verification="V2", conversation_id=call_id)

# history search follows the same purpose rule as the context
hits = niadra.search(
    caller,
    "August bill dispute",
    verification="V2",
    conversation_id=call_id,
    voice=True,
)

# the conversation goes into the memory with its source: channel, time and agent
turns = [("customer", question), ("ai_agent", reply)]
for role, text in turns:
    niadra.track({
        "conversation_id": call_id,
        "handles": [caller],
        "speaker": {"role": role},
        "content": {"text": text},
    })
```

`context()` delivers the full context, compiled before the request and served with no AI model in the path, with the source of every fact, conversation and action. In the [Console](/en/produtos/console), the company cuts off a vendor on the spot and simulates what each agent sees before granting access. The read log streams to the SIEM, and everything can be exported in an open format. Niadra is fully managed: it runs the infrastructure, and the company's team connects the SDK. It acts as a processor under the GDPR and the LGPD, with a signed DPA.

Each vendor connects through the adapter for what it already runs: [LiveKit](/en/integracoes/livekit), [Vapi](/en/integracoes/vapi), [Retell](/en/integracoes/retell), [Twilio](/en/integracoes/twilio), [LangGraph](/en/integracoes/langgraph), [OpenAI Agents](/en/integracoes/openai-agents), [CrewAI](/en/integracoes/crewai) or [n8n](/en/integracoes/n8n). What the 2026 memory launches mean for a company with agents from several vendors is in [Twilio, ServiceNow, AWS and Google shipped memory for AI agents](/en/blog/twilio-servicenow-aws-and-google-shipped-memory-for-ai-agents), and the vendor swap is test 5 of [ten tests to run before AI agents from several vendors go live](/en/blog/ten-tests-before-ai-agents-from-several-vendors-go-live).

## Frequently asked questions

### Can one vendor see what another vendor recorded?

Each vendor sees what its agent's purpose allows, and the company sets that rule. Vendor B's voice agent gets what Marina said on WhatsApp, because both agents handle support. The internal billing agent never sees the technical open item. Every read is logged, denied reads included.

### Who decides which fact holds when two agents disagree?

A fixed rule decides, and no vendor can change it. The most recent fact holds, based on when it took effect, and the older one stays in the history with its source. During the conversation, what the customer says wins.

### What happens to customer history when the company switches vendors?

The history stays with the company, and the new vendor reads the memory from its first conversation, within its purpose. The company can export everything in an open format whenever it wants. Copies the old vendor kept fall under its contract, which should require deleting or returning them at the end of the service.

### Do all vendors need to use the same AI model?

No. Each vendor keeps the model, the prompt and the agent it already runs. The context arrives ready for the prompt of any LLM, and history search works through the SDK, the HTTP API or as an MCP tool.
