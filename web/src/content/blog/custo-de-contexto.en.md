---
id: custo-de-contexto
lang: en
slug: how-to-cut-token-costs-for-ai-agents
title: How to cut token costs for AI agents without losing context
description: Layered context, prompt caching, minimum prefix sizes and on-demand history search. How to give an AI agent full context and pay less per token.
order: 3
kind: guide
date: 2026-09-21T21:49:00-03:00
updated: 2026-10-01T10:00:00-03:00
category: Cost
icon: layers
cover: Full context, cheaper tokens
keywords: how to reduce AI agent token costs, prompt caching, prompt caching minimum tokens, LLM cost per conversation, customer context for AI agents, how to lower LLM input token costs, AI customer support token costs
---

To cut an AI agent's token costs without starving it of context, give it a compact context sized for the channel, ordered from what changes least to what changes most, and leave the customer's full history open for the agent to search during the conversation. The order matters because of prompt caching: AI providers charge less for the start of a prompt that arrives unchanged across consecutive messages, as long as that start clears a minimum size of 512 to 4,096 tokens, depending on the model ([Anthropic](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). At [public September 2026 prices](https://platform.claude.com/docs/en/about-claude/pricing), a 3,000-token context across a 10-turn conversation drops from US$ 0.0600 to US$ 0.0129 on a mid-tier model, a 78% cut.

## Why is resending the whole history with every message so expensive?

AI model APIs keep no state between requests. With every message, the agent sends everything again: instructions, tools, the customer context and the conversation so far. The provider bills every input token, every time (a token is the unit of text providers count and charge for, usually a piece of a word).

The bill multiplies three factors: context tokens, turns and price per token. A 3,000-token context over 10 turns becomes 30,000 input tokens. Pasting the customer's entire history into the prompt inflates the first factor with every new conversation: the longer someone has been a customer, the more each message costs.

More text doesn't buy a better answer, either. The [Lost in the Middle](https://arxiv.org/abs/2307.03172) study showed that models make worse use of information placed in the middle of a long context.

## How does prompt caching lower the cost per token?

Prompt caching is the provider reusing the start of a prompt. That repeated start is called the prefix. When a request begins exactly like the previous one, byte for byte, the provider skips reprocessing the prefix and charges a fraction of the price for it.

At Anthropic, writing the prefix to the cache costs 1.25 times the input price, and reading it back costs 0.1 times ([pricing](https://platform.claude.com/docs/en/about-claude/pricing)). A cached prefix lives for 5 minutes and refreshes every time it is used ([caching rules](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). OpenAI caches automatically with no write charge, and bills cached input on gpt-5 at a tenth of the regular input price ([pricing](https://developers.openai.com/api/docs/pricing)).

### Context order decides what gets reused

The discount stops at the first byte that differs, and everything after it is billed at full price. So whatever changes least goes before whatever changes most. A timestamp or a request ID at the top of the prompt changes on every call and wipes out the cache. The order that works splits the context into layers by how often each one changes:

1. **Company rules**: change monthly.
2. **Customer profile**: changes weekly.
3. **Open items**: change per conversation.
4. **Just now**: changes per message.

Anthropic builds the prefix in the order tools, system instructions, messages ([caching rules](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)), so the new message always sits at the end.

### The math for a 10-turn conversation

The 3,000-token context goes out in full on every turn. On the first turn, the provider writes the prefix (1.25 times the input price). From the second turn through the tenth, it reads from the cache (0.1 times), with less than 5 minutes between turns. With caching, the bill becomes 3,000 × price × (1.25 + 9 × 0.1), which is 3,000 × price × 2.15. Input prices are [Anthropic's public prices](https://platform.claude.com/docs/en/about-claude/pricing) as of September 2026, and the math covers the context alone, without messages or replies.

| Model (input, per million tokens) | No caching | With caching | Cut |
|---|---|---|---|
| Mid-tier: Claude Sonnet 5 (US$ 2.00) | US$ 0.0600 | US$ 0.0129 | 78% |
| Frontier: Claude Opus 5 (US$ 5.00) | US$ 0.1500 | US$ 0.0323 | 78% |

## Why does caching sometimes fail without the provider telling you?

Providers only reuse a prefix above a minimum size. Below it, the reply comes back the same, the discount never applies and no error shows up. The documented minimums:

- **Anthropic**: 512 tokens on Claude Opus 5, 1,024 on Claude Sonnet 5 and 4,096 on Claude Haiku 4.5 ([caching rules](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). The cheapest model has the highest minimum.
- **OpenAI**: 1,024 tokens ([prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)).
- **Google**: 4,096 tokens for implicit caching on current Gemini models ([context caching](https://ai.google.dev/gemini-api/docs/caching)).

Using the same math as the table, a 1,000-token prefix on the mid-tier model costs US$ 0.0200 per conversation, cached or not: it falls 24 tokens short of the 1,024 minimum. The minimum applies to the whole repeated start of the prompt, adding up tools, agent instructions and the stable layers of the context. A short voice context gets cached along with everything in front of it. When even the combined prefix falls short, the savings come from size: a small context is already cheap.

On a single-turn conversation, Anthropic's cache costs 25% more, because the provider bills the write and nothing reads it afterwards. OpenAI and Google also treat the discount as conditional: it applies when the request finds the prefix still cached. That is why savings get measured in production. Every response reports the tokens read from cache: `cache_read_input_tokens` on Anthropic, `cached_tokens` on OpenAI. If that field stays at zero from the second turn on, the prefix changed or fell below the minimum.

## How much context does the agent need, and when should it search the history?

The agent needs two things: what it uses in almost every conversation, and what it uses now and then. The first goes into the context, before the first word, sized for the channel: the voice agent gets less than the chat agent, and the in-app agent can get the full context. The second stays in the history, open for search.

Search complements the context instead of replacing it, because search has a cost of its own. At Anthropic, enabling tools adds 286 to 406 tokens of instructions per request on Claude Opus 5, on top of the tool definition itself ([tool use pricing](https://platform.claude.com/docs/en/about-claude/pricing#tool-use-pricing)). Each search also adds an inference round: the model asks for the search, gets the result and only then replies. A tool declaration that never changes becomes part of the cached prefix.

Marina's case shows the split. At 2:07 pm she calls the voice agent from vendor B. The context already carries the technician visit that never happened (WhatsApp, 2:02 pm), the dispute on her August bill (app, 2:05 pm) and one line from her history: two conversations about the same problem since the start of the year, the last one settled with a $40 credit on the bill, on March 12. When she says "last time you gave me a credit", the agent searches for the details.

```python
from niadra import Niadra, phone

niadra = Niadra(channel="voice")
caller = phone(caller_id)

# while the phone rings: the carrier's full attestation proves level 2 for this call
niadra.verify("network_attestation", "V2", handle=caller, conversation_id=call_id)

# before replying: the customer's context, sized for voice
ctx = niadra.context(caller, view="voice", verification="V2", conversation_id=call_id)

# during the conversation: the customer brings up the March credit
hits = niadra.search(
    caller,
    "credit for missed technician visit",
    verification="V2",
    conversation_id=call_id,
    voice=True,
)
# Mar 12, 2026 · Phone · US$ 40 credit on the April bill · settled

# after replying: the conversation goes into the memory
turns = [("customer", question), ("ai_agent", reply)]
for role, text in turns:
    niadra.track({
        "conversation_id": call_id,
        "handles": [caller],
        "speaker": {"role": role},
        "content": {"text": text},
    })
```

The context carried the line that answers the most likely question. The search brought back the date and the amount when the conversation asked for them. The rest of Marina's history never entered the prompt.

## How Niadra solves it

Niadra applies these rules on any channel, vendor and LLM.

- `context()` delivers the customer context already compiled, with no AI model in the path, tailored to the channel and the agent, with the source of every fact, conversation and action (who, channel and when).
- The context comes in the four layers above, from most stable to most volatile.
- `search()` opens the entire history during the conversation, through the SDK, the HTTP API or as an MCP tool, with date, channel and outcome on every result.
- `track()` sends the conversation to the memory after the reply.
- Billing is per conversation or task, US$ 2 to 3 per thousand ([pricing](/en/preco)), not per token. If Niadra charged by volume of text, it would earn more by sending bigger contexts.

Cache savings depend on the provider and the model you use. Niadra is fully managed: on your side, just the SDK, open source, in Python and TypeScript ([Context](/en/produtos/contexto)).

What the memory itself costs, measured, is in [what an AI agent's memory should cost per conversation](/en/blog/what-memory-should-cost-per-conversation): US$ 0.36 of model spend per thousand conversations in the [benchmark of September 30, 2026](/en/benchmark), and US$ 2 to 3 per thousand at list price, reads included. The voice view, shorter and speakable, is in [memory for voice agents](/en/blog/memory-for-voice-agents-the-latency-budget).

## Frequently asked questions

### Does prompt caching change the model's answer?

No. The text that reaches the model is the same, byte for byte, with or without caching. The provider skips reprocessing a prefix it already stored and charges less for it.

### What is the minimum prompt size for caching to work?

It depends on the model: 512 to 4,096 tokens at Anthropic, 1,024 at OpenAI and 4,096 for implicit caching on current Gemini models. Below the minimum, the provider charges full price without telling you.

### Is it better to put context in the prompt or let the agent search?

Use both, each in its own role. What the agent uses in almost every conversation belongs in the context, because every search costs a tool declaration and an extra inference round. The full history stays searchable, because it is large and rarely needs to reach the model in full.

### Does Niadra charge per token?

No. Niadra charges per conversation or task that uses the memory, US$ 2 to 3 per thousand, depending on volume. What you spend on the AI model goes to your provider, and a compact context tends to bring it down.
