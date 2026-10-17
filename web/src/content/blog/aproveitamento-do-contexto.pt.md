---
id: aproveitamento-do-contexto
lang: pt
slug: como-saber-se-o-agente-de-ia-usou-o-contexto
title: Como saber se o agente de IA usou o contexto que recebeu
description: Um agente de IA pode receber o histórico do cliente e ignorá-lo. Como medir, por agente e por fornecedor, o que foi usado, o que foi perguntado de novo e o que foi contradito.
order: 10
kind: explainer
date: 2026-09-22T14:20:00-03:00
updated: 2026-10-01T10:00:00-03:00
category: Governança
icon: eye
cover: O agente usou o que recebeu?
keywords: como saber se o agente de IA usou o contexto, medir uso do contexto por agente de IA, agente de IA repete perguntas, avaliação de agentes de IA de atendimento, métricas de memória para agentes de IA, recontato em 72 horas, aproveitamento do contexto, governança de agentes de IA de vários fornecedores
---

Um agente de IA pode receber o histórico inteiro do cliente e, mesmo assim, perguntar "me conta o que aconteceu". Quem entrega a memória precisa provar que ela foi usada, e só consegue provar quem segura os dois lados: o contexto que foi entregue e a conversa que veio depois. Cruzando os dois, dá para medir, por agente e por fornecedor, quatro coisas: o que foi aproveitado, o que foi perguntado de novo, o que foi dito contra o que a memória já sabia e quem transferiu a conversa sem que o destino lesse o contexto.

## Por que "entregamos o contexto" não basta?

Porque entrega não é uso. O agente recebe um bloco de contexto antes da primeira resposta, e o prompt dele decide o que fazer com isso. Alguns prompts mandam confirmar tudo. Outros ignoram o que não está no formato esperado. Outros usam o contexto na primeira resposta e esquecem na quinta. Do lado do cliente, o resultado é o mesmo de sempre: repetir a história.

A empresa costuma descobrir isso tarde, pelo recontato: o cliente volta em 72 horas porque o problema não foi resolvido, ou porque a segunda conversa começou do zero. O recontato é o número que importa para o negócio, mas ele não explica. Ele diz que algo falhou, não diz qual agente, de qual fornecedor, em qual ponto.

## O que dá para medir, e como

A memória compartilhada entrega o contexto com um comprovante: a lista do que foi entregue, item por item (a pendência aberta, a promessa anterior, a ação de outro agente, o fato de identidade), no nível de verificação em que foi entregue. Quando a conversa termina, o mesmo processo que extrai fatos da conversa devolve também duas listas: o que o agente perguntou e o que o agente afirmou. O cruzamento dessas listas com o comprovante é determinístico, sem modelo de IA adicional, e produz quatro sinais.

| Sinal | O que é | O que não conta |
|---|---|---|
| **Aproveitamento** | Itens entregues que o agente citou ou usou: mencionou a visita perdida, o crédito lançado, a promessa anterior; a ação registrada depende de um item entregue | Item entregue e irrelevante para a tarefa: conta como não usado, sem penalidade, e serve para enxugar as próximas entregas daquele agente |
| **Repetição** | O agente pediu um dado que estava no contexto entregue: nome, documento, número do pedido, o motivo do contato | Pergunta de verificação exigida pela política (código por SMS, confirmação de identidade antes de dado sensível); confirmação explícita de um valor entregue quando a fonte está configurada para confirmar antes de agir |
| **Contradição** | O agente afirmou algo que um fato entregue desmente: "a visita está marcada para sexta" quando o fato entregue diz quinta | Divergência com um fato que não estava na entrega: é falha de seleção, medida à parte, nunca atribuída ao agente |
| **Transferência sem leitura** | A conversa passou para um humano ou outro agente, e o destino não pediu o contexto em dez minutos | Transferência para um destino que não está integrado: vira um contador de cobertura, não de repetição |

Dois contadores acompanham os sinais: conversas em que o agente nem pediu o contexto, e conversas em que o contexto chegou depois da primeira resposta. Sem cobertura, não há uso a medir, e o número aparece como "não medido", nunca como zero.

## Um exemplo, minuto a minuto

Às 14h02 a cliente escreve no WhatsApp que o técnico não apareceu. Às 14h05, no app, contesta a fatura. Às 14h06, um agente interno de cobrança lança um crédito no ERP. Às 14h07 ela liga.

O agente de voz recebe, antes de dizer alô, quatro itens: o nome dela, a visita perdida, o crédito lançado e a falha de março. Ele diz: "Oi, Marina. Vi que o técnico não apareceu hoje cedo, e que isso já tinha acontecido em março. Já remarquei a visita com prioridade, e o crédito da fatura de agosto já foi lançado." A extração da conversa devolve zero perguntas, três afirmações (a visita perdida, a falha de março e o crédito lançado) e uma ação, a visita remarcada. O cruzamento dá aproveitamento de quatro em quatro, repetição zero, contradição zero.

Na versão sem memória compartilhada da mesma ligação, o agente perguntou nome, documento e "o que aconteceu": três repetições, e a cliente contou a história pela terceira vez em cinco minutos. É essa diferença que a medição mostra, agente por agente.

## O que o número faz pela empresa

- **Mostra onde o contexto não chega ao cliente.** O agente de voz do fornecedor B usa o contexto em 91% das entregas; o de WhatsApp do fornecedor A, em 74%. O que a empresa faz com esse número é decisão dela.
- **Explica o recontato.** Ao lado de cada taxa de recontato, a medição de cada conversa mostra, entrega a entrega, o que o agente repetiu ou contradisse. O número que importa para o negócio ganha uma causa.
- **Enxuga o contexto.** O tipo de item que um agente recebeu em dezenas de entregas medidas e nunca usou deixa de ser enviado para aquele agente, e o contexto fica menor e mais barato. Pendências, promessas e dados sensíveis nunca saem, e o item volta na medição seguinte quando o agente pergunta por ele, fala dele ou age sobre ele.
- **Entra em contrato só com prova.** Num programa de 90 dias, a meta escrita antes é o recontato em 72 horas contra um grupo de controle, e a medição mostra onde ele nasce. O aproveitamento por agente só vira cláusula depois que a revisão por amostra mostra concordância de 90% ou mais com o time da empresa. Veja [os 90 dias, semana a semana](/blog/os-90-dias-da-memoria-semana-a-semana).

## O que essa medição não é

Não é nota de qualidade do agente. Ela mede uso da memória: não julga tom, não avalia se o agente foi simpático, não sugere prompt e não bloqueia nada. Falsos positivos existem: um agente pode repetir uma pergunta porque o prompt dele manda confirmar tudo. A resposta é configuração por fonte (marcar o agente como "confirma antes de agir") e uma revisão por amostra, em que o time da empresa marca cada sinal como procedente ou não. Cada veredito entra na taxa de concordância daquele ambiente, que diz quanto dá para confiar na medição.

Também não compara fornecedores em público. O número é da empresa, e o que ela faz com ele é decisão dela.

## Por que o fornecedor do agente não consegue dar esse número?

Porque ele só segura um lado. A plataforma de agentes audita os próprios agentes contra as próprias regras, dentro do próprio produto. Ela não vê o que outro fornecedor entregou nem o que aconteceu na ligação atendida pelo concorrente. Uma ferramenta de memória para um agente só também não vê a conversa seguinte quando ela acontece em outro canal, com outro fornecedor.

Só uma memória neutra, que entrega a todos os agentes e recebe de todos, tem o comprovante da entrega e a conversa que veio depois. Para a diferença entre as duas arquiteturas, veja [memória dentro da plataforma de agentes ou memória neutra](/blog/memoria-dentro-da-plataforma-de-agentes-ou-memoria-neutra).

## Como a Niadra resolve

A Niadra entrega o contexto a cada agente com um comprovante do que foi entregue, e recebe a conversa que veio depois pelo mesmo SDK. Ao fim de cada conversa, cruza os dois e grava, por entrega, o que foi usado, o que foi perguntado de novo, o que foi contradito e se houve transferência sem leitura. O resultado fica pronto junto com a memória derivada, sem chamada extra a modelo de IA.

No Console, a empresa vê os números por agente, por fornecedor, por canal e por semana, ao lado do recontato em 72 horas, com o que cada agente deixou de receber e quantos tokens isso poupou, e, no detalhe de cada conversa, vê entrega a entrega o que foi usado. O fornecedor A nunca vê os números do fornecedor B. O time da empresa revisa uma amostra das conversas, e cada veredito entra na taxa de concordância, que diz quanto dá para confiar na medição. Quando um agente passa a ignorar o contexto, uma regra da empresa dispara um aviso assinado por webhook, e quem decide o que fazer é o sistema da empresa ([Avisos](/produtos/avisos)).

A medição segue a mesma regra de privacidade das leituras: o resultado é sobre o agente, nunca sobre o cliente. Apagar um cliente apaga também as linhas da medição que derivaram dele, com comprovante ([segurança e LGPD](/blog/lgpd-e-seguranca-na-memoria-de-clientes-para-ia)).

O número que essa medição produz é o que falta na conta do recontato: [quanto custa o cliente contar a mesma história em cada canal](/blog/quanto-custa-o-cliente-contar-a-mesma-historia-em-cada-canal) mostra a conta com os números do setor, e a [Medição](/produtos/medicao) é onde ela aparece por agente e por fornecedor.

## Perguntas frequentes

### Isso funciona com agentes de fornecedores diferentes?

Sim, e é o caso em que mais importa. Cada agente, de qualquer fornecedor, recebe o contexto pelo mesmo SDK e devolve a conversa pelo mesmo caminho. A medição é por agente e por fornecedor, e cada fornecedor vê só os próprios números.

### E se o agente precisa confirmar o documento por regra de segurança?

A pergunta de verificação exigida pela política não conta como repetição. O que conta é pedir de novo um dado já entregue no nível de verificação em que a política permitia usá-lo.

### Isso custa mais por conversa?

Não. A medição usa as listas que a extração da conversa já produz e o comprovante da entrega. É processamento sobre dados que já estão na memória. O preço da Niadra é por conversa ou tarefa, e a medição está incluída ([Preço](/preco)).
