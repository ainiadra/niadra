---
id: dez-testes-go-live
lang: pt
slug: dez-testes-antes-de-por-agentes-de-ia-de-varios-fornecedores-em-producao
title: Dez testes antes de pôr agentes de IA de vários fornecedores em produção
description: Um roteiro para o engenheiro: passagem de canal nos dois sentidos, conversa não verificada, número reciclado, troca de fornecedor, preço sem origem, memória fora do ar, apagamento, ação do agente interno e replay no CI. O que fazer, o que esperar e como medir, com o código do SDK.
order: 6
kind: guide
date: 2026-10-01T09:00:00-03:00
category: Guia
icon: check
cover: Dez testes. Depois, produção
keywords: testar agentes de IA antes do go-live, roteiro de testes para agentes de IA de atendimento, teste de passagem de canal voz WhatsApp, teste de memória de agente fora do ar, replay de conversa de agente em CI, teste de troca de fornecedor de agente de IA, teste de dado sensível agente de IA, checklist de produção para agentes de IA
---

Antes de pôr em produção agentes de IA de dois ou três fornecedores lendo a mesma memória de clientes, dez testes dizem se a memória se comporta como o contrato promete. Cada um tem o que fazer, o que esperar e como medir, e os que envolvem código usam o SDK público da Niadra, o mesmo dos [exemplos do repositório](https://github.com/ainiadra/niadra-sdk-python/tree/main/examples). O roteiro inteiro leva um dia com dois números de telefone de teste, um agente de voz e um de WhatsApp de fornecedores diferentes, e um ambiente de teste da memória. Sem Niadra, os testes continuam valendo: o que muda é como cada um é observado.

Este guia foi escrito para quem constrói e opera os agentes. Pressupõe o SDK instalado (`pip install niadra` ou `npm install @niadra/sdk`), uma chave de fonte por fornecedor e, para rodar sem a nuvem, o emulador local (`niadra-mock`, com `NIADRA_BASE_URL=http://127.0.0.1:8765`), que o próprio repositório usa nos testes de integração.

## 1. A passagem da ligação para o WhatsApp

**O que fazer.** Ligue de um número de teste para o agente de voz e feche um caso com uma promessa ("a visita fica para amanhã, entre 8h e 12h"). Desligue. Escreva no WhatsApp do mesmo número, logo em seguida e três horas depois: "que horas o técnico vem?".

**O que esperar.** O agente de WhatsApp responde com a promessa da ligação, cita a ligação pela hora e não pede nome nem motivo. Logo em seguida, as últimas falas da ligação aparecem na camada "agora há pouco" do contexto; três horas depois, a visita aparece como pendência.

**Como medir.** O comprovante da leitura lista o que foi entregue; a medição do aproveitamento diz, por agente e fornecedor, se o agente perguntou de novo o que já tinha ([como saber se o agente usou o contexto](/blog/como-saber-se-o-agente-de-ia-usou-o-contexto)). O post sobre [a passagem da ligação para o WhatsApp](/blog/como-manter-o-contexto-da-ligacao-para-o-whatsapp) detalha o que a Meta entrega ou não no webhook: para quem ativou nome de usuário, o telefone só vem se houve troca nos últimos 30 dias.

```python
from niadra import Niadra, phone

niadra = Niadra(channel="whatsapp")
with niadra.conversation("wa-5511912345678", subject=phone("+5511912345678")) as chat:
    chat.customer("que horas o técnico vem?")
    context = chat.context()
    # context.system_block traz a promessa da ligação, com canal e hora;
    # context.turn_block, o que acabou de acontecer
```

## 2. O caminho de volta: do WhatsApp para a voz

**O que fazer.** Escreva primeiro, com uma reclamação. Ligue depois.

**O que esperar.** O agente de voz recebe uma versão curta e falável do contexto, sem tabela, e cita a mensagem na saudação. A view de voz é outra: menos tokens, frases ditas em voz alta ([memória para agentes de voz](/blog/memoria-para-agentes-de-voz-o-orcamento-de-latencia)).

**Como medir.** O tempo entre o pedido e o contexto, no toque, e se o agente pediu o motivo da ligação. Nos adaptadores de [LiveKit](/integracoes/livekit), [Pipecat](/integracoes/pipecat), [Vapi](/integracoes/vapi), [Retell](/integracoes/retell) e [ElevenLabs](/integracoes/elevenlabs), a leitura acontece durante o toque.

## 3. A conversa que não provou quem é

**O que fazer.** Escreva de um número que nunca falou com a empresa, mas que está no cadastro ligado a uma cliente com dado sensível na memória (um dado de saúde, um documento). Peça esse dado.

**O que esperar.** O contexto chega no nível 1: a origem está ligada à cliente, mas nada provou quem digita. Os itens sensíveis aparecem como retidos, com a contagem, e só saem depois da verificação que a sua política exige (um código, um login). Nada do dado aparece na resposta.

**Como medir.** O comprovante registra o nível e os itens retidos. No [benchmark de 30/09/2026](/benchmark), esta é a medida "dado sensível entregue sem verificação": a Niadra entregou zero, com a verificação por conversa; os sistemas sem mecanismo de verificação, lendo com o mesmo id em todos os canais, entregaram em 31% a 100% dos casos.

```python
from niadra import Niadra, phone

niadra = Niadra(channel="whatsapp")
thread_id = "wa-5511900000000"
ctx = niadra.context(phone("+5511900000000"), view="chat", verification="V1", conversation_id=thread_id)
# os itens sensíveis ficam retidos até um verify() de nível maior
niadra.verify("otp_whatsapp", "V3", handle=phone("+5511900000000"), conversation_id=thread_id)
```

## 4. O número que mudou de dono

**O que fazer.** Simule um número que ficou 180 dias calado e volta sem id de cadastro no mesmo evento.

**O que esperar.** O agente lê um primeiro contato: contexto vazio, com comprovante, nada do dono anterior. O vínculo antigo fica suspenso no perfil antigo até um sistema ou um login ligá-lo de novo ([quando o número de telefone muda de dono](/blog/quando-o-numero-de-telefone-muda-de-dono)).

**Como medir.** O comprovante da leitura diz "primeiro contato"; nenhuma linha do perfil antigo aparece.

## 5. A troca de fornecedor

**O que fazer.** Revogue a credencial do fornecedor A. Ligue o agente do fornecedor B à mesma memória, com credencial própria e a mesma finalidade. Repita o teste 1.

**O que esperar.** O agente B lê o histórico inteiro, inclusive o que o agente A gravou. O agente A, ao tentar ler, recebe recusa, e a recusa deixa comprovante. Nada foi exportado e reimportado ([como agentes de fornecedores diferentes compartilham o histórico](/blog/como-agentes-de-fornecedores-diferentes-compartilham-o-historico)).

**Como medir.** O comprovante da recusa, no Console e no SIEM, com o fornecedor e a hora.

## 6. O preço que o agente inventou

**O que fazer.** Dê ao agente uma ferramenta de consulta ao catálogo e um rascunho de resposta com um preço diferente do que a ferramenta devolveu. Ligue o contrato de afirmação em modo de contagem.

**O que esperar.** A afirmação de preço entra no registro de turno com o veredito `mismatch`; em modo de ação, a categoria decide entre anotar, contar ou, numa saída que pode mudar e só quando a correção é inequívoca, reescrever pelo valor com origem. Um número sem origem nenhuma dá `unsupported` ([afirmações, na documentação](https://docs.niadra.com/concepts/claims)).

**Como medir.** `niadra contract test` no CI, com o seu corpus de frases que nunca podem disparar; e a contagem de vereditos por agente e fornecedor depois de uma semana em produção.

```python
from niadra import Niadra, phone

niadra = Niadra(channel="whatsapp")

@Niadra.tool("check_price", provenance=lambda p: [{"ref": f"product:store:{p['sku']}", "fields": {"price_sale": p["price_sale"]}}])
def check_price(sku: str) -> dict:
    return {"sku": sku, "price_sale": 149.9}

with niadra.conversation("thread-82", subject=phone("+5511912345678"), agent_id="store") as conversation:
    conversation.customer("Quanto está o vestido PX?")
    with conversation.turn(build=Niadra.build(prompts={"store": "v16"}, model="gpt-4.1-mini")):
        check_price("PX-4471")
        guarded = conversation.claims.guard_text("O vestido sai por R$ 199,90 hoje.")
        conversation.agent(guarded.text)
```

O trecho é o do exemplo `claim_guard.py` do repositório: o preço do rascunho (199,90) discorda do que a ferramenta devolveu (149,90), e o contrato de varejo marca a afirmação. O post sobre [o agente que afirmou o que nunca consultou](/blog/o-agente-de-ia-afirmou-o-que-nunca-consultou-dois-casos-publicos) mostra o que acontece sem isso.

## 7. A memória fora do ar

**O que fazer.** Aponte `NIADRA_BASE_URL` para um endereço que não responde, ou derrube o emulador no meio de uma conversa.

**O que esperar.** O agente continua: a leitura serve o último contexto bom, marcado como degradado; os turnos esperam na fila; a lista de supressão do contato vale pela última cópia. Nada do que o agente faz espera a Niadra; é o comportamento descrito no exemplo `turn_records.py` do repositório.

**Como medir.** O tempo até o agente poder chamar o modelo, com a memória fora, e a marca `degraded` no contexto. O benchmark mede isso como "memória lenta ou fora do ar", com o cliente de cada sistema como vem.

## 8. O apagamento com comprovante

**O que fazer.** Peça o apagamento de uma cliente de teste pela API ou pelo Console, por linhagem.

**O que esperar.** O contexto dela passa a vir vazio em todos os agentes, de todos os fornecedores; os fatos derivados das conversas dela saem junto; o apagamento deixa comprovante ([LGPD, GDPR e agentes de IA](/blog/lgpd-e-seguranca-na-memoria-de-clientes-para-ia)).

**Como medir.** O comprovante do apagamento e a leitura seguinte, vazia, com o próprio comprovante.

## 9. O agente interno fecha o ciclo

**O que fazer.** Faça o agente de cobrança lançar um crédito no ERP de teste e mande o evento do ERP por webhook. Ligue em seguida.

**O que esperar.** O agente de voz recebe o crédito como feito, não como pendente, com a fonte e a hora, e a ação aparece como confirmada pelo evento do sistema, não só declarada pelo agente ([memória para agentes internos](/blog/memoria-para-agentes-de-ia-internos)).

**Como medir.** O estado da ação no contexto: `declared` até o evento chegar, `confirmed` depois, `divergent` se o ERP registrou outro valor.

## 10. O replay no CI

**O que fazer.** Grave uma conversa real do ambiente de teste com registro de turno ligado. Reproduza-a no CI com a memória da época e o mesmo build.

**O que esperar.** A mesma resposta, turno a turno, sem que o conteúdo saia da sua empresa: o registro guarda o ponteiro e o hash, e o replay roda dentro da sua fronteira. É o exemplo `replay_demo.py` do repositório, e o que torna uma regressão de prompt visível antes do cliente ([Registro](/produtos/registro)).

**Como medir.** O replay passa ou falha no CI, como qualquer teste.

## O que não sabemos

- Os números de referência citados (zero dado sensível entregue, 98,8% de acerto entre canais) são do benchmark da Niadra, com cenários sintéticos em português e inglês, numa região só. O seu resultado depende da sua política, dos seus canais e dos seus agentes; é por isso que o roteiro existe.
- O comportamento do webhook do WhatsApp muda com a configuração da Meta e do provedor. O teste 1 precisa ser repetido com uma conta com nome de usuário e sem histórico de 30 dias.
- Os adaptadores cobrem os fornecedores listados nas [integrações](/integracoes). Para outro fornecedor, os dez testes valem com o SDK usado direto.

## Como a Niadra resolve

Os dez testes são a lista do que a Niadra faz como memória omnichannel dos agentes de IA de uma empresa: recebe os eventos de todo canal, plataforma e sistema, reconhece o cliente antes de guardar, entrega a cada agente, de qualquer fornecedor, o contexto da tarefa com o nível de verificação da conversa, confere o que o agente afirmou, registra cada turno e cada leitura, e continua servindo quando está fora do alcance. Os [adaptadores](/integracoes) ligam os agentes que você já opera, e a documentação tem o [início rápido](https://docs.niadra.com/quickstart) e as páginas de [afirmações](https://docs.niadra.com/concepts/claims), [registro de turno](https://docs.niadra.com/concepts/turn-records) e [coordenação](https://docs.niadra.com/concepts/coordination). O [benchmark de 30/09/2026](/benchmark) tem o script, o conjunto de dados e os arquivos de resultado para reproduzir o que este guia pede para medir.

## Perguntas frequentes

### Preciso de dois fornecedores de verdade para rodar o roteiro?

Para os testes 1, 2 e 5, sim: a passagem entre fornecedores é o que está sendo testado. Os outros rodam com um agente só e o emulador local.

### Quanto tempo leva?

Um dia, com os números de teste e as credenciais prontos. O teste 10 entra no CI e roda a cada mudança de prompt ou de modelo.

### E se um teste falhar?

Cada teste aponta o comprovante ou o registro que explica a falha: o que foi entregue, o que foi retido, o que o agente afirmou. A correção costuma estar na política (o que o nível libera), no adaptador (o identificador que o canal entrega) ou no prompt (o que o agente faz com o contexto), e o registro diz qual.
