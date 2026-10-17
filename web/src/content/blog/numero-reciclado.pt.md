---
id: numero-reciclado
lang: pt
slug: quando-o-numero-de-telefone-muda-de-dono
title: Quando o número de telefone muda de dono, o que o agente de IA deve saber?
description: Um número reciclado chega ao agente de IA com o histórico de outra pessoa. Veja como um perfil provisório entrega só o que o novo dono disse, e o que traz o número de volta.
order: 13
kind: explainer
date: 2026-09-25T09:00:00-03:00
updated: 2026-10-01T10:00:00-03:00
category: Identidade
icon: user
cover: O número voltou. A pessoa é outra.
keywords: número de telefone reciclado, número reciclado atendimento por IA, perfil provisório, identidade do cliente por telefone, agente de IA lê dados de outra pessoa, verificação de identidade agente de IA, número compartilhado PABX, LGPD número de telefone
---

Um número de telefone que ficou meses sem aparecer volta a ligar. Para o agente de IA que usa o telefone como chave, é a mesma cliente de sempre: ele cumprimenta pelo nome, lembra da fatura contestada e oferece a segunda via. Só que a operadora reatribuiu o número, e quem está na linha nunca foi cliente. O agente acabou de ler para um estranho a vida de outra pessoa.

Este artigo explica o que uma memória compartilhada faz nesse momento: suspende o vínculo antigo, abre um perfil provisório para quem tem o número agora e só devolve o número ao dono anterior com uma prova que o novo dono não consegue dar.

## Por que o telefone não basta como chave?

O telefone é o identificador mais comum do atendimento, e o mais frágil. O número de origem pode chegar oculto ou falsificado, pertencer a um PABX que dezenas de pessoas usam, ou ter trocado de dono. Uma memória que trata o número como a pessoa vaza dados na primeira dessas situações e une pessoas diferentes na segunda.

Por isso cada identificador tem um peso, e o telefone sozinho é uma pista: reconhece a pessoa provável, e dado sensível espera uma prova mais forte. O [artigo sobre identidade entre canais](/blog/como-reconhecer-o-mesmo-cliente-em-todos-os-canais) explica os pesos. Aqui o assunto é o que acontece quando a pista aponta para a pessoa errada.

## Como a memória percebe que o número mudou de dono?

Dois sinais, nenhum deles um palpite de modelo:

1. **Silêncio longo.** Um número que fica mais de 180 dias sem aparecer e volta num evento pode ter sido reatribuído. Se o mesmo evento não traz um id de cadastro do mesmo cliente (um id de sistema, um login do app, um documento conferido), o número fica em dúvida.
2. **Ruptura com o cadastro.** Um número recente que os sistemas da empresa ligam ao id de cadastro de outro cliente, enquanto o perfil dele tem um id diferente, também fica em dúvida. É o caso da operadora que informa a troca antes de o cliente ligar.

Nos dois casos, a decisão vem do evento que chegou e das regras do espaço, e fica registrada com a origem.

## O que é um perfil provisório?

O vínculo antigo não é apagado: ele fica suspenso no perfil antigo, com toda a memória daquela pessoa. O número volta como um identificador novo, e o que quem tem o número agora disser constrói um perfil só dele.

Quando o agente lê o contexto por esse número, recebe só o que essa pessoa disse desde então. Nada do dono anterior aparece: nem o nome, nem as pendências, nem o histórico. Para o agente, é um primeiro contato, com contexto vazio e comprovante da leitura, nunca um erro. Se a pessoa nova vira cliente, a memória dela cresce no perfil provisório como em qualquer outro.

## O que traz o número de volta ao dono anterior?

Só uma prova que o dono novo não consegue dar: um id de cadastro do perfil anterior, vindo dos sistemas da empresa, de um login ou de um evento comum de um sistema de registro. Quando o CRM manda um evento com o número e o id de cadastro da cliente antiga, ou ela entra no app com login e senha, o número volta ao perfil dela e o contexto completo volta junto.

Duas coisas nunca devolvem o número:

- **Um código enviado ao próprio número.** Quem está com o aparelho hoje passaria nele. O OTP prova que a pessoa tem o telefone, não que ela é a cliente.
- **Um e-mail digitado na conversa.** Qualquer pessoa digita um e-mail. Sem um sistema de registro ou um login por trás, ele é uma declaração, não uma prova.

A pergunta "você é a Marina?" também não serve, e por um motivo diferente: ela entrega o nome. O agente pede o dado que o dono verdadeiro digitaria sem esforço, e a memória confere sem revelar nada.

## E o número que muitas pessoas usam?

O tablet da loja, o ramal do PABX e o WhatsApp do balcão são o problema oposto: um número só, muitas pessoas. Um handle ligado a três ids diferentes do mesmo tipo em 24 horas, ou a cinco em 30 dias, é marcado como compartilhado. Ele deixa de unir perfis e de servir para leitura, e o mesmo passo desfaz o que ele tinha unido: as pessoas que o número ligou antes de parecer compartilhado se separam, união por união, cada uma com o próprio histórico. Uma pessoa com dois ou três e-mails ao longo de um mês continua sendo uma pessoa, e fica com o número.

## O que isso muda para quem opera o atendimento?

- O agente nunca cumprimenta o novo dono pelo nome do antigo, porque não recebe o nome.
- O histórico do dono anterior continua inteiro, à espera da prova certa, e sai da memória só a pedido dele.
- Cada leitura por um número em dúvida deixa comprovante: quem leu, o que recebeu (nada) e por quê.
- O time vê, no Console, o peso de cada identificador e o estado de cada número (ativo, em dúvida, compartilhado, bloqueado), e pode unir ou separar com ator e motivo.

## Como a Niadra resolve

A Niadra aplica essas regras na memória compartilhada de todos os agentes da empresa: os 180 dias de silêncio, a ruptura com o cadastro, o perfil provisório, a volta só por id de cadastro, login ou sistema de registro, e a marcação do número compartilhado com a separação do que ele uniu. O agente lê o contexto pelo SDK ou por MCP como sempre; a decisão sobre quem é a pessoa fica na memória, com registro e origem, e nunca no prompt.

Simular o número que volta depois de 180 dias é o teste 4 de [dez testes antes de pôr agentes de IA de vários fornecedores em produção](/blog/dez-testes-antes-de-por-agentes-de-ia-de-varios-fornecedores-em-producao); a [Identidade](/produtos/identidade) descreve os pesos de cada identificador e os níveis de verificação.

## Perguntas frequentes

### O número reciclado apaga o histórico do cliente antigo?

Não. O vínculo é suspenso, não apagado. O histórico fica no perfil antigo, fora do contexto de quem tem o número agora, e volta inteiro quando um id de cadastro, um login ou um evento de sistema de registro liga o número de novo àquele perfil.

### O novo dono do número vira cliente?

Vira, se conversar. O que ele disser constrói um perfil provisório só dele, sem nenhuma linha do dono anterior. Se mais tarde um sistema da empresa ligar esse número a um cadastro, o perfil ganha o id e deixa de ser provisório.

### Um código por SMS não basta para confirmar quem é?

Para provar que a pessoa tem o aparelho, basta. Para provar que ela é a cliente de antes, não: o novo dono do número recebe o código do mesmo jeito. Por isso um OTP eleva o nível de verificação da conversa, mas não devolve um número em dúvida ao perfil anterior.

### Como o agente sabe que está falando com um primeiro contato?

Ele recebe um contexto vazio, com o nível de verificação da conversa e a contagem de itens retidos, e um comprovante fica registrado. O agente atende como atenderia um cliente novo e pede o dado de cadastro quando a conversa precisar dele.
