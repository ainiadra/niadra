---
id: agente-afirma-sem-consultar
lang: pt
slug: o-agente-de-ia-afirmou-o-que-nunca-consultou-dois-casos-publicos
title: O agente de IA afirmou o que nunca consultou: dois casos públicos e o mecanismo da falha
description: Um tribunal canadense condenou a Air Canada pelo que o agente do site disse sobre tarifas de luto. Um agente de suporte da Cursor inventou uma política de um dispositivo e clientes cancelaram. Por que acontece, etapa por etapa, e o que teria mudado o desfecho.
order: 2
kind: case
date: 2026-10-01T09:00:00-03:00
category: Casos
icon: eye
cover: Afirmou sem consultar. A empresa pagou
keywords: agente de IA deu informação errada quem paga, Moffatt v. Air Canada, caso Cursor agente de suporte política inventada, responsabilidade por resposta de agente de IA, agente de IA inventa política, alucinação em atendimento ao cliente, como impedir que o agente afirme o que não consultou, contrato de afirmação para agentes de IA
---

Em 14 de fevereiro de 2024, o Tribunal de Resolução Civil da Colúmbia Britânica condenou a Air Canada a pagar CAN$ 812,02 a um passageiro porque o agente de IA do site da companhia disse uma coisa sobre tarifas de luto e a página de política dizia outra ([Moffatt v. Air Canada, 2024 BCCRT 149, resumo da Dentons](https://www.dentonsdata.com/airline-ordered-to-compensate-a-b-c-man-because-its-chatbot-provided-inaccurate-information/)). Em abril de 2025, o agente de suporte da Cursor, que assinava "Sam", respondeu a um desenvolvedor que ser desconectado ao trocar de máquina era o comportamento esperado de uma política de um dispositivo por assinatura. A política não existia; clientes cancelaram em público e o cofundador pediu desculpas ([The Register, 18/04/2025](https://www.theregister.com/2025/04/18/cursor_ai_support_bot_lies/)). Os dois casos têm o mesmo mecanismo: o agente afirmou, com segurança, algo que nunca consultou, e ninguém viu antes de o cliente ler.

Este post foi escrito para quem responde pelos agentes que falam com clientes. Ele conta os dois casos com as palavras das fontes, separa as etapas da falha e diz o que teria mudado o desfecho em cada uma. As fontes foram lidas em 1º de outubro de 2026.

## O caso da Air Canada, com as palavras das partes

Em novembro de 2022, Jake Moffatt precisou viajar de Vancouver a Toronto para o funeral da avó, que morreu em 11 de novembro. Antes de comprar, perguntou ao agente de IA do site da Air Canada sobre a tarifa de luto. A resposta, reproduzida na decisão: "Se você precisa viajar imediatamente ou já viajou e quer submeter a sua passagem a uma tarifa de luto reduzida, faça isso em até 90 dias da data em que a passagem foi emitida, preenchendo o nosso formulário de pedido de reembolso" ([BD&P, 28/02/2024](https://www.bdplaw.com/insights/ai-conversations-and-chatbot-accountability-under-scrutiny-the-case-of-the-too-helpful-chatbot)). Ele comprou as passagens em 12 e 18 de novembro e pediu o ajuste dentro do prazo, com a certidão de óbito. A Air Canada negou: a página "Bereavement travel" dizia que a política não valia para pedidos feitos depois da viagem.

No tribunal, a companhia argumentou que o agente de IA era "uma entidade legal separada, responsável pelos próprios atos" ([Dentons](https://www.dentonsdata.com/airline-ordered-to-compensate-a-b-c-man-because-its-chatbot-provided-inaccurate-information/)). O membro do tribunal, Christopher C. Rivers, rejeitou: "Deveria ser óbvio para a Air Canada que ela é responsável por toda a informação do seu site", venha ela de uma página estática ou do agente interativo. E acrescentou que "não há razão para que o sr. Moffatt soubesse que uma seção do site da Air Canada é precisa e outra não" ([BD&P](https://www.bdplaw.com/insights/ai-conversations-and-chatbot-accountability-under-scrutiny-the-case-of-the-too-helpful-chatbot)). A Air Canada, escreveu ele, "não tomou o cuidado razoável de garantir que o seu agente fosse preciso". A conta: CAN$ 650,88 de diferença de tarifa, CAN$ 36,14 de juros e CAN$ 125 de custas ([Dentons](https://www.dentonsdata.com/airline-ordered-to-compensate-a-b-c-man-because-its-chatbot-provided-inaccurate-information/)).

O valor é pequeno. O precedente não é: a empresa responde pelo que o agente de IA diz como responde por qualquer página do site.

## O caso da Cursor, com as palavras das partes

Em abril de 2025, usuários do Cursor, o editor de código com IA da Anysphere, começaram a ser desconectados quando abriam o programa em uma segunda máquina. Um deles escreveu ao suporte e recebeu uma resposta assinada por "Sam" dizendo que aquilo era o comportamento esperado de uma nova política de um dispositivo por assinatura, por segurança. O desenvolvedor publicou a troca no Reddit e no Hacker News; a discussão de 15 de abril de 2025 tem o título "O suporte do Cursor IDE alucina política de bloqueio e causa cancelamentos" ([Hacker News](https://news.ycombinator.com/item?id=43683012)). Usuários anunciaram cancelamentos no mesmo fio.

Não havia política. O cofundador Michael Truell respondeu: "Não temos essa política. Você é, claro, livre para usar o Cursor em várias máquinas", e explicou que a resposta tinha vindo de um agente de IA de primeira linha do suporte e estava errada; as desconexões vinham de um defeito no tratamento de sessões ([The Register, 18/04/2025](https://www.theregister.com/2025/04/18/cursor_ai_support_bot_lies/); [Fortune, 19/04/2025](https://www.yahoo.com/news/customer-support-ai-went-rogue-120000474.html)). A empresa reembolsou o desenvolvedor e anunciou que "qualquer resposta de IA usada no suporte por e-mail agora é claramente rotulada como tal". O caso está registrado como o incidente 1039 da [AI Incident Database](https://incidentdatabase.ai/cite/1039/).

Aqui também o custo direto foi pequeno. O custo real foram os cancelamentos feitos em público e uma semana de manchetes sobre uma política que nunca existiu.

## O mecanismo, etapa por etapa

Os dois agentes falharam do mesmo jeito, em quatro etapas. Nenhuma delas é exclusiva de companhia aérea ou de editor de código.

1. **O agente respondeu a partir de texto, não da fonte oficial.** Na Air Canada, a fonte oficial era a página de política; o agente gerou uma frase coerente que a contradizia. Na Cursor, não havia política nenhuma; o modelo preencheu a lacuna com uma explicação plausível para um comportamento que era um defeito. Um modelo de IA produz a frase mais provável, e "temos uma política de segurança" é mais provável do que "não sei por que você foi desconectado".
2. **Nada exigiu evidência antes de a afirmação sair.** Um prazo de 90 dias e uma política de um dispositivo são afirmações de fato. Nenhum dos dois sistemas exigia que uma afirmação desse tipo apontasse para a página, o registro ou a ferramenta que a sustentasse. A frase saiu porque nada a segurou.
3. **Ninguém conseguia ver, depois, o que o agente tinha consultado.** A Air Canada só soube do problema quando o cliente mostrou a captura de tela. A Cursor soube pelo Reddit. Em nenhum dos casos a empresa tinha o registro do turno: o que o agente leu, o que chamou, o que afirmou.
4. **A pessoa não sabia que falava com uma IA, ou não sabia o peso daquilo.** O tribunal canadense foi direto: não cabe ao cliente saber qual parte do site é confiável. A Cursor passou a rotular as respostas de IA depois do caso. Desde 2 de agosto de 2026, na União Europeia, essa rotulagem é obrigação legal ([o que a regulação de 2026 pede](/blog/o-que-a-regulacao-de-2026-pede-de-um-agente-de-ia-que-fala-com-o-cliente)).

Há um quinto fator que os dois casos não mostram, mas que aparece em qualquer empresa com agentes de vários fornecedores: a afirmação errada de um agente vira memória do próximo. O agente de WhatsApp promete um prazo que não existe; o agente de voz, horas depois, lê "prazo prometido" e o confirma. O erro de um fornecedor passa a ser dito por outro, com mais convicção a cada passagem.

## O que teria mudado o desfecho

Em termos de mecanismo, e não de produto:

- **Uma regra sobre o que o agente pode afirmar e com que prova.** Prazo, preço, política, promessa de ação: cada categoria exige uma evidência no próprio turno, um valor com origem, uma ferramenta chamada, um trecho de documento. A frase sobre os 90 dias teria saído marcada como sem evidência; a frase sobre a política de um dispositivo, idem. A conferência é lexical e roda antes de o cliente ler; não precisa de outro modelo para julgar o primeiro.
- **O registro do turno.** O que o agente leu, chamou e afirmou, com a versão do prompt e do modelo. A Cursor teria achado o problema no próprio registro, em vez de no Reddit, e a Air Canada teria visto que o agente nunca consultou a página de política.
- **A lacuna dita em voz alta.** "Não encontrei a política sobre isso; vou verificar com uma pessoa" é uma resposta aceitável, e teria custado menos do que as duas que saíram.
- **O rótulo.** A pessoa precisa saber que fala com uma IA e em nome de quem ela fala. Não evita o erro; muda o que a pessoa faz com a resposta.

## O que não sabemos

- Os casos são de 2024 e 2025. Não sabemos como a Air Canada e a Cursor operam os seus agentes hoje; a Cursor anunciou a rotulagem na época, e não encontramos atualização pública posterior.
- A decisão canadense é de um tribunal de pequenas causas, com valor baixo. Ela é citada como precedente de responsabilidade, mas não sabemos de decisão de instância superior que a confirme ou a contradiga.
- Não sabemos quantas assinaturas a Cursor perdeu. As fontes falam em cancelamentos anunciados em público, sem número.
- Não sabemos o que o sistema da Cursor consultou antes de responder. A explicação de "preencheu a lacuna" é a leitura das fontes e a nossa; a empresa falou só em "resposta incorreta".

## Como a Niadra resolve

Na Niadra, a memória diz ao agente o que ele pode afirmar e confere o que ele afirmou. O [contrato de afirmação](/produtos/conferencia) declara, por categoria (preço, prazo, política, promessa de ação), a evidência que a afirmação precisa ter no turno: um valor com origem, uma ferramenta chamada, um trecho ancorado num documento. A conferência roda no processo do agente, por léxico, sem outro modelo, no último ponto antes do cliente; o que não se sustenta é anotado, contado ou, numa saída que pode ser alterada e só quando a correção é inequívoca, reescrito pelo valor com origem. A Niadra nunca bloqueia com mensagem genérica e nunca reescreve um documento assinado: ela confere e conta, e a empresa decide a ação. O [registro de turno](/produtos/registro) guarda o que o agente leu, chamou, mostrou, afirmou e decidiu, com o prompt e o modelo fixados, e permite reproduzir uma conversa real no seu CI sem que o conteúdo saia da sua empresa.

No [benchmark de 30/09/2026](/benchmark), a mesma regra que o SDK aplica foi aplicada pelo script às respostas de todos os sistemas: a Niadra teve zero valores sem lastro em 1.068 respostas, e o agente lendo o contexto da Niadra acertou 98,8% dos 338 casos de continuidade entre canais, com o mesmo agente e o mesmo juiz para todo sistema. O post sobre [por que o agente fala o preço errado](/blog/por-que-o-agente-fala-o-preco-errado) mede o que muda quando o turno traz o estado de agora e não só o que foi dito.

## Perguntas frequentes

### A empresa responde pelo que o agente de IA diz ao cliente?

No caso canadense, sim: o tribunal tratou o agente como parte do site e rejeitou a tese de entidade separada. Cada jurisdição decide a sua, mas a regra prática é a mesma: quem publica o agente responde pelo que ele afirma.

### Um segundo modelo revisando o primeiro resolve?

Reduz, e custa latência e dinheiro a cada turno. A conferência por contrato é diferente: lexical, determinística, sem modelo, e ligada à evidência do próprio turno (o que a ferramenta devolveu, o que a leitura de contexto serviu). Ela não julga se a frase soa bem; confere se o número e a promessa têm origem.

### Como sei quantas afirmações sem evidência os meus agentes fazem hoje?

Em modo de contagem: o contrato roda sem mudar a saída e cada afirmação entra no registro de turno com o veredito. Em uma semana, a empresa tem o número por agente e por fornecedor, antes de decidir qualquer ação.
