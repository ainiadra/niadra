---
id: seguranca-lgpd
lang: en
slug: security-and-gdpr-for-customer-memory-in-ai
title: GDPR, LGPD and AI agents: what your security team will ask
description: What to prove before AI agents read customer data: the DPA, encryption, keys held in HSMs, purpose-based access, audit trails and data subject rights.
order: 4
kind: guide
date: 2026-09-21T21:49:00-03:00
updated: 2026-10-01T10:00:00-03:00
category: Security
icon: shield
cover: GDPR and LGPD for AI agents
keywords: GDPR and AI agents, how to use AI with customer data under GDPR, controller vs processor for AI vendors, data processing agreement for AI, FIPS 140-3 HSM key management, right to erasure in AI memory, AI vendor security review, LGPD compliance for AI
---

To let AI agents read customer data under the GDPR and the LGPD, a company has to prove six things: who is the controller and who is the processor, how the data is encrypted and where the keys live, what each agent may read, who read what, what reaches the AI model, and how data subjects exercise their rights. When agents from several vendors read the same customer memory, every answer has to hold for all of them at once. The security team at a bank, an insurer or a healthcare operator will ask for each standard by name, with evidence.

## Who is the controller, who is the processor, and what must the DPA say?

The company serving the customer is the controller: it decides why the data is processed. Anyone who processes the data on its behalf is a processor ([GDPR, Art. 4(7) and (8)](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679#art_4)). The LGPD uses the same two roles, under the names controlador and operador ([Art. 5, VI and VII](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art5), official text in Portuguese).

With agents from three vendors, the company has several processors touching the same customer. The memory layer that sits above them is a processor too. A processor acts on the controller's instructions ([LGPD, Art. 39](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art39)), and the data processing agreement (DPA) is where those instructions are written down. [Article 28 of the GDPR](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679#art_28) sets out what that contract covers:

- processing only on documented instructions;
- the providers that process data on the vendor's behalf named in the contract, and no new one without written authorization;
- security measures, and help with data subject requests;
- breach notice to the controller without undue delay, so it can meet its own notification duties ([GDPR, Art. 33](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679#art_33); [LGPD, Art. 48](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art48));
- deletion or return of the data when the contract ends;
- access to the audits that demonstrate compliance.

## How is the data encrypted, and who holds the keys?

[Article 32 of the GDPR](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679#art_32) names encryption among the security measures. [Article 46 of the LGPD](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art46) requires protection against unauthorized access from the design stage onward. The standards, by name:

- **At rest:** [AES-256-GCM](https://csrc.nist.gov/pubs/sp/800/38/d/final). GCM authenticates the data, so any tampering makes decryption fail.
- **In transit:** [TLS 1.3](https://www.rfc-editor.org/rfc/rfc8446).
- **Field level:** personal data gets a second cipher, which protects it inside the database itself.
- **One key per company:** if a bug mixes records from two companies, decryption fails instead of leaking.

The strength of encryption depends on who controls the keys. Master keys belong in HSMs validated to [FIPS 140-3](https://csrc.nist.gov/pubs/fips/140-3/final), which anyone can check through the [NIST Cryptographic Module Validation Program](https://csrc.nist.gov/projects/cryptographic-module-validation-program). The standard defines four increasing security levels, and each validation states the level of the module. Three answers matter: the master key never leaves the HSM, whoever uses a key never administers it, and rotation is automatic.

## Who can read what, and how do you prove who read it?

Access starts denied. Each agent has its own credential, and its permissions follow its purpose. That rule applies purpose limitation and data minimisation under the GDPR ([Art. 5(1)(b) and (c)](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679#art_5)) and the purpose and necessity principles of the LGPD ([Art. 6, I and III](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art6)). Health data is a special category ([GDPR, Art. 9](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679#art_9); [LGPD, Art. 5, II](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art5)). The billing agent should never see it.

The rule covers the context an agent receives before it replies and every search it runs through the history. The company's team needs to simulate what each agent sees before granting access, and to cut off a vendor on the spot.

Every read leaves a record: which agent read, what, why and when. Records are chained by [SHA-256](https://csrc.nist.gov/pubs/fips/180-4/upd1/final) hash, so any alteration breaks the chain. A copy streams to the company's SIEM in real time. That record is how the controller demonstrates compliance ([GDPR, Art. 5(2)](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679#art_5); [LGPD, Art. 6, X](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art6)). An excerpt from a read log:

- **2:07 pm**, voice agent from vendor B: got the voice context.
- **2:05 pm**, in-app agent: got the full context.
- **2:02 pm**, WhatsApp agent from vendor A: got the chat context.
- **9:31 am**, billing agent, internal: denied, because billing does not see technical open items.

The denied line is the proof that the purpose rule works.

## What reaches Niadra's AI, and where does the data live?

The contract has to guarantee three things about the AI model:

- **Masking:** email addresses, phone numbers, card numbers, IBANs and postal addresses are masked before Niadra's AI reads the conversations. Sensitive data reaches the agent only after the verification the policy requires.
- **No training:** the data never trains AI models, neither Niadra's nor those of the providers Niadra contracts.
- **No sale:** the data is never sold. Only the agents the company authorizes and the providers named in the contract read it.

Training a model on support conversations would be a new purpose. Both laws bar further processing that is incompatible with the original purpose ([GDPR, Art. 5(1)(b)](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679#art_5); [LGPD, Art. 6, I](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art6)).

The data stays in a single region, stated in the contract, and anything that leaves it has to be written into the contract. At Niadra, the only thing that leaves is already masked text, for the AI to read, with no retention and no training, with the provider named in the contract. International transfers have their own rules under both laws ([GDPR, Art. 44](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679#art_44); [LGPD, Art. 33](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art33)). Certifications have owners too. [ISO 27001](https://www.iso.org/standard/27001), [SOC 2](https://www.aicpa-cima.com/topic/audit-assurance/audit-and-assurance-greater-than-soc-2) and [PCI DSS](https://www.pcisecuritystandards.org/standards/pci-dss/) can belong to the vendor or to the cloud it runs on. The security team should ask who holds each one.

## How do data subjects exercise their rights?

Data subjects have the right to access, rectification, portability and erasure ([GDPR, Arts. 15 to 20](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679#art_15); [LGPD, Art. 18](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art18)). The processor helps the controller answer each request ([GDPR, Art. 28(3)(e)](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679#art_28)). That is why these rights need to exist as an API.

In a customer memory, erasure is the hardest right to honor. Each conversation produces facts, open items, context and a search index. A serious answer walks through everything derived from the data and issues a receipt of what was erased. From then on, no agent receives that data in its context or finds it in a search.

At Niadra, the company sets the retention of the event archive, the review samples and the delivery log ([GDPR, Art. 5(1)(e)](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679#art_5); [LGPD, Art. 16](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art16)). When the period ends, the data is erased for real, and backups expire within 35 days.

## What goes into the security review?

Every answer should arrive with its evidence.

| Security team question | Answer | Evidence |
|---|---|---|
| Who is the controller and who is the processor? | The company controls; the memory processes on instruction | Signed DPA, with the providers named |
| Where do the keys live? | Master key in a FIPS 140-3 Level 3 HSM, with an exclusive data key per company | Security questionnaire and threat model |
| What does each agent see? | What its purpose allows | Per-agent simulation before access is granted |
| Who read this customer's data? | A read log chained by SHA-256 | Real-time export to the SIEM |
| How are the defenses tested? | Penetration tests by an independent firm | Report under a confidentiality agreement |
| How do we erase a customer? | Erasure across everything derived from the data | Erasure receipt |

## How Niadra solves it

Niadra is the shared memory for all of a company's AI agents, customer-facing and internal, across every channel and vendor. It acts as the processor, with a signed DPA. Its [six security layers](/en/recursos#seguranca) cover AES-256-GCM and TLS 1.3, a master key in FIPS 140-3 Level 3 HSMs, purpose-based access, a read log chained by SHA-256, [GDPR and LGPD by design](/en/recursos#privacidade), and cloud infrastructure certified to ISO 27001, SOC 2 and PCI DSS. The data never trains models, and email addresses, phone numbers, card numbers, IBANs and postal addresses are masked before Niadra's AI reads the conversations.

In the [Console](/en/produtos/console), the company's team cuts off a vendor's access on the spot, simulates what each agent sees and erases data with a receipt. Niadra runs all the infrastructure, and the company's team connects the SDK. On the [Regulated plan](/en/preco), the company gets a dedicated environment with its own servers, database and keys, an availability SLA in the contract and a [security review](/en/enterprise) with its team.

This article is for information only and is not legal advice.

Since August 2, 2026, Article 50 of the AI Act requires the agent to say it is AI in the European Union; what it, Brazil's PL 2338 and the UAE central bank's note ask together is in [what 2026 regulation asks of an AI agent that talks to customers](/en/blog/what-2026-regulation-asks-of-an-ai-agent-that-talks-to-customers). The [turn record](/en/produtos/registro) keeps what each agent read, stated and decided, and the [Console](/en/produtos/console) shows the receipt of every read.

## Frequently asked questions

### Does the GDPR allow AI agents to read customer data?

The GDPR allows it, as long as the processing has a lawful basis ([Art. 6](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679#art_6)), a defined purpose and appropriate security measures ([Art. 32](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679#art_32)). In practice, compliance teams require a contract with each processor, purpose-based access and a record of every read.

### Is encryption enough to comply with the GDPR and the LGPD?

Encryption alone is not enough. [Article 32 of the GDPR](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679#art_32) pairs it with ongoing confidentiality, the ability to restore access and regular testing of the measures. Both laws also require a defined purpose, minimal access and support for data subject rights.

### How do you erase a customer's data from every AI agent?

The controller receives the request and passes the erasure on to each recipient of the data ([GDPR, Art. 19](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679#art_19); [LGPD, Art. 18, §6](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art18)). In a shared memory, erasure reaches the original conversation and everything derived from it, with a receipt. Copies that each vendor keeps on its own fall under that vendor's contract.

### Who is liable if an agent vendor leaks data?

Under the GDPR, a processor is liable when it breaches the obligations aimed at processors or acts outside the controller's lawful instructions ([Art. 82](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679#art_82)). The LGPD makes the processor jointly liable in similar situations ([Art. 42](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art42)).
