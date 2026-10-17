---
id: identidade-no-benchmark
lang: pt
slug: por-que-a-identidade-entre-canais-quebra-a-memoria-do-agente
title: Por que a identidade entre canais quebra a memória do agente: de 77,6% para 28,5%
description: No mesmo benchmark, a mesma memória acerta 77,6% com um id único e 28,5% quando cada canal manda o próprio id. O que o número diz sobre agentes de fornecedores diferentes, e por que a identidade precisa ser resolvida antes de guardar.
order: 1
kind: measured
date: 2026-09-30T22:30:00-03:00
category: Benchmark
icon: user
cover: Um id por canal, 28,5% de acerto
keywords: identidade do cliente entre canais, memória de agente com user_id, benchmark de memória para agentes, agentes de fornecedores diferentes, resolução de identidade omnichannel, memória por usuário, mem0 id por canal
---

Uma memória por usuário precisa de um `user_id` pronto, e numa operação com vários canais e fornecedores esse id não existe: a voz entrega um telefone, o WhatsApp um `wa_id`, o app um login, o CRM um número de contato. Quando cada canal manda o próprio identificador, a memória guarda três pessoas onde há uma. O [benchmark da Niadra](/benchmark) mediu esse efeito: a memória de código aberto mais usada acerta 77,6% dos cenários entre canais com um id único e 28,5% com um id por canal, nos mesmos 165 cenários, com o mesmo agente e o mesmo juiz. A Niadra, que resolve a identidade antes de guardar, acerta 98,7%.

## O que o benchmark mediu

O conjunto tem 356 cenários sintéticos em português e inglês, cada um com uma cliente que fala em mais de um canal e uma pergunta cuja resposta só existe se a memória cruzou os canais. Todos os sistemas rodam com o mesmo agente, o mesmo juiz e os mesmos modelos, e a comparação é feita nos 165 cenários válidos que todos rodaram: um cenário só vale quando o agente acerta com o histórico inteiro no prompt e erra sem memória nenhuma. Os arquivos da execução de 30/09/2026, com três repetições, estão [publicados no repositório do script](https://github.com/ainiadra/niadra-sdk-python/tree/main/benchmarks/results/2026-09-30-6e6d07), e a página do benchmark mostra todas as medidas, inclusive as que a Niadra não lidera.

Para os sistemas que recebem um `user_id` pronto, o script roda dois cenários:

- **Mesmo id em todos os canais.** A aplicação já resolveu quem é a pessoa, e a memória recebe o mesmo id na voz, no WhatsApp e no app. É a condição em que esses sistemas são medidos nos próprios benchmarks.
- **Id de cada canal.** Cada canal manda o identificador que tem: o telefone, o `wa_id`, o login. É a condição de uma empresa com agentes de fornecedores diferentes, em que ninguém resolveu a identidade antes.

A mesma memória, com os mesmos dados, nos mesmos cenários: 77,6% pelo juiz no primeiro cenário, 28,5% no segundo. Com um reordenador de resultados, 76,4% e 27,3%. As referências ajudam a ler a escala: o agente com o histórico inteiro no prompt acerta 100%, e sem memória nenhuma, 8,5%.

## Por que o id por canal derruba o acerto

O mecanismo é simples. Uma memória por usuário indexa tudo pelo `user_id`: os fatos extraídos da conversa, os vetores, o grafo, as entidades. Quando o agente de WhatsApp pergunta "o que essa pessoa já disse?", a busca procura pelo id do WhatsApp e encontra o que entrou pelo WhatsApp. A ligação de ontem entrou pelo telefone, num id que a memória não liga a esse. Para ela, são duas pessoas, e nenhuma busca cruza.

O cenário do id único esconde isso, porque supõe que alguém resolveu a identidade antes. Numa aplicação de um fornecedor só, com login, esse alguém é a própria aplicação. Numa empresa com o agente de voz num fornecedor, o de WhatsApp em outro e o agente de cobrança no time interno, não há esse alguém: cada fornecedor recebe o identificador do próprio canal e guarda a memória do próprio agente. A memória "compartilhada" vira três memórias que não se falam, e o cliente conta a história de novo.

Há um segundo efeito, que a medida de privacidade mostra. Com um id por canal, nenhum dado sensível vazou para uma conversa que não provou quem é: zero. Não por governança, mas porque nada cruza; é o mesmo motivo pelo qual o acerto cai. Com o id único, a mesma memória entregou 32 de 32 valores sensíveis a conversas sem verificação, porque um id pronto não carrega nível de prova: quem tem o id tem tudo. A Niadra entregou 0 de 32, com o nível de verificação decidido por conversa.

## O que muda quando a identidade é resolvida antes de guardar

A Niadra não recebe um `user_id`. Recebe os eventos de cada fonte com os identificadores que a fonte tem (`phone_e164`, `wa_id`, `email`, `app_user_id`, o id do contato no CRM, o id do cliente no ERP) e resolve de quem cada evento fala antes de guardar. Três regras fazem isso funcionar em produção:

1. **Cada identificador tem um peso.** O telefone é uma pista; o documento dito na ligação é declarado; o e-mail confirmado por código é verificado; o login com senha é autenticado. Dois identificadores viram o mesmo cliente quando há evidência: um evento do sistema que diz que o login 88213 tem aquele telefone, ou um código confirmado. Nome parecido nunca une.
2. **Toda união é reversível.** Cada fato fica preso ao identificador e à conversa de onde veio, e a união é uma afirmação com método, data e origem. Retirada a afirmação, os fatos voltam para o dono certo. É o que evita que a fatura do cliente A apareça na ligação do cliente B.
3. **O nível de verificação é da conversa, não do id.** A ligação começa no nível que a operadora atestou; um código confirmado sobe o nível; a política diz o que cada nível pode ler. O contexto que o agente recebe muda conforme o que a conversa já provou.

O resultado nos mesmos 165 cenários é 98,7% pelo juiz, com o contexto compilado antes da conversa e nenhum modelo de IA na leitura. A [página de Identidade](/produtos/identidade) mostra o mecanismo, e o post sobre [como reconhecer o mesmo cliente em todos os canais](/blog/como-reconhecer-o-mesmo-cliente-em-todos-os-canais) detalha os níveis.

## Quando uma memória por usuário é a escolha certa

Quando a aplicação já é dona da identidade. Um app com login, um agente só, um fornecedor só: o `user_id` existe, é autenticado, e uma memória por usuário faz o que promete, no cenário em que foi medida. Ela é mais simples de operar, e o gasto de modelo medido no benchmark, para quem hospeda, fica abaixo de US$ 2 por mil conversas na opção mais barata. Nesse caso, a Niadra acrescenta uma camada que a empresa ainda não precisa.

O problema começa no segundo canal, ou no segundo fornecedor. A pergunta que decide é: quem resolve a identidade antes de a memória guardar? Se a resposta é "ninguém", o número que vale é o 28,5%, não o 77,6%.

Os limites do benchmark valem para os dois lados. Os cenários são sintéticos, a Niadra o produziu, e os outros sistemas rodaram uma repetição, contra três da Niadra. A resposta a isso é a reprodução: o script, o conjunto de dados e a configuração são públicos, e qualquer pessoa pode rodar de novo.

## Como a Niadra resolve

A Niadra é a camada de memória omnichannel: recebe os eventos de todo canal, plataforma e sistema, reconhece de quem cada um fala pelos identificadores que a fonte tem, com peso e união reversível, e monta uma memória só de cada cliente, que qualquer agente, de qualquer fornecedor, lê no contexto da tarefa dele e no nível que a conversa provou. Os adaptadores para [LiveKit, Vapi, WhatsApp Cloud API, LangGraph e os outros](/integracoes) entregam os identificadores de cada plataforma do jeito que ela os tem.

## Perguntas frequentes

### O que são os cenários "mesmo id em todos os canais" e "id de cada canal"?

São as duas formas de entregar a identidade a uma memória que recebe um `user_id` pronto. No primeiro, a aplicação já resolveu quem é a pessoa e manda o mesmo id em todos os canais. No segundo, cada canal manda o identificador que tem, como acontece quando os agentes são de fornecedores diferentes.

### A diferença não é só configurar um id único?

Configurar o id único exige que alguém resolva a identidade antes de a memória guardar, em todos os canais e fornecedores, com peso para cada identificador e união reversível. Esse trabalho é o que a Niadra faz. Sem ele, o id único não existe para um telefone que liga pela primeira vez.

### Onde estão os números?

Na [página do benchmark](/benchmark), com todas as medidas, e nos [arquivos da execução de 30/09/2026](https://github.com/ainiadra/niadra-sdk-python/tree/main/benchmarks/results/2026-09-30-6e6d07), com o script, o conjunto de dados e a configuração para reproduzir.
