---
id: identidade-entre-canais
lang: en
slug: how-to-recognize-the-same-customer-across-channels
title: How to recognize the same customer across channels
description: Phone, email, ID number and app login carry different weights. Here is how to unify customer identity across channels for AI agents without exposing data.
order: 2
kind: guide
date: 2026-09-21T21:49:00-03:00
updated: 2026-10-01T10:00:00-03:00
category: Identity
icon: user
cover: One customer, every channel
keywords: how to unify customer identity across channels, customer identity for AI agents, recognize the same customer on WhatsApp and voice, omnichannel identity resolution, recycled phone numbers, identity verification in AI customer service
---

To recognize the same customer on WhatsApp, voice, email and your app, you match the identifiers each channel provides (phone number, email, ID number, app login) and give each one a weight, because none of them proves on its own who is on the other end. Every link between two identifiers is recorded with its source and the weight of each one, and it can be undone without losing history. The AI agent receives a context that changes with how much the conversation has already proven about who the person is: before verification, what it needs to help; after verification, the sensitive data.

## Why does each channel identify the customer differently?

Each channel hands over its own identifier, and none was designed to talk to the others.

- **WhatsApp** usually gives you the number of whoever is writing. The [platform's official documentation](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/overview) warns that the sender ID may not match the customer's phone number, and usernames bring a [business-scoped user ID](https://developers.facebook.com/documentation/business-messaging/whatsapp/business-scoped-user-ids/), different for every business.
- **Voice** gives you the calling number, which can be spoofed, arrive hidden or belong to a PBX (which identifies the company, not the caller). The call authentication the [FCC](https://www.fcc.gov/call-authentication) requires from carriers (STIR/SHAKEN) vouches for the line, not for whoever is speaking.
- **Email** confirmed with a code is worth more than an address typed into a form.
- **The app** gives you a login with a password, the strongest identifier, but only for people who installed the app and signed in.
- **An ID number** spoken on a call shows that the person knows the number, not that it belongs to them.

Without a user ID valid on every channel, identity has to be assembled from the clues each conversation leaves behind.

## How do you link identifiers without a ready-made user ID?

The first contact creates the customer, anchored to whatever identifier the channel provided. If the company already has a CRM or app ID, it comes in as one more identifier, with a high weight. Each identifier then gets a weight based on what it proves:

- A phone number is a **hint**: the number may have a new owner.
- An ID number given on a call is **stated**: the person said it, and nothing confirmed it.
- An email confirmed with a code is **verified**.
- An app login with a password is **authenticated**.

Two identifiers become the same customer when there is evidence. The company's system reports that app login 88213 has that phone number, or the person confirms a code sent to the number. A similar name never links profiles.

Every link is recorded as an assertion, with its method, date and source. Every fact in memory stays tied to the identifier and the conversation it came from, not to the profile. That is why a wrong merge can be undone: once the assertion is retracted, the facts go back to their rightful owner.

Three rules guard against the wrong merge:

- Two already authenticated profiles never merge on their own. That merge only happens when someone forces it, with the author and the reason on record.
- Test values, such as the phone number `+5511999999999`, the address `test@example.com` or the ID number `12345678909`, never become a merge key, and an event that carries nothing but test values enters no customer's memory.
- An identifier that shows up linked to three different identifiers of the same type within 24 hours (a store's phone line, a company PBX) is flagged as ambiguous, stops linking profiles and no longer opens the memory on a read.

## What happens when a phone number gets a new owner?

Carriers reassign disconnected numbers to new subscribers as standard practice. [Princeton University researchers](https://recyclednumbers.cs.princeton.edu/) held 200 recycled numbers for a week, and 19 of them were still receiving sensitive calls and messages meant for the previous owner, such as authentication passcodes. In customer service, if the phone number is the key, the AI agent greets the new owner by the old owner's name and reads out someone else's bills.

Three practices handle this risk without deleting anything:

1. A number that goes more than 180 days without showing up and comes back without a record ID of the same customer (ID number, app login or system ID) is put in doubt. [NIST](https://pages.nist.gov/800-63-4/) asks each organization to decide when to reverify an identity after extended inactivity.
2. The link between the number and the customer is suspended, not deleted. A read by that number answers as it would for a first contact, and the previous owner's history stays stored, out of the context.
3. The confirmation reveals nothing. The agent asks for the ID number, which the real owner would type without effort, and sends it with the phone number in an `identify()`: if it matches the record, the number counts again. It never asks "is this Marina?", because the question gives the name away.

If the confirmation matches, the full context comes back. If it does not, the number stays in doubt, every read by it is a first contact, and Marina's history stays intact.

## How do you verify identity during the conversation?

Recognizing tells you which customer is probably in the conversation. Authenticating proves it is them, and the NIST guidelines cover the two in separate volumes. With progressive verification, the conversation moves up a level with each new proof:

| Level | What the conversation has proven |
|---|---|
| 0. Stated | The person said who they are |
| 1. Plausible by channel | The source is already linked to the customer |
| 2. Attested by channel | The network or platform attests that the source was not spoofed |
| 3. Challenged | The person confirmed a code sent to the channel, or signed in with a password |
| 4. Documented | The data matches the company's records, or a human agent confirmed it |

The level only rises through an explicit event, such as a confirmed code, never through an AI model's inference. In code, the agent requests the context again when the level goes up:

```python
from niadra import Niadra, phone

niadra = Niadra(channel="voice")
caller = phone(caller_id)

# While the phone rings: the carrier's partial attestation proves level 1 for this call
niadra.verify("network_attestation", "V1", handle=caller, conversation_id=call_id)
ctx = niadra.context(
    caller, view="voice", verification="V1", conversation_id=call_id
)

# SMS code confirmed: the conversation moves up to level 3
niadra.verify("otp_sms", "V3", handle=caller, conversation_id=call_id)
ctx = niadra.context(
    caller, view="voice", verification="V3", conversation_id=call_id
)

# During the conversation: the agent searches the customer's history
hits = niadra.search(
    caller,
    "credit for missed technician visit",
    verification="V3",
    conversation_id=call_id,
    voice=True,
)
```

## What does the AI agent receive before and after verification?

At 2:02 pm, Marina tells the WhatsApp agent from vendor A that the technician visit promised for this morning never happened. At 2:05 pm, she disputes her August bill in the app. At 2:07 pm, she calls, and the voice agent from vendor B picks up.

The carrier delivers the call with a partial network attestation, and the agent records it while the phone rings: the call starts at level 1. The context that arrives before the hello carries her name, the missed visit (WhatsApp, 2:02 pm), the open dispute (App, 2:05 pm) and a note that an earlier visit had also been missed (Phone, Mar 12). The company's policy requires level 3 for ID numbers and payment data, so those items are held back.

Marina confirms the code sent by SMS, and the agent requests the context again, now with the held-back items. When she mentions that she got a credit last time, the agent searches the history and finds the record from Mar 12, by phone: the technician did not show up, and a $40 credit was applied to the April bill.

Privacy is built into the design: a caller using a spoofed number gets at most what the company released for the level the call proved, without what the policy reserves for after verification. That is the data minimization the [GDPR](https://gdpr-info.eu/art-5-gdpr/) and the LGPD call for, applied to every moment of the conversation.

## How Niadra solves it

Niadra matches phone, email, ID number and app login into one customer, with the weight of each identifier. Every piece of information stays linked to the identifier it came from, which is why Niadra splits two people merged by mistake without losing the history of either. The context changes according to how much the conversation has already proven about who the person is, across five verification levels, and reaches the agent with a target under 100 ms, with the source of every fact, conversation and action. Email addresses, phone numbers, card numbers, IBANs and postal addresses are masked before they reach the AI model that processes conversations. Sensitive data reaches your agent only after the verification your policy requires. Every read goes into an audit trail chained by SHA-256. See the identity engine on the [Identity page](/en/produtos/identidade) and the protection layers in [Security](/en/recursos#seguranca).

The effect of not resolving identity has been measured: in the [benchmark of September 30, 2026](/en/benchmark), the same open-source memory answers 77.6% of the cross-channel scenarios with a single id and 28.5% when each channel sends its own id, on the same 165 scenarios ([why cross-channel identity breaks agent memory](/en/blog/why-cross-channel-identity-breaks-agent-memory)). The [WhatsApp Cloud API](/en/integracoes/whatsapp) and [Twilio](/en/integracoes/twilio) adapters deliver the `wa_id` and the phone number as clues, and [Identity](/en/produtos/identidade) decides the weight of each.

## Frequently asked questions

### Do I need a single user ID before running AI agents on several channels?

No. The first contact on any channel creates the customer, from the identifier that channel provided. A CRM or app ID, if you have one, comes in as one more identifier, with a high weight.

### Is a phone number enough to identify the customer?

No. The calling number can be spoofed, arrive hidden, belong to a PBX or have a new owner. It is good for recognizing the likely customer, and sensitive data requires stronger proof, such as a confirmed code or a login.

### What happens if two people are merged by mistake?

Niadra splits them without losing the history of either. That works because every piece of information stays linked to the identifier and the conversation it came from, not to the profile.

### How does progressive verification protect customer privacy?

What reaches the agent follows the level that call proved, and a number that comes back after 180 days of silence reads as a first contact until your system links it to the customer record again.
