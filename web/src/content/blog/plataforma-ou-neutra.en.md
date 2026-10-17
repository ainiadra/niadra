---
id: plataforma-ou-neutra
lang: en
slug: memory-inside-the-agent-platform-or-neutral-memory
title: Memory inside the agent platform or neutral memory: which to choose
description: AI agent platforms sell a context layer of their own. When it is enough, when the memory has to sit outside any vendor, and how the two live together.
order: 12
kind: explainer
date: 2026-09-22T15:00:00-03:00
updated: 2026-10-01T10:00:00-03:00
category: Comparison
icon: layers
cover: Inside the platform, or above it?
keywords: agent platform context layer, agentic memory, memory for AI agents from several vendors, AI agent platform with memory, neutral memory for AI agents, AI agent vendor lock-in, HappyRobot Context, alternatives to the agent platform's memory
---

AI agent platforms now sell a context layer of their own: everything their agents hear becomes a contact profile, with attributes, memory snippets and behavioral tags. It is the right choice when every agent in the company, on every channel, belongs to that platform, and when switching vendors is not on the horizon. When the agents come from different vendors, when there are internal agents in the company's systems, and when the company wants to measure every vendor with the same numbers, the memory has to sit outside all of them. The two architectures live together, and this article says where one ends and the other begins.

## What an agent platform calls a context layer

HappyRobot, an AI agent platform for logistics, utilities, insurance and other industries, describes its context layer like this on its [product page](https://www.happyrobot.ai/product/context/context-overview), checked in September 2026: every agent interaction is extracted and classified into fields (outcome, intent, contact reason, sentiment); every data point is mapped to the entity it belongs to (contacts, accounts, vendors, shipments); every contact builds a persistent profile with memory snippets, structured attributes and behavioral tags derived from patterns across sessions; cross-channel identity uses phone numbers for voice and SMS, email for chat and email, and identifiers such as tracking numbers or account IDs when neither is available. The layer connects to CRMs, ticketing systems and "200+ other integrations", with two-way data flow over REST.

The same platform's governance, described on its [audits and tests page](https://www.happyrobot.ai/product/governance/audits-and-tests), separates testing before deployment from auditing in production, and turns every fixed failure into a regression test.

It is a serious design, and much of what it does (extraction per interaction, entities, identity by phone and email, tags from patterns) is what any customer memory has to do. The difference is not in what the layer does. It is in who feeds it, who reads it and who owns it.

## What that memory cannot see

- **Other vendors' agents.** A platform's context layer is fed by its own agents. The call handled by another vendor's voice agent, the in-app conversation run by the internal team and a partner's chat do not go in. As far as the memory is concerned, that customer never came through.
- **Internal agents in the systems.** The billing agent that applies a credit in the ERP and the orders agent that changes a delivery record what they did in the systems, not in a support platform's context layer, unless they are that platform's agents too.
- **How others use the context.** The platform audits its own agents against its own rules. It cannot say whether a competitor's WhatsApp agent made use of the history, because it never sees that conversation ([how to know whether the agent used the context](/en/blog/how-to-know-whether-your-ai-agent-used-the-context)).
- **The day the platform leaves.** The memory belongs to the platform. Switching vendors, or adding a second one, means starting a second memory from scratch, or exporting and rebuilding.

## When the platform's memory is enough

There are cases where memory inside the platform is the right call, and saying otherwise would be dishonest:

- **One vendor for every channel**, by company decision, on a long contract. The built-in memory comes ready, with no extra integration.
- **A voice and SMS operation with few agents**, where the platform integrates the systems that matter and the company wants speed, not portability.
- **No internal agents** in the systems, or internal agents from the same platform.
- **No need to measure vendors** with the same numbers, because there is only one.

In those cases, a neutral memory adds an integration and little immediate value. The value shows when the second vendor comes in, and some companies never get there.

## When the memory has to be neutral

- **More than one agent vendor**, today or in the plan for the next two years. No vendor opens its memory to a competitor, and none will agree to feed the other's ([how agents from different vendors share the history](/en/blog/how-ai-agents-from-different-vendors-share-customer-history)).
- **Internal agents and systems of record** in the same memory as the customer-facing agents: the credit applied in the ERP at 2:06 pm has to reach the voice agent at 2:07 pm, whoever that agent belongs to.
- **Human agents and in-house tools** that also need the context, outside any platform.
- **Measurement per vendor**: only a memory that delivers to everyone and receives from everyone can say, with the same criterion, who uses the context and who repeats the question.
- **Portability required by contract or regulation**: the memory leaves in an open format, and switching vendors does not erase the customer.
- **Subjects beyond the person**: accounts and partners (the carrier, the broker, the clinic) with person-to-organization links that cross systems, not just one platform.

## The two architectures, side by side

| Criterion | Memory inside the agent platform | Neutral memory |
|---|---|---|
| **Who feeds it** | The platform's agents | Every agent, from every vendor, the human agents and the company's systems |
| **Who reads it** | The platform's agents, and external systems through integration | Any agent, with the permission the company defines by purpose |
| **Who owns it** | The platform, with export through integration | The company, with on-demand export in an open format |
| **Switching vendors** | Restarts the memory or requires migration | Changes nothing: the new vendor reads the same memory |
| **Context-use measurement** | Of its own agents, against its own rules | Of every agent and every vendor, with the same criterion |
| **Identity** | Phone, email and business identifiers | The same, with a weight per identifier and reversible merges |
| **Behavior patterns** | Tags derived in the platform's sessions | Patterns with evidence and an expiry, derived from every agent and system, with no sensitive category |
| **Integration cost** | Zero for the platform's agents | Three calls per agent, and webhooks from the systems |

## Can you have both?

Yes, and it is the usual design in a large company. The agent platform keeps its working memory, for its agents. The neutral memory receives that platform's conversations through one call at the end of each, and delivers the context before the first reply, as it does for any other agent. The platform's agents get to know what happened outside it, and the agents outside get to know what happened inside. The company measures all of them with the same criterion.

What makes no sense is duplicating orchestration: the neutral memory does not run workflows, does not handle customers and has no operations screen. It notifies over a webhook when a company rule happens, and the agent or the system acts.

## How Niadra solves it

Niadra is the neutral memory: not an agent, not a channel, and not a competitor to agent vendors. Every agent, from any vendor, and every company system feeds the same memory, and every agent receives the context for its task, compiled before the request and served with no AI model in the path, with the permission the company defined. Identity crosses phone, email, ID number, login and each system's ID, with the weight of each identifier. Patterns come from written rules, with evidence and an expiry, and a sensitive category never becomes a pattern: the ones the law defines, which Niadra ships already marked, and the ones your team marks.

The company sees, per agent and per vendor, whether the delivered context was used, and exports everything in an open format, on demand. All the infrastructure is run by Niadra, in a single region stated in the contract, with a key exclusive to each company, protected by a master key in a FIPS 140-3 HSM and an audit trail chained by SHA-256 ([architecture](/en/recursos#arquitetura)). The price is per conversation or task ([Pricing](/en/preco)).

In 2026, memory inside the platform stopped being the exception: Twilio, ServiceNow, AWS and Google each shipped one, inside the vendor that shipped it ([what that means for a company with agents from several vendors](/en/blog/twilio-servicenow-aws-and-google-shipped-memory-for-ai-agents)). In the [benchmark of September 30, 2026](/en/benchmark), Niadra's cross-channel continuity was measured at 98.8% of the 338 cases, with the same agent and the same judge for every system.

## Frequently asked questions

### Does neutral memory replace the platform's context layer?

No. The platform keeps its working memory, for its agents. The neutral memory sits above, receives everyone's conversations and delivers context to everyone. One feeds the other.

### What does it cost to connect the agent platform to the neutral memory?

Three calls in the agent: context before the first reply, history search during the conversation and recording at the end. Platforms that expose their agents through an API or MCP tools do it without changing the agent's flow.

### What if I have one vendor but want to switch in the future?

Start the neutral memory with that vendor. When the second one joins, or the first one leaves, the memory already belongs to the company, and nothing needs migrating ([build or buy](/en/blog/build-or-buy-memory-for-ai-agents)).
