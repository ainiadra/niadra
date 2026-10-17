---
id: vou-enviar-nao-e-enviei
lang: pt
slug: quando-dar-uma-pendencia-como-resolvida
title: Quando dar uma pendência como resolvida: por que "vou enviar" não é "enviei"
description: Um agente de IA que fecha a pendência ao dizer "já estou enviando" apaga a promessa antes de ela ser cumprida. O que precisa acontecer para uma pendência fechar, quem pode fechá-la e como evitar os dois erros, o de fechar cedo e o de nunca fechar.
order: 3
kind: explainer
date: 2026-10-08T09:00:00-03:00
category: Governança
icon: check
cover: Fechar só o que foi feito
keywords: quando fechar pendência de agente de IA, agente de IA marca tarefa como feita sem fazer, agente diz que enviou e não enviou, follow up de agentes de IA, fechar promessa ao cliente, conferir entrega do agente de IA, pendência resolvida por evento do sistema
---

Uma pendência deve fechar quando o que foi prometido aconteceu: o documento chegou ao cliente, o aviso foi dado, o sistema registrou o pagamento. Ela não deve fechar quando o agente diz que vai fazer, nem quando ele diz que fez sem ter feito. "Vou enviar a cópia" mantém a pendência aberta; "segue a cópia da decisão em anexo", com o anexo, a fecha.

Parece óbvio, mas é exatamente onde agentes de IA erram, porque para um modelo de linguagem as duas frases são parecidas.

## Por que fechar cedo é pior do que não fechar?

Uma pendência aberta a mais incomoda: aparece para o agente seguinte, que pergunta ou confere. Uma pendência fechada antes da hora some: nenhum agente a vê de novo, e o cliente fica esperando algo que todos acham que já foi feito.

Os dois casos que mais vimos:

- **O agente anuncia e registra.** O cliente pergunta pela cópia da decisão, o agente responde "já estou enviando para você" e registra a ação de entrega. Não havia documento nenhum anexado. Se a ação fecha a pendência, a promessa do advogado desaparece.
- **O agente repete a promessa de outro.** "O advogado vai te mandar a cópia" é uma repetição da promessa, não o cumprimento dela.

## O que precisa acontecer para fechar?

Uma pendência fecha por três caminhos, cada um com a sua conferência:

1. **Um evento do sistema.** O documento foi anexado aos autos, o pagamento foi compensado, o pedido saiu para entrega. O sistema de registro é a fonte mais forte, e o evento fecha a pendência que esperava aquela operação.
2. **Uma ação de agente que entregou.** Um agente que fala com o cliente pode fechar uma pendência quando registra a ação e a resposta dele, na mesma conversa, entregou o que foi prometido: deu o resultado, anexou o documento, pediu o que precisava pedir. A resposta "já estou enviando" sem anexo não entrega nada.
3. **Uma pessoa ou agente dizendo que foi feito.** "Já avisei o cliente da audiência" fecha a pendência de avisar. Uma conversa sobre o mesmo assunto, sem dizer que foi feito, não fecha.

Nos três caminhos, a pendência só fecha se for a mesma: o mesmo objeto, as palavras dela, a operação que ela esperava ou uma parecida. Quando as palavras não bastam ("informar a sentença" para "comunicar a sentença"), um modelo de decisão confere se a ação cumpre a pendência, e só fecha com confiança alta.

## E o erro contrário, de nunca fechar?

Uma conferência estrita demais deixa tudo aberto, e a lista de pendências perde o valor. Três ajustes evitam isso:

- **Sinônimos.** "Avisar", "comunicar" e "informar" fazem a mesma coisa. A conferência compara o que foi feito, não a palavra exata.
- **Operações parecidas.** "Anexar documento" e "entregar documento" cumprem a promessa de enviar uma cópia.
- **Pendência sem operação.** Uma promessa dita antes de a memória conhecer qualquer operação ainda fecha pelas palavras e pela conferência do modelo.

## Como a Niadra resolve

Na Niadra, uma pendência fecha pelo que foi feito: um evento do [sistema de registro](/produtos/sistemas), uma ação de agente cuja resposta entregou o conteúdo prometido, ou uma fala que diz que foi feito. Uma ação de agente de atendimento passa por uma trava de entrega antes de fechar qualquer coisa, e a ação fica registrada mesmo quando não fecha. Cada fechamento aparece no [histórico](/produtos/historico) com o motivo, e a [conferência](/produtos/conferencia) mostra quando um agente afirmou algo que não tinha origem. A documentação descreve [como as pendências fecham](https://docs.niadra.com/concepts/object-memory) e [a coordenação entre agentes](https://docs.niadra.com/concepts/coordination).

## Perguntas frequentes

### O agente que fala com o cliente pode fechar uma promessa do advogado?

Pode, quando a resposta dele ao cliente entregou o que o advogado prometeu. "Comunico que a sentença julgou o pedido improcedente" fecha a promessa de comunicar a sentença. "O advogado vai falar com você" não fecha.

### E se o agente registrar uma ação que não fez?

A ação fica registrada, mas não fecha a pendência sem a entrega na resposta. A promessa continua aparecendo para os próximos agentes até ser cumprida.

### Uma pendência pode reabrir?

Uma pendência fechada não reabre sozinha. Se o cliente disser que não recebeu, isso vira uma contestação nova sobre o mesmo objeto, visível para todo agente que trabalhar nele.
