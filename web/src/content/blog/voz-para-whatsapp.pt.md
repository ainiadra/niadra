---
id: voz-para-whatsapp
lang: pt
slug: como-manter-o-contexto-da-ligacao-para-o-whatsapp
title: Como manter o contexto da ligação para o WhatsApp, e vice-versa
description: O cliente desliga e escreve no WhatsApp. Veja o que o agente precisa saber, como o contexto passa entre fornecedores e como medir a passagem de canal.
order: 6
kind: guide
date: 2026-09-22T00:41:00-03:00
updated: 2026-10-01T10:00:00-03:00
category: Canais
icon: hub
cover: Da ligação ao WhatsApp, sem repetir
keywords: como manter o contexto da ligação no WhatsApp, passar o atendimento do agente de voz para o WhatsApp, agente de voz e agente de WhatsApp de fornecedores diferentes, contexto do cliente entre canais, a ligação abre a janela de 24 horas do WhatsApp, como medir recontato no atendimento com IA, transcrição da ligação para o agente de WhatsApp
---

Para manter o contexto quando o atendimento passa do agente de voz para o agente de WhatsApp, a ligação precisa entrar numa memória compartilhada enquanto acontece, fora do fornecedor de voz. Quando ela termina, o que foi tratado e cada promessa viram fatos com canal e horário. Quando o cliente escreve, o sistema reconhece que é a mesma pessoa que ligou, e o agente de WhatsApp recebe esses fatos antes de responder, no formato do chat. O caminho inverso usa a mesma memória, com um contexto curto e falável para o agente de voz.

## O que se perde quando o cliente sai da ligação e vai para o WhatsApp?

Às 14h07, a Marina ligou, e o agente de voz do fornecedor B remarcou a visita técnica com prioridade para amanhã, entre 8h e 12h. Ele também combinou que o aviso de chegada do técnico iria por WhatsApp, 30 minutos antes. Às 18h30, ela escreve no WhatsApp, onde atende o agente do fornecedor A: "Que horas o técnico vem amanhã?" Sem uma memória comum, três coisas se perdem:

- **O identificador.** A voz conhece o número que ligou. O WhatsApp conhece o número de quem escreve ou um ID com escopo da empresa ([identidade entre canais](/blog/como-reconhecer-o-mesmo-cliente-em-todos-os-canais)).
- **A transcrição.** Ela fica na plataforma do fornecedor de voz, fora do alcance do fornecedor A.
- **A promessa.** O agente de WhatsApp não sabe da visita remarcada e pode oferecer outro horário.

A telefonia tem um padrão para passar um pequeno dado junto com a chamada, o cabeçalho User-to-User do SIP ([RFC 7433](https://www.rfc-editor.org/rfc/rfc7433)). Esse dado viaja dentro da chamada e nunca chega a uma conversa de WhatsApp.

## O que precisa acontecer quando a ligação termina?

A passagem começa antes de a Marina desligar. A cada resposta, o agente de voz registra a fala dela e a própria resposta numa memória fora do fornecedor B. Quando a chamada termina, a Niadra processa a conversa inteira. Da ligação das 14h07 saem itens como estes:

- **Desfecho:** visita remarcada com prioridade para amanhã, 23/09, entre 8h e 12h.
- **Promessa:** aviso de chegada por WhatsApp, 30 minutos antes.
- **Feito por outro agente:** crédito de R$ 40 na fatura de agosto, lançado pelo agente de cobrança às 14h06.

Cada item guarda o canal, o horário e a conversa de onde veio. Nenhum entra sem uma fala que o sustente.

Duas falhas aparecem nesta etapa. O áudio de telefone tem banda estreita, e a transcrição pode trocar um horário. Por isso o agente de WhatsApp cita "a ligação das 14h07", e a cliente pode corrigir o que saiu errado. A ligação também pode não chegar inteira à memória. A Niadra mostra, fonte a fonte, o que o SDK diz ter enviado e o que chegou, e uma regra de aviso chama o seu sistema quando uma fonte para de mandar eventos.

## Como saber que quem escreve no WhatsApp é a pessoa que ligou?

Às 18h30, a mensagem chega com o número da Marina, já vinculado ao perfil dela. No WhatsApp, isso depende de uma regra. Para quem ativou o nome de usuário, o número de telefone não vem no webhook. A exceção é quando aquele número de WhatsApp da empresa trocou mensagens ou ligações com a pessoa nos últimos 30 dias, ou quando ela está na agenda de contatos da empresa ([documentação da Meta](https://developers.facebook.com/documentation/business-messaging/whatsapp/business-scoped-user-ids/#phone-numbers)). A Marina escreveu às 14h02, então o número vem. A ligação das 14h07, pela rede telefônica, não entra nessa conta.

Reconhecer não é confirmar. A conversa começa no [nível 1](/blog/como-reconhecer-o-mesmo-cliente-em-todos-os-canais#como-verificar-a-identidade-durante-a-conversa): a origem está vinculada à cliente, mas nada provou quem digita. A política da empresa libera o horário da visita e o crédito já lançado, e retém documento e dados de pagamento. Se a Marina pedir algo que envolva esses dados, o agente envia um código pelo WhatsApp. Com o código confirmado, ele pede o contexto de novo, agora com os itens retidos. Se a mensagem chegar com um ID sem telefone, o contexto começa vazio até uma evidência vincular esse ID ao perfil dela.

## O que o agente de WhatsApp recebe, e em que formato?

O contexto é sob medida para o canal. No chat, ele traz datas e faixas de horário. Ao lado de cada linha, a conversa de onde ela veio:

| Linha do contexto | Veio de |
|---|---|
| Marina Souza. Tratar por "Marina". | |
| Visita remarcada: amanhã, 23/09, entre 8h e 12h. | Telefone 14h07 |
| Aviso de chegada por WhatsApp, 30 minutos antes. | Telefone 14h07 |
| 2 itens retidos até a confirmação da identidade. | |
| "Que horas o técnico vem amanhã?" | WhatsApp 18h30 |

O agente responde sem perguntar nada: "Boa noite, Marina. A visita ficou para amanhã, entre 8h e 12h, como combinado na ligação das 14h07. O aviso de chegada vem por aqui, 30 minutos antes."

No caminho inverso, a mesma memória vira contexto de voz: curto, sem tabela, em frases que o agente repete em voz alta, e mais barato por mensagem ([custo de tokens](/blog/como-reduzir-o-custo-de-tokens-de-agentes-de-ia)). Se amanhã cedo a Marina ligar, o agente de voz recebe: "Visita hoje, entre oito e meio-dia. O aviso de chegada vai por WhatsApp."

```python
from niadra import Niadra, phone

niadra = Niadra()

# 14h07, agente de voz do fornecedor B: cada resposta entra na memória
caller = phone(caller_id)
turns = [("customer", question), ("ai_agent", reply)]
for role, text in turns:
    niadra.track({
        "channel": "voice",
        "conversation_id": call_id,
        "handles": [caller],
        "speaker": {"role": role},
        "content": {"text": text},
    })

# 18h30, agente de WhatsApp do fornecedor A: a mensagem chega do número
# da cliente, o que prova o nível 1 desta conversa
sender = phone(sender_phone)
niadra.track({
    "channel": "whatsapp",
    "conversation_id": thread_id,
    "handles": [sender],
    "speaker": {"role": "customer"},
    "content": {"text": question},
    "verification_hint": "V1",
})
niadra.flush()

# o contexto no formato do chat, no nível 1
ctx = niadra.context(sender, view="chat", verification="V1", conversation_id=thread_id)

# a cliente pede o protocolo lido na ligação: o agente consulta o histórico
hits = niadra.search(
    sender,
    "protocolo da ligação de hoje",
    verification="V1",
    conversation_id=thread_id,
)

# depois de responder: a resposta também entra na memória
niadra.track({
    "channel": "whatsapp",
    "conversation_id": thread_id,
    "handles": [sender],
    "speaker": {"role": "ai_agent"},
    "content": {"text": reply},
})
```

O protocolo lido na ligação ficou fora do contexto, no histórico, e a busca o traz quando a cliente pede.

## Quanto tempo pode passar entre a ligação e a mensagem?

Cada intervalo pede um cuidado:

- **Segundos.** A Marina desliga e escreve na hora. As últimas falas da ligação já aparecem no contexto, na camada "agora há pouco", com canal e horário, enquanto a Niadra termina de processar a conversa.
- **Horas.** É o caso das 18h30. A visita e o aviso já aparecem como pendências.
- **Dias.** O contexto mostra o estado mais recente de cada pendência, e a ligação continua no histórico.

No WhatsApp, a última mensagem ou ligação da pessoa abre uma janela de atendimento de 24 horas. Fechada a janela, a empresa precisa de um modelo de mensagem pré-aprovado para escrever ([documentação da Meta](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages#customer-service-windows)). Uma ligação pela rede telefônica não abre essa janela. O aviso de amanhã cedo sai como mensagem livre, porque a Marina escreveu às 18h30. Se a visita fosse daqui a três dias, o aviso precisaria de um modelo aprovado.

## Como testar e medir uma passagem de canal?

Antes da produção, rode este roteiro:

1. Ligue para o agente de voz de um número de teste e feche um caso com promessa.
2. Desligue e escreva no WhatsApp do mesmo número, logo em seguida e horas depois.
3. Confira se o agente de WhatsApp cita a ligação e a promessa, sem pedir nome nem motivo.
4. Repita com uma conta com nome de usuário que nunca escreveu para a empresa: nada da ligação aparece antes da confirmação.
5. Faça o caminho inverso: escreva primeiro e ligue depois.

Em produção, meça por cliente, somando os canais: a mensagem das 18h30 é um primeiro contato no WhatsApp e um recontato da ligação. No benchmark de centrais de atendimento da [SQM Group](https://www.sqmgroup.com/resources/library/blog/fcr-metric-operating-philosophy), 29% dos clientes precisam ligar de novo pelo mesmo motivo, e conferir o andamento de um caso aberto está entre as causas comuns. Três medidas contam:

- **Recontato:** o cliente voltou pelo mesmo motivo, em qualquer canal?
- **Pergunta repetida:** o agente pediu algo que o contexto já trazia?
- **Tempo até resolver:** do primeiro contato ao desfecho.

## Como a Niadra resolve

A Niadra é a memória compartilhada de todos os agentes de IA de uma empresa, de atendimento e internos, em todos os canais e fornecedores. Cada agente ganha três chamadas, e nenhum fornecedor precisa de integração com o outro.

- `track()` leva cada resposta para a memória sem atrasar o agente. Quando a conversa termina, a IA da Niadra lê a conversa junto com as outras do mesmo cliente que terminaram nos mesmos segundos e extrai o que foi tratado, o desfecho, as pendências e as promessas. Uma conversa curta respondida na hora fica guardada como foi dita, sem passar pelo modelo.
- `context()` reconhece o cliente por telefone, e-mail, documento ou login do app, com o peso de cada identificador. Já compilado, sem modelo de IA na leitura, entrega o contexto no formato do canal, com a origem de cada fato, conversa e ação.
- `search()` abre o histórico inteiro, com qualquer LLM, pelo SDK, pela API HTTP ou como ferramenta MCP.

E-mail, telefone, cartão, IBAN e endereço são mascarados antes de a IA da Niadra ler as conversas. Ao seu agente, o dado sensível só chega depois da verificação que a sua política exige. A Niadra é totalmente gerenciada, e do seu lado fica o SDK, em Python e TypeScript ([Produtos](/produtos)).

Os adaptadores de [LiveKit](/integracoes/livekit), [Pipecat](/integracoes/pipecat), [Vapi](/integracoes/vapi), [Retell](/integracoes/retell) e [ElevenLabs](/integracoes/elevenlabs) fazem a leitura durante o toque; os de [WhatsApp Cloud API](/integracoes/whatsapp) e [Twilio](/integracoes/twilio) recebem a mensagem do outro lado. O roteiro completo, com a passagem nos dois sentidos, está em [dez testes antes de pôr agentes de IA de vários fornecedores em produção](/blog/dez-testes-antes-de-por-agentes-de-ia-de-varios-fornecedores-em-producao); no [benchmark de 30/09/2026](/benchmark), a continuidade entre canais foi medida em 98,8% dos 338 casos.

## Perguntas frequentes

### Preciso integrar o fornecedor de voz com o fornecedor de WhatsApp?

Não. Cada agente se conecta à mesma memória com três chamadas, e nenhum fornecedor lê o sistema do outro. O agente de WhatsApp recebe o contexto que nasceu da ligação.

### Uma ligação telefônica abre a janela de 24 horas do WhatsApp?

Não. Pela [documentação da Meta](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages#customer-service-windows), a janela abre quando a pessoa manda mensagem ou liga pelo próprio WhatsApp. Fora da janela, a empresa precisa de um modelo de mensagem pré-aprovado para escrever primeiro.

### Como sei se a passagem da voz para o WhatsApp funciona?

Meça por cliente, somando os canais: recontato pelo mesmo motivo, pergunta repetida e tempo até resolver. Antes da produção, ligue de um número de teste, desligue e escreva no WhatsApp do mesmo número. O agente precisa responder sem perguntar o que a ligação já resolveu.
