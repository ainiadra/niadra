---
id: estado-tipado
lang: pt
slug: por-que-o-agente-fala-o-preco-errado
title: Por que o agente fala o preço errado, e o que muda com o estado tipado no contexto
description: O mesmo agente, com e sem o estado dos objetos do cliente no turno, medido em 126 respostas por braço: de 36,6% para 84,9% de acerto. O que é o estado de agora, por que a cotação vence por insumo e quanto custa em tokens.
order: 2
kind: measured
date: 2026-09-30T22:30:00-03:00
category: Estado
icon: sliders
cover: Com o estado no turno, 84,9%
keywords: agente de IA fala preço errado, estado do cliente para agentes de IA, cotação vencida agente de IA, frescor do dado no contexto, restrição declarada pelo cliente, memória tipada para agentes, benchmark estado tipado
---

O agente fala o preço errado porque a memória lhe entrega o que foi dito e não o que é verdade agora: o preço que a cliente ouviu na semana passada, a cotação que já venceu, o prazo que foi revisado. Quando o turno passa a trazer o estado de agora dos objetos da cliente, com a idade, a fonte e o que não se pode afirmar com aquele dado, o mesmo agente, com o mesmo modelo, acerta 84,9% dos casos que pedem um número, um prazo ou uma escolha, contra 36,6% sem o estado. A medida é da [execução de 30/09/2026](https://github.com/ainiadra/niadra-sdk-python/tree/main/benchmarks/results/typed/2026-09-30-b59a5d), com 126 respostas por braço.

## O que foi medido

O conjunto tem 42 casos em sete categorias, em português e inglês, com três setores descritos pelo papel: uma loja, um escritório de contencioso e a venda de um plano de saúde. Cada caso pede um número, um prazo ou uma escolha que só o estado ou uma restrição sustenta: "qual é o preço hoje?", "a cotação ainda vale?", "o que mudou desde a última vez que eu vi?". Dois braços, o mesmo cliente e a mesma conversa: uma leitura de contexto sem bloco nenhum e uma que pede os blocos de estado e de restrições. O que muda é só o que os dois blocos acrescentam ao bloco do turno.

O mesmo agente e o mesmo juiz (raciocínio baixo, semente fixa) nos dois braços, três repetições de cada caso, numa célula local do script com a imagem do servidor da Niadra. O intervalo é de Wilson a 95%, e as repetições de um caso não são sorteios independentes, então ele é mais estreito do que casos novos dariam.

| Categoria | Sem os blocos | Com os blocos |
|---|---|---|
| Cotação vencida por insumo | 0% | 100% |
| O que mudou desde a última vez | 0% | 100% |
| Restrição declarada | 0% | 94,4% |
| Prazo revisado | 72,2% | 88,9% |
| Preço com frescor | 50% | 66,7% |
| "Não conferido" não vira "não" | 100% | 100% |
| Efeito uma vez só | 33,3% | 44,4% |
| Todos os casos | 36,6% | 84,9%, intervalo [77, 90] |

Dois resultados merecem leitura à parte. "Não conferido" não vira "não" já estava em 100% sem os blocos: o agente não inventa uma recusa quando o dado não foi checado, com ou sem estado. E "efeito uma vez só" quase não se move com os blocos, porque não é um problema de contexto: é de coordenação. A segunda tentativa do mesmo efeito foi recusada como já feita em 18 de 18 verificações, mas por um mecanismo separado do contexto, que a página de [Coordenação](/produtos/coordenacao) descreve.

## Por que o preço sai errado sem o estado

A memória de uma conversa guarda episódios: a cliente perguntou o preço, o agente respondeu R$ 189, a cliente pediu uma cotação com entrega. Tudo isso é verdade sobre o passado. O agente seguinte lê esses episódios e, sem outra informação, trata o último valor dito como o valor de agora. Se a tabela mudou, ele repete o preço antigo com a convicção de quem leu a memória.

Três coisas faltam ao episódio para virar estado:

- **A idade e a fonte de cada campo.** O preço de R$ 189 veio da tabela de 22/09, lida pelo sistema de pedidos. Um valor com 8 dias pode ou não valer; a política de frescor da empresa diz, campo a campo.
- **O que não se pode afirmar com aquele dado.** Com um preço de 8 dias, o agente pode dizer "estava em R$ 189 na semana passada" e não pode dizer "custa R$ 189". A proibição vai escrita no bloco, para o modelo não precisar deduzir.
- **A dependência entre valores.** Uma cotação é calculada de preço, frete e prazo. Se o frete mudou, a cotação venceu, mesmo que o prazo de validade impresso nela ainda não tenha passado. O estado tipado guarda os insumos de cada valor derivado e o marca como vencido quando um deles muda: é a categoria que saiu de 0% para 100%.

A restrição declarada é o outro lado: "não ligue depois das 18h", "só pagamento à vista", "não quero o plano com coparticipação". A cliente disse uma vez, em outro canal, e o agente de hoje precisa respeitar sem que ela repita. Sem o bloco, 0% das respostas respeitaram a restrição; com ele, 94,4%.

## O que o estado tipado é, por mecanismo

A empresa declara os tipos de estado dela (uma venda, um processo, uma proposta, um sinistro, um pedido), ou a Niadra os deriva do esquema do banco e avisa quando mudam. Cada objeto tem quatro valores lógicos por campo: sim, não, não observado (com quem observou e a validade) e defeito conhecido da fonte. "Não conferido" nunca vira "não". As fontes têm precedência, o frescor é calculado por campo na hora da leitura, e os valores derivados guardam os insumos e vencem quando um insumo muda. O agente recebe o estado no bloco do turno, com a lista do que não pode afirmar com dado velho. A [documentação dos tipos de objeto](https://docs.niadra.com/concepts/object-types) descreve o contrato.

O que ele custa: o bloco acrescenta 58 tokens a um turno de voz e 51 a um turno de chat, na mediana. O mesmo dado entregue como JSON de uma ferramenta custaria 1.038 e 777 tokens. Um turno que não pede bloco custa o mesmo de antes, medido nos mesmos clientes. Em 126 leituras sem verificação, nenhuma trouxe dado sensível no bloco: o estado obedece ao nível que a conversa provou, como o resto do contexto.

## O que o número não diz

Os casos são sintéticos e o benchmark é da Niadra; a resposta é o script público, para reproduzir. A medida foi feita numa célula local, não na região de produção. Dos 126 pares de respostas, 81 passaram na regra de validade (acertar com o histórico inteiro no prompt e errar sem memória); a tabela completa, só com os válidos, está na [página do benchmark](/benchmark). E "efeito uma vez só" mostra o limite do próprio mecanismo: estado no contexto resolve o que o agente afirma, não o que ele executa duas vezes.

## Como a Niadra resolve

O estado é uma das partes da memória que a Niadra monta a partir dos eventos de todo canal e sistema: os objetos de qualquer tipo, do cliente ou compartilhados, com frescor por campo, derivados que vencem por insumo e as restrições que a cliente declarou, entregues no turno de qualquer agente, de qualquer fornecedor. A página de [Estado](/produtos/estado) mostra o artefato; a de [Afirmação](/produtos/conferencia) mostra como o que o agente afirma é conferido contra o que ele consultou, por léxico, papel e âncora, sem modelo a mais.

## Perguntas frequentes

### Isso é o mesmo que uma ferramenta de consulta ao ERP?

Não. Uma ferramenta devolve o registro quando o modelo decide chamar; o estado tipado chega no turno, antes da resposta, já com a idade, a fonte e o que não pode ser afirmado. O mesmo dado como JSON de ferramenta custou de 13 a 18 vezes mais tokens na medida.

### Por que "efeito uma vez só" quase não melhorou?

Porque não é uma falha de contexto. Evitar que a mesma mensagem saia duas vezes é coordenação: um fato de negócio, um efeito. A segunda tentativa foi recusada como já feita em 18 de 18 verificações, por um mecanismo próprio, fora do contexto.

### Os 84,9% valem para o meu setor?

A medida usa três setores descritos pelo papel e casos sintéticos. O que vale é o mecanismo: o seu tipo de estado é declarado por você, ou derivado do seu banco, e a medida no seu caso sai da sua operação, não da nossa.
