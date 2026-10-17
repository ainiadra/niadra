---
id: promessas-entre-agentes
lang: pt
slug: como-evitar-que-o-agente-de-ia-prometa-e-esqueca
title: Como evitar que o agente de IA prometa e esqueça
description: O agente de WhatsApp diz que a segunda via sai até sexta, e na segunda o agente de voz não sabe de nada. Como uma promessa com prazo passa de um agente para o outro, quem vê o quê, e por que a mesma promessa dita duas vezes não pode virar duas.
order: 2
kind: guide
date: 2026-10-08T09:00:00-03:00
category: Operação
icon: clock
cover: A promessa chega ao próximo agente
keywords: agente de IA promete e esquece, promessas de agentes de IA com prazo, follow up entre agentes de IA, pendências do cliente entre canais, agente de voz não sabe o que o WhatsApp prometeu, memória de compromissos para agentes de IA, prazo prometido ao cliente
---

Um agente de IA para de prometer e esquecer quando cada promessa vira uma pendência com dono, prazo e objeto, guardada fora do agente que a fez, e entregue a todo agente que atende o mesmo cliente ou trabalha no mesmo pedido, fatura ou processo. Se a promessa mora só na conversa em que foi dita, ela some quando a conversa acaba. Se mora na memória compartilhada, o próximo agente a vê antes de responder, com o prazo.

Este guia mostra como isso funciona na prática, com os dois lados: o agente que atende o cliente e o agente interno que trabalha no sistema.

## Por que a promessa se perde?

A cena é comum. Na sexta, o cliente pede pelo WhatsApp a segunda via de uma fatura, e o agente responde: "envio até segunda". Na segunda, o cliente liga. O agente de voz é de outro fornecedor, lê o histórico da ligação anterior (nenhum) e o cadastro (nada sobre segunda via), e pergunta de novo o que o cliente precisa. O cliente conta a história pela segunda vez e desliga achando que ninguém lembra dele.

Três coisas falharam:

- **A promessa não saiu da conversa.** Ela existia como frase dentro do histórico do WhatsApp, não como algo com prazo e dono.
- **Ela não estava presa ao objeto.** "A segunda via" era da fatura 7731; sem essa ligação, nenhum agente que abrisse a fatura saberia da promessa.
- **Ninguém do lado de dentro sabia.** O agente de cobrança no ERP, que poderia gerar a segunda via, nunca soube que ela tinha sido prometida.

## O que uma promessa precisa guardar?

Para atravessar agentes e canais, uma promessa precisa de quatro informações:

1. **O que foi prometido**, nas palavras de quem prometeu: "enviar a segunda via da fatura".
2. **Até quando.** Uma data, quando foi dita. "Até segunda" vira o dia certo, calculado a partir do dia em que a frase foi dita, não do dia em que alguém leu.
3. **Sobre o quê.** O objeto: a fatura 7731, o pedido 48213, o processo citado pelo número. Quando o cliente escreve o número, a promessa fica ligada a ele.
4. **De que tipo.** Uma promessa da empresa ("vou enviar"), um pedido ao cliente ("me mande o comprovante") ou uma contestação ("a cobrança está errada"). Cada tipo aparece com o nome dele, para o agente não confundir o que a empresa deve com o que o cliente deve.

## Quem vê a promessa?

Todo agente que trabalha com o mesmo cliente ou o mesmo objeto, com o filtro de público certo:

- **O agente de voz da segunda-feira** vê "promessa até 13/10: enviar a segunda via da fatura" e começa a ligação pelo que está pendente.
- **O agente de cobrança no ERP** vê a mesma pendência na linha da fatura e pode gerá-la sem que ninguém peça.
- **O assistente da equipe** vê a pendência quando um atendente humano abre o caso.

O que um advogado, gerente ou analista anota internamente sobre o caso não aparece para o cliente. O que foi prometido ao cliente, sim, para que qualquer agente cumpra.

## E quando a promessa é dita duas vezes?

Acontece o tempo todo. O advogado promete enviar a cópia da decisão; horas depois, o agente de atendimento repete ao cliente: "o advogado vai te mandar a cópia". Se cada frase virar uma pendência, a fatura tem duas promessas e o próximo agente não sabe se são duas coisas.

A regra é tratar a repetição como a mesma promessa. A mesma coisa sobre o mesmo objeto, com prazos compatíveis, é uma pendência só, mesmo dita com outras palavras ("cópia da sentença" e "cópia da decisão"). Quando as palavras não bastam para decidir, um modelo de decisão confere se a frase nova repete a que está aberta. Uma coisa diferente, como "o comprovante para o financeiro" e "o comprovante para os autos", continua sendo outra pendência.

## E quando o prazo passa?

A pendência não some. Ela aparece como "até 06/10 (atrasada)", com a data. A forma importa: em testes com a pergunta "até quando era?", a palavra "atrasada" sem a data fazia o agente responder que não havia prazo. Com a data na frente, ele responde a data.

## Como a Niadra resolve

Na Niadra, cada promessa, pedido e contestação que um agente ou uma pessoa diz vira uma pendência com prazo, tipo e objeto, guardada sob quem disse e entregue no [contexto](/produtos/contexto) de todo agente que trabalha com aquele cliente ou objeto, por público. A pendência fecha pelo que foi feito, como explica o post sobre [por que "vou enviar" não é "enviei"](/blog/quando-dar-uma-pendencia-como-resolvida), e uma pendência vencida pode [avisar quem precisa](/produtos/avisos). O [histórico](/produtos/historico) mostra quando cada uma abriu, quem prometeu e como fechou. A documentação descreve [a memória do objeto](https://docs.niadra.com/concepts/object-memory) em detalhe.

## Perguntas frequentes

### O agente precisa chamar uma ferramenta para registrar a promessa?

Não. A promessa é extraída da própria conversa, a partir do que o agente ou a pessoa disse. O agente só precisa registrar os turnos, o que o SDK faz.

### E se o agente prometer uma data que não existe?

Uma data que só o agente disse, sem nada na memória que a sustente, não vira prazo da pendência. A promessa fica registrada sem data, em vez de carregar um prazo inventado para os próximos agentes.

### A promessa aparece para agentes de outros fornecedores?

Sim. A memória é neutra: o agente de WhatsApp de um fornecedor e o de voz de outro leem a mesma pendência, como mostra o post sobre [agentes de fornecedores diferentes](/blog/como-agentes-de-fornecedores-diferentes-compartilham-o-historico).
