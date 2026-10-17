---
name: niadra
description: Connect an AI agent codebase to Niadra, the omnichannel memory layer for a company's AI agents. Use when asked to add Niadra, give an agent memory of past conversations across channels, wire context before the model call, record turns, or add the history tools to an agent built with any framework (LiveKit, Pipecat, ElevenLabs, Vapi, OpenAI Agents, LangChain, LangGraph, Vercel AI SDK, Mastra, Google ADK, Strands, Pydantic AI, LlamaIndex, CrewAI, Agno, Microsoft Agent Framework, Semantic Kernel, Haystack, CAMEL-AI, DSPy, AG2, Genkit, VoltAgent, Cloudflare Agents, Anthropic, Bedrock, Google GenAI, LiteLLM, OpenAI) or through webhooks (WhatsApp Cloud API, Twilio, Retell AI).
license: Apache-2.0
metadata:
  source: https://docs.niadra.com/skill/niadra/SKILL.md
  docs: https://docs.niadra.com/en
  version: "2026-09-29"
---

# Integrate Niadra

Niadra is the omnichannel memory layer for a company's AI agents: it takes the events from every channel, platform and system, builds one memory of each customer and delivers it to any agent, from any vendor. An agent reads the customer's **context** before the model call, **records** what was said and done, and **searches** the history when the context does not answer. Your job is to wire the agent the repository already has, not to change its model, prompt or vendor.

Rules that hold everywhere:

- The customer is never a value the model writes. The subject (phone, e-mail, `wa_id`, app user id) comes from the platform (SIP header, webhook payload, the app's own session), and the SDK binds it outside the model's reach.
- Niadra never fails the agent. Every SDK call has its own time budget (300 ms for a context read; in the voice view, the pinned body comes from the SDK's memory at once, a turn waits 200 ms at most for the slots of its words, and the call's first read, started with `begin()` when the call starts, is waited for with `ready()` up to 1.5 s) and returns an empty value instead of raising, unless `strict=True`. Do not wrap Niadra calls in retries or fallbacks of your own.
- Instructions first, then the agent's own notes (if agent memory is on), then the customer's context, then the conversation, then the `turn_block` (what other channels said since the pack was pinned). The instructions stay the cacheable prefix of the prompt.
- Never log handles, message text or keys. Never put a handle, a conversation id or a query in a URL.
- Never write customer data into agent memory (`remember`): the server refuses it with `personal_data_in_agent_memory`.

## Step 1: find the model call and the identifiers

Search the repository for the place where the model is called (`chat.completions.create`, `messages.create`, `generate_content`, `converse`, `Runner.run`, `agent.run`, `streamText`, `generateText`, a LiveKit `Agent`, a Pipecat `Pipeline`, a webhook handler for ElevenLabs, Vapi, Meta or Twilio). Note:

1. Which framework or provider SDK it uses, and its version (lockfile).
2. Where the customer's identifier comes from (a phone number, a `wa_id`, a logged-in user id) and where the conversation id comes from (call SID, thread id, chat id).
3. Where the agent's own instructions live (system prompt).
4. Whether the platform proves anything about who is there: a STIR/SHAKEN attestation (`StirVerstat`, `sip.h.x-stir-verstat`), a login, an OTP.
5. Where the agent hands the conversation to a person or another agent.

## Step 2: pick the adapter

Install the SDK with the extra of the framework. Python: `pip install 'niadra[<extra>]'`. TypeScript: `npm install @niadra/sdk` plus the framework as a peer dependency; the integrations are subpath imports (`@niadra/sdk/<name>`), in `@niadra/sdk` 0.7.0 or newer on npm.

| Framework | Python (`niadra.integrations.*`, extra) | TypeScript (`@niadra/sdk/*`) | Docs |
|---|---|---|---|
| LiveKit Agents | `livekit` (`NiadraAgent`, `NiadraMemory`, `conversation_for`) | `livekit` (`NiadraAgent`, `NiadraMemory`, `sipSubject`, `sipConversationId`, `attestationProof`) | /en/integrations/livekit |
| Pipecat | `pipecat` (`NiadraMemoryProcessor`, `history_tools`, `conversation_for_call`) | not available | /en/integrations/pipecat |
| ElevenLabs Agents Platform | `elevenlabs` (`ElevenLabsWebhooks`, `tool_configs`) | `elevenlabs` (`elevenLabs`) | /en/integrations/elevenlabs |
| Vapi | `vapi` (`VapiServer`, `tool_definitions`) | `vapi` (`vapi`, `vapiTools`) | /en/integrations/vapi |
| Retell AI | `retell` (`RetellWebhooks`, `tool_configs`) | `retell` (`retell`: `inbound`, `webhook`, `tool`, `llm`, `toolConfigs`) | /en/integrations/retell |
| WhatsApp Cloud API | `whatsapp` (`parse_webhook`, `subscribe`, `sent`) | `whatsapp` (`readWhatsApp`, `whatsAppChallenge`, `recordInbound`, `recordOutbound`) | /en/integrations/whatsapp |
| Twilio | `twilio` (`parse_call`, `parse_message`) | `twilio` (`readTwilio`, `verifyTwilio`, `recordTwilioInbound`) | /en/integrations/twilio |
| OpenAI Agents SDK | `openai-agents` (`NiadraAgentsMemory`) | `openai-agents` (`NiadraSession`, `niadraInstructions`, `niadraTools`, `niadraRunHooks`) | /en/integrations/openai-agents |
| LangChain | `langchain` (`context_runnable`, `NiadraCallbackHandler`, `history_tools`) | `langchain` (`niadraContext`, `withNiadraContext`, `NiadraCallbackHandler`, `niadraTools`) | /en/integrations/langchain |
| LangGraph | `langgraph` (`NiadraMiddleware`, `pre_model_hook`) | use `langchain` (`withNiadraContext`) | /en/integrations/langgraph |
| Vercel AI SDK | not available | `ai-sdk` (`niadraMiddleware`, `niadraTools`) | /en/integrations/ai-sdk |
| Mastra | not available | `mastra` (`niadraProcessor`, `niadraTools`) | /en/integrations/mastra |
| Google ADK | `google-adk` (`NiadraADK`) | `google-adk` (`niadraAdk`) | /en/integrations/google-adk |
| Strands Agents | `strands` (`NiadraHooks`) | `strands` (`NiadraPlugin`, `niadraTools`), Node 22+ | /en/integrations/strands |
| Pydantic AI | `pydantic-ai` (`NiadraCapability`) | not available | /en/integrations/pydantic-ai |
| LlamaIndex | `llamaindex` (`NiadraMemory`, `history_tools`) | `llamaindex` (`NiadraMemory`, `NiadraMemoryBlock`, `niadraTools`) | /en/integrations/llamaindex |
| CrewAI | `crewai` (`NiadraCrew`), Python 3.11+ | not available | /en/integrations/crewai |
| Agno | `agno` (`NiadraAgno`) | not available | /en/integrations/agno |
| Microsoft Agent Framework | `agent-framework` (`NiadraContextProvider`) | not available | /en/integrations/agent-framework |
| Semantic Kernel | `semantic-kernel` (`NiadraKernel`) | not available | /en/integrations/semantic-kernel |
| Haystack 3.2+ | `haystack` (`NiadraContext`, `NiadraReply`, `NiadraAgentHooks`, `history_tools`) | not available | /en/integrations/haystack |
| CAMEL-AI | `camel` (`NiadraMemory`, `NiadraToolkit`), context with the sync client | not available | /en/integrations/camel |
| DSPy | `dspy` (`niadra_adapter`, `NiadraModule`, `history_tools`) | not available | /en/integrations/dspy |
| AG2 (AutoGen's community line) | `ag2` (`NiadraAG2`) | not available | /en/integrations/ag2 |
| Genkit | not available | `genkit` (`niadraMiddleware`, `niadraTools`), pass `model` for usage | /en/integrations/genkit |
| VoltAgent | not available | `voltagent` (`niadraHooks`, `niadraTools`), AI SDK 6 | /en/integrations/voltagent |
| Cloudflare Agents SDK | not available | `cloudflare-agents` (`niadraAgent`, `workersAiUsage`) | /en/integrations/cloudflare-agents |
| OpenAI, Azure OpenAI | `openai` (`niadra.wrap`) | `wrap` from `@niadra/sdk` | /en/integrations/openai |
| Anthropic | `anthropic` (`wrap`) | `anthropic` (`wrapAnthropic`) | /en/integrations/anthropic |
| Amazon Bedrock Converse | `bedrock` (`wrap`) | `bedrock` (`wrapBedrock`) | /en/integrations/bedrock |
| Google GenAI | `google-genai` (`wrap`) | `google-genai` (`wrapGoogleGenAI`) | /en/integrations/google-genai |
| LiteLLM | `litellm` (`completion`, `acompletion`, `NiadraLogger`) | not available | /en/integrations/litellm |
| n8n, Flowise, Langflow, Dify | Langflow: `integrations-extras/langflow/niadra_component.py`; Dify: the plugin in `integrations-extras/dify` | `packages/n8n-nodes-niadra`, `packages/flowise-nodes-niadra` | /en/integrations/overview |

No adapter for the framework? Use the SDK directly: `conversation.context()` before the model call, `conversation.customer()` and `conversation.agent()` for the turns, `conversation.tools()` for the kit. Read https://docs.niadra.com/en/sdk/python.md or https://docs.niadra.com/en/sdk/typescript.md.

## Step 3: wire the five primitives

Every adapter does the same five things. Check each one in the code you write:

1. **Context before the model call.** Open `niadra.conversation(conversation_id, subject=..., view="voice" | "chat")` once per conversation; the adapter reads `context()` before each model call and places `system_block` after the instructions and `turn_block` at the end. Do not paste the context into the instructions yourself when an adapter exists.
2. **Turns.** The customer's message and the agent's answer are recorded once each (`customer()`, `agent()`), with the provider's usage when the framework exposes it. The conversation ends with `end()` (leaving the `with` block, the session `close`, the post-call webhook).
3. **Tools.** The history tools (`search_customer_history`, `get_customer_timeline`, `open_history_item`) come from the adapter, bound to the customer. Never add a customer parameter to them.
4. **Verification.** What the platform proves goes to `verify()` before the first `context()`: `network_attestation` with the STIR/SHAKEN level (A proves V2, B and C prove V1), `login`, `otp_whatsapp`, `otp_sms`. Without proof the read is at V0 and sensitive items stay withheld, which is correct.
5. **Handoff.** Where the code transfers to a person or another agent, call `handoff("human" | "agent", reason=...)` (or the adapter's `transferred_to_human()`), and give the next agent the same conversation id.

Optional: agent memory (`agent_memory=True` in Python, `agentMemory: true` in TypeScript) puts the agent's own working notes before the customer's context and adds `search_agent_memory` (and `remember` for keys with `agent_memory:write`). The space must turn it on in the Console first.

## Step 3b: the agent features, only when the space turned them on

Everything below is off until the space lists the feature in its `features` document, and `niadra.profile()` (Python) or `await niadra.profile()` (TypeScript) says which are on. Never wire these blindly: a route of a feature that is off answers 404, and the adapters record nothing with the feature off.

- **Turn records** (`turns`): wrap each answer in `conversation.turn(build=Niadra.build(prompts=..., model=...))` (Python, a context manager) or `convo.turn({ build: Niadra.build({ prompts, model }) }, async () => { ... })` (TypeScript), and record the tools the agent calls with `@Niadra.tool("name", provenance=...)` / `Niadra.tool("name", fn, { provenance })`. Adapters take `turns=True` / `turns: true` (Google ADK, OpenAI Agents, LangGraph, LangChain in Python; LangChain and LangGraph, Mastra, AI SDK, OpenAI Agents JS, Google ADK and VoltAgent in TypeScript). A tool that returns fields the key may not read takes `mask_output=True` / `maskOutput: true`; a tool the space bound in its `tool-bindings` document needs no `binding` in code, the SDK profile serves it. The record never repeats the conversation text and never waits for Niadra. Docs: https://docs.niadra.com/en/concepts/turn-records.md
- **Claims** (`claims`): count mode is automatic inside a turn. To act on the contract's verdicts, pass the answer through `conversation.claims.guard_text(text)` / `convo.claims.guardText(text)` (or `guard(stream)`) before it reaches the customer. Never rewrite an immutable output yourself. Docs: https://docs.niadra.com/en/concepts/claims.md
- **Coordination** (`coordination`, scope `coordinate`): before an outbound contact, `conversation.check(intent, purpose=...)` / `convo.check(intent, { purpose })`; act only on `allow`, send the `contact_token` to the gateway, then `declare.contact_made(...)`. For an act that must happen once, pass `effect_key` and settle it with `declare.effect(key, "done" | "failed" | "unknown_outcome")`. `niadra.may_contact()` / `niadra.mayContact()` applies the local suppression list even with Niadra down. Docs: https://docs.niadra.com/en/concepts/coordination.md
- **Working memory** (`agent_state`, scope `agent_state`): `conversation.agent_state.get()` / `put(body)` (Python), `convo.agentState.get()` / `put(body)` (TypeScript), with a type of `ownership: agent` declared in the space. Written by code, never by a model tool. Docs: https://docs.niadra.com/en/concepts/agent-state.md
- **Typed state and constraints** (`state`, `signals`): read them with the context, `context(include=["state", "constraints"])`, and place `turn_block` / `suffix` at the end of the prompt as usual. Docs: https://docs.niadra.com/en/concepts/object-types.md and https://docs.niadra.com/en/concepts/signals.md

## Step 4: configuration

- `NIADRA_API_KEY` holds a source key (`nia_sk_test_...` for sandbox, `nia_sk_live_...` for production). The key names the region and the space; the SDK derives the address. Never commit it.
- `NIADRA_BASE_URL` points the SDK elsewhere, such as the local emulator.
- One `Niadra` (or `AsyncNiadra`) instance per process; `channel="voice" | "whatsapp" | "chat" | ...` names where the agent lives.

## Step 5: test against the emulator

`niadra-mock` ships with the Python SDK and emulates the API in memory, agent memory routes included.

```sh
pip install niadra
niadra-mock --port 8765
NIADRA_BASE_URL=http://127.0.0.1:8765 NIADRA_API_KEY=nia_sk_test_local_mock_k1_mocksecret python your_agent.py
```

In Python tests, run it in-process:

```python
import httpx
from niadra import Niadra
from niadra_mock import MOCK_KEY, MockApp

mock = MockApp()
niadra = Niadra(MOCK_KEY, base_url="http://mock", http_client=httpx.Client(transport=httpx.WSGITransport(app=mock.wsgi)), strict=True)
```

`mock.cell` lets a test read the recorded events, turn agent memory on (`enable_agent_memory()`) and inject failures (`fail_next`, `revoke`, `cut`). Write one test that proves the five primitives (the context lands after the instructions, one customer turn and one agent turn, a tool call bound to the customer, `verify` before the first context, `handoff` on transfer) and one that proves the agent still answers when Niadra is down.

## Step 6: checklist before you finish

- [ ] The subject comes from the platform or the app's own auth, never from the model or the browser.
- [ ] The instructions stay first; the pack goes after them; the `turn_block` goes at the end.
- [ ] Each turn is recorded once; the conversation ends.
- [ ] `verify()` runs before the first `context()` when the platform proves something.
- [ ] No handle, message text or key in logs or URLs.
- [ ] The agent answers with the emulator stopped.
- [ ] Extras and peer dependencies are pinned in the lockfile.
- [ ] Turn records, coordination and working memory are wired only behind the space's features, and the agent still answers when they are off.

## References

- Quickstart: https://docs.niadra.com/en/quickstart.md
- Integrations overview: https://docs.niadra.com/en/integrations/overview.md
- Python SDK: https://docs.niadra.com/en/sdk/python.md
- TypeScript SDK: https://docs.niadra.com/en/sdk/typescript.md
- Agent memory: https://docs.niadra.com/en/concepts/agent-memory.md
- Turn records and replay: https://docs.niadra.com/en/concepts/turn-records.md and https://docs.niadra.com/en/guides/replay-in-ci.md
- Coordination: https://docs.niadra.com/en/concepts/coordination.md
- Claims: https://docs.niadra.com/en/concepts/claims.md
- Working memory: https://docs.niadra.com/en/concepts/agent-state.md
- MCP server and clients: https://docs.niadra.com/en/guides/mcp.md
- API contract (OpenAPI 3.1): https://docs.niadra.com/openapi/en/cell.json
- Full documentation as text: https://docs.niadra.com/llms.txt
