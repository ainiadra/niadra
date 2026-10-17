---
id: custo-de-contexto
lang: pt
slug: como-reduzir-o-custo-de-tokens-de-agentes-de-ia
title: Como reduzir o custo de tokens de agentes de IA sem perder contexto
description: Contexto em camadas, cache de prompt, tamanho mínimo e busca no histórico sob demanda. Como dar contexto completo ao agente de IA e pagar menos por token.
order: 3
kind: guide
date: 2026-09-21T21:49:00-03:00
updated: 2026-10-01T10:00:00-03:00
category: Custo
icon: layers
cover: Contexto completo, token mais barato
keywords: como reduzir o custo de tokens de agentes de IA, cache de prompt, tamanho mínimo do cache de prompt, custo de LLM por conversa, contexto de cliente para agente de IA, como economizar tokens no atendimento com IA, prompt caching
---

Para reduzir o custo de tokens de um agente de IA sem tirar contexto dele, entregue um contexto compacto e sob medida para o canal, ordenado do que menos muda para o que mais muda, e deixe o histórico completo do cliente para consulta durante a conversa. A ordem importa por causa do cache de prompt: o provedor de IA cobra mais barato o começo do prompt que chega igual em mensagens seguidas, acima de um tamanho mínimo que vai de 512 a 4.096 tokens, conforme o modelo ([Anthropic](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). Com os [preços públicos de setembro de 2026](https://platform.claude.com/docs/en/about-claude/pricing), um contexto de 3.000 tokens numa conversa de 10 turnos cai de US$ 0,0600 para US$ 0,0129 num modelo intermediário, uma queda de 78%.

## Por que reenviar o histórico inteiro a cada mensagem sai caro?

As APIs dos modelos de IA não guardam estado entre requisições. A cada mensagem, o agente manda de novo instruções, ferramentas, contexto do cliente e a conversa até ali. O provedor cobra cada token de entrada, toda vez (token é a unidade de texto que o provedor conta e cobra, em geral um pedaço de palavra).

A conta multiplica três fatores: tokens de contexto, turnos e preço por token. Um contexto de 3.000 tokens em 10 turnos vira 30.000 tokens de entrada. Colar o histórico inteiro do cliente no prompt aumenta o primeiro fator a cada nova conversa: quanto mais antigo o cliente, mais cara fica cada mensagem.

Mais texto também não garante resposta melhor. A pesquisa [Lost in the Middle](https://arxiv.org/abs/2307.03172) mostrou que os modelos aproveitam pior a informação que fica no meio de um contexto longo.

## Como o cache de prompt reduz o custo por token?

O cache de prompt é o reaproveitamento que o provedor faz do começo de um prompt. Esse começo repetido se chama prefixo. Quando uma requisição começa igual à anterior, byte a byte, o provedor não reprocessa o prefixo e cobra por ele uma fração do preço.

Na Anthropic, gravar o prefixo custa 1,25 vez o preço de entrada, e ler do cache custa 0,1 vez ([preços](https://platform.claude.com/docs/en/about-claude/pricing)). O prefixo gravado vale 5 minutos e se renova a cada uso ([regras de cache](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). A OpenAI aplica o cache sozinha, sem cobrar a gravação, e cobra o token lido do cache a um décimo do preço de entrada no gpt-5 ([preços](https://developers.openai.com/api/docs/pricing)).

### A ordem do contexto decide o que é reaproveitado

O desconto para no primeiro byte diferente: dali em diante, tudo volta ao preço cheio. Por isso, o que muda pouco vem antes do que muda muito. Uma hora exata ou um ID de requisição no começo do prompt muda a cada chamada e anula o cache. A ordem que funciona separa o contexto em camadas, pela frequência de mudança:

1. **Regras da empresa**: mudam por mês.
2. **Perfil do cliente**: muda por semana.
3. **Pendências**: mudam por conversa.
4. **Agora há pouco**: muda por mensagem.

A Anthropic monta o prefixo na ordem ferramentas, instruções de sistema e mensagens ([regras de cache](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)), então a mensagem nova fica sempre no fim.

### A conta de uma conversa de 10 turnos

O contexto de 3.000 tokens vai inteiro em cada turno. No primeiro, o provedor grava o prefixo (1,25 vez o preço de entrada). Do segundo ao décimo, lê do cache (0,1 vez), com menos de 5 minutos entre turnos. Com cache, a conta fica 3.000 × preço × (1,25 + 9 × 0,1), ou 3.000 × preço × 2,15. Os preços de entrada são os públicos da [Anthropic](https://platform.claude.com/docs/en/about-claude/pricing) em setembro de 2026, e a conta cobre só o contexto, sem mensagens nem respostas.

| Modelo (entrada, por milhão de tokens) | Sem cache | Com cache | Queda |
|---|---|---|---|
| Intermediário: Claude Sonnet 5 (US$ 2,00) | US$ 0,0600 | US$ 0,0129 | 78% |
| Fronteira: Claude Opus 5 (US$ 5,00) | US$ 0,1500 | US$ 0,0323 | 78% |

## Por que o cache às vezes não funciona, e o provedor não avisa?

Os provedores só reaproveitam o prefixo acima de um tamanho mínimo. Abaixo dele, a resposta chega igual, o desconto não acontece e nenhum erro aparece. Os mínimos documentados:

- **Anthropic**: 512 tokens no Claude Opus 5, 1.024 no Claude Sonnet 5 e 4.096 no Claude Haiku 4.5 ([regras de cache](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). O modelo mais barato tem o maior mínimo.
- **OpenAI**: 1.024 tokens ([prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)).
- **Google**: 4.096 tokens no cache implícito dos modelos Gemini atuais ([context caching](https://ai.google.dev/gemini-api/docs/caching)).

Com a conta da tabela, um prefixo de 1.000 tokens no modelo intermediário custa US$ 0,0200 por conversa, com ou sem cache: faltam 24 tokens para o mínimo de 1.024. O mínimo conta sobre todo o começo repetido, somando ferramentas, instruções do agente e camadas estáveis do contexto. Um contexto curto de voz entra no cache junto com o que vem antes dele. Quando nem o conjunto passa do mínimo, a economia vem do tamanho: contexto pequeno já sai barato.

Numa conversa de um turno só, o cache da Anthropic custa 25% a mais: o provedor cobra a gravação e ninguém lê depois. A OpenAI e o Google também tratam o desconto como condicional: ele vale quando a requisição encontra o prefixo ainda guardado. Por isso a economia se mede em produção. Cada resposta informa os tokens lidos do cache: `cache_read_input_tokens` na Anthropic, `cached_tokens` na OpenAI. Se o campo fica em zero a partir do segundo turno, o prefixo mudou ou ficou abaixo do mínimo.

## Quanto contexto o agente precisa receber, e quando buscar no histórico?

O agente precisa de duas coisas: o que usa em quase toda conversa e o que usa de vez em quando. O primeiro vai no contexto, antes da primeira palavra, no tamanho do canal: o agente de voz recebe menos que o de chat, e o agente do app pode receber o contexto completo. O segundo fica no histórico, para consulta.

A busca completa o contexto, mas não o substitui, porque tem custo próprio. Na Anthropic, habilitar ferramentas acrescenta de 286 a 406 tokens de instrução por requisição no Claude Opus 5, além da definição da ferramenta ([preço de ferramentas](https://platform.claude.com/docs/en/about-claude/pricing#tool-use-pricing)). Cada busca soma também uma rodada de inferência: o modelo pede a busca, recebe o resultado e só então responde. Uma declaração de ferramenta que não muda entra no prefixo reaproveitado.

O caso da Marina mostra a divisão. Às 14h07, ela liga para o agente de voz do fornecedor B. O contexto já traz a visita técnica que não aconteceu (WhatsApp, 14h02), a contestação da fatura de agosto (app, 14h05) e uma linha do histórico: duas conversas sobre o mesmo problema desde o começo do ano, a última resolvida com crédito de R$ 40 na fatura, em 12/03. Quando ela diz "da outra vez vocês me deram um crédito", o agente busca os detalhes.

```python
from niadra import Niadra, phone

niadra = Niadra(channel="voice")
caller = phone(caller_id)

# durante o toque: o atestado completo da operadora prova o nível 2 desta ligação
niadra.verify("network_attestation", "V2", handle=caller, conversation_id=call_id)

# antes de responder: o contexto da cliente, sob medida para voz
ctx = niadra.context(caller, view="voice", verification="V2", conversation_id=call_id)

# durante a conversa: a cliente cita o crédito de março
hits = niadra.search(
    caller,
    "crédito por visita técnica perdida",
    verification="V2",
    conversation_id=call_id,
    voice=True,
)
# 12/03/2026 · Telefone · crédito de R$ 40 na fatura de abril · resolvido

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

O contexto levou a linha que responde à pergunta mais provável. A busca trouxe a data e o valor quando a conversa pediu. O resto do histórico da Marina nunca entrou no prompt.

## Como a Niadra resolve

A Niadra aplica essas regras em qualquer canal, fornecedor e LLM.

- `context()` entrega o contexto do cliente já compilado, sem modelo de IA no caminho, sob medida para o canal e o agente, com a origem de cada fato, conversa e ação (quem, canal e quando).
- O contexto vem nas quatro camadas acima, da mais estável para a mais volátil.
- `search()` abre o histórico inteiro durante a conversa, pelo SDK, pela API HTTP ou como ferramenta MCP, com data, canal e desfecho em cada resultado.
- `track()` leva a conversa para a memória depois da resposta.
- A cobrança é por conversa ou tarefa, de US$ 2 a 3 a cada mil ([preço](/preco)), e não por token. Cobrando por volume de texto, a Niadra ganharia mais mandando contextos maiores.

A economia de cache depende do provedor e do modelo que você usa. A Niadra é totalmente gerenciada: do seu lado, só o SDK, de código aberto, em Python e TypeScript ([Contexto](/produtos/contexto)).

O que a memória em si custa, medido, está em [quanto deve custar a memória de um agente de IA por conversa](/blog/quanto-deve-custar-a-memoria-por-conversa): US$ 0,36 de modelo por mil conversas no [benchmark de 30/09/2026](/benchmark), e US$ 2 a 3 por mil no preço de tabela, com as leituras incluídas. A view de voz, mais curta e falável, está em [memória para agentes de voz](/blog/memoria-para-agentes-de-voz-o-orcamento-de-latencia).

## Perguntas frequentes

### O cache de prompt muda a resposta do modelo?

Não. O texto que chega ao modelo é o mesmo, byte a byte, com ou sem cache. O provedor deixa de reprocessar o prefixo que já tinha guardado e cobra menos por ele.

### Qual é o tamanho mínimo para o cache de prompt funcionar?

Depende do modelo: de 512 a 4.096 tokens na Anthropic, 1.024 na OpenAI e 4.096 no cache implícito dos modelos Gemini atuais. Abaixo do mínimo, o provedor cobra o preço cheio sem avisar.

### É melhor mandar o contexto no prompt ou deixar o agente buscar?

Vale usar os dois, cada um no seu papel. O que o agente usa em quase toda conversa vai no contexto, porque cada busca custa a declaração da ferramenta e uma rodada extra de inferência. O histórico inteiro fica para busca, porque é grande e raramente precisa ir inteiro ao modelo.

### A Niadra cobra por token?

Não. A Niadra cobra por conversa ou tarefa que usa a memória, de US$ 2 a 3 a cada mil, conforme o volume. O gasto com o modelo de IA fica com o provedor, e o contexto compacto tende a reduzi-lo.
