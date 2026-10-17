---
id: memoria-tinha-a-resposta
lang: pt
slug: a-memoria-tinha-a-resposta-erro-da-memoria-ou-do-modelo
title: A memória tinha a resposta? Como separar o erro da memória do erro do modelo
description: Quando um agente de IA erra uma pergunta do cliente, o erro pode estar na memória, que não entregou a informação, ou no modelo, que leu mal. Como medir os dois separadamente, por que um juiz automático estrito engana e o que fazer com cada tipo de erro.
order: 5
kind: explainer
date: 2026-10-08T09:00:00-03:00
category: Benchmark
icon: target
cover: Memória ou modelo?
keywords: como avaliar memória de agentes de IA, a memória tinha a resposta, erro do modelo ou da memória, avaliação de agentes de atendimento com IA, juiz automático LLM as a judge estrito, métrica de contexto com a resposta, testar memória de agentes antes de produção
---

Para saber se a memória de um agente de IA está funcionando, meça primeiro se o contexto entregue ao agente tinha a resposta, e só depois se o agente respondeu certo. A primeira medida é da memória. A segunda mistura a memória com o modelo que leu o contexto. Quando as duas ficam juntas num placar só, um modelo que lê mal parece uma memória ruim, e o time passa semanas corrigindo a peça errada.

Este post mostra como separar os dois erros com perguntas reais de cliente, e o que fazer com cada um.

## Por que um placar só engana?

O teste habitual é escrever vinte perguntas que um cliente faria, com a resposta certa ao lado, deixar o agente responder e contar os acertos. O número sai, mas não diz o que corrigir:

- **O agente disse "não sei".** A informação não estava no contexto, ou estava e o agente não achou?
- **O agente errou a data.** A memória entregou a data errada, ou entregou duas datas e o agente escolheu a da linha de cima?
- **O agente acertou e foi marcado como erro.** Ele respondeu a verdade com um detalhe a mais, e o juiz achou que o detalhe era invenção.

O terceiro caso é mais comum do que parece. Um juiz automático que compara a resposta com um gabarito curto pune qualquer detalhe que não está no gabarito, mesmo verdadeiro. "O preparo vencia em 13/10 e foi pago em 05/10" vira erro quando o gabarito diz só "13/10".

## Como medir a memória sozinha?

Para cada pergunta, guarde o bloco de contexto exatamente como o agente o recebeu, e pergunte a um juiz uma coisa só: o bloco, sozinho, contém a informação necessária para responder? Três respostas possíveis: sim, em parte, não.

Isso dá a medida da memória: em quantas perguntas o contexto tinha a resposta. Ela não depende do modelo que responde, do prompt do agente nem do humor do juiz sobre detalhes extras.

Dois cuidados:

- **Espere a memória terminar.** Se o teste roda logo depois das conversas, a memória ainda está aprendendo, e o bloco sai sem o que foi dito minutos antes. Confira que as conversas já viraram memória antes de perguntar.
- **Use perguntas sobre o que aconteceu.** O gabarito precisa refletir o estado depois das conversas, não o planejado antes. Se o agente já avisou o cliente, "o advogado ficou de avisar" deixou de ser a resposta certa.

## Como medir o modelo?

Com o mesmo bloco, troque o modelo que responde. Se três modelos diferentes erram a mesma pergunta com o mesmo bloco, o problema provavelmente está em como a informação foi escrita, não no modelo. Se só o modelo menor erra, o problema é dele.

E corrija o juiz: um detalhe a mais é erro só quando nada sustenta o detalhe, nem o gabarito nem o bloco que o agente recebeu. É a mesma regra dos valores sem lastro: um número, data ou código que o agente afirmou precisa ter origem no que ele leu.

## O que fazer com cada erro?

| O que a medição mostra | Onde está o problema | O que fazer |
|---|---|---|
| O bloco não tinha a resposta | Na memória | Ver se a informação foi extraída, ligada ao objeto certo e entregue a quem perguntou |
| O bloco tinha, todos os modelos erram | Na forma como a memória escreve | Mudar a redação: o prazo como prazo, o feito separado do pendente, a regra dizendo a quem se aplica |
| O bloco tinha, só um modelo erra | No modelo | Trocar de modelo ou ajustar o prompt do agente |
| O juiz marcou erro, o detalhe tem origem | No juiz | Contar como acerto |

A segunda linha é a mais útil e a menos óbvia. Muitas vezes a memória tem a resposta, mas a forma induz o erro: a data de uma promessa ao lado do prazo do processo, "cumprido" encostado numa pendência aberta, "atrasada" sem a data. Mudar a redação resolveu mais erros do que trocar de modelo.

## Como a Niadra resolve

A Niadra aplica essa separação no próprio [benchmark](/benchmark): o mesmo agente e o mesmo juiz respondem com a memória de cada sistema, e os valores sem lastro de cada resposta são contados com a mesma regra para todos, a partir do que o bloco de memória continha. No dia a dia, a [medição](/produtos/medicao) mostra por agente e por fornecedor o que foi entregue e usado, e o [registro de turno](/produtos/registro) guarda o bloco exato que cada agente recebeu, para reproduzir a pergunta depois. O post sobre [como saber se o agente usou o contexto](/blog/como-saber-se-o-agente-de-ia-usou-o-contexto) cobre a outra metade: o que o agente fez com o que recebeu.

## Perguntas frequentes

### Quantas perguntas preciso para um teste confiável?

Mais importa a variedade que a quantidade: perguntas sobre fatos, prazos, promessas, restrições e coisas que mudaram durante a semana. Rode cada pergunta mais de uma vez; a resposta de um modelo varia entre execuções.

### Posso usar o mesmo modelo como agente e como juiz?

Pode, mas o juiz precisa ver o gabarito e o bloco, e julgar o detalhe a mais pelo que o bloco sustenta. Sem isso, ele pune respostas verdadeiras.

### Por que não medir só a satisfação do cliente?

Porque ela chega tarde e não diz a causa. A medida da memória diz, antes de o cliente reclamar, se a informação estava lá.
