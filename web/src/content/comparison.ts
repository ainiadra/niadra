/**
 * The feature comparison of /comparativo: what Niadra does against what each memory system documents.
 *
 * Every cell is one of four marks, with the page it was read on and the date it was read. "yes" and
 * "no" only where the vendor's own documentation says so; "partial" where it documents part of the row
 * (the note says which part); "undocumented" where the documentation read says nothing, which is not
 * the same as "no". The notes are the reader's evidence, in both languages. Nothing here is measured:
 * the measured comparison is /benchmark, which this page links to.
 *
 * Reading each vendor's documentation again on a new date is the only way to change a cell.
 */

export type Mark = 'yes' | 'no' | 'partial' | 'undocumented'

type Text2 = { pt: string; en: string }

export type Cell = {
  v: Mark
  /** The page read, on the vendor's own site. */
  src: string
  /** The day the page was read, ISO. */
  read: string
  note?: Text2
}

/** The price row: the vendor's published unit price, quoted, never converted. */
export type PriceCell = { text: Text2; src: string; read: string }

export const SYSTEMS = [
  { key: 'niadra', name: 'Niadra', logo: '/benchmark/logos/niadra.svg' },
  { key: 'mem0_oss', name: 'Mem0 (código aberto)', nameEn: 'Mem0 (open source)', logo: '/benchmark/logos/mem0.svg' },
  { key: 'mem0_platform', name: 'Mem0 Platform', logo: '/benchmark/logos/mem0.svg' },
  { key: 'zep', name: 'Zep', logo: '/benchmark/logos/zep.png' },
  { key: 'letta', name: 'Letta', logo: '/benchmark/logos/letta.png' },
  { key: 'supermemory', name: 'Supermemory', logo: '/benchmark/logos/supermemory.svg' },
  { key: 'hindsight', name: 'Hindsight', logo: '/benchmark/logos/hindsight.png' },
  { key: 'honcho', name: 'Honcho', logo: '/benchmark/logos/honcho.svg' },
  { key: 'cognee', name: 'Cognee', logo: '/benchmark/logos/cognee.png' },
  { key: 'agentcore', name: 'Amazon Bedrock AgentCore Memory', logo: '/benchmark/logos/aws.png' },
  { key: 'twilio', name: 'Twilio Conversation Memory', logo: '/integrations/logos/twilio.png' },
] as const

export type SystemKey = (typeof SYSTEMS)[number]['key']

export type Row = {
  key: string
  label: Text2
  /** What a "yes" means on this row, so "partial" can be read. */
  means: Text2
  cells: Record<SystemKey, Cell>
}

const READ = '2026-10-01'

// The pages of each system that more than one cell cites.
const N = 'https://docs.niadra.com'
const MEM0_SCOPE = 'https://docs.mem0.ai/platform/features/entity-scoped-memory'
const MEM0_README = 'https://github.com/mem0ai/mem0'
const MEM0_OSS_SERVER = 'https://docs.mem0.ai/open-source/features/rest-api'
const MEM0_OVERVIEW = 'https://docs.mem0.ai/platform/overview'
const MEM0_VS = 'https://docs.mem0.ai/platform/platform-vs-oss'
const MEM0_VOICE = 'https://docs.mem0.ai/integrations/livekit'
const MEM0_RESEARCH = 'https://mem0.ai/research'
const ZEP_USERS = 'https://help.getzep.com/users-and-user-graphs'
const ZEP_GOV = 'https://help.getzep.com/governance'
const ZEP_CONCEPTS = 'https://help.getzep.com/concepts'
const ZEP_MCP = 'https://help.getzep.com/memory-mcp-server'
const LETTA_MEMORY = 'https://docs.letta.com/agent-sdk/memory'
const LETTA_SHARED = 'https://docs.letta.com/concepts/shared-memory'
const LETTA_PRICING = 'https://docs.letta.com/pricing'
const SM_PROFILES = 'https://supermemory.ai/docs/concepts/user-profiles'
const SM_GRAPH = 'https://supermemory.ai/docs/concepts/graph-memory'
const SM_AUTH = 'https://supermemory.ai/docs/authentication'
const HS_BANKS = 'https://docs.hindsight.vectorize.io/memory-banks'
const HS_RETAIN = 'https://docs.hindsight.vectorize.io/retain'
const HS_KEYS = 'https://docs.hindsight.vectorize.io/api-keys'
const HS_README = 'https://github.com/vectorize-io/hindsight'
const HO_ARCH = 'https://honcho.dev/docs/v3/documentation/core-concepts/architecture'
const HO_DELETE = 'https://honcho.dev/docs/v3/documentation/features/advanced/deleting-data'
const CG_PERMS = 'https://docs.cognee.ai/core-concepts/multi-user-mode/permissions-system/overview'
const CG_TIME = 'https://docs.cognee.ai/guides/time-awareness'
const CG_README = 'https://github.com/topoteretes/cognee'
const AC = 'https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide'
const AC_ORG = `${AC}/memory-organization.html`
const AC_HOME = `${AC}/memory.html`
const AC_STRATEGIES = `${AC}/memory-strategies.html`
const AC_CROSS = `${AC}/memory-cross-account-access.html`
const TW_HOME = 'https://www.twilio.com/docs/conversations/memory'
const TW_API = 'https://www.twilio.com/docs/conversations/memory/api-quickstart'

const yes = (src: string, note?: Text2): Cell => ({ v: 'yes', src, read: READ, note })
const no = (src: string, note?: Text2): Cell => ({ v: 'no', src, read: READ, note })
const part = (src: string, note: Text2): Cell => ({ v: 'partial', src, read: READ, note })
const undoc = (src: string, note?: Text2): Cell => ({ v: 'undocumented', src, read: READ, note })

export const ROWS: readonly Row[] = [
  {
    key: 'identity',
    label: { pt: 'Identidade entre canais', en: 'Identity across channels' },
    means: {
      pt: 'A memória descobre que o telefone, o WhatsApp, o e-mail e o ID de cada sistema são da mesma pessoa. A aplicação não precisa entregar um ID pronto.',
      en: 'The memory works out that the phone, the WhatsApp number, the e-mail and each system’s ID belong to the same person. The application does not hand over a ready-made ID.',
    },
    cells: {
      niadra: yes(`${N}/concepts/identity`, { pt: 'Identificadores por tipo, com peso, união reversível e nível de verificação por conversa.', en: 'Identifiers by type, with a weight, reversible merges and a verification level per conversation.' }),
      mem0_oss: no(MEM0_README, { pt: 'A memória é guardada pelo user_id que a aplicação envia.', en: 'Memory is kept under the user_id the application sends.' }),
      mem0_platform: no(MEM0_SCOPE, { pt: 'user_id, agent_id, app_id e run_id vêm da aplicação.', en: 'user_id, agent_id, app_id and run_id come from the application.' }),
      zep: no(ZEP_USERS, { pt: 'O user_id "mapeia o seu ID interno de usuário".', en: 'The user_id "maps to your internal User ID".' }),
      letta: no('https://docs.letta.com/api-reference/identities/create', { pt: 'Uma identidade tem a chave que a aplicação escolhe (identifier_key).', en: 'An identity carries the key the application chooses (identifier_key).' }),
      supermemory: no(SM_PROFILES, { pt: 'Um perfil por containerTag, que a aplicação define.', en: 'One profile per containerTag, set by the application.' }),
      hindsight: no(HS_BANKS, { pt: 'Um banco de memória por usuário, projeto ou agente, isolado dos outros.', en: 'One memory bank per user, project or agent, isolated from the others.' }),
      honcho: no(HO_ARCH, { pt: 'Cada peer tem o identificador que a aplicação dá.', en: 'Each peer carries the identifier the application gives it.' }),
      cognee: no(CG_PERMS, { pt: 'Usuários e tenants vêm da aplicação; a memória é por dataset.', en: 'Users and tenants come from the application; memory is per dataset.' }),
      agentcore: no(AC_ORG, { pt: 'O actorId é definido pela aplicação em cada evento.', en: 'The actorId is set by the application on every event.' }),
      twilio: yes('https://www.twilio.com/docs/conversations/memory/identity-resolution', { pt: 'Telefone, e-mail, WhatsApp e ID de usuário viram um perfil, só para conversas que passam pelos canais da Twilio.', en: 'Phone, e-mail, WhatsApp and user ID become one profile, only for conversations that flow through Twilio’s channels.' }),
    },
  },
  {
    key: 'shared',
    label: { pt: 'Uma memória para agentes de fornecedores diferentes', en: 'One memory for agents from different vendors' },
    means: {
      pt: 'Agentes de fornecedores diferentes leem e alimentam a mesma memória do cliente, cada um com a própria permissão, e o corte de um fornecedor não apaga nada.',
      en: 'Agents from different vendors read and feed the same customer memory, each with its own permission, and cutting one vendor off erases nothing.',
    },
    cells: {
      niadra: yes(`${N}/integrations/overview`, { pt: 'Cada fornecedor tem credencial e permissão próprias; nenhum lê a memória nem os números do outro.', en: 'Each vendor has its own credential and permission; none reads the other’s memory or numbers.' }),
      mem0_oss: part(MEM0_README, { pt: 'Qualquer agente com a chave lê o mesmo user_id. Sem permissão por fornecedor.', en: 'Any agent with the key reads the same user_id. No permission per vendor.' }),
      mem0_platform: part(MEM0_SCOPE, { pt: 'Vários agentes filtram o mesmo user_id por agent_id. Sem permissão por fornecedor.', en: 'Several agents filter the same user_id by agent_id. No permission per vendor.' }),
      zep: part(ZEP_MCP, { pt: 'O agente da empresa e o cliente MCP da pessoa compartilham o mesmo grafo de usuário.', en: 'The company’s agent and the person’s MCP client share one user graph.' }),
      letta: part(LETTA_SHARED, { pt: 'Blocos compartilhados entre agentes Letta, por repositório git. Outros fornecedores não são citados.', en: 'Blocks shared between Letta agents, through git repositories. Other vendors are not mentioned.' }),
      supermemory: part(SM_AUTH, { pt: 'Chaves restritas a containerTags; qualquer agente com a chave lê o perfil.', en: 'Keys restricted to containerTags; any agent with the key reads the profile.' }),
      hindsight: part(HS_KEYS, { pt: 'Chaves restritas a bancos; qualquer agente com a chave lê o banco.', en: 'Keys restricted to banks; any agent with the key reads the bank.' }),
      honcho: part(HO_ARCH, { pt: 'Vários peers numa sessão, com a configuração de quem observa quem.', en: 'Several peers in a session, with a setting for who observes whom.' }),
      cognee: part(CG_PERMS, { pt: 'Datasets compartilhados por lista de permissão (ler, escrever, apagar, compartilhar).', en: 'Datasets shared through an access list (read, write, delete, share).' }),
      agentcore: part(AC_CROSS, { pt: 'Qualquer principal do IAM, até de outra conta AWS, lê a mesma memória.', en: 'Any IAM principal, even from another AWS account, reads the same memory.' }),
      twilio: part(TW_API, { pt: 'Qualquer agente chama a API de recall com a chave da conta; a conversa precisa passar pela Twilio.', en: 'Any agent calls the recall API with the account key; the conversation must flow through Twilio.' }),
    },
  },
  {
    key: 'systems',
    label: { pt: 'Eventos do CRM e do ERP', en: 'Events from the CRM and the ERP' },
    means: {
      pt: 'Pedido, fatura e ticket entram na memória pelos eventos que o sistema já emite, ligados ao cliente certo pelo ID daquele sistema.',
      en: 'Orders, bills and tickets enter the memory through the events the system already emits, tied to the right customer by that system’s ID.',
    },
    cells: {
      niadra: yes(`${N}/concepts/systems`, { pt: 'Webhook com mapeamento versionado, lote e arquivo; sem IA no caminho.', en: 'Webhook with a versioned mapping, batch and file; no AI in the path.' }),
      mem0_oss: undoc('https://docs.mem0.ai/open-source/overview'),
      mem0_platform: undoc('https://docs.mem0.ai/platform/features/webhooks', { pt: 'Os webhooks da Mem0 são de saída: avisam quando uma memória muda.', en: 'Mem0’s webhooks are outbound: they notify when a memory changes.' }),
      zep: part('https://help.getzep.com/adding-business-data', { pt: 'Registros de negócio entram como JSON por graph.add; sem webhook de entrada nem ID por sistema.', en: 'Business records go in as JSON through graph.add; no inbound webhook and no per-system ID.' }),
      letta: undoc(LETTA_MEMORY),
      supermemory: undoc('https://supermemory.ai/docs/connectors/google-drive', { pt: 'Os conectores são de fontes de conhecimento: Drive, Gmail, Notion, OneDrive.', en: 'The connectors are knowledge sources: Drive, Gmail, Notion, OneDrive.' }),
      hindsight: undoc(HS_RETAIN),
      honcho: undoc('https://honcho.dev/docs/v3/documentation/features/advanced/webhooks', { pt: 'Os webhooks do Honcho são de saída: avisam quando a fila esvazia.', en: 'Honcho’s webhooks are outbound: they notify when the queue empties.' }),
      cognee: undoc('https://www.cognee.ai/pricing', { pt: 'As fontes de dados são Slack, Notion, Linear e Google Drive.', en: 'The data sources are Slack, Notion, Linear and Google Drive.' }),
      agentcore: undoc(`${AC}/memory-terminology.html`, { pt: 'Um evento é uma mensagem, uma ação do sistema ou uma chamada de ferramenta da conversa.', en: 'An event is a message, a system action or a tool call of the conversation.' }),
      twilio: part(TW_API, { pt: 'Perfis e traços entram em lote por importação; sem evento de pedido ou fatura.', en: 'Profiles and traits go in through bulk import; no order or bill event.' }),
    },
  },
  {
    key: 'state',
    label: { pt: 'Estado de negócio com tipo e data', en: 'Typed business state with freshness' },
    means: {
      pt: 'Um pedido, uma fatura, uma cotação: cada valor chega ao agente com o tipo, a data em que foi atualizado, a fonte e o que não se pode afirmar com ele.',
      en: 'An order, a bill, a quote: every value reaches the agent with its type, the date it was last updated, the source and what may not be stated with it.',
    },
    cells: {
      niadra: yes(`${N}/concepts/object-types`, { pt: 'Tipos inferidos do tráfego, declarados ou derivados do banco; validade por campo calculada na leitura.', en: 'Types inferred from traffic, declared or derived from the database; freshness per field computed on the read.' }),
      mem0_oss: no(MEM0_README, { pt: 'A memória é texto extraído da conversa.', en: 'Memory is text extracted from the conversation.' }),
      mem0_platform: part('https://docs.mem0.ai/platform/features/timestamp', { pt: 'Data de criação e validade por memória; sem objeto tipado nem validade por campo.', en: 'A creation date and an expiry per memory; no typed object and no freshness per field.' }),
      zep: part('https://help.getzep.com/facts', { pt: 'Fatos com valid_at e invalid_at; sem objeto tipado por campo.', en: 'Facts with valid_at and invalid_at; no typed object per field.' }),
      letta: no(LETTA_MEMORY, { pt: 'Blocos de memória em texto, editados pelo agente.', en: 'Memory blocks in text, edited by the agent.' }),
      supermemory: part(SM_GRAPH, { pt: 'Fatos com relações de atualização e vencimento; sem objeto tipado por campo.', en: 'Facts with update relations and expiry; no typed object per field.' }),
      hindsight: no(HS_RETAIN, { pt: 'Itens em texto, com carimbo de tempo opcional.', en: 'Text items, with an optional timestamp.' }),
      honcho: no(HO_ARCH, { pt: 'Representações e conclusões sobre a pessoa, em texto.', en: 'Representations and conclusions about the person, in text.' }),
      cognee: part(CG_TIME, { pt: 'Eventos com carimbo de tempo e validade de fato; sem objeto de negócio por campo.', en: 'Events with timestamps and fact validity; no business object per field.' }),
      agentcore: no(AC_STRATEGIES, { pt: 'Fatos, preferências e resumos extraídos da conversa.', en: 'Facts, preferences and summaries extracted from the conversation.' }),
      twilio: part(TW_HOME, { pt: 'Memória factual em traços chave e valor; sem validade por campo.', en: 'Factual memory as key-value traits; no freshness per field.' }),
    },
  },
  {
    key: 'claims',
    label: { pt: 'Conferência do que o agente afirma', en: 'The claim check' },
    means: {
      pt: 'Antes de a resposta sair, cada preço, data e número que o agente vai dizer é conferido contra o que ele consultou. O que não tem origem é sinalizado.',
      en: 'Before the answer goes out, every price, date and number the agent is about to say is checked against what it consulted. What has no source is flagged.',
    },
    cells: {
      niadra: yes(`${N}/concepts/claims`, { pt: 'Contrato por categoria; conferência no processo do agente, sem IA a mais.', en: 'A contract per category; the check runs in the agent’s process, with no extra AI.' }),
      mem0_oss: undoc(MEM0_README),
      mem0_platform: undoc(MEM0_OVERVIEW),
      zep: undoc(ZEP_CONCEPTS),
      letta: undoc(LETTA_MEMORY),
      supermemory: undoc(SM_GRAPH),
      hindsight: undoc(HS_RETAIN),
      honcho: undoc(HO_ARCH),
      cognee: undoc(CG_README),
      agentcore: undoc(AC_HOME),
      twilio: undoc(TW_HOME),
    },
  },
  {
    key: 'coordination',
    label: { pt: 'Coordenação entre agentes', en: 'Coordination between agents' },
    means: {
      pt: 'Antes de agir, o agente pergunta quem está com o cliente e o que já foi feito. Um fato de negócio gera um efeito só.',
      en: 'Before acting, the agent asks who holds the customer and what was already done. One business fact causes one effect.',
    },
    cells: {
      niadra: yes(`${N}/concepts/coordination`, { pt: 'Posse por declaração e observação, efeito uma vez só, token de contato para o gateway.', en: 'Ownership by declaration and observation, effect exactly once, a contact token for the gateway.' }),
      mem0_oss: undoc(MEM0_README),
      mem0_platform: undoc(MEM0_OVERVIEW),
      zep: undoc(ZEP_CONCEPTS),
      letta: undoc(LETTA_SHARED, { pt: 'A página de memória compartilhada não fala de trava nem de posse.', en: 'The shared memory page says nothing about locks or ownership.' }),
      supermemory: undoc(SM_GRAPH),
      hindsight: undoc(HS_BANKS),
      honcho: undoc(HO_ARCH),
      cognee: undoc(CG_README),
      agentcore: undoc(AC_HOME),
      twilio: undoc(TW_HOME),
    },
  },
  {
    key: 'turns',
    label: { pt: 'Registro de turno e replay', en: 'Turn records and replay' },
    means: {
      pt: 'O que o agente leu, chamou, mostrou e afirmou em cada turno, com a versão fixada, e a reprodução de uma conversa real no seu CI.',
      en: 'What the agent read, called, showed and stated on every turn, with the build pinned, and the replay of a real conversation in your CI.',
    },
    cells: {
      niadra: yes(`${N}/concepts/turn-records`, { pt: 'Capturado no processo do agente; replay no CI com as ferramentas respondendo do registro.', en: 'Captured in the agent’s process; replay in CI with the tools answering from the record.' }),
      mem0_oss: undoc(MEM0_README),
      mem0_platform: undoc(MEM0_OVERVIEW),
      zep: undoc(ZEP_CONCEPTS),
      letta: part('https://docs.letta.com/api-reference/steps/list', { pt: 'A API de passos guarda modelo, tokens e id de rastro de cada passo; replay não é documentado.', en: 'The steps API keeps the model, tokens and trace id of each step; replay is not documented.' }),
      supermemory: undoc(SM_GRAPH),
      hindsight: undoc(HS_RETAIN),
      honcho: undoc(HO_ARCH),
      cognee: undoc(CG_README),
      agentcore: part(`${AC}/memory-observability.html`, { pt: 'Métricas e logs da extração no CloudWatch; replay não é documentado.', en: 'Extraction metrics and logs in CloudWatch; replay is not documented.' }),
      twilio: undoc(TW_HOME),
    },
  },
  {
    key: 'permissions',
    label: { pt: 'Permissão por agente e acesso por campo', en: 'Per-agent permissions and field access' },
    means: {
      pt: 'Cada agente só lê o que a finalidade dele permite, item a item, e um campo sensível só chega a quem pode vê-lo.',
      en: 'Each agent reads only what its purpose allows, item by item, and a sensitive field reaches only who may see it.',
    },
    cells: {
      niadra: yes(`${N}/concepts/privacy`, { pt: 'Negado por padrão, liberado por finalidade; categorias sensíveis fixas; acesso por campo no estado.', en: 'Denied by default, granted by purpose; fixed sensitive categories; field access on the state.' }),
      mem0_oss: part(MEM0_OSS_SERVER, { pt: 'Chave de API por usuário do servidor; sem finalidade nem campo.', en: 'An API key per server user; no purpose and no field.' }),
      mem0_platform: part('https://docs.mem0.ai/api-reference/organizations-projects', { pt: 'Papéis de leitor e dono por projeto; sem finalidade nem campo.', en: 'Reader and owner roles per project; no purpose and no field.' }),
      zep: part(ZEP_GOV, { pt: 'Políticas em chaves de API e grupos de usuários; sem finalidade nem campo.', en: 'Policies on API keys and user groups; no purpose and no field.' }),
      letta: part(LETTA_PRICING, { pt: 'Controle de acesso por papel no plano Enterprise.', en: 'Role-based access control on the Enterprise plan.' }),
      supermemory: part(SM_AUTH, { pt: 'Chaves restritas a containerTags; sem finalidade nem campo.', en: 'Keys restricted to containerTags; no purpose and no field.' }),
      hindsight: part(HS_KEYS, { pt: 'Chaves restritas a bancos; sem finalidade nem campo.', en: 'Keys restricted to banks; no purpose and no field.' }),
      honcho: part(HO_ARCH, { pt: 'Configuração de quais peers observam quais; sem finalidade nem campo.', en: 'A setting for which peers observe which; no purpose and no field.' }),
      cognee: part(CG_PERMS, { pt: 'Ler, escrever, apagar e compartilhar por dataset; sem agente nem campo.', en: 'Read, write, delete and share per dataset; no agent and no field.' }),
      agentcore: part(AC_ORG, { pt: 'Políticas do IAM por namespace e actor; sem acesso por campo.', en: 'IAM policies by namespace and actor; no field access.' }),
      twilio: undoc(TW_API, { pt: 'Autenticação pela chave da conta; sem permissão por agente.', en: 'Authentication with the account key; no permission per agent.' }),
    },
  },
  {
    key: 'receipts',
    label: { pt: 'Comprovante de quem leu o quê', en: 'Receipts of who read what' },
    means: {
      pt: 'Cada leitura, inclusive a recusada, deixa um registro de qual agente leu o quê, para quê e quando, à prova de adulteração.',
      en: 'Every read, the refused one included, leaves a record of which agent read what, for what and when, tamper-evident.',
    },
    cells: {
      niadra: yes(`${N}/concepts/receipts`, { pt: 'Comprovante por leitura, encadeado por SHA-256, com raiz diária em armazenamento de gravação única.', en: 'A receipt per read, chained by SHA-256, with a daily root in write-once storage.' }),
      mem0_oss: part(MEM0_OSS_SERVER, { pt: 'Registro recente das chamadas à API do servidor.', en: 'A recent log of the server’s API calls.' }),
      mem0_platform: part(MEM0_VS, { pt: 'Feed de eventos do projeto (add, busca, apagamento); logs de auditoria no Enterprise.', en: 'A project event feed (add, search, delete); audit logs on Enterprise.' }),
      zep: part(ZEP_GOV, { pt: 'Logs de auditoria da atividade de API e do painel; sem comprovante por leitura.', en: 'Audit logs of API and dashboard activity; no receipt per read.' }),
      letta: undoc(LETTA_PRICING),
      supermemory: undoc(SM_AUTH),
      hindsight: undoc('https://docs.hindsight.vectorize.io/security/webhooks-siem', { pt: 'O SIEM recebe só as violações do Memory Defense, com a chave que gravou.', en: 'The SIEM receives only Memory Defense violations, with the key that wrote.' }),
      honcho: undoc(HO_DELETE),
      cognee: undoc(CG_PERMS),
      agentcore: part(AC_CROSS, { pt: 'O CloudTrail registra as chamadas à API; sem comprovante encadeado por leitura.', en: 'CloudTrail logs the API calls; no chained receipt per read.' }),
      twilio: part('https://www.twilio.com/docs/conversations/memory/observability-logs', { pt: 'Logs de erros e avisos das operações, inclusive recuperações.', en: 'Error and warning logs of the operations, retrievals included.' }),
    },
  },
  {
    key: 'erasure',
    label: { pt: 'Apagamento com comprovante', en: 'Erasure with a receipt' },
    means: {
      pt: 'Apagar um cliente percorre tudo o que derivou dos dados dele e devolve um comprovante do que foi apagado.',
      en: 'Erasing a customer walks through everything derived from their data and returns a receipt of what was erased.',
    },
    cells: {
      niadra: yes(`${N}/concepts/privacy`, { pt: 'Apagamento por linhagem, com comprovante do que foi apagado.', en: 'Erasure by lineage, with a receipt of what was erased.' }),
      mem0_oss: part(MEM0_OSS_SERVER, { pt: 'Apagamento por memória ou usuário; sem comprovante.', en: 'Deletion per memory or user; no receipt.' }),
      mem0_platform: part('https://docs.mem0.ai/api-reference/entities/delete-user', { pt: 'Apagar usuário é assíncrono e devolve um event_id; sem comprovante.', en: 'Deleting a user is asynchronous and returns an event_id; no receipt.' }),
      zep: part('https://help.getzep.com/sdk-reference/user/delete', { pt: 'Apaga o usuário, as threads e o grafo; devolve uma mensagem de sucesso.', en: 'Deletes the user, the threads and the graph; returns a success message.' }),
      letta: part('https://docs.letta.com/api-reference/agents/delete', { pt: 'Apagar um agente devolve um objeto vazio.', en: 'Deleting an agent returns an empty object.' }),
      supermemory: part('https://supermemory.ai/docs/api-reference/content-management/forget-a-memory', { pt: 'Esquecer é apagamento lógico; devolve o id e o marcador forgotten.', en: 'Forgetting is a soft delete; it returns the id and the forgotten flag.' }),
      hindsight: yes('https://docs.hindsight.vectorize.io/security/data-privacy', { pt: 'Apagamento em dois passos com comprovante no ledger de eventos de privacidade; só no Enterprise.', en: 'Two-step erasure with a receipt in the privacy events ledger; Enterprise only.' }),
      honcho: part(HO_DELETE, { pt: 'Apagamento assíncrono por sessão; conclusões derivadas sobrevivem; sem comprovante.', en: 'Asynchronous deletion per session; derived conclusions survive; no receipt.' }),
      cognee: part('https://docs.cognee.ai/core-concepts/main-operations/forget', { pt: 'forget devolve ids e um status; sem comprovante.', en: 'forget returns ids and a status; no receipt.' }),
      agentcore: part('https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_DeleteMemoryRecord.html', { pt: 'Apagar um registro devolve o id dele; sem comprovante.', en: 'Deleting a record returns its id; no receipt.' }),
      twilio: part('https://www.twilio.com/docs/api/memory/v1/profile/delete-profile', { pt: 'Apagar um perfil é aceito (202) e irreversível; sem comprovante.', en: 'Deleting a profile is accepted (202) and irreversible; no receipt.' }),
    },
  },
  {
    key: 'context',
    label: { pt: 'Contexto compacto, pronto para o prompt', en: 'Prompt-ready compact context' },
    means: {
      pt: 'O agente recebe um texto pronto para o prompt, montado antes do pedido, sem IA na leitura.',
      en: 'The agent receives text ready for the prompt, assembled before the request, with no AI on the read.',
    },
    cells: {
      niadra: yes(`${N}/concepts/context`, { pt: 'Compilado quando a memória muda, por agente e tarefa, em camadas que o provedor reaproveita.', en: 'Compiled when the memory changes, per agent and task, in layers the provider reuses.' }),
      mem0_oss: part(MEM0_README, { pt: 'A busca devolve uma lista de memórias; o prompt é montado pela aplicação.', en: 'Search returns a list of memories; the application builds the prompt.' }),
      mem0_platform: part(MEM0_OVERVIEW, { pt: 'A busca devolve uma lista de memórias; o prompt é montado pela aplicação.', en: 'Search returns a list of memories; the application builds the prompt.' }),
      zep: yes('https://help.getzep.com/retrieving-context', { pt: 'Bloco de contexto montado como texto; a ingestão pode levar minutos.', en: 'A context block assembled as text; ingestion can take minutes.' }),
      letta: part(LETTA_MEMORY, { pt: 'Os blocos vivem no prompt do agente Letta, não são servidos a outro agente.', en: 'The blocks live in the Letta agent’s prompt; they are not served to another agent.' }),
      supermemory: yes(SM_PROFILES, { pt: 'Perfil estático e dinâmico, para o prompt.', en: 'A static and a dynamic profile, for the prompt.' }),
      hindsight: part('https://docs.hindsight.vectorize.io/recall', { pt: 'recall devolve memórias; reflect devolve uma resposta escrita por um modelo na leitura.', en: 'recall returns memories; reflect returns an answer a model writes on the read.' }),
      honcho: yes(HO_ARCH, { pt: 'get_context monta conclusões e mensagens recentes para o prompt.', en: 'get_context assembles conclusions and recent messages for the prompt.' }),
      cognee: part(CG_README, { pt: 'A busca responde com um modelo na hora da leitura.', en: 'Search answers with a model at read time.' }),
      agentcore: part(AC_ORG, { pt: 'A recuperação devolve registros; o prompt é montado pela aplicação.', en: 'Retrieval returns records; the application builds the prompt.' }),
      twilio: part(TW_API, { pt: 'O recall devolve observações e resumos estruturados; o prompt é montado pela aplicação.', en: 'Recall returns structured observations and summaries; the application builds the prompt.' }),
    },
  },
  {
    key: 'voice',
    label: { pt: 'Integrações de voz', en: 'Voice integrations' },
    means: {
      pt: 'Adaptador documentado para plataformas de voz: LiveKit, Pipecat, Vapi, Retell, ElevenLabs, Twilio.',
      en: 'A documented adapter for voice platforms: LiveKit, Pipecat, Vapi, Retell, ElevenLabs, Twilio.',
    },
    cells: {
      niadra: yes(`${N}/integrations/overview`, { pt: 'LiveKit, Pipecat, Vapi, Retell, ElevenLabs e Twilio, com o contexto antes do alô.', en: 'LiveKit, Pipecat, Vapi, Retell, ElevenLabs and Twilio, with the context before the hello.' }),
      mem0_oss: yes(MEM0_VOICE, { pt: 'LiveKit, ElevenLabs e Pipecat.', en: 'LiveKit, ElevenLabs and Pipecat.' }),
      mem0_platform: yes(MEM0_VOICE, { pt: 'LiveKit, ElevenLabs e Pipecat.', en: 'LiveKit, ElevenLabs and Pipecat.' }),
      zep: yes('https://help.getzep.com/memory-for-agent-frameworks', { pt: 'ElevenLabs e LiveKit.', en: 'ElevenLabs and LiveKit.' }),
      letta: yes('https://docs.letta.com/guides/voice/livekit/', { pt: 'LiveKit e Vapi, marcados como experimentais.', en: 'LiveKit and Vapi, marked experimental.' }),
      supermemory: yes('https://supermemory.ai/docs/integrations/livekit', { pt: 'LiveKit e Pipecat.', en: 'LiveKit and Pipecat.' }),
      hindsight: yes(HS_README, { pt: 'Pipecat, Vapi e ElevenLabs.', en: 'Pipecat, Vapi and ElevenLabs.' }),
      honcho: undoc('https://honcho.dev/docs/llms.txt', { pt: 'As integrações documentadas são LangGraph, CrewAI, Vercel AI SDK e n8n.', en: 'The documented integrations are LangGraph, CrewAI, Vercel AI SDK and n8n.' }),
      cognee: undoc(CG_README, { pt: 'As integrações documentadas são de assistentes de código e MCP.', en: 'The documented integrations are coding assistants and MCP.' }),
      agentcore: undoc(AC_HOME),
      twilio: yes('https://www.twilio.com/en-us/blog/developers/tutorials/product/orchestrate-multi-call-conversations-with-llm-twilio-conversation-memory', { pt: 'Voz pela própria Twilio, com o ConversationRelay.', en: 'Voice through Twilio itself, with ConversationRelay.' }),
    },
  },
  {
    key: 'mcp',
    label: { pt: 'Servidor MCP', en: 'MCP server' },
    means: {
      pt: 'A memória como ferramentas MCP, para qualquer agente que fale o protocolo.',
      en: 'The memory as MCP tools, for any agent that speaks the protocol.',
    },
    cells: {
      niadra: yes(`${N}/guides/mcp`, { pt: 'Oito ferramentas, com o cliente amarrado fora do alcance do modelo.', en: 'Eight tools, with the customer bound out of the model’s reach.' }),
      mem0_oss: yes('https://mem0.ai/blog/introducing-openmemory-mcp', { pt: 'OpenMemory MCP, local, sobre a Mem0 aberta.', en: 'OpenMemory MCP, local, on top of open-source Mem0.' }),
      mem0_platform: yes('https://docs.mem0.ai/platform/mem0-mcp', { pt: 'Servidor hospedado com onze ferramentas.', en: 'A hosted server with eleven tools.' }),
      zep: yes(ZEP_MCP, { pt: 'Memory MCP Server, com login pelo provedor de identidade da empresa.', en: 'Memory MCP Server, with sign-in through the company’s identity provider.' }),
      letta: part('https://docs.letta.com/agent-sdk/mcp', { pt: 'O Letta conecta a servidores MCP como cliente; não é documentado como servidor.', en: 'Letta connects to MCP servers as a client; it is not documented as a server.' }),
      supermemory: yes('https://supermemory.ai/docs/supermemory-mcp/mcp', { pt: 'Servidor hospedado; não existe na versão local.', en: 'A hosted server; not available in the local version.' }),
      hindsight: yes(HS_README, { pt: 'Endpoint MCP embutido, um por banco.', en: 'A built-in MCP endpoint, one per bank.' }),
      honcho: yes('https://honcho.dev/docs/v3/guides/integrations/mcp'),
      cognee: yes('https://docs.cognee.ai/cognee-mcp/mcp-overview'),
      agentcore: part(`${AC}/memory-gateway-connector.html`, { pt: 'Pelo AgentCore Gateway, com um conector de memória configurado por você.', en: 'Through AgentCore Gateway, with a memory connector you set up.' }),
      twilio: undoc(TW_HOME),
    },
  },
  {
    key: 'benchmark',
    label: { pt: 'Benchmark publicado', en: 'A published benchmark' },
    means: {
      pt: 'O fornecedor publica resultados de medição da própria memória, com o método.',
      en: 'The vendor publishes measured results of its own memory, with the method.',
    },
    cells: {
      niadra: yes('https://niadra.com/benchmark', { pt: 'Acerto entre canais, privacidade, custo e latência, com script e arquivos de resultado públicos.', en: 'Cross-channel accuracy, privacy, cost and latency, with a public script and result files.' }),
      mem0_oss: yes(MEM0_RESEARCH, { pt: 'LoCoMo, LongMemEval e BEAM, com artigo no arXiv.', en: 'LoCoMo, LongMemEval and BEAM, with a paper on arXiv.' }),
      mem0_platform: yes(MEM0_RESEARCH, { pt: 'LoCoMo, LongMemEval e BEAM, com artigo no arXiv.', en: 'LoCoMo, LongMemEval and BEAM, with a paper on arXiv.' }),
      zep: yes('https://arxiv.org/abs/2501.13956', { pt: 'DMR e LongMemEval, no artigo da Zep.', en: 'DMR and LongMemEval, in Zep’s paper.' }),
      letta: yes('https://www.letta.com/blog/benchmarking-ai-agent-memory/', { pt: 'LoCoMo com o sistema de arquivos do Letta, no blog.', en: 'LoCoMo with Letta’s filesystem, on the blog.' }),
      supermemory: part('https://supermemory.ai/docs/memorybench/overview', { pt: 'Publica o harness aberto MemoryBench; os resultados são para você rodar.', en: 'Publishes the open MemoryBench harness; the results are yours to run.' }),
      hindsight: yes('https://arxiv.org/abs/2512.12818', { pt: 'LongMemEval, com artigo e resultados contínuos.', en: 'LongMemEval, with a paper and live results.' }),
      honcho: yes('https://honcho.dev/evals/', { pt: 'LongMemEval, LoCoMo e BEAM.', en: 'LongMemEval, LoCoMo and BEAM.' }),
      cognee: yes(CG_README, { pt: 'BEAM, com relatório no repositório e artigo no arXiv.', en: 'BEAM, with a report in the repository and a paper on arXiv.' }),
      agentcore: undoc(AC_STRATEGIES, { pt: 'Diz que as estratégias são "benchmarked"; nenhum resultado publicado.', en: 'Says the strategies are "benchmarked"; no result published.' }),
      twilio: undoc(TW_HOME),
    },
  },
  {
    key: 'oss',
    label: { pt: 'Código aberto e instalação própria', en: 'Open source and self-hosting' },
    means: {
      pt: 'O motor da memória é aberto e pode rodar na infraestrutura do cliente.',
      en: 'The memory engine is open and can run on the customer’s own infrastructure.',
    },
    cells: {
      niadra: no('https://github.com/ainiadra', { pt: 'Os SDKs são abertos (Apache 2.0); o serviço é totalmente gerenciado pela Niadra, por decisão da empresa.', en: 'The SDKs are open (Apache 2.0); the service is fully managed by Niadra, by the company’s decision.' }),
      mem0_oss: yes(MEM0_README, { pt: 'Apache 2.0, com servidor em Docker.', en: 'Apache 2.0, with a Docker server.' }),
      mem0_platform: no('https://mem0.ai/pricing', { pt: 'Serviço gerenciado; instalação própria no Enterprise.', en: 'A managed service; on-premises on Enterprise.' }),
      zep: part('https://help.getzep.com/graphiti/getting-started/overview', { pt: 'O Graphiti é aberto; o Zep Cloud é gerenciado, com nuvem própria no Enterprise.', en: 'Graphiti is open; Zep Cloud is managed, with bring-your-own-cloud on Enterprise.' }),
      letta: yes('https://github.com/letta-ai/letta', { pt: 'Apache 2.0, com servidor próprio.', en: 'Apache 2.0, with its own server.' }),
      supermemory: yes('https://supermemory.ai/docs/self-hosting/overview', { pt: 'Binário local aberto; sem conectores nem MCP no local.', en: 'An open local binary; no connectors and no MCP locally.' }),
      hindsight: yes(HS_README, { pt: 'MIT, um contêiner.', en: 'MIT, one container.' }),
      honcho: yes('https://github.com/plastic-labs/honcho', { pt: 'AGPL-3.0, com docker compose.', en: 'AGPL-3.0, with docker compose.' }),
      cognee: yes(CG_README, { pt: 'Apache 2.0, pip ou Docker.', en: 'Apache 2.0, pip or Docker.' }),
      agentcore: no(AC_HOME, { pt: 'Serviço gerenciado da AWS.', en: 'A managed AWS service.' }),
      twilio: no(TW_HOME, { pt: 'Serviço gerenciado da Twilio; não é elegível para HIPAA nem PCI.', en: 'A managed Twilio service; not HIPAA eligible and not PCI compliant.' }),
    },
  },
]

/** The published unit price of each one, quoted as the page states it. The conversion to a thousand conversations is on /benchmark, measured. */
export const PRICE: Record<SystemKey, PriceCell> = {
  niadra: { text: { pt: 'US$ 2 a 3 por mil conversas ou tarefas, com os modelos incluídos; mínimo de US$ 500 por mês', en: 'US$ 2 to 3 per thousand conversations or tasks, models included; US$ 500 a month minimum' }, src: 'https://niadra.com/preco', read: READ },
  mem0_oss: { text: { pt: 'Software gratuito; você paga o modelo e os servidores', en: 'Free software; you pay for the model and the servers' }, src: MEM0_README, read: READ },
  mem0_platform: { text: { pt: 'Hobby grátis; Starter US$ 19 por mês; Pro US$ 249 por mês (500 mil adições e 50 mil leituras); Enterprise sob consulta', en: 'Hobby free; Starter US$ 19 a month; Pro US$ 249 a month (500,000 adds and 50,000 retrievals); Enterprise on request' }, src: 'https://mem0.ai/pricing', read: READ },
  zep: { text: { pt: 'Flex US$ 125 por mês com 50 mil créditos (1 crédito por episódio de até 350 bytes); Flex Plus US$ 375; Enterprise sob consulta', en: 'Flex US$ 125 a month with 50,000 credits (1 credit per episode of up to 350 bytes); Flex Plus US$ 375; Enterprise on request' }, src: 'https://www.getzep.com/pricing', read: READ },
  letta: { text: { pt: 'API US$ 20 por mês, mais US$ 0,10 por agente ativo por mês e US$ 0,00015 por segundo de ferramenta; o modelo à parte', en: 'API US$ 20 a month, plus US$ 0.10 per active agent a month and US$ 0.00015 per second of tool execution; the model apart' }, src: LETTA_PRICING, read: READ },
  supermemory: { text: { pt: 'Pro US$ 19, Max US$ 100 e Scale US$ 399 por mês; tabela de US$ 5 por milhão de SM tokens e US$ 5 por milhão de buscas', en: 'Pro US$ 19, Max US$ 100 and Scale US$ 399 a month; rate card of US$ 5 per million SM tokens and US$ 5 per million searches' }, src: 'https://supermemory.ai/pricing', read: READ },
  hindsight: { text: { pt: 'Por uso: retain US$ 10 por milhão de tokens, recall US$ 0,75 por milhão, reflect US$ 0,05 por chamada, guarda US$ 0,25 por milhão por mês', en: 'Usage-based: retain US$ 10 per million tokens, recall US$ 0.75 per million, reflect US$ 0.05 per call, storage US$ 0.25 per million a month' }, src: 'https://vectorize.io/pricing', read: READ },
  honcho: { text: { pt: 'Ingestão US$ 2 por milhão de tokens; context() sem limite; raciocínio de US$ 0,001 a 0,50 por pergunta', en: 'Ingestion US$ 2 per million tokens; context() unlimited; reasoning from US$ 0.001 to 0.50 per question' }, src: 'https://honcho.dev/', read: READ },
  cognee: { text: { pt: 'Grátis até 1 milhão de tokens; Standard US$ 1 por milhão de tokens mais US$ 5 por workspace extra; Enterprise sob consulta', en: 'Free up to 1 million tokens; Standard US$ 1 per million tokens plus US$ 5 per extra workspace; Enterprise on request' }, src: 'https://www.cognee.ai/pricing', read: READ },
  agentcore: { text: { pt: 'US$ 0,25 por mil eventos; US$ 0,75 por mil registros guardados por mês (US$ 0,25 com estratégia própria); US$ 0,50 por mil recuperações', en: 'US$ 0.25 per 1,000 events; US$ 0.75 per 1,000 records stored a month (US$ 0.25 with your own strategy); US$ 0.50 per 1,000 retrievals' }, src: 'https://aws.amazon.com/bedrock/agentcore/pricing/', read: READ },
  twilio: { text: { pt: 'US$ 0,005 por perfil usado por dia; US$ 0,0028 por mil caracteres gerados; US$ 0,007 por recall', en: 'US$ 0.005 per profile used per day; US$ 0.0028 per 1,000 characters generated; US$ 0.007 per recall' }, src: 'https://www.twilio.com/en-us/products/conversational-ai/pricing', read: READ },
}
