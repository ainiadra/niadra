/**
 * The integration pages (/integracoes), in Portuguese. Portuguese defines the shape; the English file
 * is typed against it. Each page answers one question a builder asks ("memória para agente LiveKit"):
 * the problem the framework leaves open, how the adapter wires the five primitives (the same five of
 * docs.niadra.com/integrations), what it does not do, and the questions a buyer asks before the request.
 * Every mechanism here is the docs page's, in the site's words; nothing is promised beyond it.
 */
import type { IntegrationGroup, IntegrationKey } from '../integrations'

type Faq = { q: string; a: string }
type IntegrationPage = {
  /** The name as the page and the index show it. */
  name: string
  title: string
  description: string
  h1a: string
  h1b: string
  sub: string
  /** Why the memory the framework has is not the company's memory, in two short paragraphs. */
  problem: readonly string[]
  /** How the adapter wires each primitive, one or two sentences each. */
  how: { context: string; turns: string; tools: string; verify: string; handoff: string }
  limits: readonly string[]
  faq: readonly Faq[]
  /** For an integration without an SDK snippet (the n8n node): how to install it, in the code panel's place. */
  install?: string
}

const pages: Record<IntegrationKey, IntegrationPage> = {
    livekit: {
      name: 'LiveKit Agents',
      title: 'Memória para agentes de voz no LiveKit: contexto antes do alô',
      description:
        'Como dar memória de cliente a um agente de voz do LiveKit Agents, em Python e Node: a primeira leitura começa quando o cliente entra na sala, cada turno recebe o contexto antes da chamada ao modelo, e as ferramentas do histórico vão amarradas a quem liga. Código do SDK, limites e o que o agente recebe.',
      h1a: 'Memória para agentes de voz',
      h1b: 'no LiveKit.',
      sub: 'O agente do LiveKit começa a ligação sabendo quem está na linha. A primeira leitura começa quando o cliente entra na sala e é esperada em até 1,5 s, enquanto o telefone toca. A cada turno, o contexto vem da memória do SDK na hora, e o que a transcrição parcial antecipou chega em até 200 ms. Nada disso derruba um turno.',
      problem: [
        'A sessão do LiveKit sabe tudo o que aconteceu nesta ligação e nada do que aconteceu antes dela: a reclamação no WhatsApp de ontem, o crédito que o agente de cobrança lançou no ERP, a promessa feita pelo agente de outro fornecedor. O histórico do chat context morre com a sala.',
        'A Niadra é a memória fora da sala. O adaptador lê o contexto da cliente antes de cada chamada ao modelo, registra cada fala final como turno e entrega as três ferramentas do histórico amarradas ao número SIP. O que esta ligação produz vira memória para o agente de WhatsApp de amanhã, de qualquer fornecedor.',
      ],
      how: {
        context: 'A primeira leitura começa quando o cliente entra na sala (begin()) e a primeira chamada ao modelo a espera dentro de 1,5 s (ready()). Em Python, cada chamada seguinte recebe o contexto em llm_node, numa cópia do chat context, para nada se acumular no histórico do agente e a geração antecipada do LiveKit continuar batendo; com modelo realtime, em on_user_turn_completed. Em Node, em onUserTurnCompleted. Cada user_input_transcribed, parcial ou final, manda o turno até ali com prefetch(), em segundo plano.',
        turns: 'O evento conversation_item_added da sessão registra cada transcrição final do cliente, com a confiança do STT, e cada resposta do agente, com o uso que o modelo informou. O close da sessão encerra a conversa.',
        tools: 'As três ferramentas do histórico, como function tools com os esquemas do kit, amarradas a quem liga. history_tools=False deixa de fora.',
        verify: 'attestation= (Python) ou verify: attestationProof(...) (Node) com o nível STIR/SHAKEN da operadora: A prova V2, B e C provam V1. O LiveKit não lê esse cabeçalho sozinho: mapeie o cabeçalho SIP para um atributo do participante e passe.',
        handoff: 'Quando a sessão passa para outro agente, handoff("agent"), e o próximo NiadraAgent recebe a mesma conversa. transferred_to_human() numa transferência SIP ou assistida para uma pessoa.',
      },
      limits: [
        'Nada aqui derruba um turno: encaixes que não chegam em 200 ms ficam de fora e o turno sai com o corpo fixado; uma falha ao registrar vai para o log, sem conteúdo.',
        'O atestado STIR/SHAKEN não vem nos atributos sip.* do LiveKit. Sem o mapeamento do cabeçalho, a leitura sai em V0 e traz só o que a política libera nesse nível.',
        'Em Python, os extras livekit e openai-agents fixam versões incompatíveis de uma dependência comum: instale um por ambiente.',
        'Testado contra livekit-agents 1.8.3 e @livekit/agents 1.9.0, com LLM, STT e TTS substituídos por fakes e a Niadra no emulador, na CI de cada SDK.',
      ],
      faq: [
        {
          q: 'O contexto atrasa a primeira palavra do agente?',
          a: 'A primeira leitura começa no momento em que o cliente entra na sala, antes de o agente falar, e é esperada em até 1,5 s, o tempo de uma conexão fria mais a compilação. Se não chegar, a saudação sai sem o contexto, e ele entra no turno seguinte. Numa conversa já aberta, o corpo fixado vem da memória do SDK na hora.',
        },
        {
          q: 'E se a Niadra ficar lenta no meio da ligação?',
          a: 'O turno sai com o corpo fixado e sem os encaixes que não chegaram em 200 ms. A leitura continua em segundo plano, revalida o corpo e deixa o delta para o turno seguinte. A ligação nunca espera a Niadra.',
        },
        {
          q: 'Funciona com modelo realtime, em áudio para áudio?',
          a: 'Funciona. Com modelo realtime, o adaptador entrega o contexto em on_user_turn_completed, o gancho que o próprio LiveKit documenta para RAG. Com pipeline de STT, LLM e TTS, em llm_node.',
        },
      ],
    },

    pipecat: {
      name: 'Pipecat',
      title: 'Memória para agentes de voz no Pipecat: um processador entre o usuário e o LLM',
      description:
        'Como dar memória de cliente a um agente do Pipecat: um FrameProcessor entre o agregador do usuário e o LLM lê o contexto em cada LLMContextFrame, registra os turnos pelos agregadores e entrega as ferramentas do histórico como FunctionSchema. Código do SDK, limites e o que o agente recebe.',
      h1a: 'Memória para agentes de voz',
      h1b: 'no Pipecat.',
      sub: 'Um processador de frames entre aggregators.user() e o LLM. Em cada LLMContextFrame ele põe o contexto do cliente no lugar certo, com o corpo fixado da memória do SDK e os encaixes que a transcrição parcial antecipou. observe(aggregators) registra os turnos. O pipeline nunca para por causa da memória.',
      problem: [
        'O LLMContext do Pipecat é o contexto desta chamada: as mensagens desde o StartFrame. O que a cliente disse ontem ao agente de WhatsApp, o que a cobrança resolveu hoje de manhã e a promessa que o atendente humano fez na semana passada não estão nele, e nenhum serviço de memória por mensagem resolve isso, porque cada um guarda a memória de um agente só.',
        'A Niadra entra como um processador: lê o contexto compilado da cliente, fixado por ligação, e o coloca logo depois das mensagens de sistema, com as falas de outros canais no fim. Os blocos do turno anterior saem antes, então o contexto compartilhado nunca os acumula. Uma inferência especulativa também os recebe, na cópia provisória dela.',
      ],
      how: {
        context: 'O StartFrame do pipeline começa a primeira leitura (begin()), e a primeira inferência a espera dentro de 1,5 s (ready()). Em cada LLMContextFrame, entre o agregador do usuário e o LLM, context() na view voice: o contexto entra logo depois das mensagens system ou developer iniciais e o turn_block no fim. memory.prefetcher(), logo depois do serviço de STT, manda o turno até ali com prefetch() a cada InterimTranscriptionFrame e TranscriptionFrame, sem segurar frame nenhum.',
        turns: 'observe() assina os agregadores: cada mensagem do usuário escrita no contexto (on_user_turn_message_added, final nos modos cascata e realtime) é o turno do cliente, e cada turno do assistente encerrado (on_assistant_turn_stopped) é o do agente. EndFrame e CancelFrame encerram a conversa.',
        tools: 'history_tools() devolve as três ferramentas do histórico como FunctionSchema que carregam os próprios tratadores, então o serviço de LLM as registra a partir do contexto. Os esquemas JSON são os do kit.',
        verify: 'attestation= com o nível STIR/SHAKEN da operadora (A, B, C, ou o StirVerstat da Twilio), registrado uma vez antes do primeiro contexto.',
        handoff: 'transferred_to_human() e transferred_to_agent() registram a transferência; chame onde o pipeline, ou o Pipecat Flows, passa a ligação adiante.',
      },
      limits: [
        'O processador sempre passa o frame adiante, mesmo quando a Niadra falha: o pipeline nunca para por causa da memória.',
        'Usa LLMContext e LLMContextFrame, o contexto universal do Pipecat desde a 1.9; OpenAILLMContext e LLMMessagesFrame não existem mais no main do Pipecat e não são tratados.',
        'Só Python 3.11 ou mais novo, porque o Pipecat pede, e só Python: os pacotes JavaScript do Pipecat e do Daily são clientes de navegador, onde uma chave da Niadra nunca deve ir. Os extras pipecat e crewai fixam versões incompatíveis de uma dependência comum: instale um por ambiente.',
        'Testado contra pipecat-ai 1.11.0 com um pipeline real, LLM falso e frames empurrados à mão, sem áudio e sem rede.',
      ],
      faq: [
        {
          q: 'Onde o processador entra no pipeline?',
          a: 'Entre o agregador do usuário e o LLM: Pipeline([transport.input(), stt, user, memory, llm, tts, transport.output(), assistant]). O prefetcher vai logo depois do STT, para a memória da cliente estar aberta quando a fala terminar.',
        },
        {
          q: 'É uma busca por mensagem, como um serviço de memória vetorial?',
          a: 'Não. A leitura é a da Niadra: um contexto fixado por ligação, compilado quando a memória muda, servido da memória do SDK em cada turno, com encaixes das palavras do turno quando elas pedem. A busca no histórico fica como ferramenta, para o modelo chamar quando o contexto não responde.',
        },
        {
          q: 'E se a Niadra demorar mais do que o turno aguenta?',
          a: 'O frame segue com o corpo fixado e sem os encaixes que não chegaram em 200 ms; a leitura continua em segundo plano e o delta entra no turno seguinte. Com a Niadra fora, o frame segue sem contexto, nunca com erro.',
        },
      ],
    },

    vapi: {
      name: 'Vapi',
      title: 'Memória para assistentes da Vapi: contexto no assistant-request, num URL de servidor só',
      description:
        'Como dar memória de cliente a um assistente da Vapi: um URL de servidor responde o assistant-request com o contexto, roda as ferramentas do histórico em tool-calls, registra o transbordo e lê o end-of-call-report como turnos. Código do SDK, limites e o que o assistente recebe.',
      h1a: 'Memória para assistentes',
      h1b: 'da Vapi.',
      sub: 'A Vapi manda todas as mensagens de servidor para um URL, e um tratador só responde as que importam: o contexto no assistant-request, enquanto o assistente ainda não responde; as ferramentas do histórico em tool-calls; o destino do transbordo; e o end-of-call-report como turnos. Sem pacote da Vapi.',
      problem: [
        'O assistente da Vapi recebe o número de quem liga e um prompt. O que essa pessoa disse ontem no WhatsApp a outro fornecedor, o crédito que o agente de cobrança lançou e a visita remarcada ficaram em outros sistemas. A base de conhecimento do assistente é sobre a empresa, não sobre esta cliente.',
        'A Niadra responde o assistant-request com o contexto da cliente, lido enquanto o telefone toca: com um assistente transitório, dentro de model.messages, logo depois das mensagens de sistema; com um assistente salvo, na variável niadra_context, para um prompt que diz {{niadra_context}} depois das instruções. O que a ligação produz vira memória para qualquer outro agente.',
      ],
      how: {
        context: 'No assistant-request, o adaptador começa a primeira leitura de quem liga (begin(), depois do atestado quando você o passa), a espera dentro de 1,5 s e responde o assistente. Com um assistente transitório (assistant=), o contexto entra em model.messages logo depois das mensagens de sistema; com um assistente salvo (assistant_id=), vai em assistantOverrides.variableValues.niadra_context.',
        turns: 'O end-of-call-report registra cada mensagem do usuário e do assistente como turno no momento dela e encerra a conversa. As chaves de idempotência vêm da ligação, então um relatório reentregue não grava nada duas vezes.',
        tools: 'tool_definitions(url) (Python) e vapiTools() (TypeScript) geram as ferramentas do histórico como ferramentas de função da Vapi, palavra por palavra como o kit; tool-calls roda cada uma para o cliente da ligação, nunca para um argumento do modelo.',
        verify: 'O assistant-request chama verify() quando você passa o atestado da operadora.',
        handoff: 'transfer-destination-request e handoff-destination-request registram a transferência, para uma pessoa ou para outro assistente de um squad, e respondem o destino que a sua função destination= (Python) ou transfer (TypeScript) devolve; uma ligação encaminhada no relatório final também vira handoff.',
      },
      limits: [
        'Pedidos sem o segredo do servidor (x-vapi-secret, ou Authorization: Bearer em TypeScript) respondem 401: eles leriam dado do cliente.',
        'A Niadra lenta ou fora não derruba a ligação: o assistente começa sem o contexto e uma ferramenta responde que o histórico está indisponível.',
        'O atestado da operadora não vem na mensagem da Vapi; sem ele, a leitura é V0 e traz só o que a política libera nesse nível.',
        'Testado com cargas gravadas no formato público e o segredo de teste; nenhuma conta da Vapi é necessária.',
      ],
      faq: [
        {
          q: 'Uso assistente salvo ou transitório?',
          a: 'Os dois funcionam. Com o assistente salvo no painel da Vapi, o contexto chega na variável niadra_context, e o seu prompt diz {{niadra_context}} depois das instruções. Com o assistente transitório montado no servidor, o contexto entra em model.messages. O resto é igual.',
        },
        {
          q: 'A ferramenta do histórico pode ler o cliente errado?',
          a: 'Não. O tratador amarra o kit ao cliente da ligação que a mensagem tool-calls traz, nunca a um argumento do modelo. O modelo escolhe a consulta; quem é o cliente vem da Vapi.',
        },
        {
          q: 'O que acontece se a Niadra estiver fora quando a ligação entra?',
          a: 'O assistant-request é respondido do mesmo jeito, sem o contexto, e o assistente atende como atenderia hoje. Uma ferramenta do histórico responde que o histórico está indisponível. O end-of-call-report é registrado quando a Niadra voltar, pelas chaves de idempotência da ligação.',
        },
      ],
    },

    retell: {
      name: 'Retell AI',
      title: 'Memória para agentes de voz da Retell: contexto como variável dinâmica no webhook de entrada',
      description:
        'Como dar memória de cliente a um agente de voz da Retell: o webhook de ligação recebida responde niadra_context como variável dinâmica, as custom functions rodam as ferramentas do histórico, o call_ended vira turnos e, em TypeScript, o websocket do LLM próprio recebe o contexto. Código do SDK e limites.',
      h1a: 'Memória para agentes de voz',
      h1b: 'da Retell.',
      sub: 'A Retell chega ao seu servidor por três caminhos, e o adaptador responde cada um: o webhook de ligação recebida com o contexto como variável dinâmica, as custom functions do Retell LLM com as ferramentas do histórico e o webhook do agente com os eventos da ligação. Toda requisição traz x-retell-signature; sem assinatura válida, 401.',
      problem: [
        'O agente da Retell recebe as variáveis dinâmicas que o seu webhook de entrada devolve. Sem uma memória fora dele, essas variáveis são o que o seu CRM sabe no momento do toque, e não o que a cliente contou ontem ao agente de WhatsApp, nem o que o agente de cobrança fez hoje.',
        'A Niadra responde o webhook de entrada com niadra_context, lido enquanto o telefone toca, e com as notas do próprio agente. O prompt diz {{niadra_context}} depois das instruções. Numa ligação de saída, outbound() devolve as mesmas variáveis e um metadata para o create_phone_call.',
      ],
      how: {
        context: 'O webhook de entrada começa a primeira leitura de quem liga (begin(), depois do atestado quando você o passa), a espera dentro de 1,5 s e responde a variável niadra_context. Em TypeScript vem também niadra_turn, com as falas de outros canais e o delta, e o LLM próprio manda a última fala do cliente como query e a fala até ali com prefetch() a cada update_only.',
        turns: 'No call_ended, cada fala de transcript_object vira um turno no momento dela dentro da ligação, e a conversa é encerrada. As chaves de idempotência vêm da ligação, então um evento reentregue não grava nada duas vezes. Os outros eventos respondem 200.',
        tools: 'tool_configs(url) (Python) e handlers.toolConfigs({ url }) (TypeScript) geram as ferramentas do histórico como custom tools da Retell, as general_tools do Retell LLM, com nomes, descrições e esquemas do kit; a custom function roda cada uma para o cliente da ligação que a requisição traz.',
        verify: 'attestation= (Python) ou verify (TypeScript), uma função do payload de entrada (lendo custom_sip_headers, por exemplo), devolve o nível STIR/SHAKEN da operadora; ele vai a verify() antes do primeiro contexto.',
        handoff: 'Uma ligação transferida (transfer_started, ou call_transfer no call_ended) vira transbordo para uma pessoa, uma vez só.',
      },
      limits: [
        'Em Python, niadra é um Niadra ou um AsyncNiadra: com o cliente síncrono, chame as variantes *_sync. override_agent_id= (Python) ou inboundFields(call) (TypeScript) acrescentam campos à resposta do webhook de entrada, como o agente que atende.',
        'Em TypeScript, o cliente e o nível de verificação de cada ligação ficam num store entre os webhooks (em memória por padrão; passe o seu para mais de uma instância), e otherTool(name, args, call) serve as custom functions que não são da Niadra.',
        'A Niadra lenta ou fora não derruba a ligação: o webhook de entrada responde variáveis vazias, uma ferramenta responde que o histórico está indisponível, e o webhook continua respondendo 200.',
        'Testado com cargas no formato público da Retell e assinaturas calculadas no próprio teste, contra o emulador; em TypeScript os tratadores rodam também em Deno, Bun e workerd.',
      ],
      faq: [
        {
          q: 'Funciona em ligação de saída?',
          a: 'Funciona. Em Python, outbound(to_number) devolve as variáveis dinâmicas e um metadata para o create_phone_call da Retell, com o id da conversa na Niadra; em TypeScript, o call_started guarda o cliente das ligações de saída e web. O número chamado é o sujeito.',
        },
        {
          q: 'Posso usar o meu próprio LLM pelo websocket da Retell?',
          a: 'Em TypeScript, sim: o LLM próprio recebe o contexto, manda a última fala do cliente como query e antecipa a leitura com prefetch() a cada update_only, então o turno chega com os encaixes prontos.',
        },
        {
          q: 'E se o cliente for identificado por outra coisa que não o número?',
          a: 'Passe subject=, uma função da ligação, e o sujeito passa a ser o que você devolver: o id do app, o e-mail, o cliente do CRM. Por padrão, é o número de quem liga numa ligação recebida e o número chamado numa de saída.',
        },
      ],
    },

    elevenlabs: {
      name: 'ElevenLabs Agents Platform',
      title: 'Memória para agentes da ElevenLabs: três webhooks, do início da ligação ao pós-chamada',
      description:
        'Como dar memória de cliente a um agente telefônico da ElevenLabs Agents Platform: o webhook de início entrega o contexto como variável dinâmica, as ferramentas de servidor rodam o kit do histórico para quem está na linha, e o pós-chamada registra a transcrição turno a turno. Código do SDK, segredos e limites.',
      h1a: 'Memória para agentes',
      h1b: 'da ElevenLabs.',
      sub: 'Uma ligação para um agente da ElevenLabs chega ao seu servidor por três webhooks, e o adaptador responde cada um: o de início entrega o contexto como variável dinâmica, as ferramentas de servidor rodam o histórico para quem está na linha, e o pós-chamada registra a transcrição e encerra a conversa. Do lado do servidor, não do dispositivo.',
      problem: [
        'O agente da ElevenLabs sabe o que o prompt diz e o que a ligação revela. O que a cliente contou a outro canal, e o que outro agente fez por dentro da empresa, está fora do prompt, e a base de conhecimento do agente fala da empresa, não desta pessoa.',
        'O webhook de início ("Fetch initiation client data from a webhook") recebe caller_id, called_number, agent_id, call_sid e conversation_id. A Niadra abre a conversa, lê o contexto enquanto o agente ainda não falou e responde conversation_initiation_client_data com a variável niadra_context, que o prompt usa depois das instruções.',
      ],
      how: {
        context: 'O webhook de início recebe os identificadores da ligação; o adaptador abre a conversa, começa a primeira leitura (begin(), depois do atestado quando você o passa), a espera dentro de 1,5 s e responde a variável dinâmica niadra_context. Ponha {{niadra_context}} no prompt do agente, depois das suas instruções; em TypeScript, também {{niadra_turn}} onde as falas de outros canais devem entrar.',
        turns: 'O webhook post_call_transcription valida ElevenLabs-Signature (HMAC-SHA256 de "<t>.<corpo>", 30 minutos de tolerância) e registra cada item de transcript[] como turno no momento dele na ligação, com o uso do modelo; depois end(). Um webhook reentregue não grava nada duas vezes.',
        tools: 'tool_configs(url) (Python) e toolConfigs() (TypeScript) geram as três ferramentas do histórico como ferramentas webhook da ElevenLabs. Os identificadores da ligação (system__call_sid, system__conversation_id, system__caller_id) são preenchidos pela ElevenLabs, nunca pelo modelo, e o tratador amarra o kit a esse cliente.',
        verify: 'O webhook de início chama verify() quando você passa o atestado da operadora; sem ele, a leitura é V0.',
        handoff: 'transfer_to_agent e transfer_to_number chegam no pós-chamada e viram handoff.',
      },
      limits: [
        'O webhook de início e as ferramentas de servidor devolvem dado do cliente, então exigem o cabeçalho X-Niadra-Secret que você configura na ElevenLabs (shared_secret); sem ele, 401.',
        'A Niadra lenta ou fora não derruba a ligação: o início responde um contexto vazio, uma ferramenta responde que o histórico está indisponível, e o pós-chamada continua respondendo 200.',
        'O atestado da operadora não vem no webhook da ElevenLabs; sem ele, a leitura é V0.',
        'Testado com cargas gravadas no formato público dos três webhooks e assinaturas calculadas no próprio teste; nenhuma conta da ElevenLabs é necessária.',
      ],
      faq: [
        {
          q: 'O contexto chega antes de o agente falar?',
          a: 'Chega pelo webhook de início, que a ElevenLabs chama antes de o agente dizer a primeira palavra. O adaptador espera a leitura em até 1,5 s; se ela não chegar, responde um contexto vazio e a ligação segue.',
        },
        {
          q: 'As ferramentas de servidor podem ser chamadas por qualquer um?',
          a: 'Não. Elas exigem o cabeçalho X-Niadra-Secret, configurado no agente da ElevenLabs, e respondem 401 sem ele. Os identificadores da ligação vêm da própria ElevenLabs, então o modelo nunca escolhe de quem é o histórico.',
        },
        {
          q: 'Onde ponho o código: Python ou TypeScript, em que servidor?',
          a: 'Os tratadores são funções puras do corpo e dos cabeçalhos: servem em FastAPI, Flask, Django, Hono, um Lambda ou um Worker. O exemplo mostra FastAPI e Hono.',
        },
      ],
    },

    twilio: {
      name: 'Twilio',
      title: 'Memória para agentes na Twilio: voz, SMS e WhatsApp, com o StirVerstat como prova',
      description:
        'Como dar memória de cliente a um agente que atende pela Twilio: o webhook do toque começa a leitura do contexto, o StirVerstat da operadora prova quem liga antes do primeiro contexto, e as mensagens de SMS e WhatsApp viram turnos pelo MessageSid. Código do SDK, assinatura e limites.',
      h1a: 'Memória para agentes',
      h1b: 'na Twilio.',
      sub: 'O adaptador lê os webhooks de Programmable Voice e de Messaging, SMS e WhatsApp, e em TypeScript os de Conversations: confere X-Twilio-Signature, encontra o cliente, o id da ligação ou da conversa e o que a operadora atestou, e registra o turno de entrada. Ele nunca responde TwiML: a resposta é do seu agente.',
      problem: [
        'A Twilio entrega a ligação, o número e, com STIR/SHAKEN, um atestado de que o número não foi falsificado. Ela não entrega o que esse cliente disse na semana passada no WhatsApp nem o que o agente de pedidos fez ontem, e o seu agente de voz começa do zero a cada CallSid.',
        'A Niadra usa o que a Twilio prova. O webhook do toque começa a leitura em segundo plano, call.verify() registra o StirVerstat (A prova V2, B e C provam V1) e conversation.ready() espera o contexto enquanto o telefone toca. No Messaging, cada mensagem vira um turno pelo MessageSid, e a conversa do WhatsApp lê a mesma memória que a ligação alimentou.',
      ],
      how: {
        context: 'call.conversation() abre a conversa da ligação (CallSid como id, o número de quem liga como sujeito) e, no webhook do toque (ringing), começa a primeira leitura em segundo plano; call.verify() a recomeça no nível que o atestado provou; conversation.ready() a espera dentro de 1,5 s, enquanto o telefone toca, e o seu código entrega o contexto ao agente que atende.',
        turns: 'parse_message() lê um webhook de Messaging: MessageSid é a chave de idempotência, From (whatsapp:+55... ou um telefone) e WaId são os ids do remetente, Body é o texto; message.record() registra o turno do cliente. Em voz, call.ended() encerra a conversa quando o status callback diz completed (ou busy, failed, no-answer, canceled); em TypeScript, recordTwilioInbound() registra cada frase reconhecida.',
        tools: 'As do kit, pela conversa: buscar, linha do tempo e abrir um item, amarradas ao cliente no seu código.',
        verify: 'call.verify() (Python) e verifyTwilio() (TypeScript) registram o StirVerstat da Twilio: TN-Validation-Passed-A prova V2, B e C provam V1; uma validação ausente ou falha não prova nada. Uma mensagem registrada leva verification_hint V1.',
        handoff: 'O handoff() da conversa, onde o seu fluxo transfere.',
      },
      limits: [
        'Uma requisição sem a assinatura certa devolve None (Python) ou nenhum request (TypeScript): responda 403 e não registre nada. Os dois SDKs conferem a assinatura como a Twilio a calcula: o HMAC-SHA1 em Base64, com o seu auth token, do URL completo que a Twilio chamou seguido de cada parâmetro do POST, em ordem de nome.',
        'O adaptador não produz TwiML e não conecta ligação nenhuma: connect_your_voice_agent() no exemplo é o seu código.',
        'A assinatura cobre o URL completo que a Twilio chamou; atrás de um proxy que muda o host ou o esquema, passe o URL público.',
        'Testado com cargas gravadas e assinaturas calculadas no próprio teste; nenhuma conta da Twilio é necessária.',
      ],
      faq: [
        {
          q: 'Serve para o ConversationRelay e para o Media Streams?',
          a: 'O adaptador trata os webhooks de voz, do toque ao status callback, e deixa a mídia com o seu agente. Com ConversationRelay, o seu código entrega o contexto ao agente que atende; com Media Streams, o adaptador do Pipecat recebe o CallSid e o número da mesma forma.',
        },
        {
          q: 'A mesma memória vale para a ligação e para o WhatsApp pela Twilio?',
          a: 'Vale. A ligação entra pelo CallSid com o número como sujeito; a mensagem do WhatsApp entra pelo MessageSid com o mesmo número e o WaId. A Niadra reconhece a mesma pessoa, e o agente de WhatsApp lê o que a ligação produziu.',
        },
        {
          q: 'O número falsificado recebe o contexto da vítima?',
          a: 'Recebe o que a política libera no nível que a ligação provou. Sem StirVerstat válido, a leitura fica em V0, e a política inicial não libera nada nesse nível. O dado sensível espera a prova que a sua política exige.',
        },
      ],
    },

    whatsapp: {
      name: 'WhatsApp Cloud API',
      title: 'Memória para agentes de WhatsApp na Cloud API da Meta: o webhook vira turnos',
      description:
        'Como dar memória de cliente a um agente de WhatsApp na Cloud API: o adaptador confere a assinatura do webhook da Meta, extrai cada mensagem com o wa_id como identificador, registra o turno pelo wamid e registra a resposta pelo id que a Meta devolve. O contexto é lido antes de responder. Código do SDK e limites.',
      h1a: 'Memória para agentes',
      h1b: 'de WhatsApp.',
      sub: 'O adaptador do WhatsApp Cloud API só traduz: confere a assinatura do webhook da Meta, extrai cada mensagem com o identificador do cliente e o item pronto para registrar, e registra a resposta do agente pela resposta da API de envio. Ele não manda mensagem: quem fala com a Graph API é o seu código.',
      problem: [
        'A Cloud API entrega o wa_id de quem escreve e a mensagem. O agente de WhatsApp responde com o que o prompt sabe e, no máximo, com a conversa desta janela. A ligação de ontem, atendida pelo agente de voz de outro fornecedor, e a visita que o agente interno remarcou no sistema não estão aqui.',
        'Com a Niadra, cada mensagem vira um turno pelo wamid, a conversa é aberta com o wa_id como sujeito, e chat.context() devolve o contexto da cliente antes da resposta: fatos, pendências, promessas e o que outros agentes fizeram, no formato de chat. A resposta entra pelo id que a Meta devolve.',
      ],
      how: {
        context: 'O seu código lê context() na conversa que o adaptador ajuda a abrir (wa-<wa_id> como id, o wa_id como sujeito) e monta o prompt; o adaptador não toca no modelo. O flush() depois de record() faz o turno, e o V1 que ele prova, chegarem antes da primeira leitura.',
        turns: 'parse_webhook() (Python) e readWhatsApp() (TypeScript) conferem X-Hub-Signature-256 (HMAC-SHA256 do corpo cru com o app secret) e devolvem as mensagens de toda entrada e mudança, da mais antiga para a mais nova; message.record() e recordInbound() registram o turno do cliente com o wamid como chave de idempotência, porque a Meta reenvia webhooks por dias. sent() e recordOutbound() registram a resposta com o wamid que a Cloud API devolveu.',
        tools: 'As do kit, pela conversa (chat.tools()); nada específico do canal.',
        verify: 'O turno registrado leva verification_hint V1: veio daquele número. Ele sobe a sessão para V1 na próxima leitura. subscribe() (Python) e whatsAppChallenge() (TypeScript) respondem ao desafio de assinatura da Meta no GET.',
        handoff: 'chat.handoff() da conversa, quando o seu fluxo transfere.',
      },
      limits: [
        'O adaptador nunca manda mensagem nem chama a Graph API; ele traduz o webhook e registra o que o seu código enviou.',
        'Mídia chega como referência (id, tipo MIME, SHA-256): baixe os bytes da Graph API, entregue a upload_media() e passe o resultado como upload=, com a sua transcrição de uma nota de voz. O texto de PDF, imagem e arquivos Office é lido só dentro da célula, e só quando o espaço lista o tipo; o tipo vem dos bytes, nunca do MIME declarado.',
        'Cada WhatsAppMessage traz o wa_id e, para um nome de usuário do WhatsApp, o id de usuário com escopo da empresa; status e outros campos do webhook ficam de fora.',
        'Testado com cargas públicas no formato da Meta e assinaturas calculadas no próprio teste; nenhuma conta da Meta é necessária.',
      ],
      faq: [
        {
          q: 'Funciona com um provedor de WhatsApp, e não com a Cloud API direta?',
          a: 'Pela Twilio, sim, com o adaptador da Twilio, que lê o WaId no webhook de Messaging. Com outros provedores, use o SDK direto: a conversa aberta com o wa_id como sujeito, customer() e agent() para os turnos. O desenho é o mesmo.',
        },
        {
          q: 'A Meta reenvia o webhook. O turno entra duas vezes?',
          a: 'Não. O wamid é a chave de idempotência do turno, então um webhook reentregue dias depois grava nada. A resposta do agente usa o wamid que a Cloud API devolveu, pela mesma regra.',
        },
        {
          q: 'O número do WhatsApp prova quem é a pessoa?',
          a: 'Prova que a mensagem veio daquele número: o turno sobe a sessão para V1. Dado que a sua política reserva para V2 ou V3 (um login, um código confirmado) fica retido até a prova chegar.',
        },
      ],
    },

    openaiAgents: {
      name: 'OpenAI Agents SDK',
      title: 'Memória para o OpenAI Agents SDK: filtro de entrada do modelo, hooks e function tools',
      description:
        'Como dar memória de cliente a um agente do OpenAI Agents SDK, em Python e JavaScript, pelos pontos de extensão do próprio SDK: call_model_input_filter para o contexto antes de cada chamada ao modelo, RunHooks para os turnos, FunctionTool para o histórico; em JavaScript, NiadraSession e instruções dinâmicas. Código do SDK e limites.',
      h1a: 'Memória para agentes',
      h1b: 'do OpenAI Agents SDK.',
      sub: 'Em Python, NiadraAgentsMemory entrega run_config(), o filtro que injeta o contexto antes de cada chamada ao modelo, hooks, que registram os turnos, e tools. Em JavaScript, NiadraSession, niadraInstructions(), niadraTools() e niadraRunHooks(). A Session do SDK continua sua.',
      problem: [
        'A Session do Agents SDK guarda os itens deste agente: o que ele viu e produziu em cada run. É a memória de trabalho de um agente, chaveada por uma sessão da sua aplicação. O que a mesma pessoa disse ao agente de voz de outro fornecedor, ou o que o agente de cobrança fez no ERP, não está em sessão nenhuma.',
        'A Niadra guarda memória derivada da empresa sobre o cliente, não uma cópia de cada item. O call_model_input_filter põe o contexto depois das instruções do agente e o turn_block depois da entrada, a cada chamada ao modelo; os hooks registram o que o cliente disse e o que o agente respondeu, com o uso informado. Um handoff entre agentes do run leva a mesma memória.',
      ],
      how: {
        context: 'Em Python, run_config() define call_model_input_filter, que roda logo antes de cada chamada ao modelo: o contexto vai depois das instruções do agente e o turn_block depois da entrada, como mensagem de sistema; um filtro que você já tinha roda primeiro. Em JavaScript, niadraInstructions(base, conversation) torna as instruções dinâmicas: o seu texto, depois as notas do agente e o contexto, depois o sufixo.',
        turns: 'Em Python, hooks (um RunHooks) registra as mensagens novas do cliente quando o modelo é chamado pela primeira vez para elas, e a resposta do agente com o uso informado; os turnos são chaveados pela posição, então o histórico que uma Session repete no próximo run nunca é registrado duas vezes. Em JavaScript, NiadraSession é uma Session para run(agent, input, { session }): guarda os itens na sessão que você passa e registra os dois lados.',
        tools: 'tools: as três do histórico como FunctionTool com os nomes, descrições e esquemas do kit, amarradas ao cliente. Em JavaScript, niadraTools(conversation).',
        verify: 'O que o seu app provou (um login, um OTP) vai a conversation.verify() antes do run.',
        handoff: 'Um handoff do SDK entre agentes registra handoff("agent"); dê a todo agente do run a mesma memória. Em JavaScript, niadraRunHooks(runner, conversation) registra os handoffs.',
      },
      limits: [
        'O adaptador em Python não implementa a Session do SDK: uma Session guarda os itens do próprio agente, e a Niadra guarda memória derivada, não uma cópia de cada item. Use qualquer Session ao lado dele. Em JavaScript, NiadraSession embrulha a sessão que você escolher.',
        'Nada aqui derruba um run: a Niadra lenta ou fora deixa as instruções só suas.',
        'Em Python, o extra openai-agents fixa versões incompatíveis com livekit, crewai e litellm; instale um por ambiente.',
        'Testado contra openai-agents 0.22.3 e @openai/agents 0.18.0 com o modelo substituído por um fake e a Niadra no emulador.',
      ],
      faq: [
        {
          q: 'Isso substitui a Session do SDK?',
          a: 'Não. A Session é a memória de trabalho deste agente, e continua sua, em memória ou no SQLite. A Niadra é a memória da empresa sobre o cliente, derivada dos eventos de todos os canais e agentes. As duas convivem no mesmo run.',
        },
        {
          q: 'Com vários agentes e handoffs, quem lê a memória?',
          a: 'Todos os agentes do run recebem a mesma memória: o filtro roda a cada chamada ao modelo, de qualquer agente, e o handoff entre eles é registrado como transbordo para o agente seguinte, com a conversa aberta.',
        },
        {
          q: 'O contexto entra em cache no provedor?',
          a: 'O contexto vai depois das instruções, que continuam o prefixo do prompt, e vem fixado por conversa, então o começo do prompt se repete chamada após chamada e o provedor reaproveita. O que mudou entra como delta, no fim.',
        },
      ],
    },

    langgraph: {
      name: 'LangGraph',
      title: 'Memória para agentes LangGraph: middleware do create_agent, sem nada no estado do grafo',
      description:
        'Como dar memória de cliente a um agente LangGraph: NiadraMiddleware embrulha cada chamada ao modelo do create_agent, põe o contexto na mensagem de sistema, registra os turnos e traz as ferramentas do histórico, sem escrever nada no estado que o checkpointer guarda. Código do SDK e limites.',
      h1a: 'Memória para agentes',
      h1b: 'LangGraph.',
      sub: 'NiadraMiddleware embrulha cada chamada ao modelo do agente criado por langchain.agents.create_agent: o contexto entra na mensagem de sistema, os turnos são registrados e as ferramentas do histórico vêm junto. Nada é escrito no estado do grafo: o checkpointer nunca guarda um contexto.',
      problem: [
        'O checkpointer do LangGraph persiste o estado de um thread: as mensagens deste grafo, para este thread. Uma store de memória de longo prazo guarda o que este agente decidiu lembrar, num namespace da sua aplicação. Nenhum dos dois sabe o que a cliente disse ao agente de voz de outro fornecedor nem o que o agente de cobrança fez no ERP.',
        'A Niadra fica fora do grafo. O middleware acrescenta o contexto da cliente à mensagem de sistema, com as suas instruções primeiro, e o turn_block depois das mensagens, a cada chamada ao modelo. O estado continua só seu, e o checkpointer continua pequeno.',
      ],
      how: {
        context: 'O contexto é acrescentado à mensagem de sistema, o que deixa as suas instruções primeiro, e o turn_block vai depois das mensagens. Nada é escrito no estado do grafo. Para o create_react_agent mais antigo, pre_model_hook=pre_model_hook(conversation) entrega ao modelo as mesmas mensagens por llm_input_messages, também sem tocar no estado.',
        turns: 'As mensagens do cliente são registradas antes da chamada, chaveadas pela posição na conversa, então o histórico que o checkpointer repete nunca é registrado duas vezes; a resposta do modelo, com o usage_metadata, depois dela.',
        tools: 'O middleware traz as ferramentas do histórico, e as da memória do agente, como tools próprias, então create_agent as oferece sem você listar.',
        verify: 'conversation.verify() antes de invocar o agente.',
        handoff: 'transferred_to_agent() e transferred_to_human() registram a transferência; chame onde o grafo passa a conversa adiante.',
      },
      limits: [
        'Sem BaseStore próprio: a Niadra não é o armazenamento de estado do grafo.',
        'Sem nó próprio para o LangGraph.js: withNiadraContext() de @niadra/sdk/langchain já dá ao nó do modelo o contexto sem escrevê-lo no estado que o checkpointer guarda.',
        'Nada aqui derruba o agente: com a Niadra lenta ou fora, a chamada ao modelo segue sem o contexto.',
        'Testado contra langgraph 1.2 e langchain 1.4 com um chat model falso e a Niadra no emulador.',
      ],
      faq: [
        {
          q: 'Posso continuar usando o checkpointer e a store que já tenho?',
          a: 'Pode, e deve. O middleware não toca no estado do grafo nem na store: o contexto entra na chamada ao modelo e sai dela. O que a Niadra guarda é a memória da empresa sobre o cliente, derivada dos eventos de todos os canais.',
        },
        {
          q: 'E no LangGraph.js?',
          a: 'withNiadraContext() de @niadra/sdk/langchain dá ao nó do modelo o contexto logo antes da chamada, sem escrever no estado; o NiadraCallbackHandler registra as respostas. O exemplo completo está na página do LangChain.',
        },
        {
          q: 'Cada execução do agente pode virar um registro de turno?',
          a: 'Pode, com a funcionalidade de registros de turno ligada no espaço: before_agent abre o turno, wrap_tool_call grava cada chamada de ferramenta e after_agent o fecha com as chamadas de modelo e os tokens. Num replay no seu CI, as ferramentas do grafo respondem do registro.',
        },
      ],
    },

    langchain: {
      name: 'LangChain',
      title: 'Memória para LangChain e LangGraph.js: um runnable de contexto, um callback e as ferramentas do histórico',
      description:
        'Como dar memória de cliente a uma cadeia LangChain em Python e a um grafo LangGraph.js: context_runnable() põe o contexto nas mensagens do prompt, NiadraCallbackHandler registra os turnos e history_tools() entrega o kit como StructuredTool; em JavaScript, niadraContext(), withNiadraContext() e niadraTools(). Código do SDK e limites.',
      h1a: 'Memória para',
      h1b: 'LangChain e LangGraph.js.',
      sub: 'Em Python, com langchain-core só: context_runnable() coloca o contexto nas mensagens do prompt, NiadraCallbackHandler registra os turnos e history_tools() entrega o kit como StructuredTool. Em JavaScript, @niadra/sdk/langchain traz niadraContext() para cadeias LCEL, withNiadraContext() para nós de LangGraph.js, o callback handler e niadraTools().',
      problem: [
        'A memória de conversa do LangChain é o histórico desta cadeia, numa sessão da sua aplicação. Ela não cruza fornecedor, não cruza canal e não sabe o que um sistema de registro mudou. Cada agente da empresa fica com a própria versão do cliente.',
        'A Niadra entra como um runnable no meio da cadeia: as mensagens do prompt entram, e saem com o contexto da cliente como SystemMessage logo depois das mensagens de sistema e o turn_block no fim. O callback registra o que o cliente disse e o que o modelo respondeu, com o usage_metadata.',
      ],
      how: {
        context: 'Em Python, context_runnable() recebe as mensagens do prompt, uma lista ou um PromptValue, e devolve com o contexto como SystemMessage logo depois das mensagens de sistema iniciais e o turn_block como SystemMessage no fim; with_context() e awith_context() fazem o mesmo numa lista sua. Em JavaScript, niadraContext(session) é um runnable para cadeias LCEL (niadraContext(convo).pipe(model)); withNiadraContext(session, messages) faz o mesmo dentro de um nó do LangGraph.js, logo antes da chamada ao modelo, então nada entra no estado do grafo.',
        turns: 'NiadraCallbackHandler registra as mensagens do cliente quando um chat model começa, chaveadas pela posição, e a resposta do modelo com o usage_metadata quando termina. Em JavaScript, registra as respostas com o usage_metadata que o LangChain padroniza; respostas que só chamam ferramentas não registram nada.',
        tools: 'history_tools(): StructuredTool com os nomes, descrições e esquemas do kit, amarradas ao cliente. Em JavaScript, niadraTools(session).',
        verify: 'conversation.verify() antes da chamada.',
        handoff: 'conversation.handoff() onde a cadeia transfere.',
      },
      limits: [
        'Sem BaseChatMessageHistory nem BaseStore próprios: a Niadra não é o armazenamento de estado da cadeia nem do grafo, e o checkpointer nunca guarda um contexto.',
        'Nada aqui derruba a cadeia: com a Niadra lenta ou fora, as mensagens vão ao modelo como vieram.',
        'Testado contra langchain-core 1.6 e @langchain/core 1.2 com um chat model falso e a Niadra no emulador.',
      ],
      faq: [
        {
          q: 'Preciso de langchain inteiro ou só do core?',
          a: 'Em Python, só de langchain-core: o runnable, o callback e as ferramentas usam as interfaces do core. O exemplo usa langchain-openai para o modelo, mas qualquer chat model serve.',
        },
        {
          q: 'A memória de conversa do LangChain continua funcionando?',
          a: 'Continua, e é sua. O histórico da cadeia é o que o modelo viu nesta sessão; a Niadra é o que a empresa sabe da pessoa, vindo de todos os canais e sistemas. O callback registra os turnos uma vez, pela posição, então o histórico repetido na próxima chamada não entra duas vezes.',
        },
        {
          q: 'E numa cadeia que chama ferramentas várias vezes?',
          a: 'Em JavaScript, respostas que só chamam ferramentas não registram nada; o turno do agente é a resposta com texto. As ferramentas do histórico rodam pelo ToolNode, como qualquer outra, amarradas ao cliente que o seu código abriu.',
        },
      ],
    },

    crewai: {
      name: 'CrewAI',
      title: 'Memória para crews do CrewAI: contexto nos inputs do kickoff e as ferramentas do histórico',
      description:
        'Como dar memória de cliente a uma crew do CrewAI: before_kickoff põe o contexto nos inputs como niadra_context, after_kickoff registra a resposta final, e tools entrega o histórico com o esquema do kit. A memória da própria crew continua do CrewAI. Código do SDK e limites.',
      h1a: 'Memória para crews',
      h1b: 'do CrewAI.',
      sub: 'NiadraCrew entrega before_kickoff, que põe o contexto nos inputs da crew como niadra_context, after_kickoff, que registra a resposta final, e tools. A memória de curto prazo, de longo prazo e de entidades da crew continua do CrewAI; a Niadra é a memória da empresa sobre o cliente, compartilhada com todos os outros agentes.',
      problem: [
        'A memória do CrewAI é da crew: o que os agentes dela viram e aprenderam, nos armazenamentos que ela mesma mantém. Ela serve à crew. Não serve ao agente de voz de outro fornecedor que vai atender a mesma cliente amanhã, nem ao agente de cobrança que age no ERP, e não recebe o que esses fizeram.',
        'Com a Niadra, a crew recebe no kickoff o contexto que a empresa tem da cliente, pelo input niadra_context, que a descrição da tarefa ou o backstory do agente usa depois do seu texto. A resposta final da crew volta para a memória, e qualquer agente, de qualquer fornecedor, lê o que ela decidiu.',
      ],
      how: {
        context: 'before_kickoff acrescenta niadra_context aos inputs da crew: as notas do agente (com agent_memory=), o contexto e o turn_block. Ponha {niadra_context} na descrição de uma tarefa ou no backstory de um agente, depois do seu texto.',
        turns: 'O input em message (ou message_key=) é o turno do cliente, registrado antes de a crew começar; a resposta final da crew é o turno do agente, registrado quando ela termina.',
        tools: 'tools: ferramentas do CrewAI cujo esquema de argumentos é o do kit, amarradas ao cliente.',
        verify: 'conversation.verify() antes do kickoff.',
        handoff: 'transferred_to_agent() e transferred_to_human() registram a transferência.',
      },
      limits: [
        'A memória da própria crew (curta, longa, de entidades) continua do CrewAI; a Niadra é a memória da empresa sobre o cliente, compartilhada com todos os outros agentes.',
        'Só Python 3.11 a 3.13: o armazenamento vetorial do CrewAI depende do onnxruntime, que não tem wheels para o 3.10. Os extras crewai, pipecat e openai-agents fixam versões incompatíveis de uma dependência comum: instale um por ambiente.',
        'Só a resposta final da crew é registrada como turno do agente, não cada passo.',
        'Testado contra crewai 1.15 com o modelo substituído por um fake e a Niadra no emulador.',
      ],
      faq: [
        {
          q: 'Devo desligar a memória do CrewAI?',
          a: 'Não precisa. A memória da crew guarda o que os agentes dela aprenderam sobre as tarefas; a Niadra guarda o que a empresa sabe de cada cliente, de todos os canais. Uma complementa a outra, e a crew continua rodando como hoje.',
        },
        {
          q: 'Como o agente da crew acha o contexto?',
          a: 'Pelo input niadra_context, que o before_kickoff acrescenta. A descrição da tarefa diz {niadra_context} depois do seu texto, como qualquer input interpolado do CrewAI. As ferramentas do histórico vão em tools, para o agente consultar o que o contexto não responde.',
        },
        {
          q: 'Cada passo da crew vira um turno?',
          a: 'Não. Só a resposta final da crew é registrada como turno do agente, e o input em message como turno do cliente. É o que outros agentes precisam saber: o que a cliente pediu e o que a crew decidiu.',
        },
      ],
    },

    aiSdk: {
      name: 'Vercel AI SDK',
      title: 'Memória para o Vercel AI SDK: um middleware para wrapLanguageModel, com qualquer provedor',
      description:
        'Como dar memória de cliente a uma rota de chat com o Vercel AI SDK: niadraMiddleware é um middleware de modelo (LanguageModelMiddleware) para wrapLanguageModel, que funciona com qualquer provedor; transformParams põe o contexto, wrapGenerate e wrapStream registram a resposta, e niadraTools entrega o histórico como ferramentas do AI SDK. Código e limites.',
      h1a: 'Memória para',
      h1b: 'o Vercel AI SDK.',
      sub: 'niadraMiddleware(session) é um middleware de modelo (LanguageModelMiddleware) para wrapLanguageModel({ model, middleware }): a via idiomática do AI SDK, que funciona com todo provedor sem pacote de provedor separado. niadraTools(session) devolve o kit do histórico como ferramentas do AI SDK. Só em TypeScript.',
      problem: [
        'O AI SDK entrega as mensagens da interface ao modelo e devolve o stream. A memória, se existe, é o que a sua rota carrega do banco para esta sessão de chat. O que a mesma pessoa disse no WhatsApp a outro fornecedor, ou o que o agente de pedidos fez no ERP, não chega à rota.',
        'O middleware entra no wrapLanguageModel, uma vez, para qualquer provedor: transformParams põe o contexto da cliente como mensagem de sistema logo depois das suas e o sufixo, com os deltas e as falas de outros canais, no fim da última mensagem do usuário, onde todo provedor aceita. wrapGenerate e wrapStream registram o texto do modelo como turno do agente, com o uso informado.',
      ],
      how: {
        context: 'transformParams põe o contexto como mensagem de sistema logo depois das suas mensagens de sistema e o sufixo (deltas e falas de outros canais) como parte de texto no fim da última mensagem do usuário, onde todo provedor aceita.',
        turns: 'transformParams registra a mensagem mais nova do cliente; wrapGenerate e wrapStream registram o texto do modelo como turno do agente, com o uso que o provedor informou: tokens do prompt, leituras e escritas de cache. Passos que só chamam ferramentas não registram nada.',
        tools: 'niadraTools(session): o kit como ferramentas do AI SDK, amarradas ao cliente; junte às suas com spread.',
        verify: 'niadraMiddleware(session, { verify: { method, level } }) registra o que o seu app provou antes da primeira leitura, um login, por exemplo.',
        handoff: 'conv.handoff() onde a sua rota transfere.',
      },
      limits: [
        'Nada aqui derruba a chamada ao modelo: um contexto que não chega fica de fora, e uma falha ao registrar vai para o log, sem conteúdo.',
        'O middleware não embrulha o provedor: qualquer LanguageModel do AI SDK, de qualquer provedor, funciona.',
        'Testado contra ai 7, com os tipos das versões 5 e 6, e um modelo falso, com a Niadra no emulador, em Node, Deno, Bun, workerd e no Edge Runtime.',
      ],
      faq: [
        {
          q: 'Funciona no Edge Runtime e nos Workers?',
          a: 'Funciona: o SDK de TypeScript roda em Node, Deno, Bun, workerd e no Edge Runtime, e o middleware é testado nos cinco. A chave da Niadra fica na rota do servidor, nunca no navegador.',
        },
        {
          q: 'Quem decide qual é o cliente?',
          a: 'O seu código, a partir da sessão autenticada: handles.appUserId(userId) abre a conversa. O cliente nunca é algo que o modelo ou o navegador escolhe. O login prova o nível que a sua política define, e o middleware o registra antes da primeira leitura.',
        },
        {
          q: 'Posso misturar as ferramentas do histórico com as minhas?',
          a: 'Pode: niadraTools(session) devolve ferramentas do AI SDK, e você as junta às suas com spread no mesmo objeto tools. O stopWhen e o resto da chamada continuam como estão.',
        },
      ],
    },

    n8n: {
      name: 'n8n',
      title: 'Memória para o AI Agent do n8n: um nó de comunidade, antes e depois do agente',
      description:
        'Como dar memória de cliente a um AI Agent do n8n sem código: o nó de comunidade n8n-nodes-niadra lê o contexto do cliente antes do agente, registra os turnos depois, busca no histórico, registra verificação, transbordo e fim da conversa. Falha aberta por padrão. Instalação, operações e limites.',
      h1a: 'Memória para o AI Agent',
      h1b: 'do n8n.',
      sub: 'n8n-nodes-niadra é um nó de comunidade do n8n. Ponha-o antes e depois do seu nó AI Agent, ou entregue-o ao agente como ferramenta, para o agente começar cada conversa sabendo o que o cliente já contou aos outros agentes, e para o que ele diz chegar aos outros também. Sem código.',
      install: 'No n8n, Settings, Community Nodes, Install, e digite n8n-nodes-niadra. O pacote mora em packages/n8n-nodes-niadra do repositório do SDK de TypeScript e é publicado no npm junto do @niadra/sdk. A credencial Niadra API é uma chave de fonte do seu espaço (nia_sk_...): ela nomeia a região e o espaço, então o endereço vem dela; Base URL só para o emulador local.',
      problem: [
        'A memória do AI Agent do n8n é a janela de mensagens desta execução, ou um armazenamento por sessão da sua escolha. Ela é do fluxo. O agente de voz de outro fornecedor, o agente de WhatsApp de outro fluxo e o agente que age no ERP não leem o que esse fluxo sabe, e ele não lê o que eles fizeram.',
        'O nó lê o contexto do cliente antes do agente, pelo identificador que o seu gatilho traz (o wa_id do WhatsApp, o número de quem liga, o seu id de usuário), e registra o que o cliente e o agente disseram depois. Toda operação falha aberta por padrão: com a Niadra lenta ou fora, o nó devolve um resultado vazio com um campo error e o fluxo segue.',
      ],
      how: {
        context: 'Get Context devolve o contexto do cliente para o prompt do agente: text, para depois da sua mensagem de sistema, e suffix, com as falas de outros canais, para o fim do prompt.',
        turns: 'Track Turn registra o que o cliente ou o agente disse, chaveado pelo id da mensagem no provedor, então um passo repetido é registrado uma vez. End registra que a conversa terminou.',
        tools: 'Search History busca em conversas passadas, promessas, ações e objetos de negócio; When recebe o período nas palavras do cliente ("semana passada", "last week"). O nó também pode ser entregue ao AI Agent como ferramenta.',
        verify: 'Verify registra que o cliente provou quem é: um OTP, um login, o atestado da operadora.',
        handoff: 'Handoff registra a transferência para uma pessoa ou para outro agente.',
      },
      limits: [
        'Toda operação falha aberta por padrão: com a Niadra lenta ou fora, o nó devolve um resultado vazio com um campo error e o fluxo segue. Desligue Fail Open para parar o fluxo.',
        'Amarre Customer Handle aos dados do seu gatilho (o wa_id do WhatsApp, o número de quem liga, o seu id de usuário), nunca a um valor que o modelo escreva: o cliente nunca é algo que o modelo escolhe.',
        'O nó não traz a memória do agente nem a linha do tempo; para isso, use o SDK ou o servidor MCP.',
        'Construído e testado contra n8n-workflow 2.16. O desenho do nó e da credencial segue o nó do n8n da Mem0 (MIT), com atribuição em NOTICE.',
      ],
      faq: [
        {
          q: 'Como instalo?',
          a: 'No n8n, Settings, Community Nodes, Install, e digite n8n-nodes-niadra. A credencial é uma chave de fonte do seu espaço (nia_sk_...): ela nomeia a região e o espaço, então o endereço vem dela. Base URL só para o emulador local.',
        },
        {
          q: 'Onde o nó entra no fluxo?',
          a: 'Get Context antes do AI Agent, com o Customer Handle vindo do gatilho; Track Turn depois, com a mensagem do cliente e a resposta do agente; End quando a conversa termina. Search History pode ir como ferramenta do agente.',
        },
        {
          q: 'E se a Niadra não responder no meio do fluxo?',
          a: 'O nó devolve um resultado vazio com um campo error e o fluxo segue: o agente responde sem o contexto, como responde hoje. Se preferir que o fluxo pare, desligue Fail Open no nó.',
        },
      ],
    },
}

export const integrationsPt = {
  title: 'Integrações: memória omnichannel para os agentes que você já tem',
  description:
    'A memória da Niadra nos agentes de voz do LiveKit, Pipecat, Vapi, Retell e ElevenLabs, nos canais da Twilio e do WhatsApp, nos frameworks OpenAI Agents SDK, LangGraph, LangChain, CrewAI e Vercel AI SDK e no n8n: o contexto antes de cada resposta, os turnos registrados e o histórico como ferramenta, com o código real do SDK.',
  h1a: 'Memória para os agentes',
  h1b: 'que você já tem.',
  sub: 'Ligue os agentes que você já tem, sem trocar modelo, prompt ou fornecedor. Cada adaptador entrega o contexto antes da resposta, registra os turnos e abre o histórico como ferramenta. Nenhum derruba o agente quando a Niadra está lenta. Abaixo, uma página por plataforma, com o código do SDK.',
  groups: { voice: 'Plataformas de voz', channels: 'Canais', frameworks: 'Frameworks de agente', automation: 'Automação' } satisfies Record<IntegrationGroup, string>,
  allDocs: 'As 36 integrações, na documentação',
  allDocsNote: 'Agno, Mastra, Google ADK, Strands, Pydantic AI, LlamaIndex, Semantic Kernel, Bedrock, Anthropic, Google GenAI, LiteLLM, Dify, Flowise, Langflow e as outras estão na documentação, com o mesmo desenho.',
  crumb: 'Integrações',
  cardCta: 'Ver como se liga',

  // Section headings and labels shared by every page.
  tag: 'Integração',
  docsCta: 'Documentação do adaptador',
  installH: 'Instalar',
  codeH: 'O exemplo mínimo, como está na documentação',
  codeNote: 'O mesmo código está em {files} nos repositórios dos SDKs, onde roda na CI contra os tipos reais do framework e o emulador da Niadra. Para testar sem a nuvem da Niadra, niadra-mock e NIADRA_BASE_URL=http://127.0.0.1:8765.',
  copy: 'Copiar',
  copied: 'Copiado',
  langs: { python: 'Python', typescript: 'TypeScript' },
  howH: 'Como o adaptador se liga',
  howSub: 'As cinco primitivas de toda integração da Niadra, nos pontos de extensão deste framework.',
  prim: { context: 'Contexto', turns: 'Turnos', tools: 'Ferramentas', verify: 'Verificação', handoff: 'Transbordo' },
  receivesH: 'O que o agente recebe',
  receivesSub: 'O contexto é montado quando a memória muda e servido pronto, sem IA na leitura. O que outro canal disse durante a conversa chega no fim do prompt, como novidade.',
  receives: [
    'Quem é o cliente, pelo que a conversa já provou: o nível de verificação decide o que entra',
    'Fatos, pendências e promessas, com a data e o canal de origem',
    'O que outros agentes fizeram por dentro, confirmado pelo sistema de registro',
    'Padrões calculados por regra, com as evidências e o prazo',
    'As três ferramentas do histórico: buscar, linha do tempo e abrir um item, amarradas ao cliente no seu código',
    'Comprovante de cada leitura, encadeado por SHA-256',
  ],
  receivesLink: 'Ver o contexto por dentro',
  limitsH: 'O que o adaptador não faz',
  faqH: 'Perguntas frequentes',
  othersH: 'Outras integrações',
  // The questions every buyer asks, the same answer on every page.
  sharedFaq: [
    {
      q: 'Preciso trocar de modelo, de prompt ou de fornecedor?',
      a: 'Não. O adaptador coloca o contexto depois das suas instruções e o delta no fim do prompt, nos pontos de extensão que o framework já tem. O seu modelo, o seu prompt e o seu fornecedor continuam os mesmos, e trocar qualquer um deles depois não apaga a memória.',
    },
    {
      q: 'Onde ficam os dados e quanto custa?',
      a: 'Os dados ficam numa região só, informada no contrato, cifrados com AES-256-GCM e chave exclusiva por empresa, protegida em HSM FIPS 140-3. O preço é por conversa ou tarefa em que um agente leu a memória: de US$ 2 a 3 a cada mil, conforme o volume, com leituras, buscas e eventos de sistema incluídos. A Niadra está abrindo para empresas por pedido, antes do lançamento.',
    },
  ] as readonly Faq[],

  pages,
}

export type IntegrationsDict = typeof integrationsPt
