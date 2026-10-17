---
id: memoria-de-fornecedor
lang: en
slug: twilio-servicenow-aws-and-google-shipped-memory-for-ai-agents
title: Twilio, ServiceNow, AWS and Google shipped memory for AI agents. Each one lives inside the vendor that shipped it
description: In 2026, memory for agents became a platform product. What each launch documents, what it leaves out, and the question left for a company running voice, WhatsApp and app agents from different vendors.
order: 1
kind: news
date: 2026-10-01T09:00:00-03:00
category: News
icon: layers
cover: Four memories, one per vendor
keywords: Twilio Conversation Memory, ServiceNow Context Engine, AgentCore Memory, Vertex AI Memory Bank, memory for AI agents from several vendors, context layer for agents, agent memory inside the platform, AI agent memory lock-in
---

Between December 2025 and August 2026, four platforms your company probably already uses started selling memory for AI agents. Twilio made Conversation Memory generally available on May 6, 2026 ([SIGNAL 2026 announcements](https://www.twilio.com/en-us/blog/products/signal-2026-product-announcements)). ServiceNow introduced Context Engine on April 9 ([press release](https://newsroom.servicenow.com/press-releases/details/2026/ServiceNow-moves-beyond-the-sidecar-AI-era-giving-customers-a-complete-AI-native-experience-across-all-products-and-packages/default.aspx)). Google's Vertex AI Memory Bank reached general availability on December 16, 2025 ([release notes](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/release-notes)). AWS's AgentCore Memory gained the ability to store system events in August ([release notes](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/release-notes.html)). All four keep the memory where the platform itself is. For a company that runs its voice agent on one vendor, its WhatsApp agent on another and its app agent in-house, the question is not which memory is best. It is who owns the customer's memory when the agents belong to several vendors.

This post is for the people who decide the agent architecture and the vendor contracts. Every source was read on October 1, 2026, and what each one leaves out is marked.

## What each launch documents

**Twilio Conversation Memory.** The announcement says the product "creates a living, identity-resolved profile by connecting customer data with conversation history and customer traits" and is "built specifically for LLMs to reduce latency and token usage" ([SIGNAL 2026](https://www.twilio.com/en-us/blog/products/signal-2026-product-announcements)). The [documentation](https://www.twilio.com/docs/conversation-memory) describes two kinds of memory, conversational (behaviors, preferences, mood, key discussion points) and factual (name, contact details, account tier, identifiers), a recall API that combines semantic and lexical search, and an identity resolution step that decides whether to create, add to or merge profiles. Memories are generated from conversations that pass through Twilio channels (SMS, WhatsApp, email and voice), after the Conversation Orchestrator. Pricing is usage-based: US$ 0.0028 per thousand characters generated, US$ 0.007 per recall and US$ 0.005 per daily utilized profile ([pricing](https://www.twilio.com/en-us/products/conversational-ai/pricing)). The same event launched Agent Connect, an open-source toolkit that "lets your teams connect agents built on any LLM or framework directly to Twilio's infrastructure".

**ServiceNow Context Engine.** The April 9 release says Context Engine "connects relationships, policy, and decision history behind every AI agent decision" and "gives ServiceNow AI and workflows the context to sense what's happening across the enterprise"; at launch it was "available for preview with select customers" ([press release](https://newsroom.servicenow.com/press-releases/details/2026/ServiceNow-moves-beyond-the-sidecar-AI-era-giving-customers-a-complete-AI-native-experience-across-all-products-and-packages/default.aspx)). On May 6, at Knowledge 2026, the company described it as a semantic layer that integrates the CMDB, workflow data, analytics insights and third-party systems ([press release](https://newsroom.servicenow.com/press-releases/details/2026/ServiceNow-launches-the-real-time-data-foundation-that-puts-autonomous-AI-to-work-across-the-enterprise/default.aspx)). Forrester summed up the event's thesis in one line, "context fuels intelligence and AI agents drive action", with ServiceNow positioning itself as "the autonomous operating layer for the enterprise" ([Forrester, May 14, 2026](https://www.forrester.com/blogs/context-is-the-new-competitive-edge-takeaways-from-servicenow-knowledge-2026/)).

**Google Vertex AI Memory Bank.** The release notes of December 16, 2025 state that "Vertex AI Agent Engine Sessions and Memory Bank are now Generally Available" and that charging started on January 28, 2026 ([release notes](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/release-notes)). The memory is generated from the user's conversations with the agent running on Agent Engine.

**AWS AgentCore Memory.** In April 2026 the service gained structured metadata filtering on long-term memory; in August, the `CreateEvent` API started accepting a `json` payload for "non-conversational, JSON-formatted data (up to 100 KB) such as behavioral events, activity logs, and system events", extracted into long-term memory ([release notes](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/release-notes.html)). It is the memory of the agents that run on AgentCore.

**Oracle.** Oracle announced AI Database 26ai on March 24, 2026, with a "Unified Memory Core" that analysts describe as "a stateful, persistent memory for AI agents within the database engine" ([Futurum, March 25, 2026](https://futurumgroup.com/insights/oracle-positions-ai-database-26ai-to-lead-1-2-trillion-market-by-bridging-the-agentic-reasoning-gap/)). The memory lives in Oracle's database.

## The pattern: the memory sits where the platform sits

Read together, the five launches share one design. The memory is born from what passes through the platform and serves the agents that run on it:

| Platform | Where the memory comes from | Who reads it, per the documentation | What the documentation leaves out |
|---|---|---|---|
| Twilio | Conversations on Twilio channels | AI and human agents in Twilio conversations; agents from any framework connected through Twilio's infrastructure (Agent Connect) | Whether a call handled outside Twilio, or an ERP event, enters the memory |
| ServiceNow | CMDB, workflows, analytics, integrated systems | ServiceNow AI and workflows | Whether an agent from another vendor can query Context Engine; general availability |
| Google | Conversations with agents on Agent Engine | Agents on Agent Engine | Conversations from agents outside Google Cloud |
| AWS | Events and conversations sent to AgentCore | Agents on AgentCore | Conversations from agents outside AWS |
| Oracle | Data and conversations in the database | Agents with access to the database | How many third-party frameworks use it (Futurum suggests watching) |

None of these companies is wrong to do this. Memory inside the platform is the natural product for whoever already holds the conversation or the data. The problem shows up on the buyer's side: the average service and collections operation does not run everything on one platform.

## What it means for a company with agents from several vendors

Picture a common setup: voice on a voice platform, WhatsApp through Twilio, the app with an in-house agent, the CRM on Salesforce or ServiceNow, the ERP from yet another vendor. With the 2026 launches, that company can end up with four memories of the same customer, each complete inside its own wall and blind to the rest:

1. **The voice agent does not read Twilio's memory** unless the call goes through Twilio. The complaint made on WhatsApp at 2:02 pm never reaches the 2:07 pm call answered on another vendor.
2. **Context Engine knows the ticket and the workflow**, not the conversation that happened on another vendor's WhatsApp.
3. **Switching vendors takes the memory with it.** If the voice contract moves to another platform, the memory of every call stays on the old one.
4. **Nobody measures the whole.** Each platform measures its own agent against its own rules. None can say whether the competitor's agent used what the other one knew ([how to know whether the agent used the context](/en/blog/how-to-know-whether-your-ai-agent-used-the-context)).

Pricing per recall shows the same design from another angle. Twilio charges US$ 0.007 per recall ([pricing](https://www.twilio.com/en-us/products/conversational-ai/pricing)): in a conversation where the agent reads the memory at every exchange, ten exchanges cost US$ 0.07 in recalls alone, before the model and the message. That is the right unit for a memory that lives inside a channel billed per message. It is not the right unit for a memory that several agents, from several vendors, read in the same conversation.

The market has already named the problem. On March 10, 2026, a16z wrote that data agents fail for lack of context, and that a modern context layer includes "canonical entities and identity resolution", reachable "via API or MCP", as "a living and constantly evolving corpus" ([Your Data Agents Need Context](https://a16z.com/your-data-agents-need-context/)). A VentureBeat survey of 101 enterprises with more than 100 employees, in June 2026, found that 57% had traced a confident but wrong agent answer to missing or inconsistent business context, 31% more than once; 25% had a governed context layer in production, 34% were building one and 41% had not started ([VentureBeat, July 10, 2026](https://venturebeat.com/data/57-of-enterprises-have-watched-ai-agents-be-confidently-wrong-the-fix-is-an-agentic-context-layer-but-who-has-one)).

## What to check before you sign

Four questions for the vendor, and the answer that matters:

1. **Does the memory take in events that did not pass through you?** A call from another vendor, an ERP event, a message from a channel that is not yours. If not, it is the memory of your channel, not the memory of my customer.
2. **Can an agent from another vendor read the memory, with its own credential and purpose-based permission?** Without that, the customer who changes channels starts over.
3. **When I leave, does the memory leave with me, in an open format?** Export in an open format whenever you ask, not a dump at the end of the contract.
4. **Who measures whether each agent used what it received?** Per agent and per vendor, with a receipt for every delivery.

## What we don't know

- Whether Twilio will accept conversations from channels outside Twilio into Conversation Memory. The documentation read on October 1, 2026 does not describe that input; it does not rule it out either.
- When ServiceNow's Context Engine leaves preview and whether third-party agents will be able to query it. The April release says "full availability details will be shared at a later date".
- How much of Oracle's Unified Memory Core is used by third-party frameworks. Futurum recommends watching adoption; we found no public number.
- The VentureBeat survey has 101 respondents. It is a signal, not a census.

## How Niadra solves it

Niadra is the memory layer that sits outside all of these platforms. It takes the events from every channel, platform and system (the call on the voice platform, the message through Twilio or the Cloud API, the app session, the ticket, the ERP invoice), recognizes that they speak of the same person before storing anything, and delivers to any agent, from any vendor, the context of its task before the first word. Each vendor comes in with its own credential and reads only what its purpose allows; none reads another's memory, and replacing one of them erases nothing ([how agents from different vendors share customer history](/en/blog/how-ai-agents-from-different-vendors-share-customer-history)).

The adapters for [Twilio](/en/integracoes/twilio), [WhatsApp Cloud API](/en/integracoes/whatsapp), [LiveKit](/en/integracoes/livekit), [Vapi](/en/integracoes/vapi), [Retell](/en/integracoes/retell), [ElevenLabs](/en/integracoes/elevenlabs), [LangGraph](/en/integracoes/langgraph) and [OpenAI Agents](/en/integracoes/openai-agents) connect the agents you already run to the same memory, and your systems come in by webhook, batch or file ([Systems](/en/produtos/sistemas)). In the [benchmark of September 30, 2026](/en/benchmark), with the same agent and the same judge for every system, Niadra answered 98.8% of the 338 cross-channel continuity cases correctly, and no sensitive data was delivered to a conversation that had not proven who it was. Pricing is per conversation or task, US$ 2 to 3 per thousand, with every read and search in the conversation included ([Pricing](/en/preco)).

## Frequently asked questions

### If I already use Twilio for WhatsApp, do I need another memory?

It depends on where your other agents are. If every conversation in your company goes through Twilio, Conversation Memory covers what it sees. If voice is on another vendor, or if the ERP and the CRM need to feed what the agent knows, the memory has to sit outside Twilio, and Twilio becomes one of the sources.

### Can the platform's memory and a neutral memory coexist?

They can. The platform's memory keeps serving its own agents; the neutral layer takes the events from all of them and serves the whole. The post on [memory inside the agent platform or neutral memory](/en/blog/memory-inside-the-agent-platform-or-neutral-memory) says where one ends and the other begins.

### What changes in the contract with an agent vendor?

Two clauses: the agent reads and feeds the company's memory through the SDK, with its own credential; and the memory, with the full history, stays with the company in an open format when the contract ends.
