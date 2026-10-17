---
id: conversas-sem-fim
lang: pt
slug: conversas-que-ninguem-encerra-quando-a-memoria-deve-aprender
title: Conversas que ninguém encerra: quando a memória do agente de IA deve aprender
description: No WhatsApp, no chat do app e no assistente da equipe, quase ninguém encerra a conversa. Se a memória só aprende quando a conversa fecha, o próximo agente fica sem saber o que foi dito há dez minutos. Quando aprender, como lidar com quem volta a falar e o que fazer com a ligação que termina.
order: 6
kind: explainer
date: 2026-10-08T09:00:00-03:00
category: Arquitetura
icon: layers
cover: A memória não espera o fim
keywords: quando a memória do agente de IA aprende, conversa de WhatsApp sem fim, sessão de conversa agente de IA, memória atrasada entre agentes, inatividade da conversa, encerrar conversa no SDK, agente de IA não vê o que foi dito agora
---

A memória de um agente de IA deve aprender uma conversa poucos minutos depois de ela ficar em silêncio, sem esperar que alguém a encerre. No WhatsApp, no chat do app e no assistente que a equipe usa, a conversa quase nunca termina com um "tchau": a pessoa para de responder. Se a memória só aprende no encerramento formal, ela aprende tarde, e o agente seguinte responde sem saber o que foi dito há dez minutos.

Na Niadra, uma conversa que ninguém encerrou vira memória depois de 3 minutos sem turnos. Se a pessoa voltar a falar dentro da janela do canal, a conversa reabre como a mesma, e é aprendida de novo com o que veio depois.

## Por que esperar o fim atrasa tudo?

Imagine o advogado que, às 15h, conversa com o assistente do escritório: "Anote: neste processo, não propor acordo, a diretoria do cliente decidiu levar a julgamento". Ele fecha o notebook. Às 15h10, o cliente manda mensagem no WhatsApp perguntando se vai haver acordo.

Se a memória espera a conversa do advogado terminar, e a regra do canal é encerrar por inatividade depois de meia hora, o agente de WhatsApp responde às 15h10 sem a instrução. Pior: ele pode dizer que um acordo está sendo avaliado. A informação existia, foi dita à empresa, e chegou tarde.

O mesmo vale para o lado de dentro. O agente de cobrança no ERP não fica sabendo que o cliente contestou a fatura no chat até a conversa "acabar".

## Por que não aprender a cada mensagem?

Aprender a cada turno parece mais rápido, mas tem dois custos:

- **Leitura incompleta.** Uma frase sozinha muitas vezes não diz nada. "Pode ser na sexta" só faz sentido com a pergunta anterior. Aprender a cada turno gera fatos pela metade, que depois precisam ser corrigidos.
- **Custo.** Cada passagem de extração custa. Fazer uma por mensagem multiplica o custo de uma conversa longa sem melhorar o resultado.

O silêncio curto é um bom sinal de que um trecho da conversa terminou: a pessoa disse o que queria e está esperando, ou foi fazer outra coisa.

## E quando a pessoa volta a falar?

A conversa reabre como a mesma, desde que a volta caia dentro da janela de inatividade do canal (cerca de 20 minutos no WhatsApp, mais no e-mail, onde uma resposta duas horas depois ainda é o mesmo assunto). A memória aprende de novo com o trecho novo, e a versão nova substitui a anterior. Uma volta muito depois abre uma conversa nova, que entra ao lado da antiga.

Isso mantém duas coisas certas ao mesmo tempo: o que foi dito chega rápido aos outros agentes, e o assunto continua sendo um só para quem lê o histórico.

## E a ligação que termina?

Em voz, o fim é claro: a ligação desliga. O agente manda o fim da conversa com a hora real, e a memória aprende na hora, sem esperar silêncio. A hora do fim também decide de que lado cai uma transcrição que chega atrasada: antes do fim, ela entra na mesma conversa; depois, abre outra.

## Como saber se já virou memória?

Pergunte. A Niadra responde, para cada conversa ou tarefa, se ela ainda está aberta, se está sendo processada ou se já está pronta. Quando está pronta, uma leitura de contexto feita naquele instante já vê o que foi dito. Em testes e em fluxos críticos, confira o estado antes de perguntar ao próximo agente.

## Como a Niadra resolve

Na Niadra, cada conversa é dividida em trechos de atividade. Um trecho que ninguém encerrou vira memória depois de 3 minutos em silêncio, uma ligação vira memória quando desliga, e uma tarefa de agente interno fecha quando termina. O que foi aprendido entra no [contexto](/produtos/contexto) de todo agente que atende o mesmo cliente ou objeto, e o [histórico](/produtos/historico) guarda cada versão. A documentação descreve [os eventos e as sessões](https://docs.niadra.com/concepts/events), com as janelas de cada canal e o estado de cada conversa.

## Perguntas frequentes

### Preciso mandar o fim da conversa pelo SDK?

Não é obrigatório: a memória aprende pelo silêncio. Em voz, mande o fim ao desligar; ele é mais preciso que o silêncio e decide onde cai a transcrição atrasada.

### Aprender de novo quando a pessoa volta duplica fatos e promessas?

Não. A versão nova da conversa substitui a anterior, e uma promessa repetida é reconhecida como a mesma, como explica o post sobre [como evitar que o agente prometa e esqueça](/blog/como-evitar-que-o-agente-de-ia-prometa-e-esqueca).

### Isso aumenta o custo por conversa?

Uma conversa com pausas longas é aprendida uma vez por pausa. O preço por conversa não muda: ele é cobrado pela conversa, não pela quantidade de passagens, como mostra a página de [preço](/preco).
