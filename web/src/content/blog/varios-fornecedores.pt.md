---
id: varios-fornecedores
lang: pt
slug: como-agentes-de-fornecedores-diferentes-compartilham-o-historico
title: Como agentes de IA de fornecedores diferentes compartilham o histórico
description: Credencial por agente, leitura por finalidade, registro de cada leitura e memória que fica com a empresa: como fornecedores de IA compartilham o histórico.
order: 7
kind: guide
date: 2026-09-22T00:41:00-03:00
updated: 2026-10-01T10:00:00-03:00
category: Governança
icon: eye
cover: Nenhum fornecedor vê o que não deve
keywords: como agentes de IA de fornecedores diferentes compartilham o histórico do cliente, agentes de IA de vários fornecedores, governança de agentes de IA, permissão por finalidade para agentes de IA, como saber qual agente de IA leu os dados do cliente, como trocar de fornecedor de agentes de IA sem perder o histórico, como não ficar preso a um fornecedor de IA
---

Agentes de IA de fornecedores diferentes compartilham o histórico do cliente quando todos leem e alimentam a mesma memória, que fica fora de qualquer fornecedor. Cada agente entra com uma credencial própria, e a finalidade declarada nela decide o que o agente recebe. Cada leitura deixa um registro encadeado por SHA-256, que a empresa confere no próprio SIEM. Quando um fornecedor sai, a memória continua inteira para o substituto.

## Como cada fornecedor se conecta à mesma memória?

A Marina é cliente de uma empresa com cinco agentes de IA: o de WhatsApp, do fornecedor A; o de voz, do fornecedor B; o do app, do time próprio; o de pedidos, do fornecedor C; e o de cobrança, um agente interno. Os motivos de a memória ficar acima de todos estão em [memória omnichannel](/blog/o-que-e-memoria-omnichannel-para-agentes-de-ia).

Cada agente recebe da empresa uma credencial própria, e nenhum usa a de outro. Em cada credencial, a empresa declara a finalidade: atendimento, para os agentes de WhatsApp, de voz e do app; pedidos, para o agente do fornecedor C; e cobrança, para o agente interno. O fornecedor não escolhe a finalidade a cada pedido: a credencial diz quem está lendo e para quê.

A integração é igual para todos: o SDK em Python ou TypeScript, a API HTTP ou uma ferramenta [MCP](https://modelcontextprotocol.io/). O agente faz três chamadas: `context()` antes de responder, `search()` durante a conversa e `track()` depois. O SDK tem código aberto, e cada fornecedor audita o que envia. A empresa exige essa integração no contrato de cada fornecedor.

## O que cada agente pode ler e escrever?

O acesso começa negado. Cada agente lê o que a finalidade dele permite. A regra vale para o contexto e para cada busca no histórico. Nessa empresa, a política fica assim:

| Agente | Finalidade | O que recebe |
|---|---|---|
| WhatsApp, fornecedor A | atendimento | contexto de chat |
| Voz, fornecedor B | atendimento | contexto de voz |
| App, time próprio | atendimento | contexto completo |
| Pedidos, fornecedor C | pedidos | pedidos, entregas e endereços |
| Cobrança, agente interno | cobrança | faturas, acordos e contestações |

A política decide item a item. O agente de cobrança vê que a fatura de agosto foi contestada, mas não vê a pendência técnica. Às 09h31, uma leitura dele foi negada por esse motivo. A mesma regra impede que ele veja dado de saúde.

A escrita segue a mesma lógica. A empresa define quais agentes alimentam a memória. Ela também define quem enxerga o que cada fornecedor gravou. Cada agente acrescenta as próprias conversas, e nenhum reescreve o que outro gravou.

## O que acontece quando dois agentes gravam fatos que se contradizem?

Às 14h02, a Marina pede ao agente de WhatsApp do fornecedor A que o técnico ligue antes de ir. Às 14h07, ela diz ao agente de voz do fornecedor B que prefere o aviso por WhatsApp. A memória guarda os dois fatos, cada um com canal, horário e agente.

Nenhum fornecedor decide qual vale. O modelo que processa a conversa aponta a contradição, e uma regra fixa escolhe o fato mais recente. Conta a hora em que o fato passou a valer, não a ordem de chegada: uma transcrição atrasada nunca passa por cima de um fato novo. O contexto seguinte traz a preferência mais nova, e a memória guarda de onde veio cada valor: "Telefone 14h07". O pedido das 14h02 continua no histórico, marcado como substituído.

Na conversa, vale o que a cliente disser, e a correção vira fato novo na memória. O caso difícil é outro: a cliente afirma uma coisa e um sistema registra outra. Por isso, cada fato no contexto diz se a cliente declarou ou se um sistema confirmou, e cada ação diz se o agente só declarou ou se o sistema já confirmou.

## Como provar qual agente leu o quê?

Cada leitura gera um registro com o agente, o fornecedor, o que foi lido, a finalidade e a hora. O registro cobre o contexto e cada busca no histórico, inclusive as leituras negadas. As leituras sobre a Marina naquele dia:

| Hora | Quem leu | Para quê | Resultado |
|---|---|---|---|
| 14h07 | Agente de voz, fornecedor B | atendimento | recebeu o contexto de voz |
| 14h05 | Agente do app, time próprio | atendimento | recebeu o contexto completo |
| 14h02 | Agente de WhatsApp, fornecedor A | atendimento | recebeu o contexto de chat |
| 09h31 | Agente de cobrança, interno | cobrança | negado: cobrança não vê pendência técnica |

Os registros são encadeados por hash [SHA-256](https://csrc.nist.gov/pubs/fips/180-4/upd1/final). Cada linha carrega o hash da anterior, e alterar uma linha quebra a cadeia. A raiz de cada dia fica selada em armazenamento imutável. Uma cópia segue em tempo real para o SIEM da empresa. Assim, a empresa prova se o fornecedor B recebeu a contestação da fatura, sem depender do registro dele.

## O que muda quando a empresa troca de fornecedor ou corta um acesso?

A empresa corta o acesso de um fornecedor na hora, sem depender dele. Dali em diante, os agentes dele não recebem contexto nem resultado de busca.

A memória continua inteira. Tudo o que o fornecedor antigo gravou fica guardado, com a origem de cada fato. O fornecedor novo recebe uma credencial própria e lê a mesma memória desde a primeira conversa. A empresa também exporta tudo em formato aberto, quando quiser.

O ponto que exige cuidado é o que o fornecedor antigo já recebeu. Essas cópias ficam do lado dele e seguem o contrato dele. Pelo GDPR, o operador deve apagar ou devolver os dados pessoais no fim do serviço, à escolha do controlador ([art. 28(3)(g)](https://eur-lex.europa.eu/legal-content/PT/TXT/?uri=CELEX:32016R0679#art_28)). A LGPD manda eliminar os dados quando o tratamento termina, com exceções como obrigação legal ([art. 16](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm#art16)). O registro de leituras mostra o que cada fornecedor recebeu, e essa é a lista que a [cláusula de saída do DPA](/blog/lgpd-e-seguranca-na-memoria-de-clientes-para-ia) precisa cobrir.

## Como implantar, do primeiro canal ao fornecedor novo?

A implantação acontece em três etapas.

1. **Primeiro canal.** A empresa conecta um agente, como o de WhatsApp do fornecedor A, com credencial própria e a finalidade atendimento. Antes de liberar, o time simula o que esse agente vai enxergar. O agente chama `context()` antes de responder e `track()` depois. A memória cresce a cada conversa.
2. **Segundo canal.** Entram o agente de voz do fornecedor B e o agente do app. A memória [reconhece a Marina](/blog/como-reconhecer-o-mesmo-cliente-em-todos-os-canais) pelo telefone, e o agente de voz recebe antes do alô o que ela contou no WhatsApp. Nesta etapa, a cliente para de repetir a história.
3. **Fornecedor novo.** O fornecedor C chega com a finalidade pedidos. O time define o que essa finalidade lê e simula a visão do agente antes de liberar. Desde o primeiro dia, as leituras dele aparecem no SIEM.

Nenhuma etapa muda o prompt, o modelo ou o fornecedor dos agentes que já atendem.

## Como a Niadra resolve

A Niadra é a memória compartilhada de todos os agentes de IA de uma empresa, de atendimento e internos, em todos os canais e fornecedores. Ela funciona com qualquer fornecedor e qualquer modelo de IA. Cada agente faz as mesmas três chamadas, pelo SDK, pela API HTTP ou como ferramenta MCP:

```python
from niadra import Niadra, phone

niadra = Niadra(channel="voice")
caller = phone(caller_id)

# durante o toque: o atestado completo da operadora prova o nível 2 desta ligação
niadra.verify("network_attestation", "V2", handle=caller, conversation_id=call_id)

# agente de voz do fornecedor B: a credencial dele e o nível da ligação definem o que volta
ctx = niadra.context(caller, view="voice", verification="V2", conversation_id=call_id)

# a busca no histórico segue a mesma regra de finalidade do contexto
hits = niadra.search(
    caller,
    "contestação da fatura de agosto",
    verification="V2",
    conversation_id=call_id,
    voice=True,
)

# a conversa entra na memória com a origem: canal, horário e agente
turns = [("customer", question), ("ai_agent", reply)]
for role, text in turns:
    niadra.track({
        "conversation_id": call_id,
        "handles": [caller],
        "speaker": {"role": role},
        "content": {"text": text},
    })
```

O `context()` entrega o contexto completo, compilado antes do pedido e servido sem modelo de IA no caminho, com a origem de cada fato, conversa e ação. No [Console](/produtos/console), a empresa corta o acesso de um fornecedor na hora e simula o que cada agente enxerga antes de liberar. O registro de leituras segue para o SIEM, e a exportação sai em formato aberto. A Niadra é totalmente gerenciada: ela opera a infraestrutura, e o time da empresa conecta o SDK. Ela atua como operadora sob a LGPD e o GDPR, com DPA assinado.

Cada fornecedor se liga pelo adaptador do que já opera: [LiveKit](/integracoes/livekit), [Vapi](/integracoes/vapi), [Retell](/integracoes/retell), [Twilio](/integracoes/twilio), [LangGraph](/integracoes/langgraph), [OpenAI Agents](/integracoes/openai-agents), [CrewAI](/integracoes/crewai) ou [n8n](/integracoes/n8n). O que os lançamentos de memória de 2026 significam para quem tem agentes de vários fornecedores está em [Twilio, ServiceNow, AWS e Google lançaram memória para agentes de IA](/blog/twilio-servicenow-aws-e-google-lancaram-memoria-para-agentes-de-ia), e a troca de fornecedor é o teste 5 de [dez testes antes de pôr agentes de IA de vários fornecedores em produção](/blog/dez-testes-antes-de-por-agentes-de-ia-de-varios-fornecedores-em-producao).

## Perguntas frequentes

### Um fornecedor consegue ver o que outro fornecedor gravou?

Cada fornecedor vê o que a finalidade do agente dele permite, e a empresa define essa regra. O agente de voz do fornecedor B recebe o que a Marina contou no WhatsApp, porque os dois agentes atendem. O de cobrança, interno, nunca vê a pendência técnica. Cada leitura fica registrada, inclusive as negadas.

### Quem decide qual fato vale quando dois agentes discordam?

Uma regra fixa decide, e nenhum fornecedor pode mudá-la. Vale o fato mais recente, pela hora em que passou a valer, e o anterior continua no histórico com a origem. Durante a conversa, vale o que o cliente disser.

### O que acontece com o histórico quando a empresa troca de fornecedor?

O histórico fica com a empresa, e o fornecedor novo lê a memória desde a primeira conversa, dentro da finalidade dele. A empresa exporta tudo em formato aberto, quando quiser. As cópias do fornecedor antigo seguem o contrato dele, que deve prever o apagamento ou a devolução no fim do serviço.

### Os fornecedores precisam usar o mesmo modelo de IA?

Não. Cada fornecedor mantém o modelo, o prompt e o agente que já usa. O contexto chega pronto para o prompt de qualquer LLM, e a busca no histórico funciona pelo SDK, pela API HTTP ou como ferramenta MCP.
