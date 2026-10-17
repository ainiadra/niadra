---
id: identidade-entre-canais
lang: pt
slug: como-reconhecer-o-mesmo-cliente-em-todos-os-canais
title: Como reconhecer o mesmo cliente em todos os canais
description: Telefone, e-mail, documento e login do app têm pesos diferentes. Veja como unificar a identidade do cliente entre canais para agentes de IA, sem expor dados.
order: 2
kind: guide
date: 2026-09-21T21:49:00-03:00
updated: 2026-10-01T10:00:00-03:00
category: Identidade
icon: user
cover: Um cliente, todos os canais
keywords: como unificar a identidade do cliente entre canais, identidade do cliente para agentes de IA, reconhecer o mesmo cliente no WhatsApp e na voz, resolução de identidade omnichannel, número de telefone reciclado, verificação de identidade no atendimento por IA
---

Para reconhecer o mesmo cliente no WhatsApp, na voz, no e-mail e no app, o sistema cruza os identificadores que cada canal entrega (telefone, e-mail, documento, login do app) e atribui um peso a cada um, porque nenhum deles, sozinho, prova quem está do outro lado. Cada vínculo entre dois identificadores fica registrado com a origem e o peso de cada um, e pode ser desfeito sem perder histórico. O agente de IA recebe um contexto que muda conforme o quanto a conversa já provou quem é a pessoa: antes da verificação, o necessário para atender; depois dela, os dados sensíveis.

## Por que cada canal identifica o cliente de um jeito diferente?

Cada canal entrega um identificador próprio, e nenhum foi desenhado para conversar com os outros.

- O **WhatsApp** entrega, em geral, o número de quem escreve. A [documentação oficial da plataforma](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/overview) avisa que o ID do remetente pode não coincidir com o telefone, e os nomes de usuário trazem um [ID com escopo da empresa](https://developers.facebook.com/documentation/business-messaging/whatsapp/business-scoped-user-ids/), diferente para cada empresa.
- A **voz** entrega o número de origem, que pode ser falsificado, chegar oculto ou ser o de um PABX (que identifica a empresa, não a pessoa). A autenticação de chamadas que a [FCC](https://www.fcc.gov/call-authentication) exige das operadoras (STIR/SHAKEN) atesta a linha, não quem fala.
- O **e-mail** confirmado por um código vale mais que um endereço digitado num formulário.
- O **app** entrega o login com senha, o identificador mais forte, mas apenas de quem baixou o app e entrou.
- O **documento** falado na ligação mostra que a pessoa conhece o número, e não que é a dona dele.

Sem um ID de usuário válido em todos os canais, a identidade precisa ser montada a partir das pistas de cada conversa.

## Como unir identificadores sem um ID de usuário pronto?

O primeiro contato já cria o cliente, ancorado no identificador que o canal entregou. Se a empresa tem um ID de CRM ou de app, ele entra como mais um identificador, com peso alto. Cada identificador recebe um peso conforme o que prova:

- O telefone é uma **pista**: o número pode ter trocado de dono.
- O documento informado na ligação é **declarado**: a pessoa disse, e nada confirmou.
- O e-mail confirmado por código é **verificado**.
- O login do app com senha é **autenticado**.

Dois identificadores viram o mesmo cliente quando há evidência. O sistema da empresa informa que o login 88213 tem aquele telefone, ou a pessoa confirma um código enviado ao número. Nome parecido nunca une perfis.

Cada união fica registrada como uma afirmação, com método, data e origem. Cada fato da memória fica preso ao identificador e à conversa de onde veio, e não ao perfil. Por isso uma união errada se desfaz: retirada a afirmação, os fatos voltam para o dono certo.

Três regras protegem contra a união errada:

- Dois perfis já autenticados nunca se unem sozinhos. Essa união só acontece quando alguém a força, com o autor e o motivo registrados.
- Valores de teste, como o telefone `+5511999999999`, o e-mail `teste@example.com` ou o documento `12345678909`, nunca viram chave, e um evento que só traz valores de teste não entra na memória de nenhum cliente.
- Um identificador que aparece ligado a três identificadores diferentes do mesmo tipo em 24 horas (o telefone de uma loja, o PABX de uma empresa) fica marcado como ambíguo e deixa de unir perfis e de abrir a memória na leitura.

## O que fazer quando o número de telefone muda de dono?

As operadoras reatribuem números desativados a novos assinantes. Pesquisadores da [Universidade de Princeton](https://recyclednumbers.cs.princeton.edu/) ficaram uma semana com 200 números reciclados, e 19 deles ainda recebiam mensagens sensíveis do dono anterior, como códigos de autenticação. No atendimento, se o telefone é a chave, o agente de IA cumprimenta o novo dono pelo nome do antigo e lê para ele as faturas de outra pessoa.

Três práticas tratam esse risco sem apagar nada:

1. Um número que passa mais de 180 dias sem aparecer e volta sem um ID de cadastro do mesmo cliente (documento, login do app ou ID de sistema) fica em dúvida. O [NIST](https://pages.nist.gov/800-63-4/) pede que cada organização defina quando reverificar uma identidade depois de inatividade prolongada.
2. O vínculo entre o número e o cliente é suspenso, não apagado. A leitura por esse número responde como a um primeiro contato, e o histórico do dono anterior fica guardado, fora do contexto.
3. A confirmação não revela nada. O agente pede o documento, que o dono verdadeiro digitaria sem esforço, e o manda com o número num `identify()`: se é o do cadastro, o número volta a valer. Ele nunca pergunta "você é a Marina?", porque a pergunta entrega o nome.

Se a confirmação bate, o contexto completo volta. Se não bate, o número continua em dúvida, cada leitura por ele é um primeiro contato, e o histórico da Marina segue intacto.

## Como verificar a identidade durante a conversa?

Reconhecer indica qual cliente provavelmente está na conversa. Autenticar prova que é ele, e a norma do NIST trata as duas coisas em partes separadas. Na verificação progressiva, a conversa sobe de nível a cada prova nova:

| Nível | O que a conversa provou |
|---|---|
| 0. Declarado | A pessoa disse quem é |
| 1. Plausível pelo canal | A origem já está ligada ao cliente |
| 2. Atestado pelo canal | A rede ou a plataforma atesta que a origem não foi falsificada |
| 3. Desafiado | A pessoa confirmou um código enviado ao canal, ou entrou com senha |
| 4. Documental | O dado bate com o cadastro da empresa, ou um atendente confirmou |

O nível sobe apenas por um evento explícito, como um código confirmado, e nunca por inferência de um modelo de IA. No código, o agente pede o contexto de novo quando o nível sobe:

```python
from niadra import Niadra, phone

niadra = Niadra(channel="voice")
caller = phone(caller_id)

# Durante o toque: o atestado parcial da operadora prova o nível 1 desta ligação
niadra.verify("network_attestation", "V1", handle=caller, conversation_id=call_id)
ctx = niadra.context(
    caller, view="voice", verification="V1", conversation_id=call_id
)

# Código por SMS confirmado: a conversa sobe para o nível 3
niadra.verify("otp_sms", "V3", handle=caller, conversation_id=call_id)
ctx = niadra.context(
    caller, view="voice", verification="V3", conversation_id=call_id
)

# Durante a conversa: o agente consulta o histórico da cliente
hits = niadra.search(
    caller,
    "crédito por visita técnica perdida",
    verification="V3",
    conversation_id=call_id,
    voice=True,
)
```

## O que o agente de IA recebe antes e depois da verificação?

Às 14h02, a Marina avisa no WhatsApp, onde atende o agente do fornecedor A, que a visita técnica prometida para hoje de manhã não aconteceu. Às 14h05, ela contesta pelo app a fatura de agosto. Às 14h07, ela liga, e quem atende é o agente de voz do fornecedor B.

A operadora entrega a chamada com um atestado parcial de rede, e o agente registra esse atestado durante o toque: a ligação começa no nível 1. O contexto que chega antes do alô traz o nome, a visita que não aconteceu (WhatsApp, 14h02), a contestação aberta (App, 14h05) e o aviso de que outra visita já tinha falhado (Telefone, 12/03). A política da empresa exige o nível 3 para documento e dados de pagamento, então esses itens ficam retidos.

A Marina confirma o código enviado por SMS, e o agente pede o contexto de novo, agora com os itens retidos. Quando ela lembra que da outra vez recebeu um crédito, o agente consulta o histórico e encontra o registro de 12/03, por telefone: técnico não compareceu, crédito de R$ 40 aplicado na fatura de abril.

A privacidade vem do próprio desenho: quem liga de um número falsificado recebe no máximo o que a empresa liberou para o nível que a ligação provou, sem o que a política reserva para depois da verificação. É a minimização de dados do [GDPR](https://gdpr-info.eu/art-5-gdpr/) e da LGPD, aplicada a cada momento da conversa.

## Como a Niadra resolve

A Niadra cruza telefone, e-mail, documento e login do app e une tudo num só cliente, com o peso de cada identificador. Cada informação fica ligada ao identificador de onde veio, e por isso a Niadra separa duas pessoas unidas por engano sem perder o histórico de nenhuma. O contexto muda conforme o quanto a conversa já provou quem é a pessoa, em cinco níveis de verificação, e chega ao agente com meta de menos de 100 ms, com a origem de cada fato, conversa e ação. E-mail, telefone, cartão, IBAN e endereço são mascarados antes de chegar ao modelo de IA que processa as conversas. Ao seu agente, o dado sensível só chega depois da verificação que a sua política exige. Cada leitura entra numa trilha de auditoria encadeada por SHA-256. Veja o motor de identidade na [página de Identidade](/produtos/identidade) e as camadas de proteção em [Segurança](/recursos#seguranca).

O efeito de não resolver a identidade está medido: no [benchmark de 30/09/2026](/benchmark), a mesma memória de código aberto acerta 77,6% dos cenários entre canais com um id único e 28,5% quando cada canal manda o próprio id, nos mesmos 165 cenários ([por que a identidade entre canais quebra a memória do agente](/blog/por-que-a-identidade-entre-canais-quebra-a-memoria-do-agente)). Os adaptadores de [WhatsApp Cloud API](/integracoes/whatsapp) e [Twilio](/integracoes/twilio) entregam o `wa_id` e o telefone como pistas, e a [Identidade](/produtos/identidade) decide o peso de cada uma.

## Perguntas frequentes

### Preciso de um ID de usuário único antes de usar agentes de IA em vários canais?

Não. O primeiro contato em qualquer canal já cria o cliente, a partir do identificador que o canal entregou. Um ID de CRM ou de app, se existir, entra como mais um identificador, com peso alto.

### O número de telefone basta para identificar o cliente?

Não. O número de origem pode ser falsificado, chegar oculto, pertencer a um PABX ou ter trocado de dono. Ele serve para reconhecer o cliente provável, e dado sensível exige uma prova mais forte, como um código confirmado ou um login.

### O que acontece se duas pessoas forem unidas por engano?

A Niadra separa as duas sem perder o histórico de nenhuma. Isso funciona porque cada informação fica ligada ao identificador e à conversa de onde veio, e não ao perfil.

### Como a verificação progressiva protege a privacidade do cliente?

O que chega ao agente segue o nível que aquela ligação provou, e um número que volta depois de 180 dias calado é lido como primeiro contato até o seu sistema ligá-lo de novo ao cadastro.
