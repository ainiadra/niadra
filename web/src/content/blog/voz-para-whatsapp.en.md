---
id: voz-para-whatsapp
lang: en
slug: how-to-keep-context-from-voice-call-to-whatsapp
title: How to keep context from a voice call to WhatsApp, and back
description: Your customer hangs up and messages you on WhatsApp. What the agent needs to know, how context crosses vendors and how to measure the channel handoff.
order: 6
kind: guide
date: 2026-09-22T00:41:00-03:00
updated: 2026-10-01T10:00:00-03:00
category: Channels
icon: hub
cover: From the call to WhatsApp, told once
keywords: how to keep context from a voice call to WhatsApp, voice agent to WhatsApp agent handoff, voice and WhatsApp AI agents from different vendors, customer context across channels, does a phone call open the WhatsApp 24-hour window, how to measure repeat contact with AI agents, call transcript for the WhatsApp agent
---

To keep context when a conversation moves from your voice agent to your WhatsApp agent, the call has to go into a shared memory while it happens, outside the voice vendor. When the call ends, what was handled and every promise become facts with a channel and a time. When the customer writes, the system recognizes them as the person who called, and the WhatsApp agent receives those facts before it replies, in chat format. The reverse path uses the same memory, with a short, speakable context for the voice agent.

## What gets lost when the customer leaves the call for WhatsApp?

At 2:07 pm, Marina called, and the voice agent from vendor B rebooked the technician visit as a priority for tomorrow, between 8 am and noon. It also agreed that the technician's arrival notice would go out on WhatsApp, 30 minutes ahead. At 6:30 pm, she writes on WhatsApp, where the agent from vendor A answers: "What time is the technician coming tomorrow?" Without a shared memory, three things get lost:

- **The identifier.** The voice channel knows the number that called. WhatsApp knows the number of whoever is writing, or a business-scoped ID ([identity across channels](/en/blog/how-to-recognize-the-same-customer-across-channels)).
- **The transcript.** It stays on the voice vendor's platform, out of vendor A's reach.
- **The promise.** The WhatsApp agent knows nothing about the rebooked visit and may offer a different time.

Telephony has a standard for passing a small piece of data along with a call, SIP's User-to-User header ([RFC 7433](https://www.rfc-editor.org/rfc/rfc7433)). That data travels inside the call and never reaches a WhatsApp conversation.

## What needs to happen when the call ends?

The handoff starts before Marina hangs up. After every reply, the voice agent records what she said and its own answer in a memory outside vendor B. When the call ends, Niadra processes the whole conversation. The 2:07 pm call yields items like these:

- **Outcome:** visit rebooked as a priority for tomorrow, Sep 23, between 8 am and noon.
- **Promise:** arrival notice on WhatsApp, 30 minutes ahead.
- **Done by another agent:** a US$ 40 credit on the August bill, applied by the billing agent at 2:06 pm.

Every item keeps the channel, the time and the conversation it came from. None goes in without something said on the call to back it up.

Two failures show up at this stage. Phone audio is narrowband, and the transcript can get a time wrong. The WhatsApp agent cites "the 2:07 pm call," which lets the customer correct anything that came out wrong. The call may also fail to reach the memory in full. Niadra shows, source by source, what the SDK says it sent and what arrived, and a warning rule calls your system when a source stops sending events.

## How do you know the person on WhatsApp is the one who called?

At 6:30 pm, the message arrives with Marina's number, already linked to her profile. On WhatsApp, that depends on a platform rule. If a user has turned on a username, the webhook leaves out their phone number, unless that business phone number has exchanged messages or calls with them in the last 30 days, or they are in the business's contact book ([Meta's documentation](https://developers.facebook.com/documentation/business-messaging/whatsapp/business-scoped-user-ids/#phone-numbers)). Marina wrote at 2:02 pm, so her number comes through. The 2:07 pm call went over the phone network and does not count.

Recognizing is not confirming. The conversation starts at [level 1](/en/blog/how-to-recognize-the-same-customer-across-channels#how-do-you-verify-identity-during-the-conversation): the source is linked to the customer, but nothing has proven who is typing. The company's policy releases the visit time and the credit already applied, and holds back ID numbers and payment data. If Marina asks for something that involves that data, the agent sends a code over WhatsApp. Once she confirms it, the agent requests the context again, now with the held-back items. If the message arrives with an ID and no phone number, the context starts empty until evidence links that ID to her profile.

## What does the WhatsApp agent receive, and in what format?

The context is tailored to the channel. In chat, it carries dates and time windows. Next to each line, the conversation it came from:

| Context line | Came from |
|---|---|
| Marina Souza. Call her "Marina". | |
| Visit rebooked: tomorrow, Sep 23, between 8 am and noon. | Phone 2:07 pm |
| Arrival notice on WhatsApp, 30 minutes ahead. | Phone 2:07 pm |
| 2 items held back until identity is confirmed. | |
| "What time is the technician coming tomorrow?" | WhatsApp 6:30 pm |

The agent answers without a single question: "Good evening, Marina. Your visit is set for tomorrow, between 8 am and noon, as agreed on the 2:07 pm call. You'll get the arrival notice right here, 30 minutes ahead."

On the reverse path, the same memory becomes a voice context: short, no tables, in sentences the agent says out loud, and cheaper per message ([token costs](/en/blog/how-to-cut-token-costs-for-ai-agents)). If Marina calls tomorrow morning, the voice agent gets: "Visit today, between eight and noon. The arrival notice goes out on WhatsApp."

```python
from niadra import Niadra, phone

niadra = Niadra()

# 2:07 pm, voice agent from vendor B: every reply goes into the memory
caller = phone(caller_id)
turns = [("customer", question), ("ai_agent", reply)]
for role, text in turns:
    niadra.track({
        "channel": "voice",
        "conversation_id": call_id,
        "handles": [caller],
        "speaker": {"role": role},
        "content": {"text": text},
    })

# 6:30 pm, WhatsApp agent from vendor A: the message arrives from the
# customer's number, which proves level 1 for this conversation
sender = phone(sender_phone)
niadra.track({
    "channel": "whatsapp",
    "conversation_id": thread_id,
    "handles": [sender],
    "speaker": {"role": "customer"},
    "content": {"text": question},
    "verification_hint": "V1",
})
niadra.flush()

# the context in chat format, at level 1
ctx = niadra.context(sender, view="chat", verification="V1", conversation_id=thread_id)

# the customer asks for the reference number read out on the call: the agent searches the history
hits = niadra.search(
    sender,
    "reference number from today's call",
    verification="V1",
    conversation_id=thread_id,
)

# after replying: the reply goes into the memory too
niadra.track({
    "channel": "whatsapp",
    "conversation_id": thread_id,
    "handles": [sender],
    "speaker": {"role": "ai_agent"},
    "content": {"text": reply},
})
```

The reference number from the call stayed out of the context, in the history, and search finds it when she asks.

## How much time can pass between the call and the message?

Each gap calls for something different:

- **Seconds.** Marina hangs up and writes right away. The last lines of the call already show up in the context, in the "just now" layer, with channel and time, while Niadra finishes processing the conversation.
- **Hours.** That is the 6:30 pm case. The visit and the notice already appear as open items.
- **Days.** The context shows the latest state of each open item, and the call stays in the history.

On WhatsApp, the person's last message or call opens a 24-hour customer service window. Once the window closes, the business needs a pre-approved template message to reach out ([Meta's documentation](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages#customer-service-windows)). A call over the phone network does not open this window. Tomorrow morning's notice can go out as a free-form message, because Marina wrote at 6:30 pm. If the visit were three days away, the notice would need an approved template.

## How do you test and measure a channel handoff?

Before going live, run this test:

1. Call the voice agent from a test number and close a case with a promise.
2. Hang up and write on WhatsApp from the same number, right away and hours later.
3. Check that the WhatsApp agent cites the call and promise without asking for a name or reason.
4. Repeat from a WhatsApp account with a username that has never messaged the business: nothing from the call shows up before confirmation.
5. Run the reverse path: write first, then call.

In production, measure per customer, across channels: the 6:30 pm message is a first contact on WhatsApp and a repeat contact for the call. In [SQM Group](https://www.sqmgroup.com/resources/library/blog/fcr-metric-operating-philosophy)'s call center benchmark, 29% of customers have to call back about the same issue, and checking on an unresolved issue is a common cause. Three metrics matter:

- **Repeat contact:** did the customer come back for the same reason, on any channel?
- **Repeated question:** did the agent ask for something the context already had?
- **Time to resolution:** from first contact to outcome.

## How Niadra solves it

Niadra is the shared memory for all of a company's AI agents, customer-facing and internal, across every channel and vendor. Each agent gets three calls, and no vendor needs an integration with another.

- `track()` sends every reply to the memory without delaying the agent. When the conversation ends, Niadra's AI reads it together with the same customer's other conversations that ended within the same seconds and extracts what was handled, the outcome, the open items and the promises. A short conversation answered on the spot is kept as it was said, without going through the model.
- `context()` recognizes the customer by phone, email, ID number or app login, with the weight of each identifier. Already compiled, with no AI model on the read, it delivers the context in the channel's format, with the source of every fact, conversation and action.
- `search()` opens the entire history, with any LLM, through the SDK, the HTTP API or as an MCP tool.

Email addresses, phone numbers, card numbers, IBANs and postal addresses are masked before Niadra's AI reads the conversations. Sensitive data reaches your agent only after the verification your policy requires. Niadra is fully managed, and your team connects the SDK, in Python and TypeScript ([Products](/en/produtos)).

The [LiveKit](/en/integracoes/livekit), [Pipecat](/en/integracoes/pipecat), [Vapi](/en/integracoes/vapi), [Retell](/en/integracoes/retell) and [ElevenLabs](/en/integracoes/elevenlabs) adapters read the context while the phone rings; the [WhatsApp Cloud API](/en/integracoes/whatsapp) and [Twilio](/en/integracoes/twilio) adapters receive the message on the other side. The full script, with the handoff in both directions, is in [ten tests to run before AI agents from several vendors go live](/en/blog/ten-tests-before-ai-agents-from-several-vendors-go-live); in the [benchmark of September 30, 2026](/en/benchmark), cross-channel continuity was measured at 98.8% of the 338 cases.

## Frequently asked questions

### Do I need to integrate my voice vendor with my WhatsApp vendor?

No. Each agent connects to the same memory with three calls, and no vendor reads the other's system. The WhatsApp agent receives the context that came out of the call.

### Does a phone call open the WhatsApp 24-hour window?

No. According to [Meta's documentation](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages#customer-service-windows), the window opens when the person sends a message or calls through WhatsApp itself. Outside the window, the business needs a pre-approved template message to write first.

### How do I know the handoff from voice to WhatsApp works?

Measure per customer, across channels: repeat contacts for the same reason, repeated questions and time to resolution. Before going live, call from a test number, hang up and write on WhatsApp from the same number. The agent has to answer without asking about anything the call already settled.
