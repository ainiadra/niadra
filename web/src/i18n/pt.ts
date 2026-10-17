/**
 * Copy em português. É a fonte do formato: `Dict = typeof pt`, então o inglês
 * só compila se tiver exatamente as mesmas chaves.
 *
 * GLOSSÁRIO (não misturar):
 *   memória   = tudo o que a Niadra guarda e organiza sobre o cliente
 *   contexto  = o que o agente recebe antes de agir (responder ou fazer a tarefa)
 *   histórico = tudo o que já aconteceu com o cliente, e que o agente pode consultar
 *
 * Regras: voz confiante, produto pronto. Sempre "agente de
 * IA", nunca "bot". A Niadra é internacional: sem país nem cidade. Nada que
 * diminua o produto ("só", "simples", "resumo") nem que plante dúvida (queda,
 * rascunho). Sem travessão, sem buzzword, CTA que diz o que acontece.
 */
import { countWord, packHotCount, packLineCount, packLines, packTokens } from './pack'

import { integrationsPt } from './integrations.pt'
export const pt = {
  meta: {
    otherLang: 'English',
    switchLang: 'Ver o site em inglês',
    skip: 'Pular para o conteúdo',
    example: 'exemplo',
    // Texto alternativo da logo de cada plataforma integrada.
    logoOf: 'Logo do {name}',
  },

  nav: {
    products: 'Produtos',
    resources: 'Recursos',
    pricing: 'Preço',
    blog: 'Blog',
    enterprise: 'Acesso antecipado',
    // Os produtos do menu saem do catálogo (src/products.ts) e de `products`.
    allProducts: 'Ver todos os produtos',
    // Leitor de tela, na lista de grupos do menu de produtos.
    groupsLabel: 'Grupos de produtos',
    resourceItems: [
      { title: 'Arquitetura', desc: 'Como a Niadra funciona por dentro.', to: '/recursos#arquitetura' },
      { title: 'Segurança', desc: 'As seis camadas que protegem os dados.', to: '/recursos#seguranca' },
      { title: 'Privacidade', desc: 'LGPD, GDPR e os direitos do titular.', to: '/recursos#privacidade' },
      { title: 'Documentação', desc: 'Guias, referência da API e exemplos.', to: 'https://docs.niadra.com' },
      {
        title: 'Integrações',
        desc: 'LiveKit, Vapi, Twilio, WhatsApp, LangGraph, n8n e as outras, com o código.',
        to: '/integracoes',
      },
      {
        title: 'Benchmark',
        desc: 'A Niadra e os sistemas de memória, medidos nas mesmas condições. Script e resultados públicos.',
        to: '/benchmark',
      },
      {
        title: 'Comparativo',
        desc: 'O que a Niadra faz e o que cada sistema de memória documenta, com a fonte e a data.',
        to: '/comparativo',
      },
    ],
    cta: 'Pedir acesso antecipado',
    login: 'Login',
    github: 'A Niadra no GitHub: SDKs e benchmark (abre github.com)',
    // Leitor de tela, junto da seta dos links que saem do site: "(abre docs.niadra.com)".
    opens: 'abre',
    menu: 'Menu',
    close: 'Fechar',
  },

  // A ordem casa com os ícones em Shell.tsx (FACT_ICONS).
  facts: [
    'Qualquer canal, plataforma ou sistema',
    'Qualquer agente de IA, de qualquer fornecedor',
    'AES-256-GCM · TLS 1.3 · HSM FIPS 140-3',
    'Totalmente gerenciada: do seu lado, só o SDK',
  ],

  // A página da empresa: a visão do todo. Cada produto tem a página dele (src/products.ts).
  home: {
    title: 'Niadra: memória omnichannel compartilhada para todos os seus agentes de IA',
    description:
      'A memória omnichannel dos agentes de IA da sua empresa. Tudo o que um cliente diz ou faz, em qualquer canal ou sistema, vira uma memória só, e qualquer agente, de qualquer fornecedor, começa sabendo. Troque o fornecedor sem perder a memória. Totalmente gerenciada.',
    // A categoria em uma frase, para o llms.txt e os dados estruturados.
    category: 'A camada de memória omnichannel para os agentes de IA da empresa.',
    h1a: 'Memória omnichannel compartilhada',
    h1b: 'para todos os seus agentes de IA.',
    sub: 'Tudo o que um cliente diz ou faz, em qualquer canal ou sistema, vira uma memória só. Cada agente de IA, de qualquer fornecedor, começa sabendo o que os outros já viram e fizeram.',
    primary: 'Pedir acesso antecipado',
    secondary: 'Ver o que muda para o seu time',
    // The problem in the buyer's words, before any mechanism: the reader recognizes the scene first.
    problemH2: 'Hoje, cada agente de IA sabe só o que passou por ele.',
    problemSub: 'A cliente conta a história no WhatsApp, repete na ligação, e ninguém sabe que o crédito já foi lançado. O dado existe. Está espalhado em cada fornecedor e em cada sistema.',
    problems: [
      {
        t: 'Cada fornecedor guarda a própria memória',
        d: 'O agente de voz não vê o que o de WhatsApp ouviu. Nenhum fornecedor abre a memória dele para o concorrente.',
      },
      {
        t: 'Cada sistema guarda o próprio pedaço',
        d: 'O CRM, o ERP e o help desk sabem partes do mesmo cliente, e o agente não lê nenhuma delas na hora.',
      },
      {
        t: 'Trocar de fornecedor apaga tudo',
        d: 'A memória fica presa onde foi criada. O agente novo começa do zero, e o cliente paga a conta.',
      },
    ],
    problemClose: 'Trocar o modelo de IA não resolve. O que falta é a memória.',

    // O diagrama logo abaixo do herói: as fontes que mandam eventos, a memória que a Niadra monta e quem a lê.
    // Nomes reais de integrações, tirados de docs.niadra.com/integrations (36 adaptadores em 27/09/2026).
    flowH2: 'Muitas fontes, uma memória, todos os agentes',
    // Abre pela situação de quem compra (o cenário da análise de 28/09), depois o mecanismo.
    flowSub:
      'Cada canal, plataforma e sistema manda o que sabe, no formato que já tem. A Niadra descobre de quem cada evento fala e monta uma memória por cliente. Qualquer agente lê essa memória antes de agir.',
    flow: {
      inLabel: 'O que entra',
      groups: [
        { t: 'Canais de conversa', items: ['Voz', 'WhatsApp', 'E-mail', 'App e chat'] },
        { t: 'Plataformas de voz e de canal', items: ['LiveKit', 'Pipecat', 'Twilio', 'Vapi', 'Retell', 'ElevenLabs', 'WhatsApp Cloud API'] },
        { t: 'Frameworks de agente e automação', items: ['OpenAI Agents SDK', 'LangGraph', 'CrewAI', 'Agno', 'Vercel AI SDK', 'n8n', 'Dify', 'e outros 22, na documentação'] },
        { t: 'Sistemas de registro', items: ['CRM', 'ERP', 'Help desk', 'Cobrança', 'Pedidos'] },
        { t: 'Pessoas e agentes', items: ['Atendente humano', 'Agente interno', 'Agente de outro fornecedor'] },
      ],
      eventLabel: 'Cada um vira um evento: o que foi dito, o que um sistema registrou, o que um agente fez.',
      // A identidade, visível: os identificadores que os eventos trazem viram uma pessoa só. Valores sintéticos e
      // mascarados, sem documento de país nenhum; a cliente é a Marina do palco.
      idLabel: 'Quem cada evento identifica',
      ids: ['WhatsApp +•• ••• ••• 0142', 'sessão de voz 9f2c', 'marina@•••.com', 'login do app 88213', 'CRM contato 827', 'ERP cliente 4410', 'cobrança 7731'],
      idOne: 'Marina Souza, uma cliente só',
      idNote: 'A união vem de um evento, de um sistema ou de uma prova, nunca de nome parecido. E pode ser desfeita.',
      memLabel: 'Uma memória por cliente',
      memItems: [
        'Fatos, pendências e promessas, com data e origem',
        'Ações de agente, confirmadas pelo sistema de registro',
        'Padrões calculados por regra, com evidência',
        'Comprovante de cada leitura, encadeado',
      ],
      outLabel: 'Quem lê',
      readers: [
        { n: 'Agente de voz', v: 'fornecedor B' },
        { n: 'Agente de WhatsApp', v: 'fornecedor A' },
        { n: 'Agente do app', v: 'seu time' },
        { n: 'Agente de cobrança', v: 'interno · ERP' },
        { n: 'Atendente humano', v: 'central de atendimento' },
        { n: 'Qualquer LLM', v: 'SDK, API HTTP ou MCP' },
      ],
      readNote: 'Cada um recebe só o que a tarefa dele pede, e cada leitura fica registrada.',
    },


    solH2: 'O que muda para cada time',
    solSub:
      'A mesma memória serve a quem atende, a quem opera os sistemas, a quem escolhe os fornecedores de IA e a quem responde pela segurança.',
    // Os produtos de cada frente, pelo slug do catálogo.
    solutions: [
      {
        team: 'Atendimento',
        h: 'O cliente não repete a história em canal nenhum.',
        d: 'O agente de voz atende sabendo o que o de WhatsApp ouviu há dois minutos e o que a cobrança já resolveu. O atendente humano recebe o caso inteiro.',
        products: ['memoria', 'contexto', 'estado', 'historico'],
      },
      {
        team: 'Operações',
        h: 'O que um agente faz por dentro, todos ficam sabendo.',
        d: 'O crédito que o agente de cobrança lança no ERP fecha a pendência aberta no WhatsApp. Pedidos, tickets e faturas chegam ligados ao cliente certo.',
        products: ['sistemas', 'identidade', 'coordenacao', 'avisos'],
      },
      {
        team: 'Gestão dos agentes',
        h: 'Você sabe qual agente aproveita o que recebe.',
        d: 'A Niadra mede, por agente e por fornecedor, o que foi usado, o que foi perguntado de novo e o que foi dito contra a memória. É esse número que decide qual fornecedor fica.',
        products: ['medicao', 'conferencia', 'registro', 'padroes'],
      },
      {
        team: 'Segurança e compliance',
        h: 'Quem leu o quê, para quê, com comprovante.',
        d: 'Cada leitura fica registrada. O acesso é liberado por finalidade, o corte de um fornecedor vale na hora e o apagamento de um cliente sai com comprovante.',
        products: ['console'],
      },
    ],

    howH2: 'Um motor de memória por trás de cada agente',
    howSub:
      'A Niadra trabalha na hora em que as coisas acontecem: uma conversa termina, um sistema muda, um agente age. Ela reconhece o cliente, tira da conversa os fatos, as pendências e as promessas, e deixa o contexto pronto para o próximo agente.',
    arch: {
      agentsLabel: 'Seus agentes, de qualquer fornecedor: os que atendem e os que trabalham por dentro',
      agents: [
        { n: 'Agente de WhatsApp', v: 'fornecedor A' },
        { n: 'Agente de voz', v: 'fornecedor B' },
        { n: 'Agente do app', v: 'seu time' },
        { n: 'Atendente humano', v: 'central de atendimento' },
        { n: 'Agente de cobrança', v: 'interno · ERP' },
        { n: 'Agente de pedidos', v: 'interno · fornecedor C' },
      ],
      systemsLabel: 'Seus sistemas',
      systems: ['CRM', 'ERP', 'Help desk', 'Cobrança', 'Pedidos'],
      systemsText:
        'Os avisos que eles já emitem entram por webhook ou API, ligados ao cliente certo. Eles continuam sendo a fonte oficial.',
      // A ordem casa com as setas em Diagrams.tsx: sobe, vai e volta, desce.
      calls: [
        { call: 'context()', text: 'Antes de agir, o agente recebe o contexto do cliente para a tarefa dele.' },
        { call: 'search()', text: 'Durante o trabalho, o agente consulta o histórico inteiro.' },
        { call: 'track()', text: 'Depois, a conversa ou a ação entra na memória.' },
      ],
      // A ordem casa com os ícones em Diagrams.tsx (PART_ICONS).
      parts: [
        {
          n: 'Registro completo',
          d: 'Cada conversa, evento e ação, guardados como chegaram e nunca reescritos.',
        },
        {
          n: 'Identidade unificada',
          d: 'Telefone, e-mail, documento, login e o ID de cada sistema viram um só cliente.',
        },
        {
          n: 'Memória estruturada',
          d: 'Fatos, pendências, promessas, ações e padrões, cada um com data, origem e evidência.',
        },
        { n: 'Contexto sob medida', d: 'Para cada agente e cada tarefa, o contexto certo, no tamanho certo.' },
        {
          n: 'Busca no histórico',
          d: 'O agente percorre tudo o que já aconteceu com o cliente, em qualquer canal.',
        },
        { n: 'Governança', d: 'Quem lê o quê, para quê e quando, com registro de cada leitura.' },
      ],
      async:
        'Dado pessoal é mascarado antes de qualquer leitura. Quando uma conversa termina, a IA da Niadra lê a conversa uma vez e tira dela os fatos, as pendências e as promessas. Essa leitura já está no preço por conversa. Evento de sistema entra sem IA, assim que chega, e a memória do cliente fica atualizada para todos os agentes ao mesmo tempo.',
      base: 'Totalmente gerenciada pela Niadra. Do seu lado, só o SDK.',
    },

    prodH2: 'Uma memória, do SDK ao Console',
    // The one mention of "a memória que opera" on the home: an addition to the memory, never a second category.
    prodSub:
      'Treze produtos sobre a mesma memória, na ordem em que o dado anda: o que entra, o que a memória sabe, o que cada agente recebe e o que o seu time controla.',

    fitH2: 'A peça que faltava entre tudo o que você já tem',
    fitSub: 'Seus sistemas, seus agentes e suas ferramentas de dados continuam onde estão. A Niadra liga todos à mesma memória.',
    fitThem: 'O que já faz',
    fitUs: 'O que a Niadra acrescenta',
    fitRows: [
      {
        who: 'Seu CRM',
        them: 'Guarda o histórico para uma pessoa consultar depois.',
        us: 'Entrega o contexto ao agente de IA durante a conversa. O desfecho volta por webhook, e o CRM registra.',
      },
      {
        who: 'ERP, help desk e cobrança',
        them: 'Registram pedidos, tickets e faturas, cada um no próprio sistema.',
        us: 'Liga cada registro ao cliente certo e entrega ao agente o que importa para a tarefa.',
      },
      {
        who: 'A plataforma de agentes do seu fornecedor',
        them: 'Lembra só do que aconteceu dentro dela, e só para os agentes dela.',
        us: 'Fica acima de todos os fornecedores. Todos leem e alimentam a mesma memória, e você vê qual deles usa o contexto.',
      },
      {
        who: 'CDP e data lake',
        them: 'Juntam dados para análise, em ciclos de horas ou dias.',
        us: 'Entrega o contexto pronto ao agente durante a conversa, na hora.',
      },
    ],

    // A frase da neutralidade no maior tipo da página (análise de 28/09): a memória é da empresa, não do fornecedor.
    neutralH2: 'Troque o fornecedor do agente sem perder a memória.',
    neutralSub:
      'A Niadra não é agente, não é canal e não concorre com os seus fornecedores. Cada um lê e alimenta a mesma memória, e a memória é da empresa. Quando um fornecedor sai, o próximo agente começa sabendo tudo.',
    neutralItems: [
      {
        big: 'Qualquer modelo de IA.',
        small: 'O contexto chega pronto para o prompt, com qualquer LLM, de qualquer provedor.',
      },
      {
        big: 'Qualquer fornecedor ou plataforma.',
        small: 'Adaptadores prontos para 36 plataformas de voz, frameworks de agente, canais e automação, mais API e MCP.',
      },
      {
        big: 'Qualquer canal ou sistema.',
        small: 'Onde o cliente fala com você, e onde a sua empresa trabalha por dentro.',
      },
      { big: 'Os dados são seus.', small: 'Você exporta tudo, em formato aberto, quando quiser.' },
    ],

    // Velocidade, custo e integração são prova de que a camada foi bem feita, não o motivo da compra.
    perfH2: 'Os números da camada',
    perfSub:
      'Velocidade e custo vêm do jeito que a memória é montada: pronta quando o evento chega, servida sem IA no caminho. Numa ligação, é isso que decide se o contexto chega antes do alô.',
    perf: [
      {
        n: '0',
        unit: '',
        label: 'Chamadas de IA e consultas ao banco numa leitura de contexto já pronto. Numa ligação, nada roda entre o toque e o alô.',
      },
      {
        n: '8',
        unit: 's',
        label: 'Janela de espera depois que uma conversa termina, para ler de uma vez tudo o que o mesmo cliente disse. A leitura já está no preço.',
      },
      { n: '3', unit: '', label: 'Chamadas no seu código. Nada a reescrever no agente que você já tem.' },
      {
        n: '36',
        unit: '',
        label: 'Integrações prontas, em Python e TypeScript: voz, frameworks de agente, canais e automação.',
      },
    ],
    perfNote:
      'O contexto vai em camadas, do que menos muda para o que mais muda. O provedor de IA reaproveita o começo do prompt e cobra uma fração por ele. O Console mostra a economia estimada, por agente e por modelo.',
    // O benchmark é da Niadra; a resposta a isso é a reprodução, nunca a palavra "independente".
    perfLink: 'Ver o benchmark, com script e resultados públicos para reproduzir',

    secH2: 'Blindada em seis camadas',
    secSub:
      'Cada controle tem nome e evidência, do jeito que a revisão de segurança de banco, seguradora e operadora de saúde pede.',
    // A ordem casa com os ícones em Home.tsx (SEC_ICONS).
    secItems: [
      {
        t: 'Criptografia AES-256-GCM e TLS 1.3',
        d: 'Cifrado em repouso e em trânsito, com uma chave exclusiva por empresa. A chave de uma empresa jamais abre o dado de outra.',
      },
      {
        t: 'Chaves em HSM FIPS 140-3',
        d: 'A chave-mestra vive em hardware com validação FIPS 140-3 nível 3 e nunca sai de lá.',
      },
      {
        t: 'Acesso negado por padrão',
        d: 'Cada agente só lê o que a finalidade dele permite. O agente de cobrança nunca vê dado de saúde. MFA e SSO (SAML 2.0, OIDC) no Console.',
      },
      {
        t: 'Auditoria à prova de adulteração',
        d: 'Cada leitura entra num registro encadeado por SHA-256: quem leu, o quê, para quê e quando. Sai em segundos para o seu SIEM.',
      },
      {
        t: 'LGPD e GDPR por desenho',
        d: 'A Niadra atua como operadora, com DPA assinado, direitos do titular por API e apagamento com comprovante.',
      },
      {
        t: 'Infraestrutura certificada',
        d: 'Nuvem com certificação ISO 27001, SOC 2 e PCI DSS. Os dados ficam numa região só, informada no contrato.',
      },
    ],
    secBadges: ['AES-256-GCM', 'TLS 1.3', 'HSM FIPS 140-3', 'SHA-256', 'SAML 2.0 · OIDC', 'LGPD', 'GDPR', 'Nuvem ISO 27001 · SOC 2 · PCI DSS'],
    secNever: [
      'Seus dados nunca treinam modelos de IA.',
      'Seus dados nunca são vendidos. Só leem os agentes que você autorizar.',
      'Dado pessoal é mascarado antes de a IA da Niadra ler a conversa.',
    ],
    secLink: 'Ver as seis camadas de segurança',

    faqH2: 'Perguntas que todo time faz',
    faq: [
      {
        q: 'Isso não é mais uma memória para agentes de IA?',
        a: 'Uma memória para agente dá memória a um agente. A Niadra dá à empresa uma memória só de cada cliente, montada com os eventos de todo canal e sistema, que qualquer agente lê. E quando você troca o fornecedor do agente, a memória fica.',
      },
      {
        q: 'Com quais plataformas e frameworks a Niadra funciona?',
        a: 'Com as que você já usa. Há 36 adaptadores prontos, entre eles LiveKit, Pipecat, ElevenLabs, Vapi, Retell, Twilio, WhatsApp, OpenAI Agents SDK, LangGraph, LangChain, CrewAI, Agno e n8n. O resto usa o SDK, a API ou o servidor MCP.',
      },
      {
        q: 'Meu fornecedor de agentes de IA já não faz isso?',
        a: 'Faz, mas só dentro dele. O agente do WhatsApp não enxerga a ligação atendida por outro fornecedor, e nenhum fornecedor abre a própria memória para o concorrente. Por isso a memória precisa ficar acima de todos.',
      },
      {
        q: 'Isso não é o meu CRM?',
        a: 'Não. O CRM guarda o histórico para uma pessoa consultar depois. A Niadra entrega o contexto ao agente de IA durante a conversa, já pronto. O desfecho volta por webhook, e o seu CRM registra.',
      },
      {
        q: 'Serve para agentes internos, que não falam com o cliente?',
        a: 'Serve. O agente de cobrança que lança um crédito e o de pedidos que muda uma entrega usam as mesmas três chamadas. O próximo agente, interno ou de atendimento, já começa sabendo.',
      },
      {
        q: 'Como os meus sistemas entram na memória?',
        a: 'Por webhook, API ou lote de arquivos. CRM, ERP, help desk, cobrança e pedidos mandam os avisos que já emitem, e a Niadra liga cada um ao cliente certo. Os sistemas continuam sendo a fonte oficial.',
      },
      {
        q: 'Como eu sei que os meus agentes estão usando a memória?',
        a: 'A Niadra mede. Ela entregou o contexto e recebe a conversa que veio depois. Por agente e por fornecedor, você vê o que foi usado, o que foi perguntado de novo e o que foi dito contra a memória.',
      },
      {
        q: 'A Niadra executa ações ou fluxos?',
        a: 'Não. A Niadra avisa. Quando uma regra sua acontece na memória, ela manda um aviso assinado por webhook para o seu sistema ou para o seu agente. Quem age é ele.',
      },
    ],
  },

  stage: {
    tabsLabel: 'Comparar o atendimento',
    tabWithout: 'Sem Niadra',
    tabWith: 'Com Niadra',
    customer: 'Marina Souza',
    initials: 'MS',
    wa: { channel: 'WhatsApp', vendor: 'fornecedor A', time: '14h02', msg: 'O técnico não apareceu. Vou ligar aí.' },
    app: { channel: 'App', vendor: 'seu time', time: '14h05', event: 'Contestação da fatura de agosto', state: 'aberta' },
    sys: { channel: 'ERP', vendor: 'agente interno', time: '14h06', event: 'Crédito de R$ 40 na fatura de agosto', state: 'lançado' },
    switchLabel: 'a cliente liga',
    call: { channel: 'Telefone', vendor: 'fornecedor B', time: '14h07', agent: 'Agente de IA de voz' },
    withoutMsg: 'Boa tarde! Com quem eu falo? Me conta o que aconteceu.',
    withoutStatus: 'não sabe da visita, do crédito nem do histórico',
    withoutFoot: 'A cliente repete tudo, e ninguém sabe que o crédito já foi lançado.',
    withMsg: [
      { t: 'Oi, Marina. Vi que ', m: false },
      { t: 'o técnico não apareceu hoje cedo', m: true },
      { t: ', e que ', m: false },
      { t: 'isso já tinha acontecido em março', m: true },
      { t: '. Já remarquei a visita com prioridade, e ', m: false },
      { t: 'o crédito da fatura de agosto já foi lançado', m: true },
      { t: '.', m: false },
    ],
    withStatus: 'recebeu o contexto montado de quatro fontes, antes de atender',
    withFoot: 'Quatro fontes, uma memória, nenhuma pergunta repetida.',
    memTitle: 'O que a Niadra montou',
    memSub: 'Marina · prefere WhatsApp · histórico desde março de 2026',
    openLabel: 'Em aberto',
    open: [{ t: 'Visita técnica não aconteceu hoje cedo', src: 'WhatsApp 14h02' }],
    doneLabel: 'Feito por outro agente',
    done: [{ t: 'Crédito de R$ 40 lançado na fatura de agosto, confirmado pelo ERP', src: 'Cobrança · ERP 14h06' }],
    histLabel: 'Do histórico',
    hist: [{ t: 'Mesma falha em março, resolvida com crédito na fatura', src: 'Telefone 12/03' }],
    delivered: 'Contexto entregue',
    deliveredMeta: 'Ao agente de voz, antes do alô.',
    siloTitle: 'Cada um sabe um pedaço',
    silos: [
      { who: 'Fornecedor A · WhatsApp', has: 'sabe da reclamação do técnico' },
      { who: 'Seu time · app', has: 'sabe da contestação da fatura' },
      { who: 'Agente de cobrança · ERP', has: 'lançou um crédito que ninguém mais vê' },
      { who: 'Fornecedor B · telefone', has: 'não sabe de nada' },
    ],
    siloNote: 'Ninguém junta as partes. Quem paga é a cliente, que repete tudo.',
  },

  // Os artefatos do exemplo da Marina (22/09, 14h02 a 14h07), que aparecem em mais de uma página.
  demo: {
    ctx: {
      badge: 'Contexto entregue ao agente de voz',
      meta: `${packTokens('pt')} tokens`,
      source: 'O texto exato que a Niadra entrega ao agente de voz às 14h07, gerado para uma cliente de exemplo num espaço novo.',
      layersLabel:
        'O contexto vai do que menos muda para o que mais muda, e o provedor de IA reaproveita o começo. O Console mostra a economia estimada.',
      layers: ['Regras da empresa', 'Perfil', 'Pendências', 'Agora há pouco'],
      lines: packLines('pt'),
    },
    code: {
      title: 'Três chamadas no agente que você já tem',
      copy: 'Copiar',
      copied: 'Copiado',
      before: 'antes de começar: o atestado da operadora e o contexto do cliente',
      during: 'durante: o agente consulta o histórico',
      after: 'depois: o que foi dito ou feito entra na memória',
      langs: ['Python', 'TypeScript', 'API HTTP', 'Servidor MCP'],
    },
    hist: {
      title: 'Busca no histórico',
      saysLabel: 'A cliente diz',
      says: '“Da outra vez vocês me deram um crédito.”',
      askLabel: 'O agente consulta',
      query: 'niadra.search(\n  subject=caller,\n  query="crédito por visita técnica perdida",\n)',
      answerLabel: 'A Niadra responde',
      rows: [
        { when: '12/03/2026', channel: 'Telefone', t: 'Técnico não compareceu. Crédito de R$ 40 aplicado na fatura de abril.', state: 'resolvido' },
        { when: '14/03/2026', channel: 'WhatsApp', t: 'A cliente confirmou o crédito e a nova visita.', state: 'resolvido' },
        { when: '03/11/2025', channel: 'App', t: 'Visita remarcada a pedido da cliente.', state: 'sem crédito' },
      ],
      pattern: 'Mesma falha em 12/03, resolvida com crédito de R$ 40 na fatura.',
    },
    calls: {
      title: 'As três chamadas',
      cols: ['Chamada', 'Quando', 'O que faz'],
      rows: [
        { call: 'context()', when: 'Antes de começar', does: 'Entrega o contexto do cliente para a tarefa do agente, pronto para o prompt.' },
        { call: 'search()', when: 'Durante o trabalho', does: 'Busca no histórico inteiro do cliente, o que foi dito e o que foi feito. Também vira ferramenta para o LLM chamar sozinho.' },
        { call: 'track()', when: 'Depois', does: 'Registra a conversa ou o que o agente fez num sistema, sem atrasar o agente.' },
      ],
    },
    mcp: {
      title: 'Oito ferramentas MCP',
      note: 'As mesmas funções do SDK, para o agente que fala MCP. Com a memória dos agentes ligada, entram mais duas, para as notas de trabalho.',
      tools: [
        'get_customer_context',
        'track_event',
        'record_action',
        'resolve_identity',
        'search_customer_history',
        'get_customer_timeline',
        'open_history_item',
        'correct_customer_memory',
      ],
    },
  },

  // A página /produtos (o índice) e a página de cada produto, na ordem do catálogo (src/products.ts).
  products: {
    title: 'Produtos da Niadra: do evento ao contexto, com governança',
    description:
      'Os treze produtos da memória omnichannel da Niadra, cada um com a sua página: sistemas, memória, identidade, padrões, estado, contexto, histórico, avisos, conferência, coordenação, medição, registro e Console.',
    h1a: 'Conectar, lembrar, entregar e governar.',
    h1b: 'Tudo numa memória só.',
    sub: 'Treze produtos sobre a mesma memória, na ordem em que o dado anda: o que entra, o que a memória sabe, o que cada agente recebe e o que o seu time controla. Cada um tem a própria página.',
    groups: {
      connect: { label: 'Conectar', note: 'O que entra na memória' },
      remember: { label: 'Lembrar', note: 'O que a memória sabe' },
      deliver: { label: 'Entregar', note: 'O que cada agente recebe' },
      govern: { label: 'Governar', note: 'O que o seu time controla' },
    },
    // O SDK não é produto: é como qualquer agente se liga, e fica na documentação.
    sdkNote: {
      t: 'SDK, API e MCP',
      d: 'Qualquer agente se liga à memória com três chamadas, em Python, TypeScript, HTTP ou MCP.',
      cta: 'Ver a documentação',
    },
    docsCta: 'Ler na documentação',
    // Título da seção do artefato, só para leitor de tela: o artefato já tem título visível.
    how: 'Como funciona',
    moreH2: 'Uma memória, treze produtos',
    here: 'Você está aqui',
    // The pages of the memory that operates link to more than one docs page.
    docsH: 'Na documentação',
    // The code sample of each of those pages: the same code as the SDK's examples/ folder.
    codeLangs: ['Python', 'TypeScript'],

    systems: {
      name: 'Sistemas',
      menu: 'Pedidos, tickets e faturas entram na memória pelos avisos que os seus sistemas já emitem.',
      title: 'Sistemas: eventos do CRM, do ERP e do help desk na memória dos agentes de IA | Niadra',
      description:
        'Pedidos, faturas e tickets entram na memória pelos avisos que os seus sistemas já emitem, ligados ao cliente certo, sem IA no caminho.',
      line: 'O que os seus sistemas já avisam vira memória, sem IA no caminho.',
      body: 'Quando um pedido muda, uma fatura é contestada ou um ticket reabre, o seu sistema já emite um aviso. A Niadra recebe esse aviso e liga o pedido, o ticket ou a fatura ao cliente certo. O valor oficial continua no sistema.',
      flowTitle: 'Do evento à memória',
      inLabel: 'Chega do ERP',
      inCode: `POST /v1/ingest/webhook/erp

{
  "event": "invoice.credited",
  "invoice": "0823",
  "customer_id": "48213",
  "amount": 40.00,
  "currency": "BRL",
  "at": "2026-09-22T17:06:21Z"
}`,
      outLabel: 'Entra na memória',
      out: [
        { k: 'Objeto', v: 'Fatura 0823, do ERP', note: 'creditada às 14h06, com a referência ao registro de origem' },
        { k: 'Cliente', v: 'Marina Souza', note: 'reconhecida pelo ID 48213 do cadastro' },
        { k: 'Ação', v: 'Crédito de R$ 40 lançado', note: 'Agente de cobrança · interno · 14h06. A ação e o evento do ERP viram um registro só.' },
        { k: 'Pendência', v: 'Contestação da fatura de agosto', note: 'fechada pela ação das 14h06' },
      ],
      flowFoot: 'Nenhuma IA lê o evento: o mapeamento transforma, e o conteúdo fica guardado como chegou.',
      points: [
        'Entra por webhook, pela API ou em lote, por arquivo',
        'Pelo SDK ou pela API, nada a configurar: os tipos dos objetos e as operações são inferidos do que chega',
        'Num webhook, o assistente de configuração propõe o mapeamento e testa em eventos reais. Uma pessoa aprova',
        'O sistema continua sendo a fonte oficial, e a Niadra nunca escreve nele',
      ],
    },

    identity: {
      name: 'Identidade',
      menu: 'Pessoas, empresas e parceiros, reconhecidos em qualquer canal.',
      title: 'Identidade: o mesmo cliente em todos os canais e sistemas | Niadra',
      description:
        'Telefone, e-mail, documento, login e o ID de cada sistema viram um cliente só. A união é reversível, e empresas e parceiros também são reconhecidos.',
      line: 'O mesmo cliente, em qualquer canal e sistema.',
      body: 'Telefone, e-mail, documento, login do app e o ID de cada sistema viram um cliente só. A Niadra só une o que tem prova: um evento, o seu sistema ou um login. Nome parecido nunca une, e qualquer união pode ser desfeita. Empresas e parceiros também são reconhecidos.',
      idTitle: 'Cada identificador tem um peso',
      idHandles: [
        { h: '+•• •• ••••• 0000', level: 'pista', note: 'o número pode ter trocado de dono' },
        { h: 'Documento •••••6789', level: 'declarado', note: 'a pessoa informou na ligação' },
        { h: 'marina@•••.com', level: 'verificado', note: 'confirmou por código enviado ao e-mail' },
        { h: 'Login do app 88213', level: 'autenticado', note: 'entrou com senha' },
        { h: 'Cliente 48213 no CRM', level: 'do sistema', note: 'veio do cadastro da empresa' },
      ],
      idNote:
        'Na API e no Console, o peso aparece como nível de verificação. Se duas pessoas forem unidas por engano, a Niadra separa as duas sem perder nada.',
      linksTitle: 'Pessoas ligadas a organizações',
      links: [
        { who: 'Ana Ribeiro', role: 'compradora', org: 'Uma rede de supermercados', kind: 'conta', when: 'desde 03/2025', from: 'do CRM' },
        { who: 'Luís Matos', role: 'motorista', org: 'Uma transportadora', kind: 'parceiro', when: 'desde 01/2026', from: 'do sistema de transporte' },
        { who: 'Luís Matos', role: 'motorista', org: 'Outra transportadora', kind: 'parceiro', when: 'até 12/2025', from: 'informado numa ligação' },
      ],
      linksNote: 'Empresas nunca se unem por semelhança de nome nem por domínio de e-mail: só por uma afirmação do seu sistema ou de uma pessoa do seu time.',
      points: [
        'Cinco níveis de verificação: o contexto muda conforme o quanto a conversa já provou quem é a pessoa',
        'Número de telefone é pista, não prova: ele pode ter trocado de dono',
        'O ID de cada sistema vira identificador, com o nome do sistema junto',
        'Uma pessoa pode estar ligada a várias organizações, cada vínculo com papel e validade',
      ],
    },

    memory: {
      name: 'Memória',
      menu: 'O que foi dito e feito, com data, origem e evidência.',
      title: 'Memória: todos os seus agentes de IA, uma memória só | Niadra',
      description:
        'A memória que todos os agentes de IA da sua empresa compartilham: reconhece o cliente onde ele aparecer, entrega o contexto antes da primeira palavra e abre o histórico inteiro para consulta.',
      // A página do produto principal: o que era a home, com o herói e o contexto sem repetir a mesma ideia.
      h1a: 'Todos os seus agentes de IA,',
      h1b: 'uma memória só.',
      sub: 'Hoje cada agente de IA sabe só o que passou por ele. A Niadra é a memória que todos eles compartilham: reconhece o cliente onde ele aparecer, entrega o contexto antes da primeira palavra e abre o histórico inteiro. O cliente conta a história uma vez só.',
      secondary: 'Ver o que o agente recebe',
      ctxH2: 'Antes da primeira palavra, o agente já sabe tudo',
      ctxSub:
        `${packTokens('pt')} tokens, entregues antes de o agente de voz dizer alô. Das ${countWord('pt', packLineCount('pt'))} linhas sobre a cliente, ${countWord('pt', packHotCount('pt'))} vieram de fora da ligação: a mesma falha numa ligação de março, a conversa do WhatsApp às 14h02, o crédito lançado pelo agente de cobrança às 14h06 e a pendência que ficou. O grifo marca cada uma: é o que o agente não teria como saber sozinho.`,
      histH2: 'E quando a conversa pede mais, o agente pergunta',
      histSub:
        'O contexto cobre o que importa agora. Para o resto, o agente pergunta ao histórico inteiro do cliente, nas palavras dele, com qualquer LLM. Cada resposta volta com data, origem e desfecho.',
      rulesH2: 'O que foi dito e o que foi feito, com data, origem e evidência',
      sdkH2: 'Qualquer agente se liga em três chamadas',
      sdkSub:
        'O SDK não controla o seu agente, não escolhe o seu modelo e não mexe no seu prompt. Ele liga o agente que você já tem à memória: contexto antes de começar, busca durante o trabalho e registro do que foi dito e feito.',
      sdkFacts: [
        'Python e TypeScript, e API HTTP para qualquer outra linguagem',
        'Código aberto (Apache 2.0): você audita tudo o que sai da sua rede',
        'O contexto e a busca têm tempo máximo; o registro sai em segundo plano',
        'Adaptadores prontos para 36 plataformas: LiveKit, Pipecat, ElevenLabs, Vapi, Retell, Twilio, WhatsApp, OpenAI Agents, LangChain, LangGraph, CrewAI, Agno, Vercel AI SDK, n8n e as outras, na documentação',
      ],
      sdkCta: 'Começar pelo início rápido',
      faqH2: 'Perguntas sobre a memória',
      faq: [
        {
          q: 'O agente consegue consultar todo o histórico do cliente?',
          a: 'Consegue. Além do contexto entregue antes da resposta, o agente busca no histórico inteiro, com qualquer LLM, pelo SDK, pela API ou por MCP. Cada resultado respeita as permissões daquele agente.',
        },
        {
          q: 'Como a Niadra sabe que é a mesma pessoa?',
          a: 'Ela cruza os identificadores de cada canal: telefone, e-mail, documento, login do app. Um número de telefone é uma pista, um login com senha é uma prova. Uma união errada é desfeita sem perder o histórico de ninguém.',
        },
        {
          q: 'Funciona com o modelo de IA que eu já uso?',
          a: 'Funciona. O contexto chega pronto para o prompt, e a busca no histórico usa o formato de ferramenta que todo LLM entende. Não importa o provedor, nem se o modelo é hospedado por você.',
        },
        {
          q: 'Isso aumenta o meu gasto com IA?',
          a: 'Tende a diminuir. O agente recebe um contexto compacto em vez do histórico inteiro a cada mensagem. A leitura que a IA da Niadra faz de cada conversa já está no preço. O Console mostra a economia estimada no seu modelo.',
        },
        {
          q: 'Os agentes também têm memória própria, do trabalho deles?',
          a: 'Têm. Cada agente guarda as próprias notas de trabalho: procedimentos, como as ferramentas se comportam, armadilhas conhecidas. Elas nunca levam dado pessoal, e o seu time lê e revisa tudo no Console.',
        },
      ],
      line: 'O que foi dito e o que foi feito, com data, origem e evidência.',
      body: 'Quando uma conversa termina, a IA da Niadra lê a conversa uma vez, com o dado pessoal já mascarado, e tira dela os fatos, as pendências, as promessas e o desfecho. Essa leitura já está no preço por conversa. Cada item aponta para a mensagem de onde veio, e item sem origem é descartado. Quando algo muda, o item antigo deixa de valer e o novo entra, cada um com a sua data.',
      recTitle: 'A memória da Marina, às 14h07',
      items: [
        { kind: 'Fato', text: 'A visita técnica prometida para hoje de manhã não aconteceu.', src: 'WhatsApp · fornecedor A · 14h02', ev: 'Evidência: a mensagem da cliente às 14h02', status: '', statusText: '' },
        { kind: 'Pendência', text: 'Remarcar a visita técnica.', src: 'WhatsApp · fornecedor A · 14h02', ev: 'Prazo: 23/09', status: 'open', statusText: 'aberta' },
        { kind: 'Promessa', text: 'Responder à contestação da fatura de agosto até amanhã.', src: 'App · seu time · 14h05', ev: 'Cumprida às 14h06 pela ação abaixo', status: 'done', statusText: 'cumprida' },
        { kind: 'Ação', text: 'Crédito de R$ 40 lançado na fatura de agosto.', src: 'Agente de cobrança · interno · ERP · 14h06', ev: 'Registrada pelo agente e confirmada pelo evento do ERP: um registro só', status: 'done', statusText: 'feita' },
      ],
      points: [
        'Nada entra sem evidência: cada item aponta para a mensagem, o evento ou a ação de onde veio',
        'Quando um fato muda, o antigo deixa de valer e continua no histórico, com a data',
        'A pendência aberta numa conversa fecha com a ação registrada em outro sistema',
        'Cada agente extrai o que a tarefa dele pede: o de cobrança guarda o motivo da contestação; o de pedidos, a mudança de entrega',
        'Trecho de voz mal transcrito não vira fato',
      ],
    },

    patterns: {
      name: 'Padrões',
      menu: 'O que cada cliente repete, calculado do histórico e entregue a todo agente.',
      title: 'Padrões: o que cada cliente repete, com a prova de cada sinal | Niadra',
      description: 'A Niadra calcula, a partir do histórico, o que se repete com cada cliente, e entrega a todo agente com as provas e o prazo de validade.',
      line: 'O que cada cliente repete, todo agente fica sabendo.',
      body: 'A terceira reclamação sobre visita técnica em 90 dias. Duas faturas em atraso. Um cliente que prefere WhatsApp. A Niadra calcula cada padrão a partir do histórico, por uma regra que o seu time liga no Console, e entrega ao agente com as provas e um prazo de validade.',
      card: {
        title: 'Padrão encontrado',
        name: 'Terceira reclamação sobre visita técnica em 90 dias',
        ruleLabel: 'Regra',
        rule: 'Reclamação recorrente, versão 3: três reclamações do mesmo tema em 90 dias',
        evLabel: 'Evidências',
        ev: [
          { when: '04/08/2026', channel: 'App', t: 'A visita técnica atrasou três horas.' },
          { when: '29/08/2026', channel: 'WhatsApp', t: 'O técnico veio sem a peça. Visita remarcada.' },
          { when: '22/09/2026', channel: 'Telefone', t: 'O técnico não apareceu.' },
        ],
        validLabel: 'Vale até',
        valid: '02/11/2026',
        reachLabel: 'Chega a',
        reach: [
          { who: 'Atendimento', ok: true, note: 'recebe' },
          { who: 'Cobrança', ok: false, note: 'não recebe: a finalidade não permite' },
        ],
      },
      points: [
        'Oito regras no catálogo: reclamação recorrente, promessa não cumprida, pediu cancelamento, canal preferido, não atende ligação, foi transferido, satisfação em queda e faturas em atraso',
        'O seu time liga cada regra no Console e ajusta os números. Antes de ligar, vê quantos clientes ela marcaria',
        'Regra escrita, nunca palpite: cada padrão aponta para os registros que o sustentam',
        'Vence no prazo e sai do contexto sozinho. Um padrão errado sai com um clique',
        'Categoria sensível nunca vira padrão: saúde, religião, política, origem, vida sexual, sindicato, biometria e genética',
      ],
    },

    // The memory that operates, part 1: the state of now. The history says what happened; the state says what holds now.
    state: {
      name: 'Estado',
      menu: 'O que vale agora para cada cliente, com a data e a fonte de cada valor.',
      title: 'Estado: o que vale agora para cada cliente, com data, fonte e o que se pode afirmar | Niadra',
      description:
        'Um pedido, uma fatura, uma proposta, uma cotação: o que vale agora para cada cliente, cada valor com a data em que foi atualizado, a fonte e o que o agente pode afirmar com ele.',
      line: 'O que vale agora para cada cliente, com data e fonte.',
      body: 'O histórico diz o que aconteceu; o estado diz o que vale agora. Um pedido, uma fatura, uma proposta, uma cotação: cada valor chega ao agente com a data em que foi atualizado, a fonte e o que ele pode afirmar com esse dado. Um valor antigo não é escondido: o agente sabe que é antigo e não afirma com ele. O valor oficial continua no seu sistema.',
      read: {
        title: 'Leitura de estado',
        purpose: 'finalidade: afirmar',
        cols: ['Campo', 'Valor', 'Atualizado · fonte', 'Pode afirmar?'],
        objects: [
          {
            t: 'Fatura 0823',
            src: 'tipo fatura · espelha o ERP',
            rows: [
              { field: 'estado', value: 'creditada', logic: 'yes', age: 'há 1 min · ERP, ao vivo', claim: true, note: 'fresco' },
              { field: 'crédito', value: 'R$ 40,00', logic: 'yes', age: 'há 1 min · ERP, ao vivo', claim: true, note: 'fresco' },
              { field: 'boleto novo emitido', value: 'não', logic: 'no', age: 'há 1 min · ERP, ao vivo', claim: true, note: 'a fonte afirmou que não há' },
            ],
          },
          {
            t: 'Visita técnica 5521',
            src: 'tipo visita_tecnica · espelha o sistema de campo',
            rows: [
              { field: 'data', value: '23/09, de manhã', logic: 'yes', age: 'há 2 h · sistema de campo', claim: false, note: 'vencido para afirmar: releia antes de dizer' },
              { field: 'técnico confirmado', value: 'não observado', logic: 'unobserved', age: 'ninguém observou', claim: false, note: '“não conferido” não vira “não”' },
              { field: 'peça em estoque', value: 'defeito conhecido', logic: 'defect', age: 'há 10 min · estoque', claim: false, note: 'o tipo declara que esse campo dessa fonte vem errado' },
            ],
          },
        ],
        logic: { yes: 'sim', no: 'não', unobserved: 'não observado', defect: 'defeito da fonte' },
        prohibitionsLabel: 'O que o agente não pode afirmar com esse dado',
        prohibitions: ['a data da visita', 'se o técnico está confirmado', 'se a peça está em estoque'],
        foot: 'Sem IA na leitura. O estado vai junto com o contexto e passa pelo mesmo nível de verificação.',
      },
      code: {
        title: 'Antes de dizer um valor, o agente pergunta se pode',
        note: 'Trecho de examples/object_state.py do SDK: o agente só diz um valor quando pode afirmá-lo agora.',
      },
      points: [
        'Os tipos vêm do que os seus sistemas já mandam: a Niadra os infere sem configuração, e o que você declarar vale por cima. Quando o esquema muda, a Niadra avisa',
        'Quatro respostas: sim, não, não observado e defeito conhecido da fonte. “Não conferido” nunca vira “não”',
        'Validade por campo: um preço vale por segundos, um cadastro por dias',
        'Uma cotação vence na hora em que um dado dela muda',
        'Um campo sensível nunca entra no texto do contexto: o agente que precisa dele lê pela finalidade',
      ],
      docs: [
        { t: 'Tipos de objeto e estado', to: '/concepts/object-types' },
        { t: 'Derivar tipos do seu banco', to: '/guides/derive-types' },
        { t: 'O worker de resolução', to: '/guides/resolver-worker' },
        { t: 'Ler objetos tipados, na referência da API', to: '/api/state-read' },
      ],
    },

    context: {
      name: 'Contexto',
      menu: 'O que cada agente precisa saber antes de começar.',
      title: 'Contexto: o que cada agente de IA precisa saber antes de começar | Niadra',
      description:
        'O que cada agente de IA precisa saber desse cliente antes de responder: quem é, o que está em aberto, o que aconteceu em outro canal e o que outro agente já fez. Pronto para o prompt.',
      line: 'O que cada agente precisa saber, antes de começar.',
      body: 'Antes de responder, o agente recebe o que precisa saber desse cliente: quem é, o que está em aberto, o que acabou de acontecer em outro canal e o que outro agente já fez. Pronto para o prompt, sem IA no caminho.',
      points: [
        'Sem IA na leitura, e sem consulta ao banco quando o contexto já está pronto',
        'Por agente e por tarefa: o agente de voz e o de cobrança recebem o que cada um pede',
        'Até a pessoa confirmar quem é, o dado sensível fica retido',
        'A mesma entrega para o mesmo pedido: nenhum modelo improvisa no caminho',
      ],
    },

    history: {
      name: 'Histórico',
      menu: 'Tudo o que já aconteceu, para o agente consultar.',
      title: 'Histórico: tudo o que aconteceu com o cliente, para o agente de IA consultar | Niadra',
      description:
        'O agente pergunta ao histórico inteiro do cliente, nas palavras dele, com qualquer LLM. Cada resposta volta com data, canal e desfecho, e respeita as permissões do agente.',
      // O agente pergunta qualquer coisa sobre o histórico daquele cliente, nas palavras dele; a lista é só de exemplos (pedido do dono, 01/10/2026).
      asksTitle: 'Exemplos do que o agente pode perguntar',
      asksNote: 'O agente pergunta o que quiser sobre o histórico daquele cliente, nas palavras dele. Alguns exemplos:',
      asks: [
        'Isso já aconteceu antes? Quantas vezes?',
        'O que foi prometido da última vez, e por quem?',
        'Como a última reclamação foi resolvida?',
        'Ela já pediu cancelamento alguma vez?',
        'Que proposta ela recebeu em março, e por que não fechou?',
        'Essa fatura já foi parcelada antes? Em quantas vezes?',
        'O que mudou nesse pedido desde a semana passada?',
        'Alguém já cuidou desse ticket? O que foi feito?',
        'Ela prefere falar por WhatsApp ou por telefone?',
      ],
      anyLlm:
        'Funciona com qualquer LLM, pelo SDK, pela API ou como ferramenta MCP, e respeita as permissões do agente.',
      line: 'Tudo o que já aconteceu com o cliente, para o agente consultar.',
      body: 'O contexto cobre o que importa agora. Quando a conversa pede mais, o agente pergunta ao histórico inteiro do cliente, nas palavras dele: busca, folheia a linha do tempo e abre uma conversa específica. Cada resposta volta com data, canal e desfecho.',
      points: [
        'Três operações: buscar, folhear a linha do tempo e abrir uma conversa',
        '“Quantas vezes isso aconteceu” é uma contagem, não um palpite',
        'Mesmas permissões e mesmo nível de verificação do contexto',
        'Uma mensagem maliciosa não troca de cliente: o identificador vai amarrado na ferramenta, fora do alcance do modelo',
        'Cada consulta deixa registro: quem perguntou, o quê e o que voltou',
      ],
    },

    alerts: {
      name: 'Avisos',
      menu: 'Um aviso assinado para o seu sistema na hora em que algo acontece.',
      title: 'Avisos: a Niadra chama o seu sistema na hora em que algo acontece | Niadra',
      description: 'Promessa vencida, padrão novo, satisfação em queda, fonte parada: quando acontece, a Niadra avisa o seu sistema por webhook assinado. Quem age é o seu agente ou o seu sistema.',
      line: 'Quando algo acontece com um cliente, a Niadra avisa o seu sistema. Quem age é ele.',
      body: 'Uma promessa vence, um padrão aparece, a satisfação de um cliente cai, uma fonte para de mandar eventos. A Niadra manda um aviso assinado por webhook para o endereço que o seu time escolheu, e o seu sistema decide o que fazer: abrir um chamado, acionar um agente, avisar uma pessoa. O aviso leva só identificadores. O conteúdo da conversa nunca vai junto.',
      hook: {
        ruleTitle: 'Regra',
        rule: [
          { k: 'Quando', v: 'Uma promessa da empresa vence há 2 dias sem ação que a cumpra' },
          { k: 'Quantas vezes', v: 'Uma vez por cliente a cada 24 horas' },
          { k: 'Para onde', v: 'O webhook do seu sistema de chamados' },
        ],
        sentTitle: 'O aviso que sai',
        request: `POST https://sua-empresa.com/webhooks/niadra
webhook-id: 0192f8d0-7e31-7a4b-9c5d-3e4f5a6b7c8d
webhook-timestamp: 1790175900
webhook-signature: v1,K5oZfzN9…/H/pI4=

{
  "id": "0192f8d0-7e31-7a4b-9c5d-3e4f5a6b7c8d",
  "type": "trigger.fired",
  "created_at": "2026-09-23T15:05:00+00:00",
  "tenant": "0192f0a0-1b2c-7d3e-8f40-5a6b7c8d9e01",
  "subject": { "pseudonym": "p_kvkv3okp7vnw3mqs", "kind": "person" },
  "data": {
    "rule_id": "overdue-visit",
    "rule_version": "4",
    "condition": "promise.overdue",
    "late": false,
    "as_of": "2026-09-23T15:04:58+00:00",
    "evidence": [{ "kind": "open_item", "id": "0192f7a2-8c41-7e3a-9b5d-2c4e6f8a0b13" }]
  },
  "links": { "open": "/v1/profiles/0192f7a0-3b2e-7d41-8c5a-2e9f4b7a1c03" }
}`,
        foot: 'A Niadra só avisa. Quem remarca a visita é o seu agente ou o seu sistema.',
      },
      points: [
        'Nove condições no catálogo, entre elas promessa vencida, padrão presente, queda de satisfação, recontato e fonte que parou de mandar eventos',
        'O seu time liga cada condição no Console e escolhe o endereço que recebe',
        'Antes de ligar, um teste mostra quantos clientes disparariam a regra hoje',
        'Uma vez por cliente por janela, assinado, com retentativas por até 24 horas',
        'O aviso nunca executa nada: não abre ticket, não manda mensagem e não mexe no seu sistema',
      ],
    },

    // The memory that operates, part 2: the check of what the agent is about to state. Named "Conferência" since 01/10/2026
    // ("Afirmação" said nothing to a buyer). Never "impede", never "sem alucinação".
    claims: {
      name: 'Conferência',
      menu: 'O agente só afirma preço, data e número que conferiu. O que não conferiu é sinalizado.',
      title: 'Conferência: o agente só afirma o que conferiu | Niadra',
      description:
        'Antes de a resposta sair, a Niadra confere cada preço, data e número que o agente vai dizer contra o que ele consultou. O que tem origem passa. O que não tem é sinalizado.',
      line: 'O agente só afirma o que conferiu. O resto é sinalizado.',
      body: 'Um preço que ninguém cotou. Um prazo três dias fora. Um desconto que não existe. Antes de a resposta sair, a Conferência olha cada número, data e valor que o agente vai dizer e pergunta de onde veio. O que tem origem passa. O que não tem é sinalizado ou trocado por uma ressalva, conforme a regra da sua empresa.',
      guard: {
        title: 'A resposta, conferida antes de sair',
        draftLabel: 'O que o agente escreveu',
        draft: 'Oi, Marina. O crédito de R$ 40 já foi lançado na fatura de agosto, e remarquei a visita para 23/09 de manhã. Como pedido de desculpas, a próxima fatura vem com 15% de desconto.',
        claimsLabel: 'Afirmações encontradas',
        claims: [
          { span: 'R$ 40', kind: 'valor · crédito', evidence: 'leitura de estado: fatura 0823, campo crédito, ERP há 1 min', verdict: 'confere', action: 'nenhuma', ok: true },
          { span: '23/09', kind: 'data · visita', evidence: 'ferramenta remarcar_visita, chamada neste turno', verdict: 'confere', action: 'nenhuma', ok: true },
          { span: '15%', kind: 'porcentagem · desconto', evidence: 'nenhuma: nem ferramenta nem leitura do turno traz um desconto', verdict: 'sem origem', action: 'a frase dá lugar à ressalva do contrato', ok: false },
        ],
        verdictLabel: 'veredito',
        actionLabel: 'ação',
        outLabel: 'O que sai para a cliente',
        out: 'Oi, Marina. O crédito de R$ 40 já foi lançado na fatura de agosto, e remarquei a visita para 23/09 de manhã. Sobre desconto, eu confirmo com o time e te retorno.',
        foot: 'Conferido dentro do processo do agente, sem IA a mais. Os vereditos vão ao registro do turno; o texto do seu prompt, nunca.',
      },
      code: {
        title: 'A conferência, no último ponto antes da cliente',
        note: 'Trecho de examples/claim_guard.py do SDK: a resposta passa pela conferência antes de virar a fala do agente.',
      },
      points: [
        'Vale como origem: uma leitura de estado, uma ferramenta chamada neste turno ou um trecho que cita a fonte',
        'Um valor antigo não sustenta um número: chega com a data e é relido antes',
        'Reescrita só quando não há dúvida, e nunca em dose, medida ou identificador',
        'Documento assinado nunca é reescrito: é conferido, e vai a uma pessoa quando não se sustenta',
        'Quanto cada agente afirma sem conferir aparece na Medição, por agente e por fornecedor',
      ],
      docs: [
        { t: 'Afirmações e o contrato de conferência', to: '/concepts/claims' },
        { t: 'Registros de turno, onde os vereditos ficam', to: '/concepts/turn-records' },
        { t: 'Agentes jurídicos: prazos com lacunas e citações ancoradas', to: '/guides/legal-agents' },
        { t: 'Agentes de varejo: preço, disponibilidade e promessa de ação', to: '/guides/retail-agents' },
      ],
    },

    // The memory that operates, part 3: who holds the customer, and what was already done. Niadra answers and records; the company sends and bars.
    coordination: {
      name: 'Coordenação',
      menu: 'Quem está com o cliente agora e o que já foi feito, antes de um agente agir.',
      title: 'Coordenação: quem está com o cliente agora, e um efeito só por fato de negócio | Niadra',
      description:
        'Antes de agir, o agente pergunta quem está com o cliente, o que já foi feito e se pode contatar agora. Cada despedida, lembrete ou oferta sai uma vez só, seja qual for o fornecedor.',
      line: 'Quem está com o cliente agora, e o que já foi feito.',
      body: 'Agentes de vários fornecedores, pessoas em painéis e rotinas automáticas falam com o mesmo cliente. Antes de agir, o agente pergunta à Niadra: quem está com ele agora, o que já foi feito, se pode contatar. A resposta é uma de quatro: pode agora, espere, não, ou passe para quem está com ele. Uma despedida, um lembrete, uma oferta: cada um sai uma vez só, seja qual for o agente que tente primeiro.',
      check: {
        title: 'Antes de agir, o agente pergunta',
        cols: ['Quem pergunta', 'O que quer fazer', 'Resposta'],
        rows: [
          { who: 'Agente de voz · fornecedor B', what: 'despedida · atendimento · chave farewell:call-5521', decision: 'allow', answer: 'Efeito reservado. Declare como terminou.' },
          { who: 'Agente de WhatsApp · fornecedor A', what: 'despedida · a mesma chave, 14h09', decision: 'deny', answer: 'Efeito já feito pelo agente de voz.' },
          { who: 'Agente de cobrança · interno', what: 'lembrete de fatura · cobrança · saída · WhatsApp', decision: 'defer', answer: 'A cliente está com o agente de voz até 14h22: posse observada no painel da central.' },
          { who: 'Agente de ofertas · fornecedor C', what: 'oferta · marketing · saída · WhatsApp', decision: 'deny', answer: 'A cliente pediu para não receber marketing neste canal. Lista de supressão.' },
        ],
        decisions: { allow: 'pode agora', defer: 'espere', deny: 'não', handoff: 'passe adiante' },
        foot: 'A Niadra responde e registra. Quem envia é o seu agente; quem barra é o seu gateway.',
      },
      code: {
        title: 'Uma despedida por conversa, seja qual for o agente que tente primeiro',
        note: 'Trecho de examples/coordination.py do SDK: a primeira pergunta reserva o efeito, e as seguintes ouvem que já foi feito.',
      },
      points: [
        'Posse por declaração e por observação: um painel que abriu uma sessão humana conta. O mais restritivo vence',
        'Uma mensagem que o cliente mandou nunca é negada',
        'Orçamento de contato por finalidade, somado sobre todos os fornecedores, e horário de silêncio respeitado',
        'A lista de quem pediu para não receber vale para todo fornecedor',
        'Mensagem ativa só sai com um token de uso único, que o seu gateway confere sem falar com a Niadra',
      ],
      docs: [
        { t: 'Coordenação', to: '/concepts/coordination' },
        { t: 'Gateways e o token de contato', to: '/guides/gateways' },
        { t: 'Perguntar antes de agir, na referência da API', to: '/api/coordination-check' },
        { t: 'Sinais e restrições: o que o cliente quer e recusa', to: '/concepts/signals' },
      ],
    },

    measure: {
      name: 'Medição',
      menu: 'Se cada agente usou o contexto que recebeu.',
      title: 'Medição: se cada agente de IA usou o contexto que recebeu | Niadra',
      description:
        'Por agente e por fornecedor: o que foi usado, o que foi perguntado de novo, o que foi dito contra a memória e quem transferiu sem ler. No Console, o detalhe de cada conversa mostra cada entrega, item a item.',
      line: 'Você vê se cada agente usou o contexto que recebeu.',
      body: 'A Niadra entregou o contexto e recebe a conversa que veio depois. Por isso ela mede, por agente e por fornecedor: o que foi usado, o que foi perguntado de novo, o que foi dito contra a memória e quem transferiu sem ler. Nenhum fornecedor de agente consegue dar esse número, porque nenhum segura os dois lados.',
      tableTitle: 'Aproveitamento do contexto, por agente',
      cols: ['Agente', 'Usou o contexto', 'Repetiu pergunta', 'Contradições'],
      rows: [
        { agent: 'Agente de voz', vendor: 'fornecedor B', used: '91%', repeated: '3%', contradicted: '0' },
        { agent: 'Agente de WhatsApp', vendor: 'fornecedor A', used: '74%', repeated: '18%', contradicted: '2' },
        { agent: 'Agente de cobrança', vendor: 'interno', used: '88%', repeated: '1%', contradicted: '0' },
      ],
      tableFoot: 'Ao lado de cada agente, o recontato em 72 horas. No Console, o detalhe de cada conversa mostra cada entrega, item a item.',
      signalsTitle: 'Os quatro sinais',
      notLabel: 'Não conta',
      signals: [
        { t: 'Aproveitamento', d: 'O agente citou ou usou o que recebeu: a visita perdida, o crédito lançado, a promessa anterior.', not: 'item que não servia para a tarefa. Ele conta como não usado, sem penalidade.' },
        { t: 'Repetição', d: 'O agente pediu um dado que já estava no contexto: nome, documento, número do pedido, o motivo do contato.', not: 'pergunta de verificação que a sua política exige.' },
        { t: 'Contradição', d: 'O agente disse algo que um fato entregue desmente, como “a visita está marcada para sexta” quando o fato entregue diz quinta.', not: 'divergência com o que não foi entregue. Isso é falha de seleção, medida à parte.' },
        { t: 'Transferência sem leitura', d: 'A conversa foi transferida para outro agente ou para uma pessoa, e o destino não pediu o contexto.', not: 'destino que ainda não está integrado. Isso aparece como falta de cobertura.' },
      ],
      points: [
        'Sem IA a mais e sem custo a mais: pronto junto com a memória da conversa',
        'O fornecedor A nunca vê os números do fornecedor B',
        'Mede o uso da memória, não a qualidade do atendimento: não dá nota e não bloqueia nada',
        'A meta dos 90 dias é o recontato, contra um grupo de controle. A medição mostra onde ele nasce',
      ],
    },

    console: {
      name: 'Console',
      menu: 'Quem leu o quê, revisão, exportação e apagamento.',
      title: 'Console: quem leu o quê, com comprovante | Niadra',
      description:
        'Onde o seu time governa a memória: quem leu o quê, simulação do que cada agente enxerga, corte de fornecedor, revisão por amostra, exportação e apagamento com comprovante.',
      enter: 'Entrar no Console',
      line: 'Alguém vai perguntar quem leu o quê. O Console responde na hora.',
      body: 'Toda leitura fica registrada: qual agente leu, de qual fornecedor, para quê e a que horas. Você corta o acesso de um fornecedor sem depender dele, simula o que cada agente enxerga antes de liberar e apaga os dados de um cliente de uma vez, com comprovante. O Console não é tela de atendimento.',
      auditTitle: 'Registro de leituras',
      auditCols: ['Hora', 'Quem leu', 'Para quê', 'Resultado'],
      auditRows: [
        { time: '14h07', who: 'Agente de voz · fornecedor B', why: 'atendimento', result: 'recebeu o contexto de voz', denied: false },
        { time: '14h06', who: 'Agente de cobrança · interno', why: 'cobrança', result: 'recebeu faturas e contestações da cliente', denied: false },
        { time: '14h05', who: 'Agente do app · seu time', why: 'atendimento', result: 'recebeu o contexto completo', denied: false },
        { time: '14h02', who: 'Agente de WhatsApp · fornecedor A', why: 'atendimento', result: 'recebeu o contexto de chat', denied: false },
        { time: '09h31', who: 'Agente de cobrança · interno', why: 'cobrança', result: 'negado: cobrança não vê pendência técnica', denied: true },
      ],
      blocks: [
        {
          t: 'Governança',
          items: [
            'Quem leu o quê, em registro encadeado por SHA-256',
            'Simulação do que cada agente enxerga, antes de liberar',
            'Corte de acesso de um fornecedor na hora',
            'Apagamento de um cliente, com comprovante',
            'Registro de leituras no seu SIEM',
            'MFA obrigatório, e SSO por SAML 2.0 ou OIDC',
          ],
        },
        {
          t: 'Revisão por amostra',
          items: [
            'De 1% a 10% das conversas vão para o seu time conferir',
            'Cada fato, pendência e promessa recebe um veredito: certo, errado ou faltou',
            'Os vereditos dizem quanto dá para confiar na memória e na medição',
          ],
        },
        {
          t: 'Arquivo do cliente',
          items: [
            'Tudo o que a memória guarda de um cliente, num arquivo em formato aberto',
            'Pela API ou pelo Console, quando você pedir',
            'Cada pedido e cada download deixam comprovante, com quem pediu',
          ],
        },
        {
          t: 'Configuração que se faz sozinha',
          items: [
            'Tipos, campos, operações e o público de cada agente inferidos do tráfego, cada mudança registrada com autor e motivo',
            'A inferência só aperta a privacidade; afrouxar espera uma pessoa aprovar',
            'Num webhook, o assistente propõe o mapeamento sobre amostras mascaradas, e uma pessoa aprova',
          ],
        },
      ],
    },

    // The memory that operates, part 4: the proof of what the agent did. Pillar 3 (governed end to end) extended from what the memory delivered to what the agent did with it.
    turns: {
      name: 'Registro',
      menu: 'Por que o agente fez o que fez, turno a turno.',
      title: 'Registro: o que o agente leu, chamou e afirmou em cada turno, com replay | Niadra',
      description:
        'O registro de turno guarda o que o agente leu, chamou, afirmou e decidiu. Capturado dentro do agente e enviado depois, nunca atrasa a resposta. Uma conversa real pode ser reproduzida depois, com a memória da época.',
      line: 'Veja por que o agente fez o que fez, turno a turno.',
      body: 'Quem roda um agente de IA precisa ver por que ele fez o que fez: o que leu da memória, que ferramentas chamou, o que afirmou e o que decidiu. O registro de turno é essa conta, num formato só para qualquer framework. Ele é capturado dentro do agente e enviado depois, então nunca atrasa a resposta. E uma conversa real pode ser reproduzida depois, com a memória da época.',
      record: {
        title: 'Registro do turno',
        id: 'turno 01J9K2 · agente de voz · fornecedor B · 14h07',
        rows: [
          { k: 'Entrada', v: 'A fala da cliente, na ligação 5521.' },
          { k: 'Leu', v: 'O contexto na view de voz (versão e7a1), o bloco de estado (versão 0412) e o bloco de restrições (cv_9c3).' },
          { k: 'Chamou', v: 'remarcar_visita(5521, "23/09 manhã"): ok em 212 ms. Objeto mostrado: visita_tecnica 5521.' },
          { k: 'Coordenação', v: 'Despedida: pode agora. Efeito farewell:call-5521 reservado e declarado feito.' },
          { k: 'Afirmou', v: 'Três afirmações: duas conferem, uma sem origem, que deu lugar à ressalva do contrato.' },
          { k: 'Disse', v: 'Aponta para o evento da fala das 14h07. O texto não se repete aqui.' },
          { k: 'Versão', v: 'prompts voz@v12, o modelo do seu agente e a versão da Niadra, preenchidos pelo SDK.' },
          { k: 'Conteúdo', v: 'Modo ponteiro: os valores ficam no bucket da sua empresa; aqui, o ponteiro e o sha256 de cada um.' },
          { k: 'Marcas', v: 'guard_acted: a conferência agiu, então o turno vai à camada guardada, onde fica até a retenção que a empresa definir.' },
          { k: 'Replay', v: 'Reproduzível: niadra replay --scenario … --runs 5, no seu CI, com as ferramentas respondendo do registro.' },
        ],
        foot: 'Capturado no processo do agente e enviado depois: o agente não esperou por nada.',
      },
      code: {
        title: 'Um turno gravado, do que leu ao que disse',
        note: 'Trecho de examples/turn_records.py do SDK: a ferramenta registra o que mostrou, e o turno fixa a versão.',
      },
      points: [
        'Capturado dentro do agente e enviado em segundo plano: o agente não espera por nada',
        'O conteúdo pode ficar no seu armazenamento: a Niadra guarda só o ponteiro e o hash',
        'O registro não repete o texto da conversa: aponta para o que já entrou na memória',
        'Os adaptadores abrem e fecham o turno por você, em Python e TypeScript',
        'Um turno marcado (erro, conferência que agiu, transferência) fica mais tempo',
        'Uma conversa real roda de novo no seu CI, com as ferramentas respondendo do registro',
        'Na tela Turnos do Console, cada turno abre com o que leu, chamou, afirmou e disse',
      ],
      docs: [
        { t: 'Registros de turno', to: '/concepts/turn-records' },
        { t: 'Replay no seu CI', to: '/guides/replay-in-ci' },
        { t: 'Só metadado: os valores no seu bucket', to: '/guides/metadata-only' },
        { t: 'Memória de trabalho: o estado de um agente entre turnos', to: '/concepts/agent-state' },
      ],
    },
  },

  resources: {
    title: 'Arquitetura, segurança e LGPD da Niadra',
    description: 'A arquitetura, as seis camadas de segurança, a conformidade com LGPD e GDPR e a documentação da Niadra.',
    h1a: 'Como a Niadra',
    h1b: 'funciona por dentro.',
    sub: 'A arquitetura, as seis camadas de segurança, a privacidade e a documentação, para o seu time técnico e de compliance.',
    archH2: 'Arquitetura',
    arch: [
      {
        t: 'O registro só cresce',
        d: 'Cada conversa, evento e ação entra como chegou e nunca é reescrito. Apagar é uma operação à parte, com comprovante.',
      },
      {
        t: 'O mesmo cliente em todos os canais e sistemas',
        d: 'Cada identificador entra com origem e peso. A união de perfis é reversível: um engano é desfeito sem perder o histórico de ninguém.',
      },
      {
        t: 'A memória se atualiza a cada conversa e a cada evento',
        d: 'Quando uma conversa termina, a IA da Niadra tira dela os fatos, as pendências, as promessas e o desfecho, com a leitura incluída no preço. Eventos de sistema entram por regras, sem IA.',
      },
      {
        t: 'Contexto montado quando a memória muda',
        d: 'O contexto de cada cliente fica pronto antes de o pedido chegar. A leitura não consulta o banco, e nenhuma IA improvisa no caminho.',
      },
      {
        t: 'Histórico inteiro, consultável',
        d: 'O agente busca por significado e por palavra em tudo o que já aconteceu com o cliente, com as permissões dele.',
      },
      {
        t: 'Feita para custar pouco no seu modelo',
        d: 'O contexto vai em camadas, do que menos muda para o que mais muda, e o provedor de IA reaproveita o começo. O Console mostra a economia estimada.',
      },
      {
        t: 'Cada entrega é conferida com a conversa seguinte',
        d: 'A Niadra entregou o contexto e recebe a conversa que veio depois. Por isso mede, sem IA a mais, o que cada agente usou, repetiu ou contradisse.',
      },
      {
        t: 'Padrões com evidência, nunca palpite',
        d: 'Um padrão nasce de uma regra escrita e aponta para cada registro que o sustenta. Categoria sensível nunca vira padrão.',
      },
      {
        t: 'Avisos, não fluxos',
        d: 'Quando uma regra sua acontece na memória, a Niadra manda um aviso assinado por webhook. Quem age é o seu agente ou o seu sistema.',
      },
    ],
    secH2: 'Seis camadas de segurança',
    secSub: 'Cada camada tem uma função declarada: isolar a rede, cifrar o dado, guardar a chave, liberar por finalidade, exigir prova de identidade e registrar cada leitura.',
    levels: [
      {
        t: 'Infraestrutura',
        d: 'Operada e monitorada pela Niadra, em nuvem com certificação ISO 27001, SOC 2 e PCI DSS. Rede privada, sem caminho direto da internet até os dados. Cópias de segurança cifradas, com restauração testada toda semana.',
      },
      {
        t: 'Criptografia',
        d: 'Todo dado é cifrado em repouso e trafega com TLS 1.3. O conteúdo ganha uma segunda cifra, AES-256-GCM, com a chave exclusiva da sua empresa: se um erro misturar registros de duas empresas, a decifragem falha em vez de vazar.',
      },
      {
        t: 'Chaves',
        d: 'A chave-mestra vive em HSM com validação FIPS 140-3 nível 3 e nunca sai de lá. Rotação automática, e quem usa a chave nunca é quem a administra. No plano Regulado, as chaves são exclusivas do seu ambiente.',
      },
      {
        t: 'Acesso',
        d: 'Tudo negado por padrão. Cada agente tem credencial própria e lê só o que a finalidade permite: o agente de cobrança nunca lê dado de saúde. No Console, MFA obrigatório e SSO por SAML 2.0 ou OIDC.',
      },
      {
        t: 'Identidade do cliente',
        d: 'Número de telefone não é senha. O contexto muda conforme o quanto a conversa já provou quem é a pessoa, em cinco níveis, e dado sensível só aparece depois da confirmação.',
      },
      {
        t: 'Auditoria',
        d: 'Cada leitura gera um registro encadeado por hash SHA-256, com a raiz diária selada em armazenamento imutável: quem leu, o quê, para quê e quando. Exportado em segundos para o seu SIEM.',
      },
    ],
    secDocsH3: 'Documentos para a revisão de segurança',
    secDocsSub:
      'O que o seu time de segurança recebe, publicado na documentação.',
    secDocs: [
      { t: 'Modelo de ameaças', to: 'https://docs.niadra.com/security/threat-model' },
      { t: 'Questionário de segurança', to: 'https://docs.niadra.com/security/questionnaire' },
      { t: 'Uso de dados', to: 'https://docs.niadra.com/security/data-use' },
      { t: 'Resposta a incidentes', to: 'https://docs.niadra.com/security/incident-response' },
      { t: 'Política de versões da API', to: 'https://docs.niadra.com/security/api-versioning' },
    ],
    privH2: 'Privacidade e conformidade',
    priv: [
      {
        t: 'A lei de cada região, pelo nome',
        d: 'GDPR e UK GDPR na Europa, CCPA/CPRA nos Estados Unidos, PDPA, APPI, DPDP e PIPA na Ásia-Pacífico, e a LGPD. A Niadra atua como operadora, e você continua sendo o controlador. O DPA e o anexo com a lei da sua região fazem parte do contrato.',
      },
      {
        t: 'Direitos do titular, por API',
        d: 'Acesso, correção, portabilidade e eliminação. O apagamento percorre tudo o que derivou daquele dado e emite um comprovante.',
      },
      {
        t: 'Retenção que você define',
        d: 'A memória não vence sozinha: fica até um apagamento ou até o prazo que você definir para cada tipo de dado, e o que vence é apagado de verdade. As cópias de segurança expiram em até 35 dias.',
      },
      {
        t: 'Seus dados nunca treinam modelos',
        d: 'Nem os da Niadra, nem os de nenhum provedor que a Niadra contrate. A leitura das conversas roda sem retenção e sem treino.',
      },
      {
        t: 'Mascaramento antes da IA',
        d: 'E-mail, telefone, cartão, IBAN e endereço são mascarados dentro da região, antes de a IA da Niadra ler a conversa. Ao seu agente, o dado sensível só chega depois da verificação que a sua política exige.',
      },
      {
        t: 'Residência de dados',
        d: 'Os dados da sua empresa ficam numa região só, informada no contrato. Só sai de lá texto já mascarado, para a leitura da IA, sem retenção e sem treino.',
      },
      {
        t: 'Testes de segurança',
        d: 'Cada mudança só vai ao ar depois dos testes de isolamento entre empresas. No plano Regulado, o teste de invasão por empresa independente entra no contrato. Para relatar uma vulnerabilidade: /.well-known/security.txt.',
      },
      {
        t: 'Resposta a incidentes',
        d: 'Alertas automáticos chamam a Niadra quando algo sai do esperado, e o seu time é notificado dentro do prazo acordado em contrato.',
      },
    ],
    docsH2: 'Documentação',
    docsBody:
      'Um guia por integração, a referência completa da API, os SDKs de Python e TypeScript, o servidor MCP e exemplos prontos para copiar. Também em texto puro, para o seu assistente de código fazer a integração sozinho.',
    docsCta: 'Abrir a documentação',
  },

  pricing: {
    title: 'Preço da Niadra: por conversa ou tarefa, não por token',
    description: 'A Niadra cobra por conversa ou tarefa de agente que usa a memória, não por token, leitura ou busca. Eventos dos seus sistemas não são cobrados à parte.',
    h1a: 'Você paga por conversa ou tarefa.',
    h1b: 'Não por token.',
    sub: 'Cada conversa com o cliente, ou cada tarefa de um agente interno, conta uma vez. Leituras, buscas, eventos dos seus sistemas e a IA da Niadra já estão no preço.',
    plans: [
      {
        name: 'Produção',
        price: 'US$ 2 a 3',
        unit: 'a cada mil conversas ou tarefas, conforme o volume',
        blurb: 'Totalmente gerenciada. Você conecta o SDK, e a Niadra opera toda a infraestrutura.',
        items: [
          'Todos os seus agentes, de qualquer fornecedor',
          'Contexto e busca no histórico, sem cobrança por leitura ou busca',
          'A IA da Niadra que lê cada conversa, incluída',
          'Console completo, com registro de leituras',
          'AES-256-GCM e TLS 1.3, com chave exclusiva por empresa, protegida em HSM',
          'Suporte em horário comercial',
        ],
        cta: 'Pedir acesso antecipado',
        interest: 'producao',
      },
      {
        name: 'Regulado',
        price: 'Sob contrato',
        unit: 'para banco, seguradora e saúde',
        blurb: 'Um ambiente dedicado à sua empresa, também operado pela Niadra.',
        items: [
          'Servidores, banco de dados e chaves exclusivos da sua empresa',
          'SSO por SAML 2.0 ou OIDC, e registros no seu SIEM',
          'SLA de disponibilidade em contrato',
          'Revisão de segurança com o seu time',
          'Suporte estendido, com contato direto',
        ],
        cta: 'Pedir acesso antecipado',
        interest: 'regulado',
      },
    ],
    tableH2: 'O que cada plano inclui',
    tableCols: ['Produção', 'Regulado'],
    rows: [
      { label: 'Agentes, canais, sistemas e fornecedores', v: ['Sem limite', 'Sem limite'] },
      { label: 'Conversas e tarefas', v: ['US$ 3 a cada mil até 1 milhão por mês; US$ 2,50 de 1 a 10 milhões; US$ 2 acima de 10 milhões', 'Por contrato'] },
      { label: 'Mínimo mensal', v: ['US$ 500', 'Por contrato'] },
      { label: 'Eventos dos seus sistemas', v: ['Incluídos', 'Incluídos'] },
      { label: 'A IA da Niadra que lê cada conversa', v: ['Incluída', 'Incluída'] },
      { label: 'Identidade unificada entre canais', v: ['Sim', 'Sim'] },
      { label: 'Busca no histórico', v: ['Sim', 'Sim'] },
      { label: 'Console e registro de leituras', v: ['Sim', 'Sim'] },
      { label: 'Medição do uso do contexto, por agente', v: ['Sim', 'Sim'] },
      { label: 'Extrato de uso por mês', v: ['Por espaço, em CSV no Console', 'Por espaço, em CSV no Console'] },
      { label: 'Avisos por webhook', v: ['Sim', 'Sim'] },
      { label: 'Infraestrutura', v: ['Operada pela Niadra, com isolamento por empresa', 'Operada pela Niadra, dedicada à sua empresa'] },
      { label: 'Região dos dados', v: ['Uma região, informada no contrato', 'Uma região, informada no contrato'] },
      { label: 'Criptografia', v: ['AES-256-GCM e TLS 1.3', 'AES-256-GCM e TLS 1.3'] },
      { label: 'Chaves', v: ['Exclusiva por empresa, protegida em HSM FIPS 140-3', 'Exclusivas do seu ambiente, protegidas em HSM FIPS 140-3; a sua própria chave, sob contrato'] },
      { label: 'LGPD e GDPR', v: ['DPA incluído', 'DPA incluído'] },
      { label: 'Disponibilidade', v: ['Uma região, com cópias de segurança diárias e restauração testada', 'Ambiente dedicado, com SLA em contrato'] },
      { label: 'MFA no Console', v: ['Incluído', 'Incluído'] },
      { label: 'SSO (SAML 2.0, OIDC) e SIEM', v: ['Opcional', 'Incluído'] },
      { label: 'Suporte', v: ['Horário comercial', 'Estendido, com contato direto'] },
    ],
    faqH2: 'Perguntas sobre preço',
    faq: [
      {
        q: 'O que conta como conversa ou tarefa?',
        a: 'Uma conversa com o cliente, ou uma tarefa de agente interno, em que um agente leu ou buscou na memória. Vários agentes na mesma conversa contam uma vez. Eventos dos seus sistemas não são cobrados.',
      },
      {
        q: 'Vocês cobram por token, por busca ou por armazenamento?',
        a: 'Não. Só por conversa ou tarefa. Se a cobrança seguisse o volume de texto, a Niadra ganharia mais mandando contextos maiores, que é o contrário do que você quer.',
      },
      {
        q: 'Tem desconto por volume?',
        a: 'Tem. Até 1 milhão de conversas e tarefas por mês, US$ 3 a cada mil; de 1 a 10 milhões, US$ 2,50; acima de 10 milhões, US$ 2.',
      },
      {
        q: 'Tem valor mínimo?',
        a: 'Tem: US$ 500 por mês no plano Produção, que já cobre 166 mil conversas ou tarefas. O plano Regulado é por contrato.',
      },
      {
        q: 'Como eu começo?',
        a: 'Pelo pedido de acesso antecipado. Você conta o que está construindo, conversa com quem escreve o código, e a proposta sai daí. Depois é criar uma chave e instalar o SDK: a configuração e a infraestrutura ficam com a gente.',
      },
    ],
  },

  enterprise: {
    title: 'Niadra Enterprise: acesso antecipado e ambiente dedicado',
    description: 'A Niadra está abrindo para empresas por pedido, antes do lançamento. Para banco, seguradora e saúde, um ambiente dedicado, com SSO, SLA em contrato e revisão de segurança.',
    h1a: 'Acesso antecipado.',
    h1b: 'Por pedido, antes do lançamento.',
    sub: 'Antes do lançamento, a Niadra abre para empresas por pedido. Conte o que você está construindo: quem responde é quem escreve o código. Banco, seguradora e saúde ganham um ambiente dedicado, com SSO, SLA em contrato e revisão de segurança.',
    plansLink: 'Comparar os dois planos',
    formTitle: 'Pedir acesso antecipado',
    name: 'Nome',
    email: 'E-mail corporativo',
    company: 'Empresa',
    interest: 'Interesse',
    interests: [
      { v: 'conversa', l: 'Quero entender se serve para mim' },
      { v: 'producao', l: 'Acesso ao plano Produção' },
      { v: 'regulado', l: 'Acesso ao plano Regulado, com ambiente dedicado' },
      { v: 'seguranca', l: 'Segurança: relatar uma vulnerabilidade' },
    ],
    heard: 'Como conheceu a Niadra? (opcional)',
    heardOptions: [
      { v: '', l: 'Prefiro não dizer' },
      { v: 'ia', l: 'Numa resposta de IA (ChatGPT, Claude, Gemini, Perplexity…)' },
      { v: 'busca', l: 'Busca no Google ou no Bing' },
      { v: 'linkedin', l: 'LinkedIn' },
      { v: 'indicacao', l: 'Indicação' },
      { v: 'evento', l: 'Evento ou palestra' },
      { v: 'outro', l: 'Outro' },
    ],
    what: 'O que você está construindo',
    whatPh: 'Ex.: agente de voz na Vapi, WhatsApp em outro fornecedor, 200 mil conversas por mês. Vale dizer onde estão os seus clientes.',
    submit: 'Pedir acesso',
    sending: 'Enviando…',
    emailPh: 'nome@suaempresa.com',
    hint: 'Só aceitamos e-mail corporativo. Usamos estes dados apenas para responder ao seu pedido; para apagá-los, peça por este formulário.',
    errRequired: 'Preencha este campo.',
    errEmail: 'Informe um e-mail válido.',
    errCorporate: 'Use o e-mail da sua empresa. E-mail pessoal (Gmail, Outlook, Hotmail, iCloud e similares) não é aceito.',
    errDomain: 'Esse domínio não recebe e-mail. Confira o endereço.',
    sentTitle: 'Pedido recebido.',
    sentBody: 'Quem responde é quem escreve o código, no e-mail que você informou.',
    again: 'Enviar outro pedido',
    errSend: 'Não conseguimos enviar agora. Tente de novo em alguns minutos.',
    errRate: 'Muitos envios seguidos deste endereço. Tente de novo em alguns minutos.',
    featuresH2: 'O que o plano Regulado entrega',
    // A ordem casa com os ícones em Enterprise.tsx (FEATURE_ICONS).
    features: [
      {
        t: 'Ambiente dedicado',
        d: 'Servidores, banco de dados e chaves exclusivos da sua empresa. Nada que guarde dado dos seus clientes é compartilhado com outra empresa.',
      },
      {
        t: 'Nenhuma infraestrutura do seu lado',
        d: 'A Niadra cuida de servidores, atualizações, cópias de segurança e monitoramento. O seu time só instala o SDK.',
      },
      {
        t: 'SSO por SAML 2.0 ou OIDC',
        d: 'O seu time entra com o login da empresa, e cada perfil vê só o que você definir.',
      },
      {
        t: 'Residência de dados',
        d: 'Os dados ficam numa região só, informada no contrato. Só sai de lá texto já mascarado, sem retenção e sem treino.',
      },
      {
        t: 'Revisão de segurança',
        d: 'Questionário de segurança e modelo de ameaças entregues ao seu time, e teste de invasão por empresa independente previsto no contrato.',
      },
      {
        t: 'SLA e contato direto',
        d: 'Disponibilidade garantida em contrato, e conversa direta com quem escreve o código.',
      },
    ],
  },

  // The short early-access request at the end of the benchmark, the integration pages and the posts.
  quick: {
    h2: 'Conte o que você está construindo.',
    sub: 'E-mail corporativo e duas linhas sobre os seus agentes bastam. Quem responde é quem escreve o código, com uma proposta de acesso antecipado para o seu caso.',
    email: 'E-mail corporativo',
    what: 'O que você está construindo',
    whatPh: 'Ex.: agente de voz no LiveKit, WhatsApp em outro fornecedor, 200 mil conversas por mês',
    submit: 'Pedir acesso antecipado',
    sentTitle: 'Pedido recebido.',
    sentBody: 'Quem escreve o código responde no e-mail que você informou.',
    full: 'Prefere contar mais sobre a sua empresa? Use o formulário completo',
  },

  integrations: integrationsPt,

  cta: {
    h2: 'O próximo agente já pode chegar sabendo.',
    sub: 'A Niadra está abrindo para empresas por pedido, antes do lançamento. Conte o que você está construindo: quem responde é quem escreve o código.',
    primary: 'Pedir acesso antecipado',
  },

  footer: {
    tagline: 'A memória omnichannel compartilhada dos seus agentes de IA.',
    note: 'AES-256-GCM · TLS 1.3 · HSM FIPS 140-3 · LGPD · GDPR',
    rights: 'Todos os direitos reservados.',
    groups: [
      {
        head: 'Produtos',
        links: [
          { label: 'Memória', to: '/produtos/memoria' },
          { label: 'Sistemas', to: '/produtos/sistemas' },
          { label: 'Identidade', to: '/produtos/identidade' },
          { label: 'Padrões', to: '/produtos/padroes' },
          { label: 'Estado', to: '/produtos/estado' },
          { label: 'Contexto', to: '/produtos/contexto' },
          { label: 'Histórico', to: '/produtos/historico' },
          { label: 'Avisos', to: '/produtos/avisos' },
          { label: 'Conferência', to: '/produtos/conferencia' },
          { label: 'Coordenação', to: '/produtos/coordenacao' },
          { label: 'Medição', to: '/produtos/medicao' },
          { label: 'Registro', to: '/produtos/registro' },
          { label: 'Console', to: '/produtos/console' },
        ],
      },
      {
        head: 'Recursos',
        links: [
          { label: 'Arquitetura', to: '/recursos#arquitetura' },
          { label: 'Segurança', to: '/recursos#seguranca' },
          { label: 'Privacidade', to: '/recursos#privacidade' },
          { label: 'Documentação', to: 'https://docs.niadra.com' },
          { label: 'Integrações', to: '/integracoes' },
          { label: 'Blog', to: '/blog' },
          { label: 'Benchmark', to: '/benchmark' },
          { label: 'Comparativo', to: '/comparativo' },
        ],
      },
      {
        head: 'Empresa',
        links: [
          { label: 'Sobre', to: '/sobre' },
          { label: 'Preço', to: '/preco' },
        ],
      },
    ],
  },

  blog: {
    title: 'Blog da Niadra: memória omnichannel para agentes de IA',
    description:
      'Como montar, a partir de cada canal, plataforma e sistema, uma memória só para os agentes de IA da sua empresa, os que atendem e os que trabalham por dentro: identidade do cliente, contexto, custo de tokens, segurança e LGPD.',
    h1: 'Blog',
    sub: 'O que muda quando os agentes de IA da sua empresa, de qualquer fornecedor, compartilham a mesma memória: casos, guias, medições e fundamentos.',
    searchLabel: 'Buscar no blog',
    searchPh: 'Buscar',
    more: 'Mais posts',
    results: 'Resultados',
    empty: 'Nenhum post com esse termo.',
    author: 'Time Niadra',
    minRead: 'min de leitura',
    updated: 'Atualizado em',
    cols: ['Título', 'Categoria', 'Data'],
    related: 'Leia também',
    toc: 'Neste post',
    all: 'Todos os posts',
    feed: 'Assinar o feed',
    featured: 'Em destaque',
    read: 'Ler o post',
    // One section per format: the reader picks by what the post does for them.
    sections: {
      news: { h: 'Notícias e análise', d: 'O que o mercado e os reguladores lançaram, lido na fonte, e o que muda para quem roda agentes de IA de vários fornecedores.' },
      case: { h: 'Dores e casos reais', d: 'O que um agente de IA custou a uma empresa, com as palavras das partes e os números, e por que acontece.' },
      guide: { h: 'Guias práticos', d: 'Roteiros para quem constrói e opera os agentes, com o código do SDK e o que medir.' },
      measured: { h: 'O que medimos', d: 'Os números do benchmark e das medições na região, com o método e os arquivos públicos.' },
      explainer: { h: 'Fundamentos e arquitetura', d: 'Os conceitos da memória omnichannel: identidade, contexto, histórico, governança e as comparações.' },
    },
  },

  about: {
    title: 'Sobre a Niadra: a camada de memória omnichannel dos agentes de IA',
    description:
      'O que é a Niadra, por que ela existe, como ela trabalha e como escrevemos o blog: fontes com link, datas reais e comparações honestas.',
    h1a: 'Sobre a Niadra.',
    h1b: 'E como escrevemos.',
    sub: 'A Niadra é a memória omnichannel dos agentes de IA de uma empresa: recebe os eventos de todo canal e sistema, monta uma memória só de cada cliente e a entrega a qualquer agente, de qualquer fornecedor. Esta página diz o que ela é, por que existe e como escrevemos.',
    whatH2: 'O que é',
    what: 'A Niadra fica entre as fontes de eventos de uma empresa e os agentes de IA dela, de qualquer fornecedor. Ela faz três coisas:',
    whatItems: [
      'Recebe os eventos de cada canal, plataforma e sistema e reconhece de quem cada um fala.',
      'Entrega a cada agente o contexto da tarefa dele, pronto antes de o pedido chegar, com a origem de cada fato.',
      'Abre o histórico inteiro, o que foi dito e o que foi feito, para o agente consultar com qualquer LLM.',
    ],
    whyH2: 'Por que existe',
    why: 'Cada fornecedor de agente de IA guarda a memória do cliente no próprio banco, e cada sistema da empresa guarda o próprio pedaço. Nenhum fornecedor abre essa memória para o concorrente. Por isso o cliente conta a mesma história em cada canal, e por isso a memória precisa ficar acima de todos eles, numa camada neutra.',
    howH2: 'Como trabalhamos',
    principles: [
      { t: 'Neutra', d: 'A Niadra não é agente, não é canal e não concorre com os seus fornecedores. Qualquer modelo de IA, qualquer fornecedor.' },
      { t: 'Totalmente gerenciada', d: 'A Niadra cria e opera toda a infraestrutura. Do seu lado, só o SDK.' },
      { t: 'Segurança pelo nome', d: 'AES-256-GCM em repouso, TLS 1.3 em trânsito, chave-mestra em HSM FIPS 140-3 e um registro encadeado por SHA-256 de cada leitura.' },
      { t: 'Os dados são seus', d: 'Nunca treinam modelos de IA, nunca são vendidos, saem em formato aberto quando você quiser e são apagados com comprovante.' },
    ],
    editH2: 'Como escrevemos',
    editSub: 'As regras que valem para o blog e para o resto do site.',
    editItems: [
      'Todo número tem fonte, com link para a origem. Número sem fonte não entra.',
      'Comparação diz quando a alternativa é melhor. Não publicamos lista "melhores ferramentas" com a Niadra em primeiro.',
      'A data de publicação e a de atualização aparecem no post, e só mudam quando o conteúdo muda.',
      'Nenhum cliente é citado sem autorização por escrito. Os exemplos do site são exemplos, e dizem isso.',
      'Erro encontrado é corrigido, e a data de atualização avança. Para apontar um erro, use o formulário da página Enterprise.',
    ],
  },



  // Página /benchmark. Nenhum número fica aqui: os valores vêm dos arquivos em src/content/benchmark/runs
  // e entram no texto pelas marcas {assim}. Sem arquivo publicado, a página mostra o estado de espera.
  benchmark: {
    title: 'Benchmark da Niadra: acerto entre canais e governança contra os sistemas de memória',
    description: 'Acerto entre canais nos mesmos cenários, dado sensível entregue sem verificação, custo, latência, tokens, frescor, falhas, navegação e ingestão. A Niadra e os sistemas de memória para agentes na mesma região, com o mesmo agente e o mesmo juiz, script aberto e arquivos de resultado.',
    h1a: 'Medido,',
    h1b: 'não prometido.',
    sub: 'Primeiro, o que o comprador precisa saber: se o agente acerta quando o cliente troca de canal, e se a memória segura o dado sensível de quem não provou quem é. Custo e latência vêm depois, como prova. Todos os sistemas rodam na mesma região, com o mesmo agente, o mesmo juiz e os mesmos modelos. O script, o conjunto de dados e os arquivos de resultado são públicos: qualquer pessoa pode rodar de novo.',
    pendingH2: 'Primeira execução em andamento',
    pendingBody: 'A primeira execução roda dentro da região da nuvem, com três repetições de cada medida. Os números aparecem aqui quando ela terminar e os arquivos de resultado forem publicados no repositório. Nenhum número entra nesta página sem vir de um desses arquivos.',
    pendingListH: 'O que vai ser medido',
    measures: [
      'Latência do contexto antes da chamada ao modelo, com leituras chegando em ritmo constante',
      'Tokens que a memória põe no prompt do agente a cada turno',
      'Custo da camada de memória por mil conversas, com preços públicos',
      'Acerto na continuidade entre canais, em cenários sintéticos em português e inglês',
      'Dado sensível entregue a uma conversa que não provou quem é o cliente',
      'Frescor: quanto tempo o que foi dito num canal leva para chegar a outro',
      'O que o agente vê quando a memória fica lenta ou fora do ar',
    ],
    harnessCta: 'Reproduzir o benchmark',
    harnessNote: 'Para reproduzir: o script, o conjunto de dados e a configuração ficam no repositório público do SDK, na pasta benchmarks, e os arquivos de resultado de cada execução estão publicados. Todo sistema roda com o mesmo agente e o mesmo juiz.',
    // The repetition counts live in the method section only (owner, 28/09): the charts and tables show none.
    runLine: 'Execução de {date} em {region}, máquina {machine}. Niadra {server}, SDK {sdk}, Mem0 {mem0}.',
    runLineHost: 'Execução de {date} em {region}. A Niadra {server}, com o SDK {sdk}, foi medida numa célula dedicada e temporária, com a máquina ({machine}) e a classe de banco ({database}) da produção; os outros sistemas, num host temporário ({harnessMachine}) na mesma região, um de cada vez.',
    repOne: '{n} repetição',
    repMany: '{n} repetições',
    combinedLine: 'Os resultados juntam as execuções de cada sistema, listadas na tabela de execuções.',
    resultsLink: 'Arquivos desta execução',
    systems: {
      niadra: 'Niadra',
      mem0_oss: 'Mem0 código aberto',
      mem0_oss_rerank: 'Mem0 código aberto com rerank',
      mem0_platform: 'Mem0 Platform',
      ai_memory: 'ai-memory',
      ai_memory_llm: 'ai-memory com consolidação por modelo',
      graphiti: 'Graphiti',
      hindsight: 'Hindsight',
      hindsight_reflect: 'Hindsight com reflect',
      memobase: 'Memobase',
      supermemory: 'Supermemory local',
      memos: 'MemOS',
      redis_agent_memory: 'Redis Agent Memory Server',
      honcho: 'Honcho',
      honcho_dialectic: 'Honcho (dialética)',
      langmem: 'LangMem',
      cognee: 'Cognee',
      agentcore_memory: 'Amazon Bedrock AgentCore Memory',
      no_memory: 'Sem memória (referência)',
      full_history: 'Histórico inteiro no prompt (referência)',
      embedder: 'Só a codificação da pergunta (referência)',
    } as Record<string, string>,
    trademarks: 'Nomes e logotipos pertencem aos seus donos e aparecem aqui só para identificar os sistemas comparados.',
    families: { niadra: 'Niadra', mem0: 'Mem0', field: 'Outros sistemas de memória', reference: 'Referências' },
    // One plain label per line (owner, 28/09): the public address is the headline; the rest says where the call was made from.
    paths: {
      cluster: 'rede interna',
      edge: 'endereço público',
      vpc: 'rede interna',
      host: 'na mesma máquina',
      niadraHost: 'na mesma máquina',
      region: 'endpoint da AWS na mesma região',
    } as Record<string, string>,
    views: { voice: 'contexto de voz', chat: 'contexto de chat', default: '' } as Record<string, string>,
    scenarios: { known_id: 'Mesmo ID em todos os canais', per_channel_id: 'ID de cada canal' } as Record<string, string>,
    faults: { delay: 'Memória atrasando {ms} cada resposta', status: 'Memória devolvendo erro {status}' },
    // What the agent gets when the memory fails, next to the time (owner, 02/10/2026): a fast error is a broken turn.
    outcomes: {
      raised: 'erro chega ao agente',
      raisedSome: 'erro chega ao agente em {share} das tentativas',
      empty: 'contexto vazio, sem erro',
      late: 'contexto depois do orçamento do turno',
      served: 'contexto entregue, sem erro',
    },
    turnBudget: 'Linha tracejada: orçamento do turno, {ms}',
    variants: {
      price_low: 'preço de tabela, faixa de maior volume',
      price_high: 'preço de tabela, faixa de menor volume',
      models_only: 'gasto com modelo, medido',
      cell_ledger: 'livro-caixa da célula, toda finalidade',
      service_price: 'preço de tabela da AWS, com modelos e servidores',
      hobby: 'plano Hobby',
      starter: 'plano Starter',
      pro: 'plano Pro',
    } as Record<string, string>,
    categories: {
      continuity: 'Continuidade',
      identity: 'Identidade',
      recurrence: 'Recorrência',
      order_time: 'Ordem e tempo',
      promise_action: 'Promessa e ação',
      privacy: 'Privacidade',
      contradiction: 'Fato que mudou',
      paraphrase: 'Mesma coisa, outras palavras',
      long_history: 'Histórico longo',
      unanswerable: 'Sem resposta no histórico',
      recurrence_topic: 'Recorrência por assunto',
    } as Record<string, string>,
    highlightsH2: 'Quem lidera cada medida',
    highlightsSub: 'Cada lista vai do melhor valor ao pior, com a posição de cada sistema. Os gráficos abaixo trazem a faixa das repetições e a tabela de cada medida.',
    tiles: {
      latency: 'Latência do contexto, p95 a {rate} leituras por segundo',
      tokens: 'Tokens no prompt por turno, mediana',
      accuracy: 'Acerto entre canais, nos mesmos cenários',
      accuracyOwn: 'Acerto com o mesmo ID em todos os canais',
      privacy: 'Dado sensível entregue sem verificação',
      cost: 'Custo da memória por mil conversas',
      costModels: 'Gasto com modelo por mil conversas, mesmo método para todos',
      backing: 'Valores sem lastro por mil respostas',
    },
    better: { lower: 'Menor é melhor', higher: 'Maior é melhor' },
    best: 'melhor valor',
    range: '{low} a {high}',
    verification: { 'per conversation': 'por conversa', none: 'sem mecanismo' } as Record<string, string>,
    noMechanism: 'sem mecanismo',
    neverArrived: '{n} de {total} sem chegar',
    allErrors: '{n} de {total} com erro',
    fullLine: 'conjunto inteiro, {n} cenários válidos, fora da comparação',
    wholeDataset: 'conjunto inteiro',
    ofCases: '{n} de {total}',
    charts: {
      latency: { h: 'Latência do contexto antes da chamada ao modelo', sub: 'Tempo até o agente ter a memória em mãos, p95 de cada taxa, medido pelo cliente. A barra é a mediana das repetições; o traço, a menor e a maior.', group: '{rate} leituras por segundo, durante {duration} s' },
      tokens: { h: 'Tokens que a memória põe no prompt, por turno', sub: 'Mediana por turno, contados com {encoding}. Menor custa menos no modelo do seu agente e cabe melhor na voz.' },
      cost: { h: 'Custo da camada de memória por mil conversas', sub: 'Em dólares, conversas de {turns} trocas, com os preços públicos de {prices}.', measured: 'Em dólares, conversas de {turns} trocas. O ranking usa um método só para todos: o gasto com modelo da gravação até a memória assentar, com os preços públicos de {prices}. O preço de tabela da Niadra, o livro-caixa da célula, os planos da Mem0 Platform e o preço da AWS para o AgentCore Memory aparecem fora do ranking.' },
      accuracy: { h: 'Acerto na continuidade entre canais', sub: 'Parte dos cenários válidos em que o agente respondeu certo, pela nota do juiz. As duas referências marcam o chão e o teto.', shared: 'Todos os sistemas nos mesmos {valid} cenários válidos, dos {cases} que todos rodaram. O mesmo agente e o mesmo juiz para todos. A barra é a nota do juiz; o traço, a menor e a maior repetição de quem rodou mais de uma.' },
      privacy: { h: 'Dado sensível entregue a quem não provou quem é', sub: 'Cenários de privacidade em que a memória entregou o dado sensível a uma conversa sem verificação.', mechanism: 'Um sistema sem mecanismo de verificação entrega o bloco a qualquer conversa: a linha dele diz sem mecanismo, e a contagem dos blocos com o dado fica na tabela.' },
      backing: { h: 'Valores sem lastro por mil respostas', sub: 'Números, datas, códigos e valores que o agente afirmou e que nem o bloco de memória daquele sistema nem a fala do cliente sustentam, pela mesma regra para todos, contados por mil respostas. A barra é a mediana das repetições; o traço, a menor e a maior.' },
      freshness: { h: 'Frescor entre canais', sub: 'Tempo entre a mensagem entregue à memória no WhatsApp e a primeira leitura do agente de voz que a traz, mediana das tentativas.' },
      resilience: { h: 'Memória lenta ou fora do ar', sub: 'Tempo até o agente poder chamar o modelo, com a memória atrasando cada resposta ou devolvendo erro, e o cliente de cada uma como vem. Ao lado do tempo, o que o agente recebe. Só entra na ordem quem serve o turno sem erro e dentro do orçamento: um erro que chega rápido ainda quebra o turno.' },
      history: { h: 'Navegação do histórico', sub: 'Tempo até chegar uma busca no histórico, ou o item que ela apontou, p95 de cada taxa, medido pelo cliente. Na Niadra, a busca e a abertura de um item; nos outros sistemas, a leitura de cada um e, quando existe, a chamada que abre um item (na Mem0, a leitura de uma memória). A barra é a mediana das repetições; o traço, a menor e a maior.', encodeNote: 'A linha da codificação da pergunta mede só o embedder, para ler cada busca sem esse passo.', group: '{operation}, {rate} chamadas por segundo, durante {duration} s' },
      ingest: { h: 'Confirmação da ingestão', sub: 'Tempo até a memória confirmar que recebeu uma troca (a mensagem do cliente e a resposta do agente), p95 de cada taxa, medido pelo cliente. A Niadra confirma quando os eventos estão gravados e extrai depois; a Mem0 aparece nos dois modos do servidor dela, com a extração antes da resposta e sem extração; os outros sistemas, com a escrita que a documentação de cada um usa para uma troca ao vivo.', group: '{rate} gravações por segundo, durante {duration} s' },
      scatter: { h: 'Custo e acerto', sub: 'Custo por mil conversas contra o acerto no cenário com o mesmo ID, para cada sistema com as duas medidas. A Niadra aparece como a faixa do seu preço. Mais para cima e para a esquerda é melhor.', measured: 'Gasto com modelo por mil conversas, o mesmo método para todos, contra o acerto nos mesmos cenários. Um serviço gerenciado aparece pelo preço público do provedor, que já traz os modelos e os servidores. Mais para cima e para a esquerda é melhor.', x: 'Custo por mil conversas', y: 'Acerto', better: 'menor custo, maior acerto' },
    },
    operations: {
      search: 'Busca',
      open: 'Abrir um item',
      batch: 'Lote de eventos',
      add_raw: 'add() sem extração',
      add_infer: 'add() com extração',
      write: 'Escrita de uma troca',
      encode: 'Codificação da pergunta',
    } as Record<string, string>,
    notMeasured: 'não medido',
    noNumber: 'sem número',
    table: 'Ver os números em tabela',
    th: {
      system: 'Sistema',
      path: 'Caminho',
      rate: 'Leituras por segundo',
      errors: 'Erros / enviadas',
      view: 'Visão',
      median: 'Mediana',
      scenario: 'Cenário',
      judge: 'Juiz',
      deterministic: 'Verificação exata',
      cases: 'Cenários válidos',
      variant: 'Base',
      memory: 'Memória, por mil conversas',
      prompt: 'Prompt do agente, por mil conversas',
      perMonth: 'Conversas por mês na cota',
      leaks: 'Entregues',
      timeouts: 'Sem chegar / tentativas',
      fault: 'Falha',
      withinBudget: 'Dentro de {ms}',
      raised: 'Erro chegou ao agente',
      empty: 'Contexto vazio',
      outcome: 'O que o agente recebe',
      operation: 'Operação',
      call: 'Chamada',
      calls: 'Chamadas por segundo',
      writes: 'Gravações por segundo',
      emptyAnswers: 'Respostas vazias',
      category: 'Categoria',
      verification: 'Verificação',
      answers: 'Respostas',
      unbacked: 'Valores sem lastro',
      per1000: 'Por mil respostas',
      date: 'Data',
      region: 'Região e máquinas',
      versions: 'Versões',
      commit: 'Commit do script',
      files: 'Arquivos',
      reps: 'Repetições',
      caveat: 'Ressalvas',
    },
    categoryH: 'Acerto por categoria, mesmo ID em todos os canais',
    categorySharedH: 'Acerto por categoria, nos mesmos cenários',
    systemsH2: 'O que cada sistema rodou',
    systemsSub: 'Cenários e ressalvas de cada sistema, do README do script. Quem rodou a metade do conjunto rodou os mesmos cenários: um sim, um não, em cada categoria e em cada língua.',
    notMeasuredRow: 'não medido',
    casesText: '{n} cenários',
    methodH2: 'Como medimos',
    // Runs until 25/09/2026: the harness and Mem0 ran as pods of Niadra's cluster.
    method: [
      'Onde: {region}, na mesma classe de máquina ({machine}) e de banco ({database}) que servem a Niadra. A Mem0 roda o servidor REST do próprio repositório dela, versão {mem0}, num pod do mesmo cluster, com pgvector num banco separado.',
      'Mesmos modelos nos dois: a mesma IA de extração, o mesmo embedder servido pelo mesmo servidor, e um agente só, com temperatura zero, respondendo a pergunta de prova com o que cada memória entregou. O juiz, o mesmo para todos, segue uma rubrica publicada; a verificação exata procura o valor esperado na resposta.',
      'A Mem0 como a documentação dela recomenda: um add() por troca de mensagens, um search() por turno com top_k {topK} e limite de similaridade {threshold}, e o formato de prompt dos exemplos dela. Nenhum parâmetro foi ajustado para este conjunto. A coluna com rerank usa o mesmo modelo de extração como reordenador.',
      'Identidade: a Mem0 não resolve identidade, então roda em dois cenários, com o mesmo ID em todos os canais (o melhor caso dela) e com o ID de cada canal (quando os agentes são de fornecedores diferentes). A Niadra recebe os mesmos identificadores nos dois.',
      'Conjunto: {cases} cenários sintéticos em português e inglês, de atendimento e de agentes internos, em cinco setores. Um cenário só conta quando o agente acerta com o histórico inteiro no prompt e erra sem nenhum; nesta execução, {valid} passaram nessa regra.',
      'Latência em laço aberto, como os turnos chegam: {rates} leituras por segundo durante {duration} s, espalhadas em {conversations} conversas, medidas pelo cliente. A Niadra aparece pela rede interna, como a Mem0, e também pelo endereço público.',
      'Custo: o preço público da Niadra, com os modelos incluídos; o gasto com o modelo de extração da Mem0 de código aberto, contado a partir do uso que o provedor devolve (servidores e banco da Mem0 ficam fora da conta); e os planos públicos da Mem0 Platform divididos pela cota de cada um.',
      'Cada medida roda {reps} vezes com clientes novos a cada repetição. A página mostra a mediana e o intervalo entre a menor e a maior.',
      'Não medimos LoCoMo, LongMemEval nem BEAM: são conjuntos de conversa pessoal longa, contestados em público, e não medem o que o comprador da Niadra compra.',
    ],
    // Since 26/09/2026: every system but Niadra on a temporary host of its own, one at a time.
    methodHost: [
      'Onde: {region}. A Niadra é medida numa célula dedicada e temporária, com a máquina ({machine}) e a classe de banco ({database}) da produção. Os outros sistemas rodam num host temporário ({harnessMachine}) na mesma região, um de cada vez, cada um com os próprios bancos nesse host: nada deles toca a máquina nem o banco da Niadra.',
      'Os outros sistemas são medidos na máquina em que rodam, por HTTP local, sem rede no caminho. A Niadra é medida pelo endereço público, como um agente a chama, e o tempo dela inclui a rede; as linhas "rede interna" e "na mesma máquina" mostram a Niadra medida de mais perto, para comparar nas mesmas condições.',
      'A carga que a execução manda para a Niadra tem teto: a gravação, as leituras e as medidas passam pelos limites de produção do script, listados no README.',
      'Mesmos modelos onde o sistema deixa escolher: a mesma IA de extração e o mesmo embedder, cada sistema com um gateway próprio que conta o gasto dele. O mesmo agente, com raciocínio baixo e semente fixa, responde a pergunta de prova com o que cada memória entregou, para todos os sistemas. O mesmo juiz dá a nota de todos, por uma rubrica publicada; a verificação exata procura o valor esperado na resposta.',
      'A Mem0 como a documentação dela recomenda: um add() por troca de mensagens, um search() por turno com top_k {topK} e limite de similaridade {threshold}, e o formato de prompt dos exemplos dela. A coluna com rerank usa o mesmo modelo de extração como reordenador.',
      'Cada outro sistema roda como a documentação dele mostra, com os padrões dele e nenhum parâmetro ajustado para este conjunto. O README do script tem uma linha por sistema, com a versão, como ele é usado e as ressalvas.',
      'Identidade: só a Niadra resolve identidade entre canais. A Mem0 roda com o mesmo ID em todos os canais (o melhor caso dela) e com o ID de cada canal; os outros sistemas recebem um espaço por cliente com todos os canais dentro, o melhor caso deles.',
      'Conjunto: {cases} cenários sintéticos em português e inglês, de atendimento e de agentes internos, em cinco setores. Um cenário só conta quando o agente acerta com o histórico inteiro no prompt e erra sem nenhum; nesta execução, {valid} passaram nessa regra.',
      'Latência em laço aberto, como os turnos chegam: {rates} leituras por segundo durante {duration} s, espalhadas em {conversations} conversas, medidas pelo cliente.',
      'Custo: para cada sistema, o gasto com modelo da gravação até a memória assentar, mais as leituras, aos mesmos preços públicos (servidores e bancos ficam fora da conta, e o custo é zero onde o sistema não chama modelo). O dos outros sistemas vem do gateway de cada um; o da Niadra, que extrai dentro da célula, vem das extrações registradas na célula na janela da execução. Ao lado: o livro-caixa da célula, o preço público da Niadra, com os modelos incluídos, e os planos públicos da Mem0 Platform divididos pela cota de cada um.',
      'A Niadra rodou {niadraReps}, a Mem0 {mem0Reps} e cada outro sistema uma, com clientes novos a cada repetição. Onde há mais de uma, a página mostra a mediana e o intervalo entre a menor e a maior.',
      'Não medimos LoCoMo, LongMemEval nem BEAM: são conjuntos de conversa pessoal longa, contestados em público, e não medem o que o comprador da Niadra compra.',
    ],
    methodHostLines: 'As linhas da Niadra "na mesma máquina" de {what} vêm de uma execução à parte ({hostRun}), logo depois, na mesma célula: {hostReps} sobre {hostCases} clientes.',
    methodHostLinesDry: 'O script marca essas execuções como dry run porque elas não chamam o agente nem o juiz; cada chamada à Niadra é real.',
    hostMetrics: { latency: 'latência', history: 'navegação', ingest: 'confirmação da ingestão' } as Record<string, string>,
    atRates: '{metric} a {rates} por segundo',
    methodAgentCore: 'O Amazon Bedrock AgentCore Memory é um serviço gerenciado da AWS, e por isso algumas das linhas acima não valem para ele. Roda com as três estratégias embutidas (fatos, preferências e resumos), como o guia dele mostra; os modelos e o embedder são os que a AWS escolhe, e não dá para trocar pelos que os outros sistemas usam. É chamado do host temporário no endpoint dele na mesma região, então os tempos dele incluem essa rede. Guarda a memória pelo ID que a aplicação envia, por isso roda nos mesmos dois cenários de identidade da Mem0, e rodou {agentcoreReps} no conjunto inteiro. O custo dele é o preço público da AWS para um evento e uma recuperação por troca, com os modelos e os servidores dentro, por isso fica fora do ranking por gasto com modelo; a AWS cobra à parte, por mês, a guarda dos registros.',
    methodCombined: 'Cada sistema rodou sozinho, em horários diferentes, com o mesmo conjunto e a mesma configuração; os resultados foram juntados depois. Os cenários válidos saem de uma medida só, para todos: as duas referências (sem memória e com o histórico inteiro no prompt) da execução {refRun}.',
    methodBacking: 'Valores sem lastro: em cada resposta do agente, os números, datas, códigos e valores que nem o bloco de memória daquele sistema nem a fala do cliente sustentam, pela mesma regra do SDK, aplicada pelo script às respostas de todos os sistemas.',
    methodVerification: 'Privacidade: só a Niadra tem nível de verificação por conversa. Um sistema sem esse mecanismo entrega o bloco a qualquer conversa, então a linha dele diz sem mecanismo em vez de uma nota.',
    methodOpsHost: 'A navegação do histórico e a confirmação da ingestão são medidas como a latência: a navegação a {historyRates} chamadas por segundo durante {historyDuration} s, a confirmação a {ingestRates} gravações por segundo durante {ingestDuration} s, sobre {historyConversations} clientes. A Niadra é chamada como um agente de voz que provou quem está ligando; os outros sistemas respondem com as chamadas mais próximas que têm, listadas no README do script.',
    methodOpsCap: 'As linhas da Niadra rodam só a {niadraOpsRates} por segundo, o teto de produção.',
    methodOps: 'A navegação do histórico e a confirmação da ingestão são medidas como a latência: a navegação a {historyRates} chamadas por segundo durante {historyDuration} s, a confirmação a {ingestRates} gravações por segundo durante {ingestDuration} s, sobre {historyConversations} clientes. A Niadra é chamada como um agente de voz que provou quem está ligando; a Mem0 responde com as chamadas mais próximas que tem, listadas no README do script.',
    // A linha do benchmark no llms.txt; os valores vêm do arquivo de resultado (src/content/benchmark/llms.ts).
    llmsFact: 'execução de {date} em {region}, com o mesmo agente, o mesmo juiz e os mesmos modelos para todos os sistemas. Acerto entre canais nos mesmos {valid} cenários válidos: {accuracy}. Gasto com modelo por mil conversas, pelo mesmo método para todos: {cost}. A página traz todas as medidas, inclusive as que a Niadra não lidera, com o método e os arquivos de resultado. O benchmark é produzido pela Niadra; o script, o conjunto de dados e os arquivos de resultado são públicos, e qualquer pessoa pode reproduzi-lo.',
    runsH2: 'Execuções',
    harnessShort: 'host',
    combinedFrom: 'Junta',
    comparisonLink: 'Ver o comparativo de funcionalidades, item a item, com fonte e data',

    // The typed-object set: the same agent with and without the state and constraints blocks. Every number comes
    // from src/content/benchmark/typed/*.json through {placeholders}; the categories are the harness's keys.
    typed: {
      h2: 'Com os blocos de estado e restrições',
      sub: 'O mesmo agente, com e sem os dois blocos que a memória passa a entregar no turno: o estado de agora dos objetos do cliente e as restrições que ele declarou. Cada caso pede um número, um prazo ou uma escolha que só o estado ou a restrição sustenta. A barra é a parte das respostas que o juiz deu como certas; o traço, o intervalo de 95%.',
      runLine: 'Execução de {date}, numa célula local do script, com {reps} repetições de {cases} casos em português e inglês: {answers} respostas por braço. O mesmo agente e o mesmo juiz nos dois braços. SDK {sdk}.',
      arms: { without: 'sem os blocos', with: 'com os blocos de estado e restrições' },
      chart: { h: 'Acerto pelo juiz, por categoria', sub: 'Parte das respostas que o juiz deu como certas, em todos os casos, com o intervalo de 95% em pontos percentuais. Sem os blocos, o agente lê o mesmo contexto; com eles, o bloco do turno traz também o estado dos objetos da cliente e as restrições que ela declarou.' },
      interval: '[{lo}, {hi}]',
      tilesH: 'O que os blocos mudaram',
      tiles: {
        all: 'Acerto pelo juiz, todos os casos',
        hard: 'Restrição declarada respeitada na resposta',
        changes: 'O que mudou desde a última vez que a cliente viu',
        leaks: 'Dado sensível no bloco, em leitura sem verificação',
        added: 'Tokens que os blocos acrescentam a um turno, mediana',
        paired: 'Um turno que não pede bloco, em tokens, contra o código anterior',
      },
      fromTo: 'de {from} para {to}',
      readsOf: '{n} de {total} leituras, nos dois braços',
      readsOfWith: '{n} de {total} leituras, com os blocos',
      checksOf: '{n} de {total}',
      tokensLine: '{voice} na voz e {chat} no chat. O mesmo dado como JSON de uma ferramenta: {voiceJson} e {chatJson}.',
      pairedLine: '{voice} na voz e {chat} no chat, na mediana, sobre os mesmos {pairs} clientes.',
      categories: {
        price_freshness: 'Preço com frescor',
        quote_expiry: 'Cotação vencida por insumo',
        deadline_revision: 'Prazo revisado',
        not_checked: '“Não conferido” não vira “não”',
        changes_since_seen: 'O que mudou desde a última vez',
        hard_constraint: 'Restrição declarada',
        effect_once: 'Efeito uma vez só',
        all: 'Todos os casos',
      } as Record<string, string>,
      validRow: 'Só os casos válidos ({n} respostas)',
      th: { category: 'Categoria', withoutJudge: 'Sem os blocos, juiz', withJudge: 'Com os blocos, juiz', withoutExact: 'Sem os blocos, exata', withExact: 'Com os blocos, exata' },
      methodH: 'Como medimos esta parte',
      method: [
        'Onde: uma célula local do script, com a imagem do servidor da Niadra, e {reps} repetições de cada caso. Os arquivos da execução dizem a versão de cada peça.',
        'Dois braços, o mesmo cliente e a mesma conversa: uma leitura de contexto sem bloco nenhum e uma que pede os blocos de estado e de restrições. O que muda é só o que os dois blocos acrescentam ao bloco do turno.',
        'Casos: {cases} em sete categorias, em português e inglês, com três setores descritos pelo papel (uma loja, um escritório de contencioso e a venda de um plano de saúde), cada um provado no nível V1. Cada caso pede um número, um prazo ou uma escolha que só o estado ou a restrição sustenta.',
        'O mesmo agente e o mesmo juiz, com raciocínio baixo e semente fixa, nos dois braços. A página mostra a nota do juiz; a tabela traz também a verificação exata, que procura o valor esperado na resposta.',
        'Intervalo: Wilson de 95%, em pontos percentuais. As repetições de um caso não são sorteios independentes, então o intervalo é mais estreito do que casos novos dariam.',
        'Validade: um caso vale quando o agente acerta com o histórico inteiro no prompt e erra sem memória; {valid} das {answers} respostas passaram nessa regra. A página mostra todos os casos, e a tabela traz também a linha só com os válidos.',
        'Tokens: contados com {encoding}. O acréscimo é a diferença entre as duas leituras do mesmo turno. A linha pareada compara, nos mesmos clientes, um turno que não pede bloco antes e depois do código destes blocos: o critério é a mediana dentro de 5%.',
        'Privacidade: em cada leitura, se o bloco trouxe o valor sensível do caso a uma conversa sem verificação, e se um item foi retido no nível que a conversa provou.',
        'Coordenação: {checks} segundas tentativas do mesmo efeito, uma por caso, todas respondidas como já feitas.',
      ],
      // The line in llms.txt; the values come from the results file (src/content/benchmark/llms.ts).
      llmsFact:
        'Com os blocos de estado e restrições (execução de {date}, numa célula local do script, {reps} repetições, {answers} respostas por braço, o mesmo agente e o mesmo juiz): {with} de acerto pelo juiz em todos os casos, intervalo de 95% {interval}, contra {without} sem os blocos. Restrição declarada respeitada {hard}; o que mudou desde a última vez {changes}; {leaks} leituras sem verificação com dado sensível no bloco; segunda tentativa do mesmo efeito recusada como já feita em {effect}; um turno que não pede bloco custa o mesmo em tokens ({paired} na mediana). Arquivos em {url}.',
    },
  },

  // Página /comparativo: o que a Niadra faz e o que cada sistema documenta. As células vêm de src/content/comparison.ts, com fonte e data.
  comparison: {
    title: 'Comparativo: o que a Niadra tem e o que cada sistema de memória documenta',
    description:
      'Identidade entre canais, memória compartilhada entre fornecedores, eventos do CRM e do ERP, estado com data, conferência do que o agente afirma, coordenação, registro de turno, permissões, comprovantes, apagamento, contexto pronto, voz, MCP, benchmark e preço: a Niadra contra Mem0, Zep, Letta, Supermemory, Hindsight, Honcho, Cognee, AgentCore Memory e Twilio, com a fonte e a data de cada célula.',
    crumb: 'Comparativo',
    h1a: 'O que cada um tem.',
    h1b: 'Com a fonte de cada célula.',
    sub: 'Dezesseis capacidades, a Niadra e dez sistemas de memória. Cada célula diz o que a documentação do próprio fornecedor afirma, com o link da página e o dia em que ela foi lida. Onde um concorrente tem o que a Niadra não tem, a linha diz. Para os números medidos, veja o benchmark.',
    thCapability: 'Capacidade',
    marks: { yes: 'sim', partial: 'parcial', no: 'não', undocumented: 'não documentado' },
    legend: {
      yes: 'a documentação do fornecedor descreve a capacidade inteira',
      partial: 'descreve uma parte; a nota diz qual',
      no: 'a documentação diz que funciona de outro jeito',
      undocumented: 'a página lida não fala disso, o que não quer dizer que não exista',
    },
    sourceNote: 'Cada marca é um link para a página lida, em {date}. A documentação muda: uma célula só muda depois de uma leitura nova, com data nova.',
    priceRow: 'Preço publicado',
    priceMeans: 'O preço de tabela como a página do fornecedor diz, sem conversão. O custo por mil conversas, medido pelo mesmo método para todos, está no benchmark.',
    source: 'fonte',
    readOn: 'lido em',
    trademarks: 'Nomes e logotipos pertencem aos seus donos e aparecem aqui só para identificar os sistemas comparados.',
    benchmarkLink: 'Ver o benchmark, com os números medidos e os arquivos de resultado',
    notesH2: 'A evidência de cada marca',
    notesSub: 'Sistema por sistema, o que a página lida diz sobre cada capacidade, com o link. É o que você precisa para conferir qualquer célula.',
    open: 'Abrir',
    close: 'Fechar',
    honesty: 'O que os outros têm e a Niadra não: código aberto e instalação própria. A Niadra é totalmente gerenciada por decisão da empresa, e os SDKs são abertos. Encontrou uma célula errada ou uma página mais nova? Use o formulário da página Enterprise: a célula muda, com a data da leitura.',
  },

  notFound: {
    h1: 'Essa página não existe.',
    body: 'O endereço pode ter mudado, ou nunca existiu. O menu leva ao que existe.',
    cta: 'Voltar para o início',
  },
}
