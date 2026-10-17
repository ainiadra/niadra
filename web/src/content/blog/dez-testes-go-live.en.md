---
id: dez-testes-go-live
lang: en
slug: ten-tests-before-ai-agents-from-several-vendors-go-live
title: Ten tests to run before AI agents from several vendors go live
description: A script for the engineer: channel handoff in both directions, the unverified conversation, the recycled number, the vendor swap, the price with no source, the memory out of reach, deletion, the internal agent closing the loop and replay in CI. What to do, what to expect and how to measure, with the SDK's code.
order: 6
kind: guide
date: 2026-10-01T09:00:00-03:00
category: Guide
icon: check
cover: Ten tests. Then production
keywords: testing AI agents before go-live, test plan for customer service AI agents, voice to WhatsApp handoff test, agent memory outage test, replaying agent conversations in CI, AI agent vendor swap test, sensitive data test AI agent, production checklist for AI agents
---

Before AI agents from two or three vendors go live reading the same customer memory, ten tests tell you whether the memory behaves the way the contract promises. Each one has what to do, what to expect and how to measure, and the ones that involve code use Niadra's public SDK, the same code as the [repository's examples](https://github.com/ainiadra/niadra-sdk-python/tree/main/examples). The whole script takes a day with two test phone numbers, a voice agent and a WhatsApp agent from different vendors, and a test environment for the memory. Without Niadra, the tests still hold: what changes is how each one is observed.

This guide is for the people who build and run the agents. It assumes the SDK is installed (`pip install niadra` or `npm install @niadra/sdk`), one source key per vendor and, to run without the cloud, the local emulator (`niadra-mock`, with `NIADRA_BASE_URL=http://127.0.0.1:8765`), which the repository itself uses in its integration tests.

## 1. The handoff from the call to WhatsApp

**What to do.** Call the voice agent from a test number and close a case with a promise ("the visit is moved to tomorrow, between 8 am and noon"). Hang up. Write on WhatsApp from the same number, right away and three hours later: "what time is the technician coming?".

**What to expect.** The WhatsApp agent answers with the promise from the call, cites the call by its time and asks for neither name nor reason. Right away, the last lines of the call appear in the "just now" layer of the context; three hours later, the visit appears as an open item.

**How to measure.** The read receipt lists what was delivered; context-use measurement says, per agent and vendor, whether the agent asked again for what it already had ([how to know whether the agent used the context](/en/blog/how-to-know-whether-your-ai-agent-used-the-context)). The post on [the handoff from the call to WhatsApp](/en/blog/how-to-keep-context-from-voice-call-to-whatsapp) details what Meta does and does not deliver in the webhook: for users with a username, the phone number only comes through if there was an exchange in the last 30 days.

```python
from niadra import Niadra, phone

niadra = Niadra(channel="whatsapp")
with niadra.conversation("wa-15551234567", subject=phone("+15551234567")) as chat:
    chat.customer("what time is the technician coming?")
    context = chat.context()
    # context.system_block carries the promise from the call, with channel and time;
    # context.turn_block, what just happened
```

## 2. The way back: from WhatsApp to voice

**What to do.** Write first, with a complaint. Call afterwards.

**What to expect.** The voice agent receives a short, speakable version of the context, with no tables, and cites the message in its greeting. The voice view is different: fewer tokens, sentences said out loud ([memory for voice agents](/en/blog/memory-for-voice-agents-the-latency-budget)).

**How to measure.** The time between the request and the context, during the ring, and whether the agent asked for the reason for the call. In the [LiveKit](/en/integracoes/livekit), [Pipecat](/en/integracoes/pipecat), [Vapi](/en/integracoes/vapi), [Retell](/en/integracoes/retell) and [ElevenLabs](/en/integracoes/elevenlabs) adapters, the read happens while the phone rings.

## 3. The conversation that has not proven who it is

**What to do.** Write from a number that has never spoken to the company but is linked in the records to a customer with sensitive data in the memory (a health fact, an ID number). Ask for that data.

**What to expect.** The context arrives at level 1: the source is linked to the customer, but nothing has proven who is typing. The sensitive items appear as held back, with a count, and only come out after the verification your policy requires (a code, a login). None of the data appears in the reply.

**How to measure.** The receipt records the level and the held-back items. In the [benchmark of September 30, 2026](/en/benchmark), this is the measure "sensitive data delivered without verification": Niadra delivered none, with per-conversation verification; systems with no verification mechanism, reading with the same id on every channel, delivered it in 31% to 100% of cases.

```python
from niadra import Niadra, phone

niadra = Niadra(channel="whatsapp")
thread_id = "wa-5511900000000"
ctx = niadra.context(phone("+15550000000"), view="chat", verification="V1", conversation_id=thread_id)
# the sensitive items stay held back until a verify() at a higher level
niadra.verify("otp_whatsapp", "V3", handle=phone("+15550000000"), conversation_id=thread_id)
```

## 4. The number that changed hands

**What to do.** Simulate a number that has been silent for 180 days and comes back without a record ID in the same event.

**What to expect.** The agent reads a first contact: an empty context, with a receipt, nothing from the previous owner. The old link stays suspended on the old profile until a system or a login ties it back ([when a phone number changes hands](/en/blog/when-a-phone-number-changes-hands)).

**How to measure.** The read receipt says "first contact"; no line from the old profile appears.

## 5. The vendor swap

**What to do.** Revoke vendor A's credential. Connect vendor B's agent to the same memory, with its own credential and the same purpose. Repeat test 1.

**What to expect.** Agent B reads the whole history, including what agent A recorded. Agent A, on trying to read, is refused, and the refusal leaves a receipt. Nothing was exported and re-imported ([how agents from different vendors share customer history](/en/blog/how-ai-agents-from-different-vendors-share-customer-history)).

**How to measure.** The refusal receipt, in the Console and in the SIEM, with the vendor and the time.

## 6. The price the agent made up

**What to do.** Give the agent a catalog lookup tool and a draft reply with a price different from what the tool returned. Turn on the claim contract in count mode.

**What to expect.** The price claim enters the turn record with the verdict `mismatch`; in action mode, the category decides between annotating, counting or, in an output that may change and only when the correction is unequivocal, rewriting to the value with provenance. A number with no source at all gets `unsupported` ([claims, in the documentation](https://docs.niadra.com/concepts/claims)).

**How to measure.** `niadra contract test` in CI, with your corpus of phrases that must never trigger; and the count of verdicts per agent and vendor after a week in production.

```python
from niadra import Niadra, phone

niadra = Niadra(channel="whatsapp")

@Niadra.tool("check_price", provenance=lambda p: [{"ref": f"product:store:{p['sku']}", "fields": {"price_sale": p["price_sale"]}}])
def check_price(sku: str) -> dict:
    return {"sku": sku, "price_sale": 149.9}

with niadra.conversation("thread-82", subject=phone("+15551234567"), agent_id="store") as conversation:
    conversation.customer("How much is the PX dress?")
    with conversation.turn(build=Niadra.build(prompts={"store": "v16"}, model="gpt-4.1-mini")):
        check_price("PX-4471")
        guarded = conversation.claims.guard_text("The dress is US$ 199.90 today.")
        conversation.agent(guarded.text)
```

The snippet follows the repository's `claim_guard.py` example: the draft's price (199.90) disagrees with what the tool returned (149.90), and the retail contract flags the claim. The post on [the agent that stated what it never checked](/en/blog/the-ai-agent-stated-what-it-never-checked-two-public-cases) shows what happens without it.

## 7. The memory out of reach

**What to do.** Point `NIADRA_BASE_URL` at an address that does not answer, or kill the emulator in the middle of a conversation.

**What to expect.** The agent keeps going: the read serves the last good context, marked as degraded; turns wait in the queue; the contact's suppression list holds from the last copy. Nothing the agent does waits for Niadra; this is the behavior described in the repository's `turn_records.py` example.

**How to measure.** The time until the agent can call the model with the memory out, and the `degraded` mark on the context. The benchmark measures this as "memory slow or down", with each system's client as it ships.

## 8. Deletion with a receipt

**What to do.** Request the deletion of a test customer through the API or the Console, by lineage.

**What to expect.** Her context comes back empty for every agent, from every vendor; the facts derived from her conversations go with it; the deletion leaves a receipt ([GDPR, LGPD and AI agents](/en/blog/security-and-gdpr-for-customer-memory-in-ai)).

**How to measure.** The deletion receipt and the next read, empty, with its own receipt.

## 9. The internal agent closes the loop

**What to do.** Have the billing agent post a credit in the test ERP and send the ERP's event by webhook. Call right after.

**What to expect.** The voice agent receives the credit as done, not pending, with its source and time, and the action appears as confirmed by the system event, not merely declared by the agent ([memory for internal agents](/en/blog/memory-for-internal-ai-agents)).

**How to measure.** The action's state in the context: `declared` until the event arrives, `confirmed` afterwards, `divergent` if the ERP recorded a different value.

## 10. Replay in CI

**What to do.** Record a real conversation from the test environment with turn records on. Replay it in CI with the memory of the time and the same build.

**What to expect.** The same reply, turn by turn, without the content leaving your company: the record keeps the pointer and the hash, and the replay runs inside your boundary. It is the repository's `replay_demo.py` example, and what makes a prompt regression visible before the customer sees it ([Turn records](/en/produtos/registro)).

**How to measure.** The replay passes or fails in CI, like any test.

## What we don't know

- The reference numbers cited (no sensitive data delivered, 98.8% cross-channel accuracy) come from Niadra's benchmark, with synthetic scenarios in Portuguese and English, in a single region. Your result depends on your policy, your channels and your agents; that is why the script exists.
- WhatsApp webhook behavior changes with Meta's and the provider's configuration. Test 1 needs repeating with a username account and no 30-day history.
- The adapters cover the vendors listed under [integrations](/en/integracoes). For another vendor, the ten tests hold with the SDK used directly.

## How Niadra solves it

The ten tests are the list of what Niadra does as the omnichannel memory of a company's AI agents: it takes the events from every channel, platform and system, recognizes the customer before storing, delivers to each agent, from any vendor, the context of its task at the conversation's verification level, checks what the agent stated, records every turn and every read, and keeps serving when it is out of reach. The [adapters](/en/integracoes) connect the agents you already run, and the documentation has the [quickstart](https://docs.niadra.com/en/quickstart) and the pages on [claims](https://docs.niadra.com/en/concepts/claims), [turn records](https://docs.niadra.com/en/concepts/turn-records) and [coordination](https://docs.niadra.com/en/concepts/coordination). The [benchmark of September 30, 2026](/en/benchmark) has the script, the dataset and the result files to reproduce what this guide asks you to measure.

## Frequently asked questions

### Do I need two real vendors to run the script?

For tests 1, 2 and 5, yes: the handoff between vendors is what is being tested. The others run with a single agent and the local emulator.

### How long does it take?

A day, with the test numbers and credentials ready. Test 10 goes into CI and runs on every prompt or model change.

### What if a test fails?

Each test points at the receipt or the record that explains the failure: what was delivered, what was held back, what the agent stated. The fix is usually in the policy (what the level releases), the adapter (the identifier the channel delivers) or the prompt (what the agent does with the context), and the record says which.
