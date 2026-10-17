---
id: memoria-sem-configuracao
lang: pt
slug: memoria-para-agentes-de-ia-sem-configuracao
title: Memória para agentes de IA sem configuração: conectar o SDK e nunca mais abrir o painel
description: Uma memória de agentes que exige declarar tipos, campos e operações antes de funcionar vira um projeto de meses. O que dá para a memória aprender sozinha do tráfego, o que fica protegido até uma pessoa decidir e como conferir que funcionou.
order: 1
kind: explainer
date: 2026-10-08T09:00:00-03:00
category: Implantação
icon: bolt
cover: Conectar e esquecer o painel
keywords: memória para agentes de IA sem configuração, memória de agentes zero configuração, como implantar memória de agentes de IA rápido, inferir tipos de objeto dos sistemas, memória que aprende os campos do CRM e do ERP, implantar agentes de IA sem projeto de integração, memória gerenciada para agentes de IA
---

Uma memória de agentes de IA funciona sem configuração quando aprende sozinha, a partir do que os seus sistemas e agentes já mandam, quais objetos existem (pedido, fatura, processo, chamado), quais campos eles têm, em que estados ficam, quando vencem e quais operações os agentes executam sobre eles. Na Niadra, o único passo manual é criar uma chave por agente. A partir daí, o SDK manda os eventos, e a memória aprende o resto sem ninguém abrir o painel.

Este post explica o que dá para aprender do tráfego, o que precisa ficar protegido até uma pessoa decidir e como conferir, no seu próprio caso, que a memória está entregando o que deveria.

## Por que a configuração vira o gargalo?

Toda memória de agentes precisa saber o que é um pedido para dizer ao agente de voz que o pedido 48213 saiu para entrega. O caminho tradicional é declarar isso antes: o tipo "pedido", os campos, quais são datas, quais são valores, qual campo identifica o pedido, quais estados são finais, quais operações fecham uma pendência. Em uma empresa com CRM, ERP, help desk e um sistema próprio, isso são dezenas de tipos e centenas de campos.

O problema não é o esforço de uma vez. É o que acontece depois:

- **Cada campo novo para.** O sistema passa a mandar `data_de_entrega` e nenhum agente vê, até alguém declarar.
- **O limite fica escondido.** Uma regra de "mostrar no máximo oito campos" corta o campo que importava, e ninguém sabe até o agente errar.
- **Quem configura não é quem sofre.** O time que integra declara uma vez; o time de atendimento descobre a lacuna quando o cliente reclama.

Uma memória que depende de configuração para mostrar um dado novo transforma cada mudança do sistema em um chamado.

## O que a memória consegue aprender sozinha?

Quase tudo o que hoje se declara está escrito no próprio tráfego. A Niadra aprende, de forma contínua:

1. **Os tipos de objeto.** O primeiro evento de um sistema com um objeto que ninguém declarou já cria o tipo, espelhando o sistema que o mandou.
2. **Os campos e o que cada um é.** O tipo de cada campo vem dos valores: data, data e hora, valor monetário, número, sim ou não, texto. Um valor estranho isolado não muda o tipo de um campo.
3. **O identificador escrito na conversa.** Quando o cliente escreve "pedido 48213" ou um número de processo, e esse valor identifica um único objeto dele, a conversa fica ligada ao objeto sem nenhuma regra declarada.
4. **Os estados e quais são finais.** A sequência dos estados que chegam mostra quais são finais. O estado final vira desfecho medido.
5. **Os vencimentos.** Um campo de data com nome de prazo e valores no futuro vira o vencimento do objeto, com aviso antes do fim.
6. **As operações.** Os nomes que os agentes e os sistemas usam (avisar o cliente, anexar documento, pagar custas) viram o vocabulário que liga uma promessa à ação que a cumpre.
7. **As palavras do seu idioma.** Os nomes de campo e os valores aparecem para o agente na língua do espaço ("Valor da causa", "situação em andamento"), em vez de `claim_amount` e `in_progress`.

Nada disso exige que a empresa diga o que é o quê. A declaração continua possível, mas como ajuste opcional: um rótulo melhor, uma regra de acesso, um campo que você quer marcar como sensível. Quando há declaração, ela sempre prevalece sobre o que foi aprendido.

## O que fica protegido até uma pessoa decidir?

Aprender sozinho não pode significar expor sozinho. A regra que a Niadra segue: o que é aprendido nunca afrouxa a privacidade, só aperta.

- **Dado pessoal fica retido.** Um campo que nenhum tipo declara, cujo valor tem forma de e-mail, telefone ou número de documento, ou cujo nome diz contato, endereço ou documento, não chega aos agentes até uma pessoa declarar o campo. A retenção aparece como nota no painel, nunca em silêncio.
- **Texto livre longo é tratado como conteúdo.** Uma movimentação de processo ou um comentário de chamado não entra na linha do objeto como campo comum.
- **Nome de profissional da empresa é diferente de nome de cliente.** O advogado responsável ou o gerente da conta, num objeto que vem do sistema de registro, aparece; o nome de um cliente ou paciente fica retido.
- **Ninguém vira equipe por declarar.** Uma conversa com a equipe é interna quando a pessoa é identificada pelo id do sistema da empresa; um telefone ou um e-mail sempre identificam um cliente.
- **Afrouxar continua sendo decisão de uma pessoa.** Apertar uma proteção não pede aprovação; afrouxar entra como mudança proposta e só vale depois que uma pessoa da empresa aprova no Console.

## Como conferir que a memória está funcionando?

Sem configuração, a pergunta muda de "declarei tudo?" para "o agente recebeu o que precisava?". Três verificações dão a resposta:

- **O bloco tem a resposta?** Para uma pergunta real de cliente, a informação certa estava no contexto entregue ao agente? Essa é a métrica que separa erro de memória de erro do modelo, e o post sobre [como separar os dois erros](/blog/a-memoria-tinha-a-resposta-erro-da-memoria-ou-do-modelo) mostra como medir.
- **O que foi aprendido está visível.** O painel mostra cada tipo, campo e operação como "Padrão da Niadra", "Inferido pela Niadra" ou "Declarado", com o histórico de cada mudança e quem a fez.
- **O que foi retido está explicado.** Cada campo retido, cada limite atingido e cada nome recusado vira uma nota com o motivo. Uma nota nunca é tarefa obrigatória.

## Como a Niadra resolve

Na Niadra, o cliente cria uma chave por agente, conecta o SDK e não precisa voltar ao painel. A memória aprende os objetos dos [sistemas da empresa](/produtos/sistemas), entrega o [estado de agora](/produtos/estado) e o que foi dito sobre cada objeto no [contexto](/produtos/contexto) de cada agente, e mede os desfechos a partir dos estados finais na [medição](/produtos/medicao). Tudo o que é aprendido fica registrado e visível no [Console](/produtos/console), onde cada valor aparece como padrão, inferido ou declarado, com o ajuste opcional ao lado. A documentação explica [como os campos não declarados são servidos](https://docs.niadra.com/concepts/object-types) e [como a memória do objeto liga conversas a pedidos, faturas e processos](https://docs.niadra.com/concepts/object-memory).

## Perguntas frequentes

### Preciso declarar algum tipo de objeto para começar?

Não. O primeiro evento de cada sistema já cria o tipo, e os campos aparecem conforme chegam. A declaração serve para ajustar: um rótulo melhor, um campo sensível, uma regra de acesso.

### E se a memória aprender algo errado?

O que é aprendido nunca afrouxa uma proteção, e cada mudança aparece no Console com autor e data. Uma declaração sua sempre vence o que foi aprendido, então corrigir é declarar o valor certo.

### Um campo com dado pessoal pode chegar a um agente sem eu saber?

Não. Campo não declarado com valor de e-mail, telefone, documento ou nome de cliente fica retido até uma pessoa declarar, e a retenção aparece como nota.

### Isso funciona para agentes internos e de atendimento ao mesmo tempo?

Sim. A mesma chave serve uma conversa com a equipe e uma conversa com o cliente; o que cada conversa pode ver é decidido por quem está do outro lado, como explica o post sobre [o que o agente da equipe e o do cliente podem ver](/blog/memoria-para-agentes-de-ia-internos).
