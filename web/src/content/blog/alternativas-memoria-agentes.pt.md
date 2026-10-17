---
id: alternativas-memoria-agentes
lang: pt
slug: mem0-zep-ou-letta-memoria-para-agentes-de-atendimento
title: Mem0, Zep ou Letta: qual memória usar em agentes de atendimento
description: Mem0, Zep, Letta e Niadra comparadas por caso de uso: identidade do cliente, contexto, governança, forma de uso e LGPD. Veja onde cada uma é mais forte.
order: 8
kind: explainer
date: 2026-09-22T00:41:00-03:00
updated: 2026-10-01T10:00:00-03:00
category: Comparação
icon: target
cover: Mem0, Zep, Letta ou Niadra?
keywords: alternativas ao Mem0, Zep vs Mem0 vs Letta, Mem0 Zep ou Letta para agentes de atendimento, alternativas ao Mem0 para memória de agentes em produção, memória para agentes de atendimento, comparação de memória para agentes de IA, memória compartilhada entre fornecedores de agentes
---

Para dar memória de longo prazo a um agente, em código aberto ou como serviço gerenciado, a Mem0 resolve com duas chamadas: `add()` e `search()`. Para memória em grafo temporal de conhecimento, com políticas de acesso por agente, a escolha é a Zep; o Graphiti, motor aberto dela, serve a quem quer operar o próprio grafo. Para construir um agente que edita a própria memória e aprende com o uso, o caminho é o Letta. Quando agentes de fornecedores diferentes atendem o mesmo cliente no WhatsApp, na voz e no app, sem um ID de usuário comum, a Niadra cobre o que as outras três não documentam: reconhecer o cliente entre canais.

Os fatos sobre Mem0, Zep e Letta vêm da documentação e dos sites oficiais, conferidos em setembro de 2026, com o link ao lado. Onde a informação não aparece, a tabela diz "não documentado". A Niadra escreveu este texto.

## Mem0, Zep, Letta e Niadra lado a lado

| Critério | Mem0 | Zep | Letta | Niadra |
|---|---|---|---|---|
| **Foco principal** | Memória para agentes e apps | Grafo temporal de conhecimento para agentes | Agentes que aprendem com a própria memória | Memória compartilhada de todos os agentes da empresa, de atendimento e internos |
| **Identificação do cliente** | [`user_id` da aplicação](https://docs.mem0.ai/platform/features/entity-scoped-memory); resolução entre canais não documentada | [`user_id` da aplicação](https://help.getzep.com/users-and-user-graphs); resolução entre canais não documentada | [Memória por agente](https://docs.letta.com/concepts/stateful-agents); resolução entre canais não documentada | Telefone, e-mail, documento e login viram um só cliente, com o peso de cada identificador |
| **Contexto antes da resposta ou busca** | [`search()` antes de cada chamada ao modelo](https://docs.mem0.ai/core-concepts/how-it-works) | [Bloco de contexto das quatro últimas mensagens](https://help.getzep.com/retrieving-context) e busca no grafo | [Pasta `system/` sempre no prompt](https://docs.letta.com/agent-sdk/memory); o resto sob demanda | Contexto completo antes da primeira palavra e busca no histórico |
| **Vários fornecedores na mesma memória** | [`app_id` por parceiro](https://docs.mem0.ai/platform/features/entity-scoped-memory); restrição por fornecedor não documentada | [Chave com políticas por agente ou parceiro](https://help.getzep.com/attribute-based-access-control), no Enterprise | [Entre agentes do Letta](https://docs.letta.com/concepts/shared-memory); outros fornecedores: não documentado | Caso central, com credencial por fornecedor |
| **Finalidade e auditoria de leitura** | [Registro de leituras e escritas](https://mem0.ai); finalidade não documentada | [Políticas por classe de dado](https://help.getzep.com/attribute-based-access-control) e [registro de chamadas](https://help.getzep.com/api-logging) | [Papéis na organização](https://docs.letta.com/teams/permissions); auditoria de leitura não documentada | Leitura por finalidade e registro encadeado por SHA-256 |
| **Forma de uso** | [Código aberto (Apache 2.0) e gerenciada](https://docs.mem0.ai/platform/platform-vs-oss) | [Gerenciada; Graphiti em código aberto](https://help.getzep.com/zep-vs-graphiti) | [Código aberto (Apache 2.0) e Letta Cloud](https://docs.letta.com/self-hosting) | 100% gerenciada; não é self-hosted |
| **LGPD e GDPR** | [GDPR declarado](https://mem0.ai), com [DPA](https://mem0.ai/privacy-policy); LGPD não documentada | Não documentados; [SOC 2 Tipo II e HIPAA](https://help.getzep.com/security-compliance); [apagamento do usuário](https://help.getzep.com/users-and-user-graphs) | GDPR na [política de privacidade](https://www.letta.com/privacy-policy); DPA e LGPD não documentados | Operadora, com DPA e apagamento com comprovante |
| **Maturidade pública** | [65 mil estrelas](https://github.com/mem0ai/mem0) e [artigo](https://arxiv.org/abs/2504.19413) | Graphiti com [31 mil estrelas](https://github.com/getzep/graphiti) e [artigo](https://arxiv.org/abs/2501.13956) | [24 mil estrelas](https://github.com/letta-ai/letta) no repositório original e [artigo do MemGPT](https://arxiv.org/abs/2310.08560) | Nova: sem estrelas nem integrações listadas; no [benchmark aberto](/benchmark) de 30/09/2026, com o mesmo agente e o mesmo juiz, 98,7% de acerto entre canais nos mesmos 165 cenários (três repetições), contra 77,6% da Mem0 com o mesmo ID em todos os canais |

## Mem0: memória para os agentes que o seu time constrói

A [Mem0](https://docs.mem0.ai) é uma camada de memória para agentes e aplicações de IA. A aplicação envia cada troca de mensagens com `add()`, e a Mem0 extrai os fatos que valem guardar. Antes da próxima chamada ao modelo, a aplicação busca com `search()` e escolhe o que entra no prompt ([como funciona](https://docs.mem0.ai/core-concepts/how-it-works)). Cada memória fica ligada aos IDs que a aplicação informa, como `user_id` e `agent_id` ([escopo](https://docs.mem0.ai/platform/features/entity-scoped-memory)).

O código é aberto, sob Apache 2.0, e a mesma API existe no serviço gerenciado ([Platform e código aberto](https://docs.mem0.ai/platform/platform-vs-oss)). A documentação traz integrações com LangChain, CrewAI, LlamaIndex, LiveKit e dezenas de outras ([integrações](https://docs.mem0.ai/integrations)).

A [página de atendimento](https://mem0.ai/usecase/customer-support) da Mem0 promete memória entre chat, e-mail e voz. A documentação, porém, não descreve resolução de identidade entre canais: a memória segue o `user_id` que a aplicação envia. Segundo o [site](https://mem0.ai), em setembro de 2026, a Mem0 atende SOC 2 Tipo I, HIPAA e GDPR.

### Quando escolher a Mem0

- Você tem um agente, ou poucos, feitos pelo seu time, e um ID de usuário estável em todos os canais.
- Você quer código aberto e self-hosted, com o banco vetorial e o LLM que escolher.
- Você quer integração pronta com o framework que já usa.

## Zep: grafo temporal de conhecimento, com governança

A [Zep](https://help.getzep.com) entrega memória de agentes sobre um grafo temporal de conhecimento, montado a partir de conversas, dados de negócio, documentos e JSON. Cada fato guarda quando passou a valer e quando deixou de valer ([fatos](https://help.getzep.com/facts)). O motor do grafo é o [Graphiti](https://github.com/getzep/graphiti), de código aberto.

A Zep monta o bloco de contexto do agente usando as quatro mensagens mais recentes da conversa como consulta, em menos de 200 ms em 95% das chamadas ([contexto](https://help.getzep.com/retrieving-context)).

A governança é um ponto forte. Segundo a documentação, em setembro de 2026, o plano Enterprise liga a cada chave de API políticas que limitam as ações e as classes de dado que aquele agente lê ([acesso de agentes](https://help.getzep.com/attribute-based-access-control)). O registro de API guarda cada chamada com a decisão da política ([registro](https://help.getzep.com/api-logging)). A Zep tem SOC 2 Tipo II e contrato HIPAA para clientes Enterprise ([segurança](https://help.getzep.com/security-compliance)). A edição self-hosted da Zep foi descontinuada, e quem quer operar por conta própria usa o Graphiti ([FAQ](https://help.getzep.com/faq)).

### Quando escolher a Zep

- Você precisa saber quando cada fato passou a valer e quando deixou de valer, como a troca de um plano.
- Você quer conversas e dados de negócio no mesmo grafo do usuário.
- Você precisa de políticas por agente, SOC 2 Tipo II ou do serviço na nuvem da própria empresa ([segurança](https://help.getzep.com/security-compliance)).
- Você quer operar o grafo com o seu time, em código aberto: nesse caso, o Graphiti.

## Letta: agentes que cuidam da própria memória

O [Letta](https://docs.letta.com), antes chamado MemGPT ([GitHub](https://github.com/letta-ai/letta)), é uma plataforma para construir agentes com estado, que aprendem com a experiência. Segundo a documentação, em setembro de 2026, a memória pertence ao agente e fica num repositório git. Os arquivos da pasta `system/` entram no prompt a cada turno, e o resto o agente lê quando precisa ([memória](https://docs.letta.com/agent-sdk/memory)). O próprio agente edita essa memória, e subagentes em segundo plano reorganizam o que ele aprendeu ([MemFS](https://docs.letta.com/concepts/memfs)).

O código é aberto, sob Apache 2.0. O Letta roda localmente ou num servidor próprio, e o Letta Cloud é a versão hospedada ([self-hosting](https://docs.letta.com/self-hosting)). A documentação aponta como usos agentes de programação, assistentes pessoais, colegas de trabalho de IA e agentes embutidos em aplicações ([documentação](https://docs.letta.com)). Reconhecer o mesmo cliente entre canais não aparece nela.

### Quando escolher o Letta

- Você está construindo o próprio agente e quer a memória dentro do framework.
- Você quer um agente que edita a própria memória, com histórico de versões em git.
- Você precisa rodar tudo localmente, inclusive o modelo.

## Niadra: memória compartilhada de todos os agentes da empresa

A Niadra é a memória compartilhada de todos os agentes de IA de uma empresa, de atendimento e internos: os que atendem no WhatsApp, na voz, no app e com atendente humano, e os que trabalham nos sistemas, com agentes de fornecedores diferentes. Ela não constrói agentes: liga à mesma memória os agentes que você já tem.

A Niadra reconhece o cliente sem depender de um ID pronto: telefone, e-mail, documento e login viram um só cliente, com o peso de cada identificador ([como funciona](/blog/como-reconhecer-o-mesmo-cliente-em-todos-os-canais)). Cada agente lê apenas o que a finalidade dele permite. Cada leitura entra num registro encadeado por SHA-256, e a empresa corta o acesso de um fornecedor na hora. A Niadra atua como operadora sob a LGPD e o GDPR, com DPA e apagamento com comprovante.

### Quando a Niadra não é a melhor escolha

- **Um agente, um canal e um ID de usuário confiável.** A Mem0 ou a Zep atendem esse caso diretamente.
- **Infraestrutura própria ou motor de memória em código aberto.** A Niadra é 100% gerenciada e não tem versão self-hosted. Mem0, Graphiti e Letta têm.
- **Um framework para construir o agente.** A Niadra não é framework de agentes. O Letta é.
- **Maturidade comprovada.** A Niadra é nova: não tem estrelas no GitHub nem integrações listadas em frameworks, e o [benchmark aberto](/benchmark) dela tem uma rodada publicada: três repetições da Niadra e da Mem0, uma de cada outro sistema, na metade do conjunto. As outras três têm comunidade, artigos publicados e integrações documentadas.

## Como decidir entre Mem0, Zep, Letta e Niadra?

1. **Quantos fornecedores atendem o mesmo cliente?** Com agentes do seu time, Mem0, Zep e Letta atendem bem. Com fornecedores concorrentes em canais diferentes, a memória precisa de credencial e permissão por fornecedor. A Zep oferece isso no plano Enterprise, e a Niadra foi desenhada para esse caso.
2. **Existe um ID de usuário comum a todos os canais?** Se existe, Mem0 e Zep trabalham com ele diretamente. Se o cliente chega pelo telefone num canal e pelo login em outro, alguém precisa unir as duas pontas: na Niadra, isso faz parte do produto; nas outras três, fica com a sua aplicação.
3. **Onde a memória precisa morar?** Na sua infraestrutura, com código aberto: Mem0, Graphiti ou Letta. Em serviço gerenciado: Mem0, Zep, Letta Cloud ou Niadra. Na nuvem ou nos servidores da própria empresa, sob contrato: [Zep](https://help.getzep.com/security-compliance) e [Mem0](https://mem0.ai/pricing).

Antes de fechar, teste cada opção com conversas reais que mudaram de canal.

## Como a Niadra resolve

O SDK, em Python e TypeScript, tem três chamadas:

```python
from niadra import Niadra, phone

niadra = Niadra(channel="voice")
caller = phone(caller_id)

# durante o toque: o atestado completo da operadora prova o nível 2 desta ligação
niadra.verify("network_attestation", "V2", handle=caller, conversation_id=call_id)

# antes de responder: o contexto do cliente nesse nível
ctx = niadra.context(caller, view="voice", verification="V2", conversation_id=call_id)

# durante a conversa: o agente consulta o histórico
results = niadra.search(
    caller,
    "crédito por visita técnica perdida",
    verification="V2",
    conversation_id=call_id,
    voice=True,
)

# depois de responder: a conversa entra na memória
turns = [("customer", question), ("ai_agent", reply)]
for role, text in turns:
    niadra.track({
        "conversation_id": call_id,
        "handles": [caller],
        "speaker": {"role": role},
        "content": {"text": text},
    })
```

O `context()` não precisa de pergunta e devolve o contexto completo, compilado antes do pedido e servido sem modelo de IA no caminho, com a origem de cada fato, conversa e ação (quem, canal e quando). A busca no histórico também funciona pela API HTTP e por MCP. O preço é por conversa ou tarefa, de US$ 2 a 3 a cada mil, e o plano Regulado sai sob contrato. Veja [Produtos](/produtos), [Segurança](/recursos#seguranca) e [Preço](/preco).

## Perguntas frequentes

### Quais são as alternativas ao Mem0 para memória de agentes em produção?

Três alternativas cobrem casos diferentes: a Zep, para grafo temporal de conhecimento e políticas por agente; o Graphiti, para operar esse grafo em código aberto; e o Letta, para memória dentro do framework do agente. Para atendimento omnichannel entre fornecedores, a Niadra reconhece o cliente em todos os canais.

### Mem0, Zep ou Letta: qual usar em agentes de atendimento?

Com um agente e um ID de usuário estável, Mem0 e Zep atendem bem, e a Zep acrescenta fatos com validade no tempo e políticas por agente. Para construir o agente, o Letta é o framework indicado. Com fornecedores diferentes em vários canais, a Niadra resolve a identidade e a governança entre eles.

### A Niadra tem versão self-hosted ou de código aberto?

A plataforma, não. A Niadra é 100% gerenciada: ela opera toda a infraestrutura, e o cliente conecta o SDK, que é de código aberto para o time de segurança auditar o que sai da rede. No plano Regulado, a empresa ganha um ambiente dedicado, operado pela Niadra. Para rodar o motor de memória na própria infraestrutura, Mem0, Graphiti e Letta são opções maduras.
