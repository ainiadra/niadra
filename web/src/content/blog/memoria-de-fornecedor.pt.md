---
id: memoria-de-fornecedor
lang: pt
slug: twilio-servicenow-aws-e-google-lancaram-memoria-para-agentes-de-ia
title: Twilio, ServiceNow, AWS e Google lançaram memória para agentes de IA. Cada uma fica dentro de quem a lançou
description: Em 2026, a memória para agentes virou produto de plataforma. O que cada lançamento documenta, o que não documenta, e a pergunta que sobra para a empresa que roda agentes de voz, WhatsApp e app em fornecedores diferentes.
order: 1
kind: news
date: 2026-10-01T09:00:00-03:00
category: Notícias
icon: layers
cover: Quatro memórias, uma por fornecedor
keywords: Twilio Conversation Memory, ServiceNow Context Engine, AgentCore Memory, Vertex AI Memory Bank, memória para agentes de IA de vários fornecedores, camada de contexto para agentes, memória de agente dentro da plataforma, lock-in de memória de agente de IA
---

Entre dezembro de 2025 e agosto de 2026, quatro plataformas que a sua empresa provavelmente já usa passaram a vender memória para agentes de IA. A Twilio tornou a Conversation Memory disponível a todos em 6 de maio de 2026 ([anúncios do SIGNAL 2026](https://www.twilio.com/en-us/blog/products/signal-2026-product-announcements)). A ServiceNow apresentou o Context Engine em 9 de abril ([comunicado](https://newsroom.servicenow.com/press-releases/details/2026/ServiceNow-moves-beyond-the-sidecar-AI-era-giving-customers-a-complete-AI-native-experience-across-all-products-and-packages/default.aspx)). O Memory Bank do Vertex AI, do Google, ficou disponível a todos em 16 de dezembro de 2025 ([notas de versão](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/release-notes)). O AgentCore Memory, da AWS, ganhou em agosto a capacidade de guardar eventos de sistema ([notas de versão](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/release-notes.html)). Todas as quatro guardam a memória onde a própria plataforma está. Para a empresa que roda o agente de voz num fornecedor, o de WhatsApp em outro e o do app no time interno, a pergunta não é qual memória é melhor. É de quem é a memória do cliente quando os agentes são de vários.

Este post foi escrito para quem decide a arquitetura dos agentes e os contratos dos fornecedores. As fontes foram lidas em 1º de outubro de 2026, e o que cada uma não diz está marcado.

## O que cada lançamento documenta

**Twilio Conversation Memory.** O anúncio diz que o produto "cria um perfil vivo, com identidade resolvida, ligando dados do cliente ao histórico de conversas e aos traços do cliente" e que foi "construído especificamente para LLMs, para reduzir latência e uso de tokens" ([SIGNAL 2026](https://www.twilio.com/en-us/blog/products/signal-2026-product-announcements)). A [documentação](https://www.twilio.com/docs/conversation-memory) descreve dois tipos de memória, a conversacional (comportamentos, preferências, humor, pontos da conversa) e a factual (nome, contatos, nível da conta, identificadores), uma API de recall com busca semântica e lexical, e uma resolução de identidade que decide quando criar, acrescentar ou unir perfis. As memórias nascem das conversas que passam pelos canais da Twilio (SMS, WhatsApp, e-mail e voz), depois de passarem pelo Conversation Orchestrator. O preço é por uso: US$ 0,0028 por mil caracteres gerados, US$ 0,007 por recall e US$ 0,005 por perfil usado no dia ([preço](https://www.twilio.com/en-us/products/conversational-ai/pricing)). O mesmo evento lançou o Agent Connect, um kit de código aberto que "permite conectar agentes construídos em qualquer LLM ou framework diretamente à infraestrutura da Twilio".

**ServiceNow Context Engine.** O comunicado de 9 de abril diz que o Context Engine "conecta as relações, a política e o histórico de decisões por trás de cada decisão de um agente de IA" e que "dá à IA e aos fluxos da ServiceNow o contexto para perceber o que acontece na empresa"; no lançamento, ele estava "disponível em prévia para clientes selecionados" ([comunicado](https://newsroom.servicenow.com/press-releases/details/2026/ServiceNow-moves-beyond-the-sidecar-AI-era-giving-customers-a-complete-AI-native-experience-across-all-products-and-packages/default.aspx)). Em 6 de maio, no Knowledge 2026, a empresa o descreveu como uma camada semântica que integra o CMDB, os dados de fluxo, as análises e sistemas de terceiros ([comunicado](https://newsroom.servicenow.com/press-releases/details/2026/ServiceNow-launches-the-real-time-data-foundation-that-puts-autonomous-AI-to-work-across-the-enterprise/default.aspx)). A Forrester resumiu a tese do evento numa frase: "o contexto alimenta a inteligência e os agentes de IA movem a ação", com a ServiceNow se posicionando como "a camada operacional autônoma da empresa" ([Forrester, 14/05/2026](https://www.forrester.com/blogs/context-is-the-new-competitive-edge-takeaways-from-servicenow-knowledge-2026/)).

**Google Vertex AI Memory Bank.** As notas de versão de 16 de dezembro de 2025 dizem que "Sessions e Memory Bank do Vertex AI Agent Engine estão disponíveis a todos" e que a cobrança começou em 28 de janeiro de 2026 ([notas de versão](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/release-notes)). A memória é gerada das conversas do usuário com o agente que roda no Agent Engine.

**AWS AgentCore Memory.** Em abril de 2026, o serviço ganhou filtros por metadados estruturados na memória de longo prazo; em agosto, a API `CreateEvent` passou a aceitar um payload `json` para "dados não conversacionais, como eventos de comportamento, logs de atividade e eventos de sistema", extraídos para a memória de longo prazo ([notas de versão](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/release-notes.html)). É a memória dos agentes que rodam no AgentCore.

**Oracle.** A Oracle anunciou o AI Database 26ai em 24 de março de 2026, com um "Unified Memory Core" descrito por analistas como "uma memória persistente e com estado para agentes de IA dentro do motor do banco" ([Futurum, 25/03/2026](https://futurumgroup.com/insights/oracle-positions-ai-database-26ai-to-lead-1-2-trillion-market-by-bridging-the-agentic-reasoning-gap/)). A memória mora no banco da Oracle.

## O padrão: a memória fica onde a plataforma está

Lidos juntos, os cinco lançamentos têm o mesmo desenho. A memória nasce do que passa pela plataforma e serve aos agentes que rodam nela:

| Plataforma | De onde a memória nasce | Quem a lê, pela documentação | O que a documentação não diz |
|---|---|---|---|
| Twilio | Conversas nos canais da Twilio | Agentes e atendentes humanos nas conversas da Twilio; agentes de qualquer framework ligados pela infraestrutura da Twilio (Agent Connect) | Se uma ligação atendida fora da Twilio, ou um evento do ERP, entra na memória |
| ServiceNow | CMDB, fluxos, análises, sistemas integrados | A IA e os fluxos da ServiceNow | Se um agente de outro fornecedor consulta o Context Engine; a disponibilidade geral |
| Google | Conversas com agentes no Agent Engine | Agentes no Agent Engine | Entrada de conversas de agentes fora do Google Cloud |
| AWS | Eventos e conversas enviados ao AgentCore | Agentes no AgentCore | Entrada de conversas de agentes fora da AWS |
| Oracle | Dados e conversas no banco | Agentes que acessam o banco | Quantos frameworks de terceiros a usam (a Futurum sugere observar) |

Nenhuma dessas empresas está errada em fazer isso. A memória dentro da plataforma é o produto natural de quem já tem a conversa ou o dado. O problema aparece do lado de quem compra: a empresa média de atendimento e cobrança não roda tudo numa plataforma só.

## O que isso significa para quem tem agentes de vários fornecedores

Pense numa operação comum: voz numa plataforma de voz, WhatsApp pela Twilio, o app com um agente do time interno, o CRM na Salesforce ou na ServiceNow, o ERP de outro fornecedor. Com os lançamentos de 2026, essa empresa pode ter quatro memórias do mesmo cliente, cada uma completa dentro do próprio muro e cega para o resto:

1. **O agente de voz não lê a memória da Twilio**, a menos que a ligação passe pela Twilio. A reclamação feita no WhatsApp às 14h02 não chega à ligação das 14h07 atendida em outro fornecedor.
2. **O Context Engine conhece o ticket e o fluxo**, não a conversa que aconteceu no WhatsApp de outro fornecedor.
3. **A troca de fornecedor leva a memória junto.** Se o contrato de voz muda de plataforma, a memória das ligações fica na plataforma antiga.
4. **Ninguém mede o conjunto.** Cada plataforma mede o próprio agente contra as próprias regras. Nenhuma diz se o agente do concorrente aproveitou o que a outra sabia ([como saber se o agente usou o contexto](/blog/como-saber-se-o-agente-de-ia-usou-o-contexto)).

O preço por recall mostra o mesmo desenho de outro ângulo. A Twilio cobra US$ 0,007 por recall ([preço](https://www.twilio.com/en-us/products/conversational-ai/pricing)): numa conversa em que o agente lê a memória a cada troca, dez trocas custam US$ 0,07 só de recall, antes do modelo e da mensagem. É a unidade certa para uma memória que vive dentro do canal cobrado por mensagem. Não é a unidade certa para uma memória que vários agentes, de vários fornecedores, leem na mesma conversa.

O mercado já deu nome ao problema. A a16z escreveu em 10 de março de 2026 que agentes de dados falham por falta de contexto, e que a camada de contexto moderna inclui "entidades canônicas e resolução de identidade", acessível "por API ou MCP", como "um corpo vivo e em constante evolução" ([Your Data Agents Need Context](https://a16z.com/your-data-agents-need-context/)). Uma pesquisa da VentureBeat com 101 empresas de mais de cem funcionários, em junho de 2026, achou que 57% atribuíram uma resposta confiante e errada de um agente a contexto de negócio ausente ou inconsistente, 31% mais de uma vez; 25% tinham uma camada de contexto governada em produção, 34% estavam construindo e 41% não tinham começado ([VentureBeat, 10/07/2026](https://venturebeat.com/data/57-of-enterprises-have-watched-ai-agents-be-confidently-wrong-the-fix-is-an-agentic-context-layer-but-who-has-one)).

## O que conferir antes de assinar

Quatro perguntas para o fornecedor, com a resposta que importa:

1. **A memória recebe eventos que não passaram por você?** Uma ligação de outro fornecedor, um evento do ERP, uma mensagem de um canal que não é seu. Se não recebe, é a memória do seu canal, não a memória do meu cliente.
2. **Um agente de outro fornecedor lê a memória, com credencial própria e permissão por finalidade?** Sem isso, o cliente que muda de canal começa do zero.
3. **Quando eu sair, a memória sai comigo, em formato aberto?** Exportação em formato aberto quando você pedir, não um dump no fim do contrato.
4. **Quem mede se cada agente usou o que recebeu?** Por agente e por fornecedor, com o comprovante de cada entrega.

## O que não sabemos

- Se a Twilio vai aceitar conversas de canais fora dela na Conversation Memory. A documentação lida em 1º de outubro de 2026 não descreve essa entrada; não descreve também que a recusa.
- Quando o Context Engine da ServiceNow sai da prévia e se agentes de terceiros poderão consultá-lo. O comunicado de abril diz que "os detalhes de disponibilidade serão compartilhados depois".
- Quanto do Unified Memory Core da Oracle é usado por frameworks de terceiros. A Futurum recomenda acompanhar a adoção; não achamos número público.
- A pesquisa da VentureBeat tem 101 respondentes. É um sinal, não um censo.

## Como a Niadra resolve

A Niadra é a camada de memória que fica fora de todas essas plataformas. Ela recebe os eventos de cada canal, plataforma e sistema (a ligação na plataforma de voz, a mensagem pela Twilio ou pela Cloud API, a sessão do app, o ticket, a fatura do ERP), reconhece que falam da mesma pessoa antes de guardar, e entrega a qualquer agente, de qualquer fornecedor, o contexto da tarefa dele antes da primeira palavra. Cada fornecedor entra com a própria credencial e lê só o que a finalidade dele permite; nenhum lê a memória do outro, e a troca de um deles não apaga nada ([como agentes de fornecedores diferentes compartilham o histórico](/blog/como-agentes-de-fornecedores-diferentes-compartilham-o-historico)).

Os adaptadores de [Twilio](/integracoes/twilio), [WhatsApp Cloud API](/integracoes/whatsapp), [LiveKit](/integracoes/livekit), [Vapi](/integracoes/vapi), [Retell](/integracoes/retell), [ElevenLabs](/integracoes/elevenlabs), [LangGraph](/integracoes/langgraph) e [OpenAI Agents](/integracoes/openai-agents) ligam à mesma memória os agentes que você já opera, e os sistemas entram por webhook, lote ou arquivo ([Sistemas](/produtos/sistemas)). No [benchmark de 30/09/2026](/benchmark), com o mesmo agente e o mesmo juiz para todo sistema, a Niadra acertou 98,8% dos 338 casos de continuidade entre canais, e nenhum dado sensível foi entregue a uma conversa que não tinha provado quem era. O preço é por conversa ou tarefa, de US$ 2 a 3 a cada mil, com todas as leituras e buscas da conversa incluídas ([Preço](/preco)).

## Perguntas frequentes

### Se eu já uso a Twilio para WhatsApp, preciso de outra memória?

Depende de onde estão os seus outros agentes. Se toda conversa da sua empresa passa pela Twilio, a Conversation Memory cobre o que ela vê. Se a voz está em outro fornecedor, ou se o ERP e o CRM precisam alimentar o que o agente sabe, a memória precisa ficar fora da Twilio, e a Twilio vira uma das fontes.

### A memória da plataforma e uma memória neutra convivem?

Convivem. A memória da plataforma continua servindo aos agentes dela; a camada neutra recebe os eventos de todas e serve ao conjunto. O post sobre [memória dentro da plataforma ou memória neutra](/blog/memoria-dentro-da-plataforma-de-agentes-ou-memoria-neutra) diz onde termina uma e começa a outra.

### O que muda no contrato com o fornecedor de agente?

Duas cláusulas: o agente lê e alimenta a memória da empresa pelo SDK, com credencial própria; e a memória, com o histórico inteiro, fica com a empresa em formato aberto quando o contrato acabar.
