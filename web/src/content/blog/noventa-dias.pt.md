---
id: noventa-dias
lang: pt
slug: os-90-dias-da-memoria-semana-a-semana
title: Os 90 dias da memória compartilhada, semana a semana
description: O preparo antes do dia 1, quatro semanas em modo sombra, oito semanas numa jornada com grupo de controle e a decisão na semana 13. Quem faz o quê e como cada meta é medida.
order: 11
kind: guide
date: 2026-09-22T14:40:00-03:00
updated: 2026-10-01T10:00:00-03:00
category: Implantação
icon: clock
cover: Da primeira chamada à memória em produção
keywords: implantação de memória para agentes de IA, quanto tempo leva para integrar memória de agentes de IA, plano de 90 dias para agentes de IA, grupo de controle para agentes de IA, modo sombra para agentes de IA, metas para agentes de IA de atendimento, como medir recontato em 72 horas, implantação de IA em empresa grande
---

Dar uma memória a todos os agentes de IA de uma empresa leva 90 dias quando o trabalho é uma missão com escopo, meta e data, e não um projeto aberto. Antes do dia 1 vêm de duas a quatro semanas de preparo. Depois, quatro semanas em modo sombra, em que a memória é construída e o contexto é preparado e auditado sem chegar aos agentes. Nas semanas 5 a 12, uma jornada recebe a memória para uma parte dos clientes, sorteada cliente a cliente, e o restante é o grupo de controle. Na semana 13 sai o resultado, e a decisão acontece na data marcada desde a reunião de início. Um engenheiro da Niadra acompanha do diagnóstico à produção, e o time do cliente não instala nem opera infraestrutura.

## Antes do dia 1: o diagnóstico e o preparo

Nenhuma missão começa sem três números do próprio cliente, por canal: volume de conversas por mês, recontato em 72 horas e custo por contato. Ninguém estima por ele. Com esses números, o time escolhe uma jornada (a mais frequente e a que mais faz o cliente repetir a história: contestação de fatura, visita técnica, mudança de entrega) e escreve as metas.

As três metas, com o método de medição ao lado:

1. **Recontato em 72 horas** na jornada escolhida, medido contra o grupo de controle.
2. **Contexto certo**, medido pela revisão por amostra do time do cliente: a proporção de fatos extraídos que o revisor confirma.
3. **Operação sem incidente**: nenhuma leitura fora da política, nenhum dado sensível entregue sem a verificação exigida, nenhuma indisponibilidade que atrase um agente.

O aproveitamento do contexto, por agente e por fornecedor, fica ao lado das metas e explica o recontato: mostra onde o contexto não chegou ao cliente ([como saber se o agente usou o contexto](/blog/como-saber-se-o-agente-de-ia-usou-o-contexto)). Ele só vira cláusula de contrato quando a revisão por amostra mostra concordância de 90% ou mais entre o time do cliente e a medição.

O preparo leva de duas a quatro semanas e corre em paralelo:

- **Segurança e contrato.** O questionário de segurança, o modelo de ameaças e o contrato de tratamento de dados, com os provedores nomeados, chegam ao time de segurança logo no início, junto com o anexo que nomeia a lei da região. No plano Regulado, o teste de invasão por empresa independente entra no contrato. A revisão de segurança é o que mais atrasa uma compra enterprise, por isso ela começa antes do dia 1.
- **Ambiente de teste.** A Niadra abre o ambiente de teste do cliente, na região do contrato. Os dados de amostra que o diagnóstico usa são entregues sob o contrato de tratamento de dados.
- **As três chamadas no agente que já existe.** O engenheiro da Niadra senta com o time do cliente e coloca `context()` antes da primeira resposta, `search()` para o histórico e `track()` no fim da conversa. Nada do agente é reescrito.
- **O primeiro sistema.** O CRM, o ERP ou o help desk manda os eventos que já emite por webhook, pela API ou em lote. O assistente de configuração propõe o mapeamento e testa em até cem eventos já recebidos. Uma pessoa aprova.

## Semanas 1 a 4: modo sombra

A memória passa a ser construída de verdade, a cada conversa e a cada evento, e o contexto de cada agente é preparado e auditado, mas não é entregue. Os agentes continuam trabalhando como antes. É o que dá a linha de base: a Niadra mede quantas vezes cada agente pediu um dado que o contexto teria trazido, sem que nenhum cliente tenha recebido nada diferente.

É também quando a revisão por amostra começa. Entre 1% e 10% das conversas vão para uma fila em que o time do cliente marca cada fato extraído como certo, errado ou faltando, e confere os sinais da medição. Os vereditos entram na taxa de concordância daquele ambiente, e a extração é ajustada por fonte: o agente de cobrança guarda o motivo da contestação e a promessa de pagamento; o de pedidos, a mudança de entrega. A meta 2 começa a ser medida aqui.

O grupo de controle é sorteado nesta fase, cliente a cliente, com o sorteio registrado. De 5% a 20% dos clientes da jornada vão receber a memória a partir da semana 5; o restante continua sem ela até o fim dos 90 dias.

## Semanas 5 a 12: uma jornada, com grupo de controle

A memória passa a chegar aos agentes, só para os clientes sorteados. O recontato dos dois grupos é medido lado a lado, cliente a cliente, somando os canais.

### Semanas 5 e 6: o segundo canal ou fornecedor

O valor de uma memória compartilhada aparece quando o segundo canal entra: a ligação começa sabendo o que foi dito no WhatsApp. Nesta etapa entra o segundo agente, de preferência de outro fornecedor. A identidade entre canais é conferida com casos reais: número de telefone que trocou de dono, cliente que usa dois e-mails, duas pessoas unidas por engano e separadas sem perder histórico.

Os primeiros avisos entram aqui. Uma promessa vencida ou a terceira reclamação sobre o mesmo tema viram um aviso assinado por webhook para o sistema do cliente, e quem decide o que fazer é o agente ou o sistema.

### Semanas 7 e 8: o agente interno fecha o ciclo

O agente interno (cobrança, pedidos, tickets) passa a receber o contexto da tarefa dele e a registrar o que fez. A ação registrada fecha a pendência: o crédito lançado às 14h06 chega ao agente de voz às 14h07. É a etapa em que o cliente vê a memória de trabalho da empresa, e não só a do atendimento ([memória para agentes internos](/blog/memoria-para-agentes-de-ia-internos)).

### Semanas 9 a 12: a leitura contra o controle

A partir da semana 9, o recontato do grupo que recebe a memória é lido contra o do grupo de controle, com o intervalo de confiança ao lado. O engenheiro e o patrocinador leem os números juntos, por agente e por fornecedor, e corrigem o que a medição aponta: o agente que repete por ordem do próprio prompt, o item de contexto que nunca é usado, a fonte que integrou só o registro e nunca pede o contexto.

Nesta etapa o registro de leituras chega ao SIEM do time de segurança.

## Semana 13: o resultado e a decisão

O relatório final compara as três metas com o que foi escrito antes do dia 1, com o grupo de controle ao lado e o aproveitamento por agente como explicação. A reunião de decisão tem data marcada desde a reunião de início. O que sai dela é o plano de expansão: as próximas jornadas, os próximos agentes e os próximos sistemas.

## Depois da produção: quem cuida da memória

Uma memória compartilhada muda toda vez que um agente novo entra, um fornecedor é trocado ou um sistema passa a emitir um evento novo. O engenheiro da Niadra acompanha do diagnóstico à produção. Depois, o suporte segue o plano contratado, e cada agente novo entra como uma missão curta, com o mesmo desenho: integração, uma semana em modo sombra, medição. Quando uma fonte para de mandar eventos, a Niadra avisa por webhook.

## Quem faz o quê

| Papel | Responsabilidade |
|---|---|
| **Patrocinador do cliente** | Escolhe a jornada, aprova as metas e a data da decisão, lê os números a partir da semana 9 |
| **Dono técnico do cliente** | Integra as três chamadas nos agentes e aponta os eventos dos sistemas, no preparo |
| **Segurança do cliente** | Revisa os documentos de segurança antes do dia 1, aprova a política por fonte, recebe o registro de leituras no SIEM |
| **Revisores do cliente** | Conferem a amostra de conversas a partir da semana 1, poucas horas por semana |
| **Engenheiro da Niadra** | Do diagnóstico à produção: mapeamento, extração, política, medição, revisão por amostra e leitura dos números. Depois, suporte conforme o plano |
| **Fornecedores de agentes** | Integram o SDK nos próprios agentes e veem só os próprios números |

## Como a Niadra resolve

A Niadra é totalmente gerenciada: a infraestrutura, as chaves, as cópias de segurança e o monitoramento ficam com ela, numa região só, informada no contrato ([Enterprise](/enterprise)). A missão de 90 dias vem com o engenheiro da Niadra, as metas escritas antes do dia 1, o modo sombra para a linha de base e o grupo de controle sorteado cliente a cliente. O preço é por conversa ou tarefa, de US$ 2 a 3 a cada mil, e os eventos dos sistemas não são cobrados à parte ([Preço](/preco)).

Antes do dia 1, o roteiro de [dez testes](/blog/dez-testes-antes-de-por-agentes-de-ia-de-varios-fornecedores-em-producao) diz se a memória se comporta como o contrato promete; a conta que justifica a missão, com os números do setor, está em [quanto custa o cliente contar a mesma história em cada canal](/blog/quanto-custa-o-cliente-contar-a-mesma-historia-em-cada-canal).

## Perguntas frequentes

### Preciso trocar de fornecedor de agentes para começar?

Não. Os agentes que já existem recebem três chamadas no preparo e continuam onde estão. O segundo fornecedor entra nas semanas 5 e 6, pelo mesmo caminho.

### Por que quatro semanas em modo sombra?

Porque sem linha de base não há o que comparar. No modo sombra, a memória é construída e o contexto é preparado e auditado sem mudar nada para o cliente final. Com a revisão por amostra, o engenheiro da Niadra ajusta a extração antes de qualquer cliente receber o contexto.

### E se a meta não for batida?

O relatório diz por quê, por agente e por fornecedor, com as conversas e os comprovantes. A decisão sobre continuar é do cliente, na data marcada desde o começo.

### Quanto tempo do meu time isso consome?

O dono técnico gasta a maior parte do tempo no preparo, na integração das chamadas e dos eventos. A revisão por amostra toma algumas horas por semana de quem conhece a operação. O resto é do engenheiro da Niadra.
