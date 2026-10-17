---
id: custo-por-conversa
lang: pt
slug: quanto-deve-custar-a-memoria-por-conversa
title: Quanto deve custar a memória de um agente de IA por conversa
description: O que uma memória gasta de modelo por mil conversas, medido no benchmark (US$ 0,36), o que ela custa no preço de tabela (US$ 2 a 3) e quanto isso é diante da ligação que ela melhora: menos de 1% de uma chamada de cinco minutos.
order: 5
kind: measured
date: 2026-09-30T22:30:00-03:00
category: Preço
icon: target
cover: US$ 0,003 por conversa
keywords: custo de memória para agentes de IA, preço por conversa agente de IA, quanto custa memória de agente, custo por mil conversas, preço de memória omnichannel, custo de agente de voz por minuto, cobrança por conversa e não por token
---

Uma memória de agente deveria custar uma fração pequena da conversa que ela melhora, e a conta deveria ser feita por conversa, não por token, leitura ou busca. Medido no [benchmark de 30/09/2026](https://github.com/ainiadra/niadra-sdk-python/tree/main/benchmarks/results/2026-09-30-6e6d07), o gasto de modelo da Niadra por mil conversas de dez trocas é de US$ 0,36; o preço de tabela é de US$ 2 a 3 a cada mil conversas ou tarefas, com leituras, buscas e eventos de sistema incluídos. Uma ligação de cinco minutos numa plataforma de voz custa de US$ 0,39 a 0,75; a memória dessa ligação, a US$ 0,003, custa menos de 1% dela.

## O que uma memória gasta, medido

O custo de uma memória tem três partes: o modelo que extrai a memória das conversas, a infraestrutura que a guarda e a serve, e a leitura, que numa memória bem feita não chama modelo nenhum. O benchmark mede a primeira pelo mesmo método para todo sistema: as chamadas de modelo da gravação até a memória assentar, aos preços do script, por mil conversas de dez trocas.

| Medida | Por mil conversas | De onde vem |
|---|---|---|
| Gasto de modelo da Niadra, só extração | US$ 0,36 | mediana de três repetições na região |
| Gasto de modelo dos sistemas de código aberto medidos | de US$ 0,97 a 18,72 | o mesmo método, servidores fora da conta |
| O modelo do próprio agente, no chat | US$ 0,095 | o prompt do agente com o contexto, aos mesmos preços |

A [página do benchmark](/benchmark) tem a tabela com os nomes. O que a Niadra faz para ficar em US$ 0,36: uma chamada de modelo por sessão, não por mensagem, sobre texto já mascarado; evento de sistema entra sem modelo; e a leitura não chama modelo, porque o contexto é compilado quando a memória muda.

## Por que cobrar por conversa, e não por token

Três modelos de cobrança aparecem no mercado de memória: por volume de texto (tokens, bytes ou caracteres), por operação (cada escrita, cada busca, cada leitura) e por conversa. Os dois primeiros punem o que a própria documentação dos produtos recomenda.

Quem cobra por operação cobra a cada busca antes de cada resposta: da conversa leve (três leituras) para a padrão (dez leituras), o preço triplica. Quem cobra por texto cobra a conversa longa: da conversa padrão para a pesada (trinta trocas), triplica de novo. E quem cobra por token tem um incentivo ao contrário: ganha mais mandando contextos maiores, que é o oposto do que o agente de voz precisa.

Por conversa ou tarefa, a conta fecha do jeito certo. Uma conversa com o cliente, ou uma tarefa de agente interno, em que algum agente leu a memória, conta uma vez. Dez leituras e cinco buscas dentro dela contam como uma. Vários agentes na mesma conversa contam uma vez, e a cobrança vai para a fonte que abriu a sessão. Grupo de controle e sombra nunca contam. Evento de sistema, que só escreve, nunca é cobrado. A regra está no código do servidor, não num texto comercial, e o extrato mensal diz, por espaço, o que foi usado.

## Quanto isso é diante da conversa

O enquadramento certo é o que o comprador já paga por conversa às plataformas em que os agentes rodam. Preços de lista lidos em 30/09/2026, para uma ligação de cinco minutos e uma conversa de vinte mensagens no WhatsApp:

| Plataforma | O que a conversa custa | A memória da Niadra, a US$ 3 por mil, como fração |
|---|---|---|
| [Vapi](https://vapi.ai/pricing), com transcrição, modelo, voz e telefonia | US$ 0,45 a 0,71 por ligação | 0,4% a 0,7% |
| [Retell](https://www.retellai.com/pricing), com modelo, voz e telefonia | US$ 0,745 por ligação | 0,4% |
| [ElevenLabs Agents](https://elevenlabs.io/pricing/agents), minuto adicional, modelo à parte | US$ 0,40 por ligação, mais o modelo | menos de 0,75% |
| [Twilio Voice](https://www.twilio.com/en-us/voice/pricing/us) com [ConversationRelay](https://www.twilio.com/en-us/products/conversational-ai/pricing), voz e modelo à parte | US$ 0,39 por ligação | 0,8% |
| [WhatsApp pela Twilio](https://www.twilio.com/en-us/messaging/pricing/whatsapp), só a taxa da Twilio, sem as taxas da Meta nem o modelo | US$ 0,10 por conversa | 3% |

A memória custa menos de 1% de uma ligação e cerca de 3% da taxa de uma conversa de WhatsApp. O preço não precisa ser o menor da tabela; precisa ser irrelevante diante da conversa, e visível no que ela evita: uma central que recontata 30% dos clientes em 72 horas paga a memória de cem ligações evitando um recontato.

## O preço de tabela e o que ele cobre

| Volume no mês | A cada mil conversas ou tarefas |
|---|---|
| Até 1 milhão | US$ 3,00 |
| De 1 a 10 milhões | US$ 2,50 |
| Acima de 10 milhões | US$ 2,00 |

Mínimo mensal de US$ 500 no plano Produção, que cobre 166 mil conversas ou tarefas. O plano Regulado, com ambiente dedicado, SSO, SLA em contrato e revisão de segurança, é por contrato. A faixa supõe a conversa média de até vinte trocas; um cliente cuja conversa média passa disso tem preço por contrato, e o extrato já informa os tokens de extração por espaço, para ninguém descobrir isso na fatura. A [página de preço](/preco) tem a tabela inteira e as perguntas frequentes.

## Como a Niadra resolve

A Niadra cobra por conversa ou tarefa em que um agente leu a memória, com leituras, buscas e eventos de sistema incluídos, porque é a unidade que o comprador já usa para pagar as plataformas de voz e de mensagem. O gasto de modelo de todo sistema, a Niadra incluída, é medido e publicado no benchmark, com o script e os arquivos para reproduzir. O post sobre [como reduzir o custo de tokens dos agentes sem perder contexto](/blog/como-reduzir-o-custo-de-tokens-de-agentes-de-ia) trata do outro lado da conta: o que o contexto compacto economiza no modelo do próprio agente.

## Perguntas frequentes

### O gasto de modelo de US$ 0,36 é o preço?

Não. É o que a Niadra gasta de modelo por mil conversas de dez trocas, medido no benchmark pelo mesmo método de todo sistema. O preço de tabela, de US$ 2 a 3 por mil, cobre o modelo, a infraestrutura e a operação, e é o que o cliente paga.

### O que acontece numa conversa com cinquenta trocas?

Ela conta uma vez, como qualquer outra. A faixa de preço supõe a conversa média de até vinte trocas; uma operação cuja média passa disso tem preço por contrato, e o extrato mensal mostra os tokens de extração antes de isso virar surpresa.

### Vocês cobram pela leitura do atendente humano ou do agente interno?

A leitura de qualquer agente conta, uma vez por conversa ou tarefa, e vai para a fonte que abriu a sessão. Uma fonte de ticket, e-mail ou ERP que só escreve nunca é cobrada.
