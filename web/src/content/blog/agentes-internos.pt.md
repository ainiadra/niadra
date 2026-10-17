---
id: agentes-internos
lang: pt
slug: memoria-para-agentes-de-ia-internos
title: Memória para agentes de IA internos: CRM, ERP, tickets, cobrança e pedidos
description: Como agentes de IA que trabalham por dentro da empresa, no CRM, no ERP, nos tickets, na cobrança e nos pedidos, compartilham a mesma memória com os agentes de atendimento.
order: 9
kind: explainer
date: 2026-09-22T10:10:00-03:00
updated: 2026-10-01T10:00:00-03:00
category: Arquitetura
icon: hub
cover: Os agentes que trabalham por dentro
keywords: memória para agentes de IA internos, agentes de IA no ERP, agentes de IA no CRM, agente de IA de cobrança, agente de IA para tickets, memória compartilhada entre agentes de IA, contexto para agentes de IA de back office, como integrar agentes de IA com ERP e CRM
---

Um agente de IA interno trabalha por dentro da empresa, sem falar com o cliente: lança um crédito na cobrança, muda uma entrega no ERP, classifica um ticket. Para agir sem erro, ele precisa da mesma memória que os agentes de atendimento usam, e os agentes de atendimento precisam saber o que ele fez. Uma memória compartilhada liga cada conversa, cada evento de sistema (pedido, ticket, fatura) e cada ação de agente ao cliente certo. Os sistemas continuam sendo a fonte oficial: a memória guarda o que os agentes precisam lembrar e aponta para a origem.

## O que é um agente de IA interno?

É um agente que lê e escreve nos sistemas da empresa, em vez de conversar com o cliente. Alguns exemplos:

- **Cobrança:** analisa uma contestação e lança um crédito na fatura.
- **Pedidos:** troca o endereço de entrega ou reagenda uma instalação no ERP.
- **Tickets:** classifica, prioriza e encaminha cada chamado no help desk.
- **Copiloto do atendente:** prepara o caso antes de o atendente humano atender.
- **Financeiro e operações:** concilia pagamentos e acha pedidos parados.

Cada um trabalha num sistema diferente, e muitas vezes vem de um fornecedor diferente do agente que atende o cliente.

## Por que ele precisa da mesma memória dos agentes de atendimento?

Porque os dois lados agem sobre o mesmo cliente. Às 14h02, a Marina avisa no WhatsApp que o técnico não apareceu. Às 14h05, ela contesta a fatura de agosto pelo app. Às 14h06, o agente de cobrança analisa a contestação e lança um crédito de R$ 40 no ERP. Às 14h07, ela liga.

Sem memória compartilhada, cada agente erra de um jeito:

- **O agente interno age sem saber o que foi dito no atendimento.** Ele não sabe que a visita técnica falhou pela segunda vez no ano, e trata a contestação como um caso isolado.
- **O agente de atendimento promete o que outro agente já fez.** O agente de voz não vê o crédito lançado às 14h06 e oferece outro, ou diz que a contestação ainda está em análise.

Com a memória compartilhada, o agente de voz atende sabendo das duas coisas: a visita que não aconteceu e o crédito que já foi lançado. É o mesmo problema da [passagem entre canais](/blog/como-manter-o-contexto-da-ligacao-para-o-whatsapp), agora entre o atendimento e o que acontece dentro dos sistemas.

## Como CRM, ERP, help desk, cobrança e pedidos entram na memória?

Por três caminhos, e nenhum exige trocar de sistema.

1. **Os eventos que os sistemas já emitem.** Pedido criado, fatura contestada, ticket reaberto. O sistema manda o evento por webhook, e um mapeamento versionado o transforma no formato da memória. O evento original fica guardado como chegou, por 90 dias.
2. **A API.** Para sistemas sem webhook, os eventos chegam por integração ou em lote.
3. **Os próprios agentes.** Cada agente registra o que fez em cada sistema, com resultado e origem.

Cada sistema tem o próprio identificador do cliente: o cadastro do CRM, o contrato no ERP, o solicitante no help desk. A memória liga esses identificadores ao mesmo cliente com as mesmas regras usadas para telefone e e-mail: cada ligação tem origem e peso, e pode ser desfeita sem perder histórico ([como funciona a identidade](/blog/como-reconhecer-o-mesmo-cliente-em-todos-os-canais)).

O sistema continua sendo a fonte oficial. O ERP guarda o valor da fatura; a memória guarda que a fatura foi contestada e que o crédito foi lançado, com data, origem e o identificador do registro no ERP. Evento de sistema é dado estruturado, então entra por regras de mapeamento, sem modelo de IA no caminho.

## O que é uma ação de agente, e por que registrá-la?

Ação de agente é o registro do que um agente fez num sistema: o quê, onde, em qual registro e com qual resultado. Ela tem duas funções.

- **Fechar o que ficou em aberto.** A contestação aberta às 14h05 vira resolvida quando o agente de cobrança registra o crédito às 14h06.
- **Avisar o próximo agente.** O agente de voz recebe o crédito no contexto, com a origem ao lado, antes de atender.

Sem esse registro, uma promessa feita num canal e cumprida em outro sistema continua aparecendo como pendente, e alguém vai cumpri-la de novo.

## Como controlar o que cada agente interno pode ler?

Agente interno costuma ter acesso amplo aos sistemas, então a regra na memória é a mesma dos agentes de atendimento: credencial própria, leitura por finalidade e registro de cada leitura ([como funciona entre fornecedores](/blog/como-agentes-de-fornecedores-diferentes-compartilham-o-historico)). O agente de cobrança lê faturas e contestações, e não lê pendência técnica nem dado de saúde.

Isso importa porque a governança de agentes ainda é exceção: só 1 em cada 5 empresas diz ter governança madura para agentes de IA autônomos ([Deloitte, State of AI in the Enterprise 2026](https://www.deloitte.com/us/en/what-we-do/capabilities/applied-artificial-intelligence/content/state-of-ai-in-the-enterprise.html)).

## Como a Niadra resolve

A Niadra é a memória compartilhada de todos os agentes de IA de uma empresa: os que atendem o cliente em qualquer canal e os que trabalham por dentro, no CRM, no ERP, nos tickets, na cobrança e nos pedidos. Os agentes usam três chamadas do SDK, em Python ou TypeScript: `context()` antes de agir, com o contexto do cliente para a tarefa; `search()` durante o trabalho, no histórico inteiro; e `track()` depois, para registrar a conversa ou a ação. Os sistemas entram por webhook ou pela API, e o identificador de cada sistema fica ligado ao cliente certo.

Os dados ficam cifrados com AES-256-GCM em repouso e TLS 1.3 em trânsito, com chave exclusiva por empresa, protegida por uma chave-mestra em HSM FIPS 140-3 nível 3, e cada leitura entra numa auditoria encadeada por SHA-256. A Niadra opera toda a infraestrutura. O preço é por conversa ou tarefa, de US$ 2 a 3 a cada mil, e os eventos dos sistemas não são cobrados à parte ([Preço](/preco)). O plano Regulado tem ambiente dedicado ([Enterprise](/enterprise)).

Os sistemas entram por webhook, lote ou arquivo ([Sistemas](/produtos/sistemas)), e o [nó do n8n](/integracoes/n8n) liga os fluxos que já existem. A ação do agente interno chega ao agente de atendimento como declarada, confirmada pelo evento do sistema ou divergente; é o teste 9 de [dez testes antes de pôr agentes de IA de vários fornecedores em produção](/blog/dez-testes-antes-de-por-agentes-de-ia-de-varios-fornecedores-em-producao).

## Perguntas frequentes

### A memória substitui o CRM ou o ERP?

Não. O CRM, o ERP e o help desk continuam sendo a fonte oficial dos dados. A memória liga pedidos, tickets e faturas ao cliente certo, guarda o que os agentes precisam lembrar e aponta para o registro de origem.

### Preciso de um conector para cada sistema?

Não. Os sistemas mandam os eventos que já emitem por webhook, ou pela API quando não têm webhook, e um mapeamento transforma cada evento. Os agentes também registram o que fizeram. Para decidir o que construir em casa e o que usar pronto, veja [construir ou usar uma plataforma](/blog/construir-memoria-de-agentes-em-casa-ou-usar-plataforma).

### Como uma tarefa de agente interno é cobrada?

Na Niadra, cada tarefa de agente interno conta como uma conversa: as leituras e buscas dentro dela contam uma vez só. Os eventos que os sistemas mandam não são cobrados à parte.
