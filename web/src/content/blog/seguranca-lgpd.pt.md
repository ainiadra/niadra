---
id: seguranca-lgpd
lang: pt
slug: lgpd-e-seguranca-na-memoria-de-clientes-para-ia
title: LGPD, GDPR e agentes de IA: o que o time de segurança vai perguntar
description: O que provar antes de agentes de IA lerem dados de clientes: DPA, cifra, chaves em HSM, acesso por finalidade, auditoria e direitos do titular.
order: 4
kind: guide
date: 2026-09-21T21:49:00-03:00
updated: 2026-10-01T10:00:00-03:00
category: Segurança
icon: shield
cover: LGPD e GDPR para agentes de IA
keywords: LGPD e agentes de IA, como usar IA com dados de clientes na LGPD, GDPR e inteligência artificial, controlador e operador na LGPD, DPA com fornecedor de IA, chaves em HSM FIPS 140-3, direito ao apagamento em IA, revisão de segurança de fornecedor de IA
---

Para deixar agentes de IA lerem dados de clientes dentro da LGPD e do GDPR, a empresa precisa provar seis coisas: quem é controlador e quem é operador, como o dado é cifrado e onde ficam as chaves, o que cada agente pode ler, quem leu o quê, o que chega ao modelo de IA e como o titular exerce os direitos dele. Com agentes de vários fornecedores lendo a mesma memória de clientes, cada resposta precisa valer para todos eles. O time de segurança de um banco, de uma seguradora ou de uma operadora de saúde vai pedir cada padrão pelo nome, com evidência.

## Quem é o controlador, quem é o operador e o que o DPA precisa dizer?

A empresa que atende o cliente é a controladora: ela decide para que o dado é tratado. Quem trata o dado em nome dela é operador ([LGPD, art. 5º, VI e VII](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art5)). O GDPR chama os mesmos papéis de responsável pelo tratamento e subcontratante ([art. 4º](https://eur-lex.europa.eu/legal-content/PT/TXT/?uri=CELEX:32016R0679#art_4)).

Com agentes de três fornecedores, a empresa tem vários operadores tocando o mesmo cliente. A camada de memória acima deles também é operadora. O operador segue as instruções do controlador ([LGPD, art. 39](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art39)), e o contrato de tratamento de dados (DPA) é onde elas ficam escritas. Pelo [art. 28 do GDPR](https://eur-lex.europa.eu/legal-content/PT/TXT/?uri=CELEX:32016R0679#art_28), o contrato cobre:

- tratamento conforme instruções documentadas;
- os provedores que tratam dados em nome do fornecedor nomeados no contrato, e nenhum novo sem autorização por escrito;
- medidas de segurança e apoio aos direitos do titular;
- aviso de incidente ao controlador sem demora injustificada ([GDPR, art. 33](https://eur-lex.europa.eu/legal-content/PT/TXT/?uri=CELEX:32016R0679#art_33)), para ele comunicar a autoridade e o titular ([LGPD, art. 48](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art48));
- apagamento ou devolução dos dados no fim do contrato;
- acesso às auditorias que demonstram conformidade.

## Como o dado é cifrado, e quem guarda as chaves?

O [art. 32 do GDPR](https://eur-lex.europa.eu/legal-content/PT/TXT/?uri=CELEX:32016R0679#art_32) cita a cifragem entre as medidas de segurança. O [art. 46 da LGPD](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art46) exige proteção contra acesso não autorizado desde a concepção do produto. Os padrões, pelo nome:

- **Em repouso:** [AES-256-GCM](https://csrc.nist.gov/pubs/sp/800/38/d/final). O modo GCM autentica o dado, e qualquer alteração faz a decifragem falhar.
- **Em trânsito:** [TLS 1.3](https://www.rfc-editor.org/rfc/rfc8446).
- **Campo a campo:** dados pessoais ganham uma segunda cifra, que protege o dado dentro do próprio banco.
- **Uma chave por empresa:** se um erro misturar registros de duas empresas, a decifragem falha em vez de vazar.

A força da cifra depende de quem controla a chave. As chaves-mestras precisam viver em HSM com validação [FIPS 140-3](https://csrc.nist.gov/pubs/fips/140-3/final), conferível no [programa CMVP do NIST](https://csrc.nist.gov/projects/cryptographic-module-validation-program). A norma define quatro níveis crescentes de segurança, e a validação informa o nível de cada módulo. Três respostas contam: a chave-mestra nunca sai do HSM, quem usa a chave nunca a administra, e a rotação é automática.

## Quem pode ler o quê, e como provar quem leu?

O acesso começa negado. Cada agente tem credencial própria, e a permissão segue a finalidade dele. Essa regra aplica a finalidade e a necessidade da LGPD ([art. 6º, I e III](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art6)) e a limitação das finalidades e a minimização dos dados do GDPR ([art. 5(1)(b) e (c)](https://eur-lex.europa.eu/legal-content/PT/TXT/?uri=CELEX:32016R0679#art_5)). Dado de saúde é dado sensível ([LGPD, art. 5º, II](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art5); [GDPR, art. 9º](https://eur-lex.europa.eu/legal-content/PT/TXT/?uri=CELEX:32016R0679#art_9)). O agente de cobrança nunca deve vê-lo.

A regra vale para o contexto que o agente recebe antes de responder e para cada busca que ele faz no histórico. O time da empresa precisa simular o que cada agente enxerga antes de liberar o acesso, e cortar um fornecedor na hora.

Cada leitura deixa registro: qual agente leu, o quê, para quê e quando. O registro é encadeado por hash [SHA-256](https://csrc.nist.gov/pubs/fips/180-4/upd1/final), e qualquer alteração quebra a cadeia. Uma cópia segue em tempo real para o SIEM da empresa. Com esse registro, o controlador demonstra conformidade ([LGPD, art. 6º, X](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art6); [GDPR, art. 5(2)](https://eur-lex.europa.eu/legal-content/PT/TXT/?uri=CELEX:32016R0679#art_5)). Um trecho do registro de leituras:

- **14h07**, agente de voz do fornecedor B: recebeu o contexto de voz.
- **14h05**, agente do app: recebeu o contexto completo.
- **14h02**, agente de WhatsApp do fornecedor A: recebeu o contexto de chat.
- **09h31**, agente de cobrança, interno: negado, porque cobrança não vê pendência técnica.

A linha negada prova que a regra de finalidade funcionou.

## O que chega à IA da Niadra, e onde o dado fica?

O contrato precisa garantir três coisas sobre o modelo de IA:

- **Mascaramento:** e-mail, telefone, cartão, IBAN e endereço são mascarados antes de a IA da Niadra ler as conversas. Ao agente, o dado sensível só chega depois da verificação que a política exige.
- **Nenhum treino:** os dados não treinam modelos de IA, nem os da Niadra nem os dos provedores que a Niadra contrata.
- **Nenhuma venda:** os dados nunca são vendidos. Só leem os agentes que a empresa autorizar e os provedores nomeados no contrato.

Treinar modelo com conversas de atendimento seria uma finalidade nova. As duas leis vedam o tratamento posterior incompatível com a finalidade original ([LGPD, art. 6º, I](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art6); [GDPR, art. 5(1)(b)](https://eur-lex.europa.eu/legal-content/PT/TXT/?uri=CELEX:32016R0679#art_5)).

Os dados ficam numa região só, informada no contrato, e toda saída precisa estar escrita nele. Na Niadra, a única saída é texto já mascarado, para a leitura da IA, sem retenção e sem treino, com o provedor nomeado no contrato. A transferência internacional tem regras próprias nas duas leis ([LGPD, art. 33](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art33); [GDPR, art. 44](https://eur-lex.europa.eu/legal-content/PT/TXT/?uri=CELEX:32016R0679#art_44)). Certificação também tem dono. [ISO 27001](https://www.iso.org/standard/27001), [SOC 2](https://www.aicpa-cima.com/topic/audit-assurance/audit-and-assurance-greater-than-soc-2) e [PCI DSS](https://www.pcisecuritystandards.org/standards/pci-dss/) podem ser do fornecedor ou da nuvem onde ele opera. O time de segurança deve perguntar de quem é cada selo.

## Como o titular exerce os direitos dele?

O titular tem direito a acesso, correção, portabilidade e eliminação ([LGPD, art. 18](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art18); [GDPR, arts. 15 a 20](https://eur-lex.europa.eu/legal-content/PT/TXT/?uri=CELEX:32016R0679#art_15)). O operador ajuda o controlador a atender cada pedido ([GDPR, art. 28(3)(e)](https://eur-lex.europa.eu/legal-content/PT/TXT/?uri=CELEX:32016R0679#art_28)). Por isso os direitos precisam existir por API.

Numa memória de clientes, o apagamento é o direito mais difícil. Cada conversa gera fatos, pendências, contexto e índice de busca. A resposta séria percorre tudo o que derivou do dado e emite um comprovante do que foi apagado. Depois disso, nenhum agente recebe aquele dado no contexto nem o encontra na busca.

Na Niadra, a empresa define o prazo do arquivo de eventos, das amostras de revisão e do registro de entregas ([LGPD, art. 16](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art16); [GDPR, art. 5(1)(e)](https://eur-lex.europa.eu/legal-content/PT/TXT/?uri=CELEX:32016R0679#art_5)). Vencido o prazo, o dado é apagado de verdade, e as cópias de segurança expiram em até 35 dias.

## O que entregar na revisão de segurança?

Cada resposta precisa chegar com a evidência ao lado.

| Pergunta do time de segurança | O que responder | Evidência |
|---|---|---|
| Quem é controlador e quem é operador? | A empresa controla; a memória opera sob instrução | DPA assinado, com os provedores nomeados |
| Onde ficam as chaves? | Chave-mestra em HSM FIPS 140-3 nível 3, chave de dados exclusiva por empresa | Questionário de segurança e modelo de ameaças |
| O que cada agente enxerga? | O que a finalidade dele permite | Simulação por agente, antes de liberar |
| Quem leu o dado deste cliente? | Registro encadeado por SHA-256 | Exportação em tempo real para o SIEM |
| Como as defesas são testadas? | Teste de invasão por empresa independente | Relatório, sob acordo de confidencialidade |
| Como apagar um cliente? | Apagamento em tudo o que derivou do dado | Comprovante de apagamento |

## Como a Niadra resolve

A Niadra é a memória compartilhada de todos os agentes de IA de uma empresa, de atendimento e internos, em todos os canais e fornecedores. Ela atua como operadora, com DPA assinado. As [seis camadas de segurança](/recursos#seguranca) cobrem AES-256-GCM e TLS 1.3, chave-mestra em HSM FIPS 140-3 nível 3, acesso por finalidade, auditoria encadeada por SHA-256, [LGPD e GDPR por desenho](/recursos#privacidade) e nuvem com certificação ISO 27001, SOC 2 e PCI DSS. Os dados nunca treinam modelos, e e-mail, telefone, cartão, IBAN e endereço são mascarados antes de a IA da Niadra ler as conversas.

No [Console](/produtos/console), o time corta o acesso de um fornecedor na hora, simula o que cada agente enxerga e apaga dados com comprovante. A Niadra opera toda a infraestrutura, e o time da empresa conecta o SDK. No [plano Regulado](/preco), a empresa ganha um ambiente dedicado, com servidores, banco de dados e chaves exclusivos, SLA de disponibilidade em contrato e [revisão de segurança](/enterprise) com o time dela.

Este texto é informativo e não substitui orientação jurídica.

Desde 2 de agosto de 2026, o Artigo 50 do AI Act exige que o agente diga que é IA na União Europeia; o que ele, o PL 2338 e a nota do Banco Central dos Emirados pedem juntos está em [o que a regulação de 2026 pede de um agente de IA que fala com o cliente](/blog/o-que-a-regulacao-de-2026-pede-de-um-agente-de-ia-que-fala-com-o-cliente). O [registro de turno](/produtos/registro) guarda o que cada agente leu, afirmou e decidiu, e o [Console](/produtos/console) mostra o comprovante de cada leitura.

## Perguntas frequentes

### A LGPD permite que agentes de IA leiam dados de clientes?

A LGPD permite, desde que o tratamento tenha base legal ([art. 7º](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art7)), finalidade definida e medidas de segurança adequadas ([art. 46](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art46)). Na prática, o time de compliance cobra contrato com cada operador, acesso por finalidade e registro de cada leitura.

### Cifrar os dados basta para cumprir a LGPD e o GDPR?

A cifra sozinha não basta. O [art. 32 do GDPR](https://eur-lex.europa.eu/legal-content/PT/TXT/?uri=CELEX:32016R0679#art_32) pede também confidencialidade contínua, capacidade de restaurar o acesso e testes regulares das medidas. As duas leis ainda exigem finalidade definida, acesso mínimo e atendimento aos direitos do titular.

### Como apagar os dados de um cliente de todos os agentes de IA?

O controlador recebe o pedido e comunica o apagamento a cada operador que recebeu o dado ([LGPD, art. 18, § 6º](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art18); [GDPR, art. 19](https://eur-lex.europa.eu/legal-content/PT/TXT/?uri=CELEX:32016R0679#art_19)). Numa memória compartilhada, o apagamento alcança a conversa original e tudo o que derivou dela, com comprovante. Cópias guardadas por cada fornecedor seguem o contrato dele.

### Quem responde se o fornecedor de um agente vazar dados?

Pela LGPD, o controlador ou o operador que causar dano responde pela reparação ([art. 42](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art42)). O operador responde solidariamente quando descumpre a lei ou as instruções lícitas do controlador.
