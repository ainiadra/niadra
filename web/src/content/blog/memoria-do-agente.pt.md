---
id: memoria-do-agente
lang: pt
slug: memoria-de-agente-procedimentos-nao-dados-do-cliente
title: Memória de agente: o que o agente aprendeu do trabalho, nunca sobre o cliente
description: A memória do cliente é uma coisa; a memória do agente é outra: procedimentos, como as ferramentas se comportam e armadilhas, que hoje vivem no prompt e envelhecem. Como ela entra no prompt, por que recusa dado pessoal e por que nenhuma nota nasce sem revisão.
order: 4
kind: explainer
date: 2026-09-30T22:30:00-03:00
category: Agentes
icon: doc
cover: O que o agente aprendeu do trabalho
keywords: memória procedural para agentes de IA, memória de agente, notas de trabalho do agente, procedimentos no prompt do agente, agente aprende com a execução, memória de ferramentas e processos, memória de agente sem dado pessoal
---

A memória de agente é o que o agente aprendeu sobre o próprio trabalho, e não sobre o cliente: "no ERP, o crédito só aparece na fatura depois de `post_credit` e `refresh_invoice`, nessa ordem"; "a API de agendamento recusa data sem fuso"; "quando o cliente pede segunda via, o procedimento é este". É o que o time escreve no prompt à mão e esquece de atualizar. Na Niadra ela mora numa tabela própria, com política, comprovante e apagamento próprios, entra no prompt como um bloco separado do contexto do cliente, recusa qualquer dado pessoal em vez de mascará-lo, e nenhuma nota nasce de uma conversa sem uma pessoa aprovar. A [documentação](https://docs.niadra.com/concepts/agent-memory) tem o contrato inteiro; este post diz o porquê de cada regra.

## Por que separar a memória do agente da memória do cliente

Os dois tipos de memória respondem a perguntas diferentes, e misturá-los custa caro de dois jeitos.

O primeiro é a privacidade. A memória do cliente é dado pessoal: tem titular, finalidade, prazo e direito de apagamento. Um procedimento ("a segunda via sai pelo endpoint X") não tem titular. Se as duas coisas moram na mesma tabela, o apagamento de um cliente precisa varrer os procedimentos, e um procedimento que cita um cliente de exemplo vira dado pessoal sem ninguém notar. Separadas, o apagamento de um titular nunca toca a memória do agente, por construção: ela não pode conter dado pessoal.

O segundo é o cache. O contexto do cliente muda a cada cliente; o procedimento é o mesmo para todos. Se o bloco de procedimentos vem antes do contexto do cliente e tem os mesmos bytes para todo mundo, ele fica no prefixo do prompt que o provedor de IA guarda em cache, e o cliente paga uma fração do preço por ele. Por isso a ordem é fixa: as instruções do agente, depois as notas do agente, depois o contexto do cliente.

## O que é uma nota, e o que ela nunca contém

Cada nota tem um tipo: `procedure` (como fazer), `tool_note` (como uma ferramenta se comporta), `process_note` (como um processo da empresa funciona) ou `pitfall` (o que dá errado). Tem título de até 120 caracteres, corpo de até 2.000, até 8 tags em minúsculas (`invoice`, `credit`, `erp`: tipos de objeto, operações e sistemas do espaço), uma visibilidade e uma evidência.

A evidência é o `conversation_id` ou o `task_id` de onde a nota saiu. Só o id, nunca o texto. A visibilidade é `source` (só o agente dono, o padrão), `vendor` (todos os agentes do mesmo fornecedor) ou `space` (todos os agentes do espaço), e vale a mesma neutralidade da memória do cliente: o agente do fornecedor A nunca lê o que o agente do fornecedor B aprendeu, a menos que a empresa abra.

Uma nota com telefone, e-mail, documento ou nome de cliente é recusada, não mascarada: a escrita passa pelos detectores de dado pessoal do servidor de modelos, dentro da região, e por uma camada local que reconhece e-mail, telefone e dois formatos de documento sem depender dele. A recusa é `422 personal_data_in_agent_memory`, com os tipos de dado encontrados e sem o valor. Organização e lugar não contam como dado pessoal: um procedimento cita sistemas, empresas e filiais o tempo todo.

## Como ela entra no prompt

Três caminhos, todos opcionais, e o recurso vem desligado até uma pessoa ligá-lo no Console.

**O bloco.** `GET /v1/agent-memory/block` devolve as notas ativas que o agente pode ler como um texto pronto, entre `<agent_notes>` e `</agent_notes>`, aberto com "Do próprio agente (procedimentos e notas de trabalho, não dados de clientes)". As notas cujas tags casam com a view ou a tarefa vêm primeiro, depois as mais revisadas, depois as mais antigas, sempre na mesma ordem. O orçamento vai de 50 a 2.000 tokens, 300 por padrão. Cada [integração](/integracoes) recebe `agent_memory=True` em Python ou `agentMemory: true` em TypeScript e posiciona o bloco sozinha.

**As ferramentas.** `search_agent_memory` (leitura) e `remember` (escrita) entram no kit e no servidor MCP. A descrição de cada uma diz ao modelo quando não usar: "não guarda nada sobre clientes; para o histórico do cliente, use `search_customer_history`", e "nunca escreva nada sobre um cliente aqui". `remember` só é oferecida a uma chave com o escopo de escrita, e uma nota recusada volta ao modelo como erro, para ele reescrever sem o dado.

**A seção da tarefa.** Uma view de tarefa pode anexar até 200 tokens de notas com as tags dos tipos de objeto da tarefa ao fim do contexto, para quem só consegue mudar o contexto do agente, não o prompt.

## Por que nenhuma nota nasce sozinha

O que outras memórias chamam de memória procedural costuma ser um resumo da execução escrito por IA, gravado sem revisão. Na Niadra, aprender com uma conversa é um pedido explícito: `POST /v1/agent-memory/distill` recebe um `conversation_id` ou um `task_id`, lê a conversa inteira, já mascarada, pede ao modelo uma nota no esquema fixo e passa o resultado pelo redator. O que sai é uma proposta, e ninguém grava nada até uma pessoa aprovar, com ou sem edição.

Uma destilação que não rende nota diz o motivo: a conversa não tinha procedimento reaproveitável, a proposta trazia dado pessoal e foi descartada, a conversa falou só por um vínculo de identidade não confirmado, ou não há turnos. Num espaço configurado para escrita só por pessoas, até o `remember` de um agente vira proposta.

Os limites protegem o resto: um agente escreve até 20 notas por hora e guarda até o teto do espaço, 500 notas ativas por padrão. Um agente edita e aposenta só as próprias notas; toda edição gera uma versão nova, e a anterior continua legível. Nenhum modelo reordena as notas por uso recente: a ordem é por tags, versão e idade, a mesma para todos os clientes, porque é isso que mantém o bloco em cache.

## O que fica registrado

Cada leitura do bloco e cada busca deixam um comprovante na mesma cadeia dos outros, com os ids das notas entregues; cada escrita, edição, aposentadoria, destilação, aprovação e apagamento deixa um comprovante de administração. "Por que o agente fez isso?" continua respondível depois. Toda nota e toda proposta criadas emitem um aviso por webhook, com ids, tipo e origem, nunca o texto, para quem quiser revisar as notas por fora. A exportação devolve tudo para portabilidade, e o apagamento de um agente apaga todas as notas e propostas dele, em todas as versões, com comprovante.

## Como a Niadra resolve

A memória do agente é uma tabela ao lado da memória do cliente, nunca dentro dela: procedimentos, notas de ferramenta, notas de processo e armadilhas, com tags, visibilidade por agente, fornecedor ou espaço, versão e evidência por id. Entra no prompt entre as instruções e o contexto do cliente, com os mesmos bytes para todos os clientes, e cada adaptador a posiciona sozinho. Dado pessoal é recusado na escrita; aprender com uma conversa produz uma proposta, que uma pessoa aprova no Console. O post sobre [memória para agentes internos](/blog/memoria-para-agentes-de-ia-internos) mostra onde os procedimentos mais pesam: no CRM, no ERP e na cobrança.

## Perguntas frequentes

### Isso é memória procedural?

É o que a memória procedural promete, com duas diferenças: nenhuma nota é gravada sem uma pessoa aprovar, e nenhuma nota pode conter dado pessoal. A destilação propõe; o Console aprova.

### O agente pode escrever durante a conversa?

Pode, com `remember`, se a chave dele tiver o escopo de escrita e o espaço permitir escrita por agente. A nota passa pelos detectores de dado pessoal antes de entrar, e um espaço pode exigir que toda escrita de agente vire proposta.

### A memória do agente de um fornecedor vaza para outro?

Não. A visibilidade padrão é só o agente dono; `vendor` abre para os agentes do mesmo fornecedor e `space` para todos os agentes da empresa, e quem decide é a empresa, nota a nota.
