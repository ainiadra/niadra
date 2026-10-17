---
id: memoria-para-voz
lang: pt
slug: memoria-para-agentes-de-voz-o-orcamento-de-latencia
title: Memória para agentes de voz: o orçamento de latência, do toque ao último turno
description: Quanto tempo a memória pode levar numa ligação, onde cada milissegundo entra e o que acontece quando ela não chega: 1,5 s enquanto o telefone toca, 200 ms por turno, e o contexto medido em 16,8 ms no p95 pelo endereço público.
order: 3
kind: measured
date: 2026-09-30T22:30:00-03:00
category: Voz
icon: bolt
cover: 1,5 s no toque, 200 ms por turno
keywords: latência de memória para agentes de voz, contexto antes do alô, agente de voz com memória do cliente, orçamento de latência agente de voz, memória para LiveKit Pipecat Vapi, prefetch de contexto em voz, p95 do contexto
---

Numa ligação, a memória tem dois prazos: até 1,5 s enquanto o telefone toca, para a primeira leitura, e até 200 ms por turno, para os encaixes das palavras que a cliente acabou de dizer. Tudo o que não cabe nesses prazos fica de fora do turno, e a ligação segue com o que já estava fixado. O contexto da Niadra, compilado quando a memória muda e sem modelo de IA na leitura, foi medido em 11,2 ms na mediana e 16,8 ms no p95 pelo endereço público, a 10 leituras por segundo, com zero erro em 900 pedidos ([execução de 30/09/2026](https://github.com/ainiadra/niadra-sdk-python/tree/main/benchmarks/results/2026-09-30-6e6d07)). O que este post descreve é onde cada milissegundo entra, e o que acontece quando ele não chega.

## Por que a voz é o canal mais exigente para a memória

Quem liga ouve o silêncio. Num chat, 800 ms a mais entre a mensagem e a resposta passam despercebidos; numa ligação, o mesmo intervalo é uma pausa que a pessoa preenche com "alô?". A recomendação clássica da telefonia para o atraso de um sentido é de 150 ms ([ITU-T G.114](https://www.itu.int/rec/T-REC-G.114)), e o agente de voz já gasta boa parte do orçamento do turno em transcrição, modelo e síntese de fala. A memória entra depois de tudo isso, e entra a cada turno.

Uma memória que faz uma busca por mensagem, com um modelo decidindo o que lembrar na hora da leitura, não cabe nesse orçamento. No mesmo benchmark, os sistemas que raciocinam na leitura levaram de 2 a 14 s por leitura, e os que respondem rápido respondem o que a busca vetorial achou, sem compilar. A [página do benchmark](/benchmark) tem a tabela inteira, inclusive os caminhos em que a Niadra fica atrás.

## O orçamento, prazo a prazo

| Momento | Prazo | O que acontece se estourar |
|---|---|---|
| Primeira leitura, enquanto o telefone toca | 1,5 s | A saudação sai sem o contexto, e ele entra no turno seguinte |
| Encaixes do turno, pelas palavras que a cliente disse | 200 ms | O turno sai com o corpo fixado, sem os encaixes; o delta vai para o próximo |
| Busca no histórico, como ferramenta | 300 ms, 300 tokens | A ferramenta responde que o histórico está indisponível |
| Memória fora do ar (erro 503) | 168 ms na mediana | O agente recebe um contexto vazio, sem erro, dentro do orçamento do turno |
| Memória atrasando 2 s a resposta | 202 ms na mediana | O mesmo contexto vazio; a ligação nunca espera o atraso inteiro |

Os dois últimos números são da medida de resiliência: com a memória devolvendo 503, a Niadra entregou o contexto vazio em 168 ms e nenhum erro chegou ao agente; com a memória atrasando 2.000 ms cada resposta, 202 ms. Os outros sistemas medidos esperaram o atraso inteiro ou devolveram o erro ao agente em todas as tentativas. Os prazos de 1,5 s, 200 ms e 300 ms são configuráveis no SDK; os valores são os padrões documentados no [guia de agentes de voz](https://docs.niadra.com/guides/voice-agents).

## O que roda em cada momento

**Enquanto o telefone toca.** A plataforma de voz avisa da ligação antes de atendê-la: o webhook do toque na Twilio, o `assistant-request` da Vapi, o webhook de início da ElevenLabs, a entrada do cliente na sala do LiveKit. É aí que a primeira leitura começa (`begin()`), depois do atestado da operadora: o STIR/SHAKEN de nível A prova V2, B e C provam V1, e a leitura sai no nível que a ligação provou. A primeira chamada ao modelo espera essa leitura em até 1,5 s (`ready()`), o tempo de uma conexão fria (TCP, TLS e o pedido) mais a primeira compilação do servidor.

**A cada turno.** O contexto é fixado por conversa: os mesmos bytes durante toda a ligação, servidos da memória do SDK na hora, enquanto o SDK os revalida por ETag em segundo plano. Isso é o que mantém o começo do prompt igual, turno após turno, e o provedor de IA reaproveita esse prefixo. O que muda são os encaixes: enquanto a cliente fala, o SDK manda a transcrição parcial com `prefetch()`, e quando ela fica 200 ms sem mudar, lê o turno com ela. O turno final aproveita essa leitura quando as palavras dele começam pelas da parcial. Os adaptadores de [LiveKit](/integracoes/livekit), [Pipecat](/integracoes/pipecat) e Retell fazem o prefetch sozinhos.

**O que chegou de outro canal durante a ligação.** A mensagem no WhatsApp que entrou às 14h05, enquanto a cliente já estava na linha, chega como delta no fim do prompt do turno seguinte. O frescor medido do que foi escrito num canal até aparecer na leitura de outro: 62,7 ms na mediana e 90,1 ms no p95.

**Quando a cliente lembra de algo antigo.** O contexto da view de voz é curto de propósito: 88 tokens na mediana, 151 no p95, com quem liga, o que está em aberto, o que outros agentes acabaram de fazer e os destaques do histórico. O resto fica para a busca, que o modelo chama como ferramenta, com 300 ms de orçamento e 300 tokens de resposta cortados por valor.

```python
from niadra import Niadra, phone

niadra = Niadra(channel="voice")
caller = phone(caller_id)

# While the phone rings: the carrier's attestation proves the level of this call
niadra.verify("network_attestation", "V1", handle=caller, conversation_id=call_id)
conversation = niadra.conversation(call_id, subject=caller, view="voice", verification="V1")
ctx = conversation.ready()  # the first read, within 1.5 s; the greeting waits for it
prompt = f"{YOUR_PROMPT}\n\n{ctx.system_block}"

# During the call: the history as a tool, within the voice budget
kit = niadra.tools(caller, verification="V1", conversation_id=call_id, voice=True)
hits = kit.call("search_customer_history", {"query": "credit for missed technician visit"})
```

## O que o número do p95 não diz

A medida de 16,8 ms é pelo endereço público, a 10 e 25 leituras por segundo, numa célula com a máquina e o banco da produção. Ela não inclui a rede entre a sua plataforma de voz e a região da Niadra: uma plataforma noutro continente paga a ida e volta, e é por isso que a primeira leitura começa no toque e os turnos seguintes servem da memória do SDK. Na mesma máquina do script, a 10 leituras por segundo, o p95 da Niadra foi de 46,3 ms, atrás de três sistemas que respondem sem compilar; a página do benchmark diz isso com todas as letras. E "menos de 100 ms" continua sendo a meta publicada do caminho de leitura, não uma garantia de contrato: o SLA é do plano Regulado.

## Como a Niadra resolve

A Niadra compila o contexto de cada cliente quando a memória muda, não quando o agente pede, e o serve fixado por conversa, sem modelo de IA na leitura. Os adaptadores de voz ([LiveKit](/integracoes/livekit), [Pipecat](/integracoes/pipecat), [Vapi](/integracoes/vapi), [Retell](/integracoes/retell), [ElevenLabs](/integracoes/elevenlabs) e [Twilio](/integracoes/twilio)) começam a primeira leitura no toque, registram o atestado da operadora e fazem o prefetch pelas palavras da cliente, e nenhum deles derruba um turno quando a memória não chega. O post sobre [levar o contexto da ligação para o WhatsApp](/blog/como-manter-o-contexto-da-ligacao-para-o-whatsapp) mostra o que acontece depois que a ligação acaba.

## Perguntas frequentes

### O que o agente diz se o contexto não chegar em 1,5 s?

A saudação que ele diria sem a Niadra. O contexto entra no turno seguinte, e nada na ligação espera além do prazo. A leitura continua em segundo plano.

### A busca no histórico atrasa a resposta?

Ela só roda quando o modelo a chama, como ferramenta, e tem 300 ms de orçamento na voz, com 300 tokens de resposta. A linha "Histórico" do contexto já diz se aquilo já aconteceu e como foi resolvido, então a maioria dos turnos não busca.

### O prefetch manda a transcrição parcial para fora da plataforma de voz?

Manda as palavras do turno até ali para a região da Niadra, pelo mesmo canal cifrado do resto, para a leitura do turno achar a memória já aberta. Um prefetch nunca atrasa nem derruba um turno, e o texto passa pelo mesmo mascaramento antes de qualquer modelo.
