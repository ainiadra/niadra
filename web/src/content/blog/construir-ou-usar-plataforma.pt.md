---
id: construir-ou-usar-plataforma
lang: pt
slug: construir-memoria-de-agentes-em-casa-ou-usar-plataforma
title: Memória de agentes de IA: construir em casa ou usar uma plataforma?
description: O que é preciso para construir a memória dos agentes de IA com Redis, PostgreSQL e pgvector, quando isso compensa e quando uma plataforma faz mais sentido.
order: 5
kind: explainer
date: 2026-09-22T00:41:00-03:00
updated: 2026-10-01T10:00:00-03:00
category: Arquitetura
icon: sliders
cover: Construir em casa ou usar plataforma
keywords: construir memória para agentes de IA, memória de agentes com Redis e pgvector, construir ou comprar memória para agentes de IA, plataforma de memória para agentes de IA, quanto custa construir memória de agentes em casa, arquitetura de memória para agentes de IA, pgvector para memória de agentes
---

Construir a memória dos agentes de IA em casa compensa em quatro casos: um canal só, um fornecedor de agentes só, um time de dados dedicado ou um requisito que nenhuma plataforma cumpre. Com Redis, PostgreSQL e pgvector, o protótipo sai rápido, porque guardar e buscar mensagens é a parte fácil. O trabalho que nunca termina é o resto: identidade entre canais, permissões, auditoria, apagamento e operação 24 horas. Com vários canais e fornecedores, compliance pesado ou pressa, uma plataforma neutra faz mais sentido.

## O que é preciso construir, além de Redis e pgvector?

A arquitetura típica tem Redis para as mensagens recentes, PostgreSQL com pgvector para o histórico, uma fila e um serviço próprio. Para servir agentes de vários fornecedores, o serviço precisa de nove peças:

1. **O registro** guarda cada mensagem, na ordem em que aconteceu, sem duplicar os reenvios do fornecedor.
2. **A identidade** junta telefone, e-mail, documento e login do app num cliente só. Ela também percebe quando um [número muda de dono](/blog/como-reconhecer-o-mesmo-cliente-em-todos-os-canais).
3. **A extração** tira fatos, promessas e pendências de cada conversa. Ela recusa fato sem mensagem de origem.
4. **O contexto** sai sob medida para cada canal, no tamanho e na ordem que o [cache do provedor aproveita](/blog/como-reduzir-o-custo-de-tokens-de-agentes-de-ia).
5. **A busca** devolve cada resultado do histórico com data, canal e desfecho.
6. **As permissões** seguem a finalidade de cada agente e o fornecedor dele.
7. **A auditoria** registra cada leitura à prova de alteração.
8. **Os direitos do titular** pedem um [apagamento em tudo o que derivou do dado](/blog/lgpd-e-seguranca-na-memoria-de-clientes-para-ia).
9. **A operação** exige plantão 24 horas, alertas e troca de modelos.

## Quanto disso é protótipo, e quanto é trabalho que nunca termina?

O protótipo cobre três peças num canal só: registro, contexto e busca. Na demonstração, o agente parece lembrar de tudo.

Até essas três escondem armadilhas. Com índice aproximado (HNSW), o pgvector filtra depois de varrer o índice: na configuração padrão, se o filtro casa com 10% das linhas, voltam em média 4 resultados ([pgvector](https://github.com/pgvector/pgvector)). Filtrada por um cliente entre milhões, a busca tende a voltar quase vazia. A mesma documentação indica começar por um índice na coluna do filtro.

O resto nunca termina. Um canal novo traz um identificador novo. Uma união errada de perfis precisa ser desfeita sem perder histórico. Os modelos mudam as regras: na Anthropic, o mínimo que o cache aproveita passou de 4.096 tokens no Claude Opus 4.5 para 512 no Claude Opus 5 ([documentação](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). Cada troca de modelo também pede nova avaliação da extração. E alguém atende o alerta de madrugada: o livro [Site Reliability Engineering](https://sre.google/sre-book/being-on-call/), do Google, calcula em oito engenheiros o mínimo para um plantão 24 horas num único local.

## Quando construir em casa é a escolha certa?

Construir em casa é a escolha certa em quatro situações.

- **Um canal só.** O cliente chega sempre pelo mesmo identificador, e a identidade entre canais sai da lista.
- **Um fornecedor só.** A memória desse fornecedor já enxerga todas as conversas, e o que faltar cabe num serviço pequeno.
- **Um time de dados dedicado.** Com gente para operar, avaliar a extração e responder à revisão de segurança, construir dá controle total. Isso conta mais quando a memória é parte do produto.
- **Um requisito que nenhuma plataforma cumpre.** Se o dado precisa ficar em servidores operados pelo próprio time, uma plataforma totalmente gerenciada não atende. A Niadra não atende esse caso, porque opera toda a infraestrutura, inclusive no ambiente dedicado do plano Regulado.

A tecnologia não é o obstáculo. A OpenAI atende 800 milhões de usuários do ChatGPT com um único servidor primário de PostgreSQL e cerca de 50 réplicas de leitura ([OpenAI](https://openai.com/index/scaling-postgresql/)). O que pesa é o trabalho.

## Quando uma plataforma neutra faz mais sentido?

Uma plataforma neutra faz mais sentido em três situações.

- **Vários canais e fornecedores.** A [memória de cada fornecedor fica presa nele](/blog/o-que-e-memoria-omnichannel-para-agentes-de-ia). Uma memória construída em casa resolve isso, porque pertence à empresa. O custo aparece na integração: cada fornecedor precisa de SDK, API, credencial própria e permissões por finalidade. Em casa, a empresa escreve e mantém tudo isso.
- **Compliance pesado.** A revisão de segurança pede cada padrão pelo nome, com evidência. Até a cifra campo a campo vira projeto: a documentação do pgcrypto pede confiança no administrador do banco e recomenda cifrar na aplicação quando isso não é possível ([PostgreSQL](https://www.postgresql.org/docs/current/pgcrypto.html)). Cifrar na aplicação exige uma chave por empresa, guardada em HSM.
- **Pressa.** Na plataforma, as nove peças já existem. A integração cabe no agente que você já tem.

## Como as duas opções se comparam, critério a critério?

Construir em casa vence em controle e em onde o dado fica. A plataforma vence em prazo, operação e evidência para a revisão de segurança.

| Critério | Construir em casa | Usar uma plataforma neutra |
|---|---|---|
| Primeiro resultado | Protótipo rápido num canal | Três chamadas no agente que você já tem |
| Identidade entre canais | Você escreve cada regra | Pronta, com o peso de cada identificador |
| Contexto por canal | Nova medição a cada modelo | Em camadas, do que menos muda ao que mais muda |
| Busca no histórico | Índice, filtros e permissões por sua conta | Pelo SDK, pela API HTTP ou por MCP |
| Permissões e auditoria | Projeto próprio | Por finalidade e fornecedor, com cada leitura registrada |
| Apagamento | Você rastreia o que derivou | Com comprovante |
| Operação e monitoramento | Plantão próprio, 24 horas | Da plataforma |
| Controle | Total | Dentro do que a plataforma oferece |
| Onde o dado fica | Onde você decidir, até em servidores seus | Numa região só, operada pela Niadra |
| Custo | Infraestrutura, modelo e time | Por uso (na Niadra, por conversa ou tarefa) |
| Saída | Nada a migrar | Exportação (na Niadra, em formato aberto) |

## Como decidir entre construir e usar uma plataforma?

Quatro perguntas separam os dois casos.

1. Quantos canais e fornecedores vão ler a mesma memória?
2. Que evidência a revisão de segurança vai pedir?
3. Quem opera o sistema de madrugada?
4. A memória é o produto da empresa ou a infraestrutura dele?

Com um canal, um fornecedor e memória como produto, construa. Com vários canais, vários fornecedores e memória como infraestrutura, use uma plataforma. Nas respostas mistas, decidem a segunda e a terceira, porque evidência e plantão viram custo fixo.

Teste com um caso real. Às 14h02, a Marina avisa no WhatsApp que o técnico não apareceu, e o agente do fornecedor A responde. Às 14h05, ela contesta a fatura de agosto pelo app. Às 14h07, ela liga, e quem atende é o agente de voz do fornecedor B. O protótipo passa se esse agente souber das duas coisas antes do alô. Quando ela citar o crédito da outra vez, o agente precisa achar no histórico o registro de 12/03, com os R$ 40.

## Como a Niadra resolve

A Niadra é a memória compartilhada de todos os agentes de IA de uma empresa, de atendimento e internos, em todos os canais e fornecedores. As nove peças da lista fazem parte do serviço, e a Niadra opera toda a infraestrutura, com monitoramento contínuo e alertas automáticos. Do seu lado, fica o SDK, em Python ou TypeScript, com três chamadas: `context()` antes de responder, `search()` durante a conversa e `track()` depois de responder.

O `context()` entrega o contexto já compilado, sem modelo de IA no caminho, com a origem de cada fato, conversa e ação. O `search()` também funciona pela API HTTP e por MCP, com qualquer LLM. Os dados ficam cifrados com AES-256-GCM em repouso e TLS 1.3 em trânsito, com chave exclusiva por empresa, protegida por uma chave-mestra em HSM FIPS 140-3 nível 3. Cada leitura entra numa auditoria encadeada por SHA-256. O preço é por conversa, de US$ 2 a 3 a cada mil ([Preço](/preco)). O plano Regulado tem ambiente dedicado, operado pela Niadra ([Enterprise](/enterprise)).

O custo da parte que nunca termina está medido: no [benchmark de 30/09/2026](/benchmark), o gasto de modelo da Niadra é de US$ 0,36 por mil conversas de dez trocas, e o dos sistemas de código aberto medidos pelo mesmo método vai de US$ 0,97 a 18,72, sem contar servidores nem operação ([quanto deve custar a memória por conversa](/blog/quanto-deve-custar-a-memoria-por-conversa)). Os [adaptadores](/integracoes) ligam os agentes que você já tem, sem reescrever nada.

## Perguntas frequentes

### Dá para construir a memória dos agentes só com Redis e pgvector?

Dá para construir o protótipo. O Redis guarda as mensagens recentes. O PostgreSQL com pgvector guarda o histórico e busca por semelhança. Faltam identidade entre canais, permissões por finalidade, auditoria e apagamento, que a revisão de segurança cobra.

### Quanto custa manter a memória dos agentes em casa?

A conta soma a infraestrutura, o modelo que extrai fatos de cada conversa e o time que opera tudo. Só o plantão 24 horas pede no mínimo oito engenheiros num único local, pela conta do livro [Site Reliability Engineering](https://sre.google/sre-book/being-on-call/), do Google. Na Niadra, o [preço é por conversa](/preco), de US$ 2 a 3 a cada mil.

### Posso começar numa plataforma e construir em casa depois?

Pode, se a plataforma exportar tudo em formato aberto e não controlar o seu agente. É assim que a Niadra funciona: o agente ganha três chamadas, sem trocar prompt, modelo nem fornecedor. Para sair, você importa a exportação no seu sistema e aponta as três chamadas para ele.
