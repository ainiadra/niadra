/**
 * Contract values (English ASCII) to the words a tenant reads.
 * The brand glossary rules: "contexto", "padrão", "aproveitamento do
 * contexto", "comprovante", "aviso". Internal codenames never reach the screen.
 */
import type {
  AccessChangeKind,
  AssertionMethod,
  AssertionState,
  AssistTask,
  Audience,
  ConfigType,
  DeliveryStatus,
  DiffStatus,
  EventKind,
  HandleStatus,
  HandleType,
  ReceiptKind,
  ReceiptSurface,
  Role,
  Scope,
  SubjectKind,
  TriggerCondition,
  Verification,
} from '../api/types'

const pick = (map: Record<string, string>, key: string | null | undefined, fallback = 'não informado') => (key ? (map[key] ?? key) : fallback)

export const subjectKind = (k: SubjectKind) => pick({ person: 'Pessoa', account: 'Conta', partner: 'Parceiro' }, k)
export const SUBJECT_KINDS: SubjectKind[] = ['person', 'account', 'partner']

export const HANDLE_TYPES: Record<HandleType, string> = {
  phone_e164: 'Telefone',
  wa_id: 'WhatsApp',
  wa_jid: 'WhatsApp (JID)',
  wa_lid: 'WhatsApp (LID)',
  wa_bsuid: 'WhatsApp (conta comercial)',
  email: 'E-mail',
  gov_id_hmac: 'Documento',
  app_user_id: 'ID no app',
  system_id: 'ID no sistema',
  org_registry_hmac: 'Registro da organização',
  email_domain: 'Domínio de e-mail',
  anon_id: 'ID anônimo',
}
export const handleType = (t: HandleType) => pick(HANDLE_TYPES, t)
/** Types a person can type by hand; hashed ones come from systems. */
export const TYPABLE_HANDLES: HandleType[] = ['phone_e164', 'email', 'wa_id', 'app_user_id', 'system_id', 'email_domain', 'anon_id']

export const handleStatus = (s: HandleStatus) =>
  pick({ active: 'Ativo', ambiguous: 'Ambíguo', blocked: 'Bloqueado', suspected_recycled: 'Possível número reciclado' }, s)

/** The weight of an identifier, in the words of the site: pista, declarado, verificado, autenticado, do sistema. */
export const handleWeight = (w: string) => pick({ hint: 'Pista', declared: 'Declarado', verified: 'Verificado', authenticated: 'Autenticado', system: 'Do sistema' }, w)

export const verification = (v: Verification | string | null | undefined) => (v === 'no_customer' ? 'Sem cliente' : (v ?? 'não informado'))
export const VERIFICATIONS: Verification[] = ['V0', 'V1', 'V2', 'V3', 'V4', 'no_customer']

export const audience = (a: Audience | string) =>
  pick(
    {
      customer_agent: 'Agente de atendimento',
      internal_agent: 'Agente interno',
      human: 'Atendente humano',
      reviewer: 'Revisão',
      analyst: 'Análise',
      trigger_target: 'Destino de aviso',
      export: 'Exportação',
    },
    a,
  )

export const SCOPES: Scope[] = ['track', 'act', 'context', 'search', 'identify', 'admin', 'analytics']
export const scope = (s: Scope) =>
  pick({ track: 'Enviar eventos', act: 'Registrar ações', context: 'Ler contexto', search: 'Consultar o histórico', identify: 'Identificar clientes', admin: 'Governança', analytics: 'Análise' }, s)

export const role = (r: Role | string) =>
  pick({ admin: 'Administração', security: 'Segurança e privacidade', integration: 'Integração', review: 'Revisão', analysis: 'Análise', vendor: 'Fornecedor' }, r)
export const ROLES: Role[] = ['admin', 'security', 'integration', 'review', 'analysis', 'vendor']
/** What each role reaches; the server decides, this only explains. */
export const roleReach = (r: Role) =>
  pick(
    {
      admin: 'Fontes, chaves, pessoas, papéis e SSO; passa em toda checagem',
      security: 'Comprovantes, política e simulação, corte de acesso, apagamento e exportação',
      integration: 'Mapeamentos, visões, regras de padrão e de aviso, webhooks e o assistente',
      review: 'A fila de revisão por amostra, com a máscara de cada finalidade',
      analysis: 'Medição, padrões e uso, em agregado ou por pseudônimo',
      vendor: 'Só as fontes do próprio fornecedor e a medição delas',
    },
    r,
  )

export const accessChange = (k: AccessChangeKind | string) =>
  pick(
    {
      invited: 'Convite enviado',
      invitation_accepted: 'Senha criada pelo convite',
      roles_changed: 'Papéis alterados',
      deactivated: 'Desativada',
      reactivated: 'Reativada',
      second_factor_reset: 'Segundo fator redefinido',
      sso_person_created: 'Criada pelo SSO no primeiro login',
      sso_roles_synced: 'Papéis atualizados pelos grupos do SSO',
      sso_saved: 'SSO configurado',
      sso_removed: 'SSO removido',
    },
    k,
  )

/** Why an SSO sign-in came back without a session (domain/control/sso.py SsoFailure). */
export const ssoFailure = (reason: string | null | undefined) =>
  pick(
    {
      denied: 'O provedor de identidade não liberou o login, ou ele foi cancelado.',
      invalid: 'A resposta do provedor de identidade não passou na conferência da Niadra: assinatura, emissor, público, horário ou vínculo com este login.',
      domain: 'O e-mail que o provedor mandou não é de um domínio desta empresa, ou o provedor diz que ele não foi confirmado.',
      no_role: 'Nenhum grupo seu no provedor dá um papel no Console, e a empresa não definiu um papel padrão.',
      other_tenant: 'Este e-mail já pertence a outra empresa no Console.',
      deactivated: 'A sua conta está desativada. Quem administra o Console da sua empresa pode reativar.',
      unavailable: 'O provedor de identidade não respondeu a tempo. Tente de novo em instantes.',
      disabled: 'O SSO da empresa foi desligado enquanto você entrava.',
    },
    reason,
    'O login pelo SSO não foi concluído.',
  )

export const speaker = (r: string | null | undefined) =>
  pick({ customer: 'Cliente', ai_agent: 'Agente de IA', human_agent: 'Atendente', system: 'Sistema' }, r, 'quem falou')
export const eventKind = (k: EventKind | string | null | undefined) => pick({ message: 'mensagem', system_event: 'evento de sistema', action: 'ação' }, k, 'evento')

const CHANNELS: Record<string, string> = {
  whatsapp: 'WhatsApp',
  voice: 'Voz',
  email: 'E-mail',
  app: 'App',
  web: 'Web',
  sms: 'SMS',
  chat: 'Chat',
  crm: 'CRM',
  erp: 'ERP',
  helpdesk: 'Help desk',
  tickets: 'Tickets',
  billing: 'Cobrança',
  orders: 'Pedidos',
}
export const channel = (c: string | null | undefined) => (c ? (CHANNELS[c] ?? c) : 'canal não informado')

/** Pending items and statuses as the memory module names them. */
export const openItemStatus = (s: string) => pick({ open: 'Aberta', resolved: 'Resolvida', overdue: 'Vencida' }, s)
export const factStatus = (s: string) => pick({ active: 'Ativo', expired: 'Expirado', quarantined: 'Em quarentena', retracted: 'Retirado' }, s)
export const actionStatus = (s: string) =>
  pick({ declared: 'Declarada pelo agente', confirmed: 'Confirmada pelo sistema', divergent: 'Divergente do sistema', quarantined: 'Em quarentena' }, s)

/** Policy effects of the simulation (contracts/policy.py). */
export const effect = (e: string) => pick({ allow: 'Entregue', mask: 'Entregue com máscara', deny: 'Negado' }, e)

/** Why an item was withheld; the keys of `withheld` and the `reason` of simulated items. */
const WITHHELD: Record<string, { label: string; means: string }> = {
  policy: { label: 'Política', means: 'A política da empresa não libera este item para esta finalidade ou audiência.' },
  verification: { label: 'Nível de verificação', means: 'O item exige um nível de verificação maior que o da sessão.' },
  budget: { label: 'Não coube no contexto', means: 'Itens mais relevantes para a tarefa ocuparam o espaço disponível.' },
  unused: {
    label: 'Não usado por este agente',
    means: 'Nas entregas medidas, este agente nunca usou itens deste tipo e categoria. Eles saem do contexto dele e voltam quando ele precisar de um.',
  },
  quarantine: { label: 'Em quarentena', means: 'O item está em revisão e não é entregue a nenhum agente.' },
}
export const withheldReason = (r: string) => WITHHELD[r]?.label ?? r
export const withheldMeans = (r: string) => WITHHELD[r]?.means ?? ''

export const receiptKind = (k: ReceiptKind) =>
  pick({ read: 'Leitura', history: 'Consulta ao histórico', analytics: 'Análise', admin: 'Ação no Console', holdout: 'Grupo de controle', denied: 'Leitura negada' }, k)
export const RECEIPT_KINDS: ReceiptKind[] = ['read', 'history', 'denied', 'admin', 'analytics', 'holdout']

/** Where the chain check broke (domain/audit/chain.py verify, app/audit.py verify_chain), in words. */
export const chainBreak = (reason: string) =>
  pick(
    {
      prev_hash_mismatch: 'o hash do anterior guardado neste comprovante não é o do comprovante anterior',
      sequence_gap: 'a sequência pula um número, e falta um comprovante antes deste',
      content_mismatch: 'o conteúdo do comprovante não confere com o hash gravado nele',
      head_mismatch: 'a cabeça gravada da fatia não confere com a cadeia refeita',
    },
    reason,
  )

export const surface = (s: ReceiptSurface | null | undefined) =>
  pick(
    { pack: 'Contexto', live: 'Turnos ao vivo', delta: 'Atualização do contexto', search: 'Busca no histórico', timeline: 'Linha do tempo', open: 'Item aberto', object: 'Objeto de negócio', console: 'Leitura no Console' },
    s,
  )

export const itemKind = (k: string | null | undefined) =>
  pick(
    { fact: 'Fato', episode: 'Episódio', open_item: 'Pendência ou promessa', action: 'Ação de agente', trait: 'Padrão', object: 'Objeto de negócio', system_event: 'Evento de sistema', profile_attr: 'Dado do perfil', event: 'Evento' },
    k,
    'item',
  )

export const VIEWS: { value: string; label: string }[] = [
  { value: 'voice', label: 'Voz' },
  { value: 'chat', label: 'Chat' },
  { value: 'brief', label: 'Breve' },
  { value: 'full', label: 'Completa' },
  { value: 'account', label: 'Conta' },
  { value: 'partner', label: 'Parceiro' },
]

const TRAITS: Record<string, string> = {
  recurring_complaint: 'Reclamação recorrente',
  broken_company_promise: 'Promessa da empresa não cumprida',
  asked_to_cancel: 'Pediu cancelamento',
  preferred_channel: 'Canal preferido',
  does_not_answer_calls: 'Não atende ligação',
  escalated_to_human: 'Foi transferido para atendimento humano',
  declining_sentiment: 'Satisfação em queda',
  overdue_invoices: 'Faturas em atraso',
}
export const traitName = (n: string) => TRAITS[n] ?? n

export const assertionMethod = (m: AssertionMethod) =>
  pick(
    {
      explicit_identify: 'Identificação explícita',
      otp: 'Código de verificação',
      login: 'Login',
      system_import: 'Importado de sistema',
      same_event: 'Mesmo evento',
      co_occurrence: 'Apareceram juntos',
      channel_rotation: 'Troca de identificador no canal',
      accepted_suggestion: 'Sugestão aceita',
      external_resolver: 'Resolvedor externo',
      declared: 'Declarado',
    },
    m,
  )
export const ASSERTION_METHODS: AssertionMethod[] = ['explicit_identify', 'system_import', 'declared', 'accepted_suggestion', 'external_resolver']
export const assertionState = (s: AssertionState) => pick({ applied: 'Aplicada', pending: 'Pendente', rejected: 'Recusada' }, s)

export const operationStatus = (s: string) =>
  pick({ queued: 'Na fila para aplicar', applied: 'Aplicada', linked: 'Vínculo criado', blocked: 'Identificador bloqueado' }, s)

export const deliveryStatus = (s: DeliveryStatus | string) => pick({ pending: 'Na fila', delivered: 'Entregue', failing: 'Falhando', dead: 'Esgotou as tentativas' }, s)
export const DELIVERY_STATUSES: DeliveryStatus[] = ['pending', 'delivered', 'failing', 'dead']

/**
 * The event types the cell sends to a `standard` endpoint today (the server's app/*, enqueue_event), in the
 * order a person reads them. The control plane also accepts the documented ones not sent yet.
 */
export const OUTBOUND_EVENTS: { type: string; label: string }[] = [
  { type: 'trigger.fired', label: 'Uma regra de aviso disparou' },
  { type: 'trigger.retracted', label: 'Um disparo foi retirado depois de uma separação de perfis' },
  { type: 'action.recorded', label: 'Um agente registrou o que fez num sistema' },
  { type: 'open_item.closed', label: 'Uma pendência foi fechada por uma ação' },
  { type: 'open_item.reopened', label: 'Uma pendência fechada foi reaberta' },
  { type: 'conversation.outcome', label: 'O desfecho de uma conversa' },
  { type: 'fact.added', label: 'A memória passou a guardar um fato' },
  { type: 'fact.retracted', label: 'Um fato deixou de valer, retirado ou substituído' },
  { type: 'fact.restored', label: 'Um fato retirado voltou a valer, restaurado pela segurança' },
  { type: 'import.completed', label: 'Uma importação de arquivo terminou' },
  { type: 'import.failed', label: 'Uma importação de arquivo falhou' },
  { type: 'agent_memory.note_created', label: 'Um agente guardou uma nota de trabalho' },
  { type: 'trait.added', label: 'Um padrão apareceu num perfil' },
  { type: 'trait.expired', label: 'Um padrão deixou de valer' },
  { type: 'identity.merged', label: 'Dois perfis foram unidos' },
  { type: 'identity.unmerged', label: 'Um perfil foi separado' },
  { type: 'identity.suggestion', label: 'Uma possível ligação aguarda revisão' },
  { type: 'erasure.completed', label: 'Um apagamento terminou' },
  { type: 'export.completed', label: 'Uma execução da exportação contínua terminou' },
  { type: 'export.failed', label: 'Uma execução da exportação contínua falhou' },
  { type: 'usage.budget_reached', label: 'O gasto de IA do dia chegou ao teto, e a memória espera o dia seguinte' },
  { type: 'object.timer_due', label: 'Um prazo de um objeto de negócio venceu' },
  { type: 'object.watch_fired', label: 'Um objeto de negócio chegou ao estado vigiado' },
  { type: 'object.value_revised', label: 'O valor de um objeto de negócio foi revisado' },
  { type: 'object.expired_by_input', label: 'Um objeto de negócio expirou por um evento de entrada' },
  { type: 'open_item.due', label: 'Uma pendência chegou ao prazo' },
  { type: 'conversation.ended', label: 'Uma conversa terminou' },
  { type: 'fact.contradicted_by_system_event', label: 'Um fato foi desmentido por um evento de sistema' },
  { type: 'identity.ambiguous', label: 'Um identificador ficou ambíguo entre dois perfis' },
  { type: 'trait.present', label: 'Um padrão vigiado apareceu num perfil' },
  { type: 'turn.flagged', label: 'Um turno de agente foi marcado' },
  { type: 'data_issue.opened', label: 'Um problema de dados foi aberto' },
  { type: 'data_issue.acknowledged', label: 'Um problema de dados recebeu ciência' },
  { type: 'type.drift', label: 'O formato de um tipo de objeto mudou' },
  { type: 'webhook.test', label: 'Um teste do endpoint, enviado do Console' },
]

/** The last error of a webhook delivery, as the cell writes it (app/notify.py), in words. */
export const deliveryError = (e: string | null | undefined) =>
  pick(
    {
      Unavailable: 'o endereço não respondeu: ninguém atendeu na porta, o nome não resolveu ou a conexão caiu',
      endpoint_disabled: 'o endpoint está desligado no documento de endpoints',
      timeout: 'o endereço demorou mais que o limite para responder',
      signature_refused: 'o endereço recusou a assinatura do aviso',
    },
    e,
    'sem erro registrado',
  )

/** The formats of a SIEM stream (config/models.py WebhookEndpoint.format, domain/notify/ocsf.py), in words. */
export const SIEM_FORMATS: { value: 'ocsf' | 'hec'; title: string; label: string; means: string }[] = [
  {
    value: 'ocsf',
    title: 'OCSF 1.3',
    label: 'OCSF 1.3, um evento por linha',
    means: 'Um evento API Activity (classe 6003) por comprovante, um JSON por linha (application/x-ndjson), para o SIEM que lê OCSF.',
  },
  {
    value: 'hec',
    title: 'HTTP Event Collector',
    label: 'HTTP Event Collector, com o evento OCSF 1.3 no envelope',
    means: 'O mesmo evento OCSF 1.3 no envelope do HTTP Event Collector, com o sourcetype niadra:ocsf:api_activity, para o coletor no padrão HEC.',
  },
]
export const siemFormat = (f: string | null | undefined) => SIEM_FORMATS.find((x) => x.value === f)?.label ?? f ?? 'formato não informado'

/** What a webhook delivery carries: an event type in words, or a batch of receipts for a SIEM stream (app/notify.py RECEIPTS_EVENT). */
export const deliveryEvent = (type: string): string | null => (type === 'receipts' ? 'Lote de comprovantes para o SIEM' : (OUTBOUND_EVENTS.find((e) => e.type === type)?.label ?? null))

/** Why an endpoint cannot receive a warning (domain/triggers/conditions.py unreadable_reason), in words. */
const TARGET_PROBLEMS: Record<string, string> = {
  endpoint_unknown: 'o endpoint não existe, ou a fonte dele foi cortada.',
  endpoint_disabled: 'o endpoint está desligado.',
  endpoint_not_subscribed: 'o endpoint não assina o evento trigger.fired.',
  audience_cannot_receive_triggers: 'a fonte do endpoint é de análise ou de exportação, que não recebem avisos.',
  audience_cannot_read_identity: 'um agente de atendimento não recebe aviso de identidade ambígua.',
  purpose_not_allowed: 'a regra do padrão não tem finalidade em comum com a fonte do endpoint.',
  audience_not_allowed: 'o padrão não é liberado para a audiência da fonte do endpoint.',
}
export const targetProblem = (code: string) => TARGET_PROBLEMS[code] ?? code

export const triggerCondition = (c: TriggerCondition | string) =>
  pick(
    {
      'promise.overdue': 'Promessa vencida',
      'open_item.due': 'Pendência no prazo',
      'trait.present': 'Padrão presente',
      'sentiment.drop': 'Queda de satisfação',
      'identity.ambiguous': 'Identidade ambígua',
      'fact.contradicted_by_system_event': 'Ação que o sistema desmente ou não confirma',
      recontact: 'Recontato',
      'context_use.repetition_rate': 'Taxa de repetição do agente',
      'source.silent': 'Fonte parou de mandar eventos',
      'source.gap': 'Fonte mandou mais do que chegou',
    },
    c,
  )

export const diffStatus = (s: DiffStatus) => pick({ pending: 'Aguardando aprovação', proposed: 'Proposta do assistente', applied: 'Aplicada', rejected: 'Recusada' }, s)

/**
 * Portuguese names for every document type the control plane serves (the server's app/control_config.py
 * DOC_TYPES); an unknown type shows its own name. `test/config-labels.test.ts` fails when a type is missing.
 */
export const CONFIG_LABELS: Record<ConfigType, string> = {
  policies: 'Política',
  schemas: 'Esquema de extração',
  mappings: 'Mapeamentos de sistemas',
  views: 'Visões de tarefa',
  'trait-rules': 'Regras de padrão',
  'trigger-rules': 'Regras de aviso',
  'webhook-endpoints': 'Endpoints de webhook',
  'export-schedules': 'Exportação contínua',
  'review-sampling': 'Amostragem de revisão',
  experiments: 'Experimentos',
  'agent-memory': 'Memória dos agentes',
  media: 'Texto de arquivos e imagens',
  'extraction-guidance': 'Orientação da extração',
  'ingest-masking': 'Máscara na entrada',
  purposes: 'Finalidades e bases legais',
  settings: 'Ajustes da empresa',
  features: 'Recursos ligados',
  recording: 'Registro de turnos',
  'object-types': 'Tipos de objeto',
  'tool-bindings': 'Vínculos das ferramentas',
  coordination: 'Coordenação dos agentes',
  'claim-contract': 'Contrato de afirmação',
  measurement: 'Medição de desfechos',
}

/** The legal bases of LGPD arts. 7 and 11 and GDPR art. 6, as the `purposes` document names them. */
export const legalBasis = (b: string) =>
  pick(
    {
      consent: 'Consentimento',
      contract: 'Execução de contrato',
      legal_obligation: 'Obrigação legal ou regulatória',
      legitimate_interest: 'Interesse legítimo',
      vital_interest: 'Proteção da vida',
      health_protection: 'Tutela da saúde',
      public_task: 'Execução de políticas públicas',
      research: 'Estudos por órgão de pesquisa',
      legal_claims: 'Exercício regular de direitos',
      credit_protection: 'Proteção do crédito',
      fraud_prevention: 'Prevenção à fraude',
    },
    b,
  )

/** What a customer asks for, by the value types of the turn analysis (domain/serve/turn.py) or a predicate. */
export const askedSubject = (s: string) =>
  pick(
    {
      protocol: 'protocolo',
      ticket: 'chamado',
      order: 'pedido',
      record: 'registro',
      receipt: 'comprovante',
      postal_code: 'CEP',
      amount: 'valor',
      code: 'código',
      claim: 'sinistro',
      invoice: 'fatura',
      tracking: 'rastreio',
      address: 'endereço',
      document: 'documento',
      coupon: 'cupom',
      policy: 'apólice',
      identifier: 'número sem tipo dito',
    },
    s,
  )

export const erasureStatus = (s: string) => pick({ pending: 'Na fila', running: 'Apagando', completed: 'Concluído', failed: 'Falhou' }, s)
export const exportRunStatus = (s: string) => pick({ waiting: 'Aguardando', running: 'Em execução', completed: 'Concluída', failed: 'Falhou' }, s)

export const assistTask = (t: AssistTask) =>
  pick(
    {
      webhook_mapping: 'Mapeamento de webhook',
      extraction_schema: 'Esquema de extração',
      source_policy: 'Política de uma fonte',
      trigger_or_trait_rule: 'Regra de aviso ou de padrão',
    },
    t,
  )
export const assistStatus = (s: string) => pick({ queued: 'Na fila', proposed: 'Proposta pronta', delivered: 'Enviada para aprovação', rejected: 'Recusada', failed: 'Falhou' }, s)

export const signal = (s: string) =>
  pick({ used: 'Aproveitado', repeated: 'Perguntou de novo', contradicted: 'Contradisse a memória', selection_miss: 'Faltou no contexto' }, s)
/** The signals of the measurement, in the order the screens read them (domain/review/verdicts.py SIGNALS). */
export const SIGNALS = ['used', 'repeated', 'contradicted', 'selection_miss'] as const

/**
 * A row of the review agreement (domain/review/verdicts.py kinds_of): a kind of target, one row per signal
 * of the measurement (`signal:used`, ...) and `all`, every verdict together.
 */
export const agreementTarget = (target: string) =>
  target.startsWith('signal:') ? `Sinal: ${signal(target.slice('signal:'.length))}` : pick({ fact: 'Fatos', open_item: 'Pendências e promessas', signal: 'Sinais da medição', trait: 'Padrões', all: 'Todos os vereditos' }, target)

/** Why a session went to review (domain/review/sampling.py CONDITIONS). */
export const sampleCondition = (c: string) =>
  pick(
    {
      base: 'sorteio pela taxa base',
      measure_signal: 'sinal da medição',
      sensitive: 'categoria sensível',
      new_source: 'fonte nova',
      new_extractor: 'extrator novo',
      new_trait: 'padrão novo',
    },
    c,
  )

/** Why a delivery was not measured (domain/measure/model.py NotMeasured). */
export const notMeasuredReason = (r: string) =>
  pick(
    {
      not_extracted: 'conversa ainda não processada',
      partial_transcript: 'transcrição parcial da voz',
      manifest_missing: 'manifesto da entrega não encontrado',
      receipt_gap: 'comprovante da entrega ainda não gravado',
    },
    r,
  )

/** What each purpose of the AI spend ceiling pays for (app/extract.py DEFAULT_DAILY_CAPS_MICROS). */
export const budgetPurpose = (p: string) => pick({ extraction: 'Extração da memória', assistant: 'Assistente de configuração' }, p)

export const metric = (m: string) =>
  pick(
    {
      session_billable: 'Conversas e tarefas cobradas',
      context_read: 'Leituras de contexto',
      history_call: 'Consultas ao histórico',
      event_ingested: 'Eventos recebidos',
      extraction_run: 'Extrações',
      extraction_tokens: 'Tokens da extração',
      measure_run: 'Medições',
      trigger_fired: 'Avisos disparados',
      webhook_delivered: 'Webhooks entregues',
      export_run: 'Execuções da exportação',
      export_bytes: 'Bytes exportados',
      assistant_tokens: 'Tokens do assistente',
      review_sample: 'Amostras de revisão',
    },
    m,
  )

/* ---------------- Values the systems and the extraction write, in words (founder's review, 01/10) ---------------- */

/** A raw value with its label, or the value itself when no label exists: the screen shows the raw in a tooltip then. */
export interface Labeled {
  label: string
  /** The raw value, only when it differs from the label. */
  raw: string | null
}
const labeled = (map: Record<string, string>, raw: string): Labeled => (map[raw] ? { label: map[raw]!, raw } : { label: raw, raw: null })

/** The agent features of the `features` document (lib/features.ts). */
const FEATURES: Record<string, string> = {
  turns: 'Registro de turnos',
  state: 'Objetos de negócio',
  agent_state: 'Memória de trabalho',
  signals: 'Inferências',
  claims: 'Afirmações',
  coordination: 'Coordenação',
  measurement: 'Medição de desfechos',
}
export const feature = (f: string) => FEATURES[f] ?? f

/** The purposes a tenant declares. The common ones in words; what the tenant wrote otherwise. */
const PURPOSES: Record<string, string> = {
  support: 'Atendimento',
  atendimento: 'Atendimento',
  service: 'Atendimento',
  sales: 'Vendas',
  vendas: 'Vendas',
  billing: 'Cobrança',
  cobranca: 'Cobrança',
  cobrança: 'Cobrança',
  collections: 'Cobrança',
  collection: 'Cobrança',
  transactional: 'Transacional',
  marketing: 'Marketing',
  retention: 'Retenção',
  onboarding: 'Integração do cliente',
  logistics: 'Logística',
  delivery: 'Entrega',
  claims: 'Sinistros',
  scheduling: 'Agendamento',
  finance: 'Financeiro',
  legal: 'Jurídico',
  fraud: 'Prevenção à fraude',
  analytics: 'Análise',
}
export const purpose = (p: string | null | undefined) => (p ? (PURPOSES[p.toLowerCase()] ?? p) : 'não informada')
export const purposes = (list: readonly string[] | null | undefined) => (list && list.length > 0 ? list.map(purpose).join(', ') : 'nenhuma')

/** The types of business object the systems keep (contracts/objects.py, the `type` of a ref). */
const OBJECT_TYPES: Record<string, string> = {
  order: 'Pedido',
  invoice: 'Fatura',
  ticket: 'Chamado',
  case: 'Caso',
  proposal: 'Proposta',
  quote: 'Orçamento',
  contract: 'Contrato',
  subscription: 'Assinatura',
  plan: 'Plano',
  shipment: 'Entrega',
  delivery: 'Entrega',
  payment: 'Pagamento',
  refund: 'Reembolso',
  appointment: 'Agendamento',
  reservation: 'Reserva',
  claim: 'Sinistro',
  policy: 'Apólice',
  product: 'Produto',
  item: 'Item',
  item_variant: 'Item do catálogo',
  sku: 'Item do catálogo',
  cart: 'Carrinho',
  account: 'Conta',
  customer: 'Cliente',
  lead: 'Lead',
  opportunity: 'Oportunidade',
  loan: 'Empréstimo',
  card: 'Cartão',
  vehicle: 'Veículo',
  property: 'Imóvel',
  exchange: 'Troca',
  return: 'Devolução',
  complaint: 'Reclamação',
  process: 'Processo',
  document: 'Documento',
}
export const objectType = (t: string | null | undefined) => (t ? (OBJECT_TYPES[t] ?? t) : 'objeto')

/** The fields of an object's state, as the systems name them, in words. */
const STATE_FIELDS: Record<string, string> = {
  state: 'Estado',
  status: 'Situação',
  last_event_type: 'Último evento',
  event_type: 'Evento',
  lines: 'Itens',
  items: 'Itens',
  line_id: 'Linha',
  ref: 'Item',
  item_variant: 'Item',
  sku: 'Código do item',
  product: 'Produto',
  name: 'Nome',
  title: 'Título',
  description: 'Descrição',
  quantity: 'Quantidade',
  qty: 'Quantidade',
  amount: 'Valor',
  value: 'Valor',
  total: 'Total',
  subtotal: 'Subtotal',
  discount: 'Desconto',
  shipping: 'Frete',
  freight: 'Frete',
  tax: 'Impostos',
  fee: 'Taxa',
  price: 'Preço',
  unit_price: 'Preço unitário',
  currency: 'Moeda',
  due_date: 'Vencimento',
  due_at: 'Vencimento',
  paid_at: 'Pago em',
  issued_at: 'Emitida em',
  delivery_estimate: 'Previsão de entrega',
  delivered_at: 'Entregue em',
  shipped_at: 'Enviado em',
  cancelled_at: 'Cancelado em',
  created_at: 'Criado em',
  updated_at: 'Atualizado em',
  closed_at: 'Fechado em',
  scheduled_at: 'Agendado para',
  tracking_code: 'Código de rastreio',
  tracking: 'Rastreio',
  carrier: 'Transportadora',
  address: 'Endereço',
  payment_method: 'Forma de pagamento',
  installments: 'Parcelas',
  channel: 'Canal',
  store: 'Loja',
  priority: 'Prioridade',
  category: 'Categoria',
  subject: 'Assunto',
  reason: 'Motivo',
  assignee: 'Responsável',
  owner: 'Responsável',
  customer_ref: 'Referência do cliente',
  order_ref: 'Pedido',
  invoice_ref: 'Fatura',
  notes: 'Observações',
}
export const stateField = (k: string): Labeled => labeled(STATE_FIELDS, k)
/** Fields a person never needs: tokens, hashes and ids the systems use between themselves. */
export const technicalField = (k: string) => /(^|_)(token|hash|signature|secret|checksum|etag|idempotency)(_|$)|_key$|^id$|_id$/.test(k)
/** Fields that carry money, formatted in the currency of the object (BRL when the object says none). */
export const moneyField = (k: string) => /(^|_)(amount|total|subtotal|discount|shipping|freight|tax|fee|price|value|paid|balance|credit|debit|refund|cost)(_|$)/.test(k)

/** The states and statuses the systems send, in words. */
const STATE_VALUES: Record<string, string> = {
  new: 'Novo',
  created: 'Criado',
  draft: 'Rascunho',
  open: 'Em aberto',
  opened: 'Aberto',
  pending: 'Pendente',
  waiting: 'Aguardando',
  processing: 'Em processamento',
  in_progress: 'Em andamento',
  confirmed: 'Confirmado',
  approved: 'Aprovado',
  rejected: 'Recusado',
  paid: 'Pago',
  unpaid: 'Não pago',
  overdue: 'Vencido',
  issued: 'Emitida',
  credited: 'Creditada',
  refunded: 'Reembolsado',
  partially_refunded: 'Reembolsado em parte',
  shipped: 'Enviado',
  in_transit: 'Em trânsito',
  out_for_delivery: 'Saiu para entrega',
  delivered: 'Entregue',
  returned: 'Devolvido',
  exchanged: 'Trocado',
  cancelled: 'Cancelado',
  canceled: 'Cancelado',
  closed: 'Fechado',
  resolved: 'Resolvido',
  escalated: 'Escalado',
  done: 'Concluído',
  completed: 'Concluído',
  failed: 'Falhou',
  expired: 'Expirado',
  active: 'Ativo',
  inactive: 'Inativo',
  suspended: 'Suspenso',
  scheduled: 'Agendado',
  rescheduled: 'Reagendado',
  on_hold: 'Em espera',
  blocked: 'Bloqueado',
  disputed: 'Contestado',
  lost: 'Extraviado',
}
export const stateValue = (v: string): Labeled => labeled(STATE_VALUES, v)

/** What happened to an object, `type.what`, as the systems send it (the `last_event_type` and the event types of the mappings). */
const EVENT_TYPES: Record<string, string> = {
  'order.created': 'Pedido criado',
  'order.placed': 'Pedido feito',
  'order.confirmed': 'Pedido confirmado',
  'order.paid': 'Pedido pago',
  'order.processing': 'Pedido em processamento',
  'order.shipped': 'Pedido enviado',
  'order.delivered': 'Pedido entregue',
  'order.returned': 'Pedido devolvido',
  'order.cancelled': 'Pedido cancelado',
  'order.canceled': 'Pedido cancelado',
  'order.updated': 'Pedido atualizado',
  'order.exchanged': 'Pedido trocado',
  'order.refunded': 'Pedido reembolsado',
  'invoice.created': 'Fatura criada',
  'invoice.issued': 'Fatura emitida',
  'invoice.paid': 'Fatura paga',
  'invoice.overdue': 'Fatura vencida',
  'invoice.credited': 'Fatura creditada',
  'invoice.cancelled': 'Fatura cancelada',
  'invoice.canceled': 'Fatura cancelada',
  'invoice.disputed': 'Fatura contestada',
  'payment.received': 'Pagamento recebido',
  'payment.failed': 'Pagamento falhou',
  'payment.refunded': 'Pagamento reembolsado',
  'refund.issued': 'Reembolso emitido',
  'refund.completed': 'Reembolso concluído',
  'shipment.created': 'Entrega criada',
  'shipment.shipped': 'Entrega enviada',
  'shipment.in_transit': 'Entrega em trânsito',
  'shipment.delivered': 'Entrega concluída',
  'shipment.failed': 'Entrega falhou',
  'shipment.returned': 'Entrega devolvida',
  'ticket.opened': 'Chamado aberto',
  'ticket.created': 'Chamado aberto',
  'ticket.updated': 'Chamado atualizado',
  'ticket.escalated': 'Chamado escalado',
  'ticket.resolved': 'Chamado resolvido',
  'ticket.closed': 'Chamado fechado',
  'ticket.reopened': 'Chamado reaberto',
  'exchange.requested': 'Troca pedida',
  'exchange.completed': 'Troca concluída',
  'return.requested': 'Devolução pedida',
  'return.received': 'Devolução recebida',
  'return.completed': 'Devolução concluída',
  'appointment.scheduled': 'Agendamento marcado',
  'appointment.rescheduled': 'Agendamento remarcado',
  'appointment.cancelled': 'Agendamento cancelado',
  'appointment.completed': 'Agendamento concluído',
  'subscription.created': 'Assinatura criada',
  'subscription.renewed': 'Assinatura renovada',
  'subscription.cancelled': 'Assinatura cancelada',
  'subscription.suspended': 'Assinatura suspensa',
  'contract.signed': 'Contrato assinado',
  'contract.cancelled': 'Contrato cancelado',
  'proposal.sent': 'Proposta enviada',
  'proposal.accepted': 'Proposta aceita',
  'proposal.rejected': 'Proposta recusada',
  'claim.opened': 'Sinistro aberto',
  'claim.approved': 'Sinistro aprovado',
  'claim.denied': 'Sinistro negado',
  'claim.closed': 'Sinistro fechado',
}
/** `order.delivered` in words; an unknown pair falls back to the type and the state when both are known. */
export const eventType = (t: string): Labeled => {
  if (EVENT_TYPES[t]) return { label: EVENT_TYPES[t]!, raw: t }
  const dot = t.indexOf('.')
  if (dot > 0) {
    const noun = OBJECT_TYPES[t.slice(0, dot)]
    const verb = STATE_VALUES[t.slice(dot + 1)]
    if (noun && verb) return { label: `${noun}: ${verb.toLowerCase()}`, raw: t }
  }
  return { label: t, raw: null }
}

/** What an agent did in a system (the `operation` of an action), in words. */
const OPERATIONS: Record<string, string> = {
  'invoice.credit': 'Crédito na fatura',
  'invoice.cancel': 'Cancelamento da fatura',
  'invoice.reissue': 'Segunda via da fatura',
  'invoice.extend': 'Prazo da fatura estendido',
  'order.cancel': 'Cancelamento do pedido',
  'order.exchange': 'Troca do pedido',
  'order.return': 'Devolução do pedido',
  'order.update': 'Pedido alterado',
  'order.create': 'Pedido criado',
  'order.reship': 'Reenvio do pedido',
  'refund.issue': 'Reembolso emitido',
  'payment.retry': 'Nova tentativa de pagamento',
  'payment.link': 'Link de pagamento enviado',
  'shipment.reschedule': 'Entrega reagendada',
  'shipment.redirect': 'Entrega redirecionada',
  'ticket.open': 'Chamado aberto',
  'ticket.close': 'Chamado fechado',
  'ticket.reopen': 'Chamado reaberto',
  'ticket.escalate': 'Chamado escalado',
  'ticket.update': 'Chamado atualizado',
  'appointment.schedule': 'Agendamento marcado',
  'appointment.reschedule': 'Agendamento remarcado',
  'appointment.cancel': 'Agendamento cancelado',
  'subscription.cancel': 'Assinatura cancelada',
  'subscription.pause': 'Assinatura pausada',
  'subscription.change': 'Plano alterado',
  'discount.apply': 'Desconto aplicado',
  'coupon.issue': 'Cupom emitido',
  'address.update': 'Endereço atualizado',
  'contact.update': 'Contato atualizado',
  'crm.note': 'Nota no CRM',
  'crm.update': 'CRM atualizado',
}
export const operation = (op: string): Labeled => labeled(OPERATIONS, op)

/** The intent of an episode (domain/extract). */
export const intent = (i: string): Labeled =>
  labeled(
    {
      request: 'Pedido',
      question: 'Pergunta',
      complaint: 'Reclamação',
      purchase: 'Compra',
      cancel: 'Cancelamento',
      cancellation: 'Cancelamento',
      information: 'Informação',
      feedback: 'Opinião',
      support: 'Suporte',
      exchange: 'Troca',
      return: 'Devolução',
      schedule: 'Agendamento',
      payment: 'Pagamento',
      follow_up: 'Acompanhamento',
      other: 'Outro',
      unknown: 'Não identificado',
    },
    i,
  )

/** The outcome of an episode. */
export const outcome = (o: string): Labeled =>
  labeled(
    {
      resolved: 'Resolvido',
      unresolved: 'Não resolvido',
      partial: 'Resolvido em parte',
      escalated: 'Transferido para uma pessoa',
      transferred: 'Transferido',
      pending: 'Em andamento',
      abandoned: 'Abandonada pelo cliente',
      dropped: 'Caiu',
      scheduled: 'Agendado',
      sold: 'Venda feita',
      no_sale: 'Sem venda',
      unknown: 'Sem desfecho registrado',
      other: 'Outro',
    },
    o,
  )

/** The category of an episode, a fact or an item of the policy; the extraction writes some in Portuguese already. */
const CATEGORIES: Record<string, string> = {
  uncategorized: 'Sem categoria',
  other: 'Outro',
  delivery: 'Entrega',
  shipping: 'Envio',
  exchange: 'Troca',
  return: 'Devolução',
  refund: 'Reembolso',
  billing: 'Cobrança',
  payment: 'Pagamento',
  invoice: 'Fatura',
  order: 'Pedido',
  orders: 'Pedidos',
  product: 'Produto',
  pricing: 'Preço',
  promotion: 'Promoção',
  warranty: 'Garantia',
  account: 'Conta',
  identity: 'Identidade',
  technical: 'Técnico',
  cancellation: 'Cancelamento',
  complaint: 'Reclamação',
  sales: 'Vendas',
  support: 'Atendimento',
  scheduling: 'Agendamento',
  appointment: 'Agendamento',
  contact: 'Contato',
  preference: 'Preferência',
  health: 'Saúde',
  finance: 'Financeiro',
  contract: 'Contrato',
  address: 'Endereço',
  family: 'Família',
  work: 'Trabalho',
  personal: 'Dados pessoais',
  consent: 'Consentimento',
  legal: 'Jurídico',
  fraud: 'Fraude',
  feedback: 'Opinião',
  loyalty: 'Fidelidade',
}
export const category = (c: string | null | undefined): Labeled => (c ? labeled(CATEGORIES, c) : { label: 'sem categoria', raw: null })

/** What a fact is about (its predicate), in words; the extraction and the mappings write the keys. */
const PREDICATES: Record<string, string> = {
  name: 'Nome',
  first_name: 'Primeiro nome',
  preferred_name: 'Como prefere ser chamado',
  email: 'E-mail',
  phone: 'Telefone',
  address: 'Endereço',
  city: 'Cidade',
  state: 'Estado',
  postal_code: 'CEP',
  country: 'País',
  birth_date: 'Data de nascimento',
  birthday: 'Aniversário',
  age: 'Idade',
  gender: 'Gênero',
  occupation: 'Profissão',
  company: 'Empresa',
  document: 'Documento',
  preferred_channel: 'Canal preferido',
  preferred_language: 'Idioma preferido',
  preferred_store: 'Loja preferida',
  contact_time: 'Melhor horário para contato',
  contact_preference: 'Preferência de contato',
  shoe_size: 'Número do calçado',
  clothing_size: 'Tamanho de roupa',
  size: 'Tamanho',
  payment_method: 'Forma de pagamento',
  delivery_preference: 'Preferência de entrega',
  delivery_address: 'Endereço de entrega',
  billing_address: 'Endereço de cobrança',
  plan: 'Plano',
  loyalty_tier: 'Nível de fidelidade',
  budget: 'Orçamento',
  interest: 'Interesse',
  vehicle: 'Veículo',
  pet: 'Animal de estimação',
  children: 'Filhos',
  marital_status: 'Estado civil',
  allergies: 'Alergias',
  health_condition: 'Condição de saúde',
  medication: 'Medicação',
  diet: 'Dieta',
  income: 'Renda',
  credit_limit: 'Limite de crédito',
  due_day: 'Dia de vencimento',
  product_owned: 'Produto que tem',
  last_purchase: 'Última compra',
  cancellation_reason: 'Motivo do cancelamento',
  complaint_topic: 'Assunto da reclamação',
  order_problem: 'Problema no pedido',
}
export const predicate = (p: string): Labeled => labeled(PREDICATES, p)

/** The role of a person in an organization (links of a profile). */
export const linkRole = (r: string): Labeled =>
  labeled(
    {
      owner: 'Titular',
      buyer: 'Comprador',
      payer: 'Pagador',
      admin: 'Administrador',
      manager: 'Gestor',
      employee: 'Funcionário',
      member: 'Membro',
      driver: 'Motorista',
      technical_contact: 'Contato técnico',
      billing_contact: 'Contato de cobrança',
      legal_representative: 'Representante legal',
      contact: 'Contato',
      partner: 'Parceiro',
      agent: 'Representante',
      guardian: 'Responsável',
      dependent: 'Dependente',
    },
    r,
  )

/** The types of the fields of a declared object type (config object-types). */
export const fieldType = (t: string) =>
  pick(
    {
      string: 'texto',
      text: 'texto',
      number: 'número',
      integer: 'número inteiro',
      float: 'número',
      decimal: 'número',
      boolean: 'sim ou não',
      bool: 'sim ou não',
      date: 'data',
      datetime: 'data e hora',
      timestamp: 'data e hora',
      money: 'valor em dinheiro',
      currency: 'moeda',
      enum: 'lista de valores',
      array: 'lista',
      list: 'lista',
      object: 'objeto',
      ref: 'referência',
      reference: 'referência',
      id: 'identificador',
      url: 'endereço',
      email: 'e-mail',
      phone: 'telefone',
    },
    t,
  )

/* ---------------- Data issues (the server's `domain/turns/issues.py`): a title and what it means ---------------- */

const DATA_ISSUES: Record<string, { title: string; means: string }> = {
  null_field: { title: 'Campo vazio', means: 'Um campo que a ferramenta de um agente mostrou veio vazio.' },
  out_of_vocabulary: { title: 'Valor fora do vocabulário', means: 'Um valor que o tipo do objeto não conhece.' },
  stale_source: { title: 'Valor desatualizado', means: 'Um valor mais antigo do que o tipo aceita para responder.' },
  coverage_drop: { title: 'Campo preenchido menos vezes', means: 'Um campo chega preenchido menos vezes do que antes.' },
  invalid_value: { title: 'Valor fora do formato', means: 'Um valor que não tem o formato do campo.' },
  divergence: { title: 'Fontes discordam', means: 'Duas fontes de um mesmo valor dizem coisas diferentes.' },
  drift: { title: 'Campo novo no tráfego', means: 'Um campo novo chegou e já é servido aos agentes como veio.' },
  rule_conflict: { title: 'Regras em conflito', means: 'Duas regras decidem a mesma coisa de jeitos diferentes.' },
  type_undeclared: { title: 'Tipo novo nas ferramentas', means: 'As ferramentas mostraram objetos de um tipo novo.' },
  field_held: { title: 'Campo retido', means: 'Um campo novo que pode ser dado pessoal ou texto livre fica fora dos agentes até alguém decidir.' },
  budget_reached: {
    title: 'Teto de IA do dia atingido',
    means: 'O gasto de IA do dia chegou ao teto: a memória espera o dia seguinte para continuar, sem perder nada do que chegou.',
  },
  row_failed: {
    title: 'Lote guardado para nova tentativa',
    means: 'Um lote de dados não pôde ser guardado depois de várias tentativas. Ele fica guardado, sem nenhuma perda, e pode ser tentado de novo nesta tela.',
  },
  inference_limit: {
    title: 'Limite da inferência',
    means: 'O que os sistemas mandam passou de um limite do que a Niadra infere sozinha, ou trouxe um nome que ela não usa. Nada se perde: o que passou é guardado e servido aos agentes como chegou.',
  },
}

/**
 * What an `inference_limit` issue names in its `field` (the server's `app/observed_config.py`): the limit of
 * what Niadra infers on its own that was reached, or the kind of name it could not use.
 */
export const INFERENCE_LIMITS: Record<string, string> = {
  types: 'Tipos demais para inferir: os objetos de tipos novos são guardados e servidos como chegam, sem um tipo inferido',
  fields_or_states: 'Campos ou estados demais neste tipo: os novos são guardados e servidos como chegam, sem inferência',
  operations: 'Operações diferentes demais: as novas não entram na contagem, e os eventos que as trazem são guardados inteiros',
  operations_not_offered: 'Operações confirmadas além do limite: elas ficam contadas e não são oferecidas como operações observadas',
  unsafe_types: 'Um nome de tipo fora do formato de identificador: os objetos são guardados, mas o tipo não é inferido',
  unsafe_names: 'Nomes de campo ou estado fora do formato de identificador neste tipo: os valores são guardados, mas esses nomes não são inferidos',
  unreadable_values: 'Valores que a inferência não conseguiu ler: são guardados como chegaram e contados',
  left_out: 'Um tipo inferido que não se sustenta com os demais: ele fica fora do registro, e os tipos declarados continuam servidos',
}

export const inferenceLimit = (what: string) => INFERENCE_LIMITS[what] ?? `Um limite da inferência: ${what}`

export const dataIssue = (kind: string) => DATA_ISSUES[kind] ?? { title: kind, means: 'Um aviso sobre os dados que chegam.' }

export const intakeKind = (k: string) => pick({ events: 'Eventos', turns: 'Conversas gravadas' }, k)
