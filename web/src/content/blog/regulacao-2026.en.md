---
id: regulacao-2026
lang: en
slug: what-2026-regulation-asks-of-an-ai-agent-that-talks-to-customers
title: Since 2 August, an AI agent in the EU must say it is AI. What 2026 regulation asks of agents that talk to customers
description: Article 50 of the AI Act has applied since 2 August 2026, with fines of up to 15 million euros or 3% of turnover. Brazil's PL 2338 awaits a committee report. The UAE central bank published its note. What each text asks, and what all three ask together: proof of what the agent was, said and did.
order: 5
kind: news
date: 2026-10-01T09:00:00-03:00
category: Regulation
icon: shield
cover: Say it is AI. Prove what it said
keywords: AI Act Article 50 customer service AI agents, duty to disclose AI 2 August 2026, Brazil PL 2338 AI bill status 2026, regulation of AI agents in customer service, Digital Omnibus AI Act delay, record of conversation with AI agent, CBUAE AI guidance note, AI Act fine 15 million
---

Since August 2, 2026, anyone offering an AI agent that talks to people in the European Union has to make sure those people know they are talking to an AI. That is Article 50 of the AI Act: "Providers shall ensure that AI systems intended to interact directly with natural persons are designed and developed in such a way that the natural persons concerned are informed that they are interacting with an AI system", unless that is obvious from the circumstances, and the information must reach them "in a clear and distinguishable manner at the latest at the time of the first interaction" ([text of Article 50](https://artificialintelligenceact.eu/article/50/)). The 2026 delay for high-risk systems did not touch this article: Regulation (EU) 2026/1744 pushed the high-risk obligations to December 2027 and kept Article 50's date ([Baker Botts, September 10, 2026](https://www.bakerbotts.com/thought-leadership/publications/2026/september/eu-ai-act-article-50-transparency-obligations-go-live)). Fines reach 15 million euros or 3% of worldwide turnover, whichever is higher.

In Brazil, bill PL 2338/2023 remains in the Chamber of Deputies, "awaiting a report" since September 2, 2026 ([CASRAI, updated September 25, 2026](https://casrai.org/guides/brazil-ai-bill-pl-2338-status)). In the Emirates, the central bank published a guidance note on AI for financial institutions in February ([Zawya, February 23, 2026](https://www.zawya.com/en/business/banking-and-insurance/uae-central-bank-issues-guidance-for-responsible-use-of-ai-in-finance-fp1n3i0c)). The three texts carry different degrees of force. At bottom, they ask the same thing of anyone running AI agents in service, collections and sales: to be able to prove what the agent was, what it said and what it did.

This post is for the people accountable for the agents and for the security and privacy team that will receive the question. Every source was read on October 1, 2026.

## European Union: Article 50 in force

What the article requires of an agent that serves customers, from the text ([Article 50](https://artificialintelligenceact.eu/article/50/)):

- **Say it is AI** (50(1)): a system that interacts directly with people is designed so they know they are talking to an AI, unless that is obvious to a reasonably informed person.
- **At first contact, clearly** (50(5)): the information arrives "at the latest at the time of the first interaction or exposure".
- **Generated content marked** (50(2) and 50(4)): synthetic output marked in a machine-readable way; text published to inform the public on matters of public interest, and deepfakes, disclosed as generated.

According to Baker Botts' analysis, the article reaches providers and deployers outside the EU when the system's output is used inside it: a Brazilian company whose WhatsApp agent serves customers in Portugal is in scope. Systems placed on the market before August 2, 2026 have until December 2, 2026 for the machine-readable marking of 50(2); the duty to say it is AI has no grace period ([Baker Botts](https://www.bakerbotts.com/thought-leadership/publications/2026/september/eu-ai-act-article-50-transparency-obligations-go-live)).

What the article does not say: how to prove the information was given. That is where the record comes in: the time the agent introduced itself as AI, on which channel, on whose behalf, kept per conversation, or the company has no way to answer a complaint.

## Brazil: PL 2338 and what already applies

PL 2338/2023 was approved by the Senate on December 10, 2024 and is before a special committee of the Chamber, with deputy Aguinaldo Ribeiro as rapporteur. In May 2026, the Senate's vice-president, Eduardo Gomes, said the bill should only advance in the next legislative year, because of the electoral calendar; the rapporteur argued for a "living law", because "every day there is a change" ([Telesíntese, May 19, 2026](https://telesintese.com.br/eduardo-gomes-diz-que-pl-de-ia-deve-ficar-para-2027-juscelino-e-aguinaldo-defendem-lei-viva/)). On September 2, 2026, the recorded status was "awaiting a report" ([CASRAI](https://casrai.org/guides/brazil-ai-bill-pl-2338-status)). The text the Senate approved classifies systems by risk, creates rights to transparency, explanation and contestation for the affected person, and provides for sanctions; what will apply in Brazil is what the Chamber approves, and that does not exist yet.

What already applies, regardless of the bill: the LGPD, which defines controller and processor, requires a legal basis and a purpose for every processing operation, and gives the data subject the right to access, correct and erase ([GDPR, LGPD and AI agents](/en/blog/security-and-gdpr-for-customer-memory-in-ai)). An AI agent that reads a customer's memory processes personal data, and the company that publishes it is the controller, whoever the agent's vendor is.

## Emirates: the central bank's note

The CBUAE guidance note of February 23, 2026 is not law; it is the supervisory expectation financial institutions will be measured against ([Zawya](https://www.zawya.com/en/business/banking-and-insurance/uae-central-bank-issues-guidance-for-responsible-use-of-ai-in-finance-fp1n3i0c)). As read by Hadef & Partners, it asks that "customers should be informed when they are interacting with AI systems", that high-impact decisions have "meaningful human oversight" and a review path, that the institution keep "a comprehensive AI inventory capturing model purpose, risk classification and key metadata", and that "institutions remain accountable for third-party AI systems", with audit and information rights "secured contractually" ([Hadef & Partners](https://hadefpartners.com/news-insights/insights/ai-in-banking-finance-in-the-uae-2026-legal-regulatory-considerations/)). Two months later, the same regulator took WhatsApp away from the banks ([what that did to customer memory](/en/blog/the-uae-central-bank-took-whatsapp-away-from-its-banks)).

## What the three ask together

Read side by side, the three texts converge on five practical duties for anyone running AI agents that talk to customers, especially when the agents come from different vendors:

| Duty | EU (Article 50) | Brazil (LGPD today; PL 2338 pending) | Emirates (CBUAE note) | What the company needs to keep |
|---|---|---|---|---|
| Say it is AI, at first contact | Required | Transparency in the bill; LGPD requires informing about processing | Expected | The time and text of the introduction, per conversation and channel |
| On whose behalf the agent speaks | Implicit in transparency | Controller identified | The institution answers for the third party | The agent, the vendor and the version that handled each conversation |
| A path to a person | Not in Article 50 | Right to contest in the bill | Human oversight in high-impact decisions | The handoff, when it happened and whether the destination read the context |
| Explain and contest a decision | Not in Article 50 | Right to explanation in the bill; access and correction in the LGPD | Explanation where the decision is AI-driven | What the agent read, called and stated before deciding |
| Answer for the vendor | Provider and deployer, each with their own duties | The controller answers for the processor | Audit rights in the contract | The receipt of every read, per vendor, exportable |

The right-hand column is the same on every row: the record of the conversation, turn by turn. Not the transcript alone. What the agent was, what it read, what it stated, what it did and whom it handed off to.

## What we don't know

- How the EU's national authorities will enforce Article 50 in practice, and what the first fine will be. As of the reading date, we found no published decision.
- The final text of PL 2338. What the Chamber approves may change the risk classification of service agents and the rights of the affected person.
- The original text of the CBUAE note: we worked from the Zawya summary and the Hadef & Partners analysis, which agree.
- How Article 50 treats a voice agent that introduces itself as AI once and is interrupted before it finishes. The texts we read do not address that case.

## How Niadra solves it

Niadra does not make the agent compliant: saying it is AI, offering a person and deciding the action belong to the agent and the company. What Niadra does is keep the proof. The [turn record](/en/produtos/registro) notes what each agent, from each vendor, read from the memory, which tools it called and what they returned, what it showed, what it stated and what it decided, with the prompt and the model pinned by version; the content can stay in your storage, with Niadra keeping the pointer and the hash, and a real conversation can be replayed in your CI with the memory of the time. Every read of the memory, including a refused one, leaves a receipt chained by SHA-256, with a daily root in write-once storage and delivery to your SIEM, and access is denied by default and granted by purpose ([Console](/en/produtos/console)). Deletion comes with a receipt, and each customer's file leaves in an open format, whenever you ask.

That is what the right-hand column of the table asks for, for every vendor at once, because the memory sits outside all of them. In the [benchmark of September 30, 2026](/en/benchmark), no sensitive data was delivered to a conversation that had not proven who it was, with the per-conversation verification the company's policy requires. The post on [GDPR, LGPD and AI agents](/en/blog/security-and-gdpr-for-customer-memory-in-ai) lists, standard by standard, what the security team will ask for.

## Frequently asked questions

### Does a Brazilian company's WhatsApp agent have to say it is AI?

If it serves people in the European Union, yes, under Article 50, since August 2, 2026. In Brazil, the LGPD requires informing about data processing, and PL 2338, if approved as it left the Senate, will bring a right to transparency. The recommended practice is the same everywhere: say it, at first contact, and keep the time you said it.

### Does the turn record replace the recording of the conversation?

No. The recording says what was said; the turn record says what the agent read, called, stated and decided in each reply, and with which version. It is what lets you explain a decision, not only replay it.

### Who answers when the agent belongs to a vendor?

In the EU, provider and deployer have their own duties; in Brazil, the company serving the customer is the controller and answers for the processor; in the Emirates, the note says the institution remains accountable for the third party. In every case, the company needs the record even when the agent is not its own.
