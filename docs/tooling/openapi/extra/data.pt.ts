/**
 * Portuguese for the write, context, history, objects, identity and profiles routes,
 * and for the data API schema and field descriptions that had no translation yet.
 */
const IDEM =
  'Obrigatório. Fica guardado 24 horas com o hash do corpo: repetir com a mesma chave devolve a primeira resposta, e a mesma chave com outro corpo devolve 409 `conflict`.'

export const PT: Record<string, string> = {
  // Generic
  'res:*:Successful Response': 'Resposta de sucesso.',

  // Write
  'op:post /v1/batch':
    'Até 500 itens de tipos misturados: mensagens, eventos de sistema, ações, `identify`, `verify`, `handoff` e o fim de uma conversa ou tarefa. O evento bruto é gravado antes da resposta e deduplicado pelo `idempotency_key`, então repetir é sempre seguro. Os itens são validados um a um: quando todos entram, a resposta é 200; quando algum é recusado, é 207, com uma entrada por item recusado em `errors`, nunca um 422 para o lote inteiro. Os escopos são conferidos por item: `track` para eventos, `act` para ações, `identify` para identificação.',
  'res:post /v1/batch:200': 'Todos os itens entraram ou foram reconhecidos como duplicados.',
  'op:post /v1/media/uploads':
    'Reserva o lugar de um áudio ou anexo e devolve uma `upload_url` pré-assinada. Envie os bytes com `PUT` mandando exatamente os cabeçalhos de `upload_headers`: o armazenamento recusa qualquer outro conteúdo, e é assim que o SHA-256 declarado vale. Depois mande o evento com `content.media_ref` e `content.media_sha256`. Passe `subject` para que um `forget` alcance o arquivo mesmo que nenhum evento aponte para ele.',
  'res:post /v1/media/uploads:201': 'O lugar do upload, válido até `expires_at`.',
  'op:post /v1/ingest/files':
    'Um CSV que semeia a identidade (um `identify` por linha) ou um JSONL com um item de lote por linha. O corpo vai direto para o armazenamento de objetos; a tarefa de importação processa o arquivo, e a rota GET abaixo acompanha o andamento.',
  'res:post /v1/ingest/files:202': 'O arquivo está guardado e na fila; acompanhe com `GET /v1/ingest/files/{import_id}`.',
  'op:get /v1/ingest/files/{import_id}':
    'Acompanha uma importação iniciada com `POST /v1/ingest/files`: o estado, quantos registros entraram, foram duplicados ou recusados, e as primeiras 100 linhas recusadas com o motivo. Nenhum valor do arquivo volta na resposta.',
  'res:get /v1/ingest/files/{import_id}:200': 'A importação, como está agora.',
  'param:get /v1/ingest/files/{import_id}:import_id': 'O `import_id` devolvido no envio do arquivo.',
  'op:post /v1/ingest/webhook/{source_id}':
    'Autenticado pelo mapeamento da fonte (bearer, HMAC ou token na URL), e não por chave de fonte.',
  'res:post /v1/ingest/webhook/{source_id}:200':
    'O conteúdo depois do mapeamento da fonte, como resultado de lote: aceitos, duplicados e um erro por item recusado. O conteúdo bruto fica guardado em todos os casos.',
  'param:post /v1/ingest/webhook/{source_id}:source_id': 'A fonte configurada para este sistema.',
  'op:get /v1/ingest/webhook/{source_id}':
    'A verificação que alguns sistemas mandam por `GET` antes da primeira entrega (o `hub.challenge` da Meta): o valor do parâmetro `verification_challenge` do mapeamento volta em texto puro. Só um mapeamento autenticado por token na URL responde, e só com o `token` dele na query (401 sem ele); nos outros modos o `GET` não traz credencial nenhuma, e a resposta é 404.',
  'res:get /v1/ingest/webhook/{source_id}:200': 'O valor do desafio, devolvido em texto puro.',
  'param:get /v1/ingest/webhook/{source_id}:source_id': 'A fonte configurada para este sistema.',
  'res:post /v1/otel/v1/traces:200': 'Os spans depois do mapeador, como resultado de lote: aceitos, duplicados e um erro por span recusado.',
  'op:post /v1/feedback':
    'Retira ou corrige um fato, resolve uma pendência ou registra o desfecho de uma conversa. Cada ação pede os próprios campos, senão a resposta é 422: `retract_fact` pede `fact_id`, `correct_fact` pede `fact_id` e `value`, `resolve_open_item` pede `open_item_id`, e `conversation_outcome` pede `conversation_id` e `value`. Toda correção vira um evento, e a memória derivada é recalculada a partir dele.',
  'res:post /v1/feedback:200': 'A correção foi gravada como evento, informada como resultado de lote.',

  // Context
  'op:get /v1/context':
    'O mesmo Context Pack de `POST /v1/context`, pelo id do perfil em vez de um handle, para ferramentas que já têm o perfil. Mande o ETag que você tem em `If-None-Match` e a resposta é 304, sem corpo, enquanto o contexto continuar atual. Mesma política, mesmo nível de verificação e mesmo comprovante do `POST`. Prefira `POST /v1/context` com `profile_id`: esta rota leva o id da conversa na query, um id de conversa pode ser um telefone ou um e-mail, e a query string chega aos logs de acesso.',
  'res:get /v1/context:200': 'O contexto.',
  'res:get /v1/context:304': 'O contexto indicado em `If-None-Match` continua atual.',
  'param:get /v1/context:profile_id': 'O perfil, como as rotas de identidade e de perfis devolvem.',
  'param:get /v1/context:view': 'View de canal, de sujeito ou de tarefa, como `voice`, `chat` ou `task:billing`.',
  'param:get /v1/context:verification': 'O nível provado nesta conversa; o nível efetivo nunca passa do teto da fonte.',
  'param:get /v1/context:conversation_id': 'Fixa o contexto na conversa: todo turno recebe os mesmos bytes.',
  'param:get /v1/context:task_id': 'Para agentes internos, no lugar de `conversation_id`.',
  'param:get /v1/context:if-none-match': 'O ETag que você já tem. Se ele ainda vale, a resposta é 304.',
  'op:post /mcp':
    'O servidor MCP remoto do espaço, por Streamable HTTP com JSON-RPC 2.0. Autentique com a chave da fonte e amarre o cliente com o cabeçalho `Niadra-Subject-Token`, emitido por `POST /v1/subject-tokens`. Ele serve oito ferramentas (`get_customer_context`, `track_event`, `record_action`, `resolve_identity`, `search_customer_history`, `get_customer_timeline`, `open_history_item`, `correct_customer_memory`), o recurso `niadra://context` e um prompt, `customer_context`, com o contexto pronto para o seu modelo. Com os sinais do sujeito ligados, serve também `get_constraints`, o bloco de restrições do cliente, renderizado para uma ferramenta pela vinculação do espaço quando o modelo a nomeia. Cada ferramenta exige o próprio escopo, e nenhuma recebe o cliente como argumento.',
  'res:post /mcp:200': 'A resposta JSON-RPC. Falha de ferramenta volta como resultado com `isError`, não como erro HTTP.',

  // History and objects
  'op:get /v1/history/timeline':
    'A mesma linha do tempo de `POST /v1/history/timeline`, pelo id do perfil na query, para ferramentas que já têm o perfil: uma conversa ou uma ação de agente por linha, do mais recente para o mais antigo, paginada por `cursor`. Mesma política, mesmo nível de verificação e mesmo comprovante. Prefira `POST /v1/history/timeline` com `profile_id`, que deixa o id da conversa fora da URL.',
  'res:get /v1/history/timeline:200': 'Uma página.',
  'op:post /v1/history/open':
    'O mesmo que `GET /v1/history/items/{item_id}`, com o id da conversa e o cliente no corpo, nunca na URL.',
  'res:post /v1/history/open:200': 'O item aberto, como `GET /v1/history/items/{item_id}` devolve.',
  'param:get /v1/history/timeline:profile_id': 'O perfil cujo histórico será lido.',
  'param:get /v1/history/timeline:cursor': 'Cursor opaco, vindo de `next_cursor`.',
  'param:get /v1/history/timeline:conversation_id': 'A conversa que pergunta, para valer o nível de verificação daquela sessão.',
  'op:get /v1/objects/{object_type}/{namespace}/{external_id}':
    'O objeto como uma leitura de estado `display` o serve (object-state v1).',
  'res:get /v1/objects/{object_type}/{namespace}/{external_id}:200': 'O objeto.',
  'param:get /v1/objects/{object_type}/{namespace}/{external_id}:external_id': 'O id no sistema de registro. Pode conter barras.',
  'op:get /v1/objects/{object_type}/{namespace}/{external_id}/timeline': 'Os eventos e as ações de um objeto, do mais recente para o mais antigo.',
  'res:get /v1/objects/{object_type}/{namespace}/{external_id}/timeline:200': 'Uma página.',
  'param:get /v1/objects/{object_type}/{namespace}/{external_id}/timeline:cursor': 'Cursor opaco, vindo de `next_cursor`.',
  'param:get /v1/objects/{object_type}/{namespace}/{external_id}/timeline:external_id': 'O id no sistema de registro. Pode conter barras.',

  // Identity
  'op:get /v1/identity/assertions':
    'O registro imutável da identidade: quais handles foram declarados do mesmo sujeito, por qual método, com qual evidência, e se a asserção foi aplicada, está pendente ou foi recusada. Filtre por perfil ou por estado.',
  'param:get /v1/identity/assertions:profile_id': 'Só as asserções deste perfil.',
  'param:get /v1/identity/assertions:state': '`applied`, `pending` (esperando a união fora da requisição) ou `rejected`.',
  'op:post /v1/identity/assertions':
    'Afirma que dois ou mais handles são do mesmo sujeito, com o método que prova isso. A asserção passa pelas mesmas proteções das que a ingestão faz: valores bloqueados, limites por tipo de handle e nunca uma união automática de dois perfis que já têm handle em V3 ou acima. A união roda fora da requisição, então a resposta é 202 com a operação.',
  'res:post /v1/identity/assertions:202': 'A operação, na fila ou aplicada.',
  'param:post /v1/identity/assertions:Idempotency-Key': IDEM,
  'res:post /v1/identity/assertions/{assertion_id}/retract:202': 'A operação. O perfil é recalculado e pode se dividir em dois ou mais.',
  'param:post /v1/identity/assertions/{assertion_id}/retract:Idempotency-Key': IDEM,
  'op:post /v1/identity/merge':
    'Une dois ou mais perfis à mão, com o ator e o motivo registrados. Sem `force`, valem as mesmas proteções das uniões automáticas: dois perfis com handles verificados, ou duas organizações, são recusados. `force` passa por cima delas e deve ser raro. A memória não é copiada: os fatos continuam apontando para o handle de origem, então uma separação depois devolve tudo ao lugar.',
  'res:post /v1/identity/merge:202': 'A operação, na fila ou aplicada.',
  'param:post /v1/identity/merge:Idempotency-Key': IDEM,
  'op:post /v1/identity/unmerge':
    'Divide um perfil retirando as asserções que ligam alguns dos handles dele: nomeie os handles que saem, ou as asserções a retirar. O componente é recalculado e pode virar vários perfis; fatos, objetos e padrões seguem o handle de origem, e os contextos afetados são recompilados.',
  'res:post /v1/identity/unmerge:202': 'A operação, na fila ou aplicada.',
  'param:post /v1/identity/unmerge:Idempotency-Key': IDEM,
  'op:post /v1/identity/handles/block':
    'Marca um valor que nunca deve ligar perfis: o telefone de uma loja, uma caixa de e-mail compartilhada, um e-mail de teste. Um handle bloqueado continua servindo para leitura, mas nunca mais provoca uma união.',
  'res:post /v1/identity/handles/block:202': 'A operação, com o estado `blocked`.',
  'param:post /v1/identity/handles/block:Idempotency-Key': IDEM,
  'param:post /v1/identity/links:Idempotency-Key': IDEM,
  'param:post /v1/identity/links/{link_id}/end:Idempotency-Key': IDEM,
  'op:get /v1/identity/suggestions':
    'Pares de perfis que parecem ser o mesmo sujeito, encontrados fora do caminho da conversa e nunca aplicados sem uma pessoa. Cada lado vem com o perfil e o identificador, mascarados pelo papel de quem lê.',
  'res:get /v1/identity/suggestions:200': 'Uma página de sugestões em aberto.',
  'op:post /v1/identity/suggestions/{suggestion_id}/accept': 'O par vira uma asserção `accepted_suggestion`, que entra na fila de aplicação da identidade.',
  'res:post /v1/identity/suggestions/{suggestion_id}/accept:202': 'A operação que une o par.',
  'param:post /v1/identity/suggestions/{suggestion_id}/accept:Idempotency-Key': IDEM,
  'op:post /v1/identity/suggestions/{suggestion_id}/dismiss': 'Descarta uma sugestão. O par de perfis não volta para revisão, seja qual for a evidência.',
  'res:post /v1/identity/suggestions/{suggestion_id}/dismiss:200': 'A sugestão, agora `rejected`.',
  'param:post /v1/identity/suggestions/{suggestion_id}/dismiss:Idempotency-Key': IDEM,

  // Profiles
  'op:get /v1/profiles':
    'Os clientes do espaço pela atividade mais recente, para uma pessoa navegar: pseudônimo, tipo e handles mascarados, nunca um por inteiro. Um perfil apagado não é listado. Uma página pode trazer menos linhas que `limit`; a lista termina onde `next_cursor` vem nulo. Um comprovante por página.',
  'res:get /v1/profiles:200': 'Uma página de perfis, com o cursor da próxima.',
  'param:get /v1/profiles:order': 'A ordem: `last_seen_desc`, a atividade mais recente primeiro (o padrão), ou `last_seen_asc`, quem está há mais tempo sem atividade primeiro.',
  'param:get /v1/profiles:kind': 'Só perfis deste tipo: `person`, `account` ou `partner`.',
  'param:get /v1/profiles:cursor': 'O `next_cursor` da página anterior, como veio.',
  'param:get /v1/profiles:limit': 'Quantas linhas por página, de 1 a 100; o padrão é 50.',
  'op:get /v1/graph':
    'As relações de um cliente (`center`, `depth` 1 ou 2, até `limit` nós, 150 por padrão) ou os clientes e empresas mais conectados do espaço entre os de atividade mais recente (`overview=1`, 100 por padrão). Um perfil apagado ou unido a outro não é encontrado.',
  'Graph.center': 'O perfil no centro do grafo; nulo no mapa do espaço.',
  'Graph.truncated':
    'Se ficaram nós de fora: além do limite, os mais distantes primeiro (nunca o centro, os identificadores dele nem as empresas, parceiros e perfis parecidos ligados a ele antes de qualquer outro), ou os objetos de um cliente além dos 25 mais recentes.',
  'Graph.withheld':
    'Nós deixados de fora porque o papel de quem lê não pode vê-los: identificadores unidos por uma alegação que ninguém confirmou e objetos que a política nega.',
  'GraphNode.id': 'Opaco, e estável só dentro desta resposta.',
  'GraphNode.label':
    'Um cliente ou uma empresa pelo pseudônimo, um identificador mascarado seja qual for o papel de quem lê, um objeto pelo tipo e pelo ID, um agente ou um sistema pelo nome da fonte.',
  'GraphNode.ref':
    'O ID real a que o Console leva: o perfil, o objeto de negócio ou a fonte. Nulo para um identificador e para o destino de um repasse que não é uma fonte.',
  'GraphNode.meta':
    '`hop`, a distância do centro (0 no centro; 0 em todo nó do mapa do espaço), e por tipo: de um cliente, `pseudonym`, `handles` e, quando as relações dele foram lidas, `objects_total` (quantos objetos ele tem ou de que participa; um grafo mostra os 25 mais recentes); de um identificador, `type`, `channel`, `weight`, `provisional` e `last_seen`; de um objeto, `type`, `namespace`, `external_id`, `state`, `state_label`, `masked` e `as_of`; de uma fonte, `channel` e `vendor`, ou do destino de um repasse, `target`. O mapa do espaço soma a cada cliente `links`, `objects`, `interactions` e `score`.',
  'GraphEdge.from': 'O nó onde a relação começa.',
  'GraphEdge.meta':
    '`identifies`: `strength` (strong ou weak), `weight`, `since`. `member_of` e `partner_of`: `role`, `since`. `owns`: `role` (owner ou participant). `relates_to`: `relation`. `acted_on`: `count`, `last_at`, `operations`. `talked_with`: `count`, `last_at`, `channels`. `reported`: `count`, `last_at`. `handed_off`: `count`, `status`, `outcome`, `last_at`, `about` (o nó do cliente). `suggested_same`: `score`.',
  'op:post /v1/profiles/search': 'É uma leitura, então não gera comprovante; a consulta vai no corpo e nunca é registrada em log.',
  'res:post /v1/profiles/search:200': 'Os perfis encontrados, com os handles mascarados pelo papel de quem lê.',
  'op:get /v1/profiles/{profile_id}':
    'Um perfil: tipo de sujeito, pseudônimo, handles com estado e nível de verificação, e os vínculos com contas e parceiros. Os valores de contato ficam mascarados, a menos que o papel de quem lê possa vê-los.',
  'res:get /v1/profiles/{profile_id}:200': 'O perfil.',
  'op:get /v1/profiles/{profile_id}/memory':
    'A memória de um cliente como o Console a mostra; a linha do tempo vem por página, com `timeline_cursor`.',
  'res:get /v1/profiles/{profile_id}/memory:200': 'A memória do perfil.',
  'op:get /v1/profiles/{profile_id}/objects':
    'Os objetos e as ações de um cliente como o Console os mostra, cada lista por página, com o cursor dela.',
  'res:get /v1/profiles/{profile_id}/objects:200': 'Os objetos do perfil.',

  // Data API schemas and fields
  ActionOut: 'O que um agente fez num sistema para o cliente, com o estado da reconciliação.',
  'ActionOut.result': 'Nulo quando ausente ou mascarado para quem lê.',
  'AggregateRequest.entity': '`episodes`, `open_items`, `traits`, `actions`, `objects` ou `profiles`.',
  'AggregateResponse.suppressed_groups': 'Grupos abaixo do tamanho mínimo, retidos.',
  'AssistRequest.description': 'Para que serve o agente, ou a regra em palavras simples; mascarado antes de qualquer uso.',
  'AssistRunOut.checks': 'Validação contra as amostras, dentro da célula.',
  'AssistRunOut.diff_id': 'O diff que espera aprovação no plano de controle.',
  'AssistRunOut.document': 'O documento de configuração proposto, como foi enviado ao plano de controle.',
  'AgreementOut.target':
    '`fact`, `open_item`, `signal`, `trait` ou `contradiction`; `signal:used`, `signal:repeated`, `signal:contradicted` ou `signal:selection_miss` para cada sinal da medição; `contradiction:model` e `contradiction:band` para os pares que cada origem achou; `all`.',
  'ChainVerification.anchor_copy_matches':
    'Se a cópia da raiz guardada fora do banco tem a raiz recalculada; falso quando ela falta ou difere, nulo enquanto o dia não foi ancorado ou a cópia não pode ser lida.',
  'ChainVerification.anchor_locked':
    'Se essa cópia está em armazenamento de gravação única (Object Lock em modo de conformidade), que ninguém, nem a própria célula, apaga ou substitui antes do fim da retenção.',
  'ChainVerification.anchored_root': 'Raiz de Merkle, em hexadecimal, gravada pela âncora diária.',
  'ChainVerification.root_matches': 'Nulo enquanto o dia ainda não foi ancorado.',
  ContextTrimOut: 'O que o contexto de uma fonte, numa view, deixa de fora porque ela nunca usou.',
  'ContextTrimOut.active': 'Falso quando o espaço desligou o recorte ou a fonte está fora dele.',
  'ContextTrimOut.derived_at': 'A medição que o calculou por último.',
  'ContextUseBucket.late_deliveries': 'Contexto entregue depois do primeiro turno do agente.',
  'ContextUseBucket.no_context': 'Sessões sem nenhuma leitura de `context()`: cobertura, nunca uma taxa zero.',
  'ContextUseBucket.selection_misses': 'Contradições de itens que o contexto não levava; nunca atribuídas ao agente.',
  'ContextUseBucket.tokens_saved':
    'Tokens que esses contextos deixaram de levar, somados por entrega: a estimativa do compilador, a mesma dos orçamentos.',
  'ContextUseBucket.trimmed_deliveries': 'Entregas cujo contexto deixou de fora o que aquele agente não usa.',
  'ContextUseBucket.vendor': 'Vem da configuração da fonte; `""` quando a fonte não tem fornecedor.',
  'ContextUseBucket.view': 'Vazio nos contadores por sessão (cobertura, transferências, recontatos).',
  'ContextUseEntry.tokens_saved': 'Tokens que o contexto desta entrega deixou de levar por falta de uso.',
  'ContextUseReport.review':
    'Quanto a revisão por amostra confirma cada sinal por trás destes números: vereditos sobre as mesmas fontes, no período ou nos últimos 90 dias dele, quando for mais longo.',
  'ContextUseReport.trims':
    'Por fonte e view, os tipos de item que o contexto dela deixa de fora hoje por falta de uso, com a evidência; `tokens_saved` nos grupos é o que isso poupou no período.',
  'CoverageDay.sent': 'O que os batimentos do SDK dizem ter enviado; 0 para fontes sem SDK.',
  'ErasureOut.erased': 'Linhas apagadas por tabela.',
  'ErasureOut.export_run_ids': 'Execuções da exportação contínua que já levavam linhas apagadas.',
  'ErasureOut.receipt_hash': 'SHA-256, em hexadecimal, do comprovante de apagamento.',
  'ErasureOut.target_id': 'Id do perfil, do handle ou da conversa; nunca o valor do handle.',
  'ExportPackageOut.download_url': 'Link pré-assinado para o zip no armazenamento de objetos; `GET /v1/exports/{run_id}/download` serve o mesmo arquivo pela célula.',
  'ExportPackageOut.files': 'Nome do arquivo dentro do zip e o SHA-256 dele.',
  'ExportPackageOut.profile_id': 'O titular, também quando ele foi indicado por um handle.',
  'ExportPackageOut.sha256': 'SHA-256 do zip.',
  'FactOut.origin': 'O evento de evidência mais antigo.',
  'FactOut.value': 'Nulo quando a política mascara o valor para quem lê.',
  FactViewers: 'Responde "quem viu o fato F?": cada comprovante que o entregou.',
  FileImportOut: '`POST /v1/ingest/files` responde 202 com isto; `GET /v1/ingest/files/{import_id}` acompanha o andamento.',
  'FileImportOut.errors': 'As primeiras 100 linhas recusadas; os valores nunca voltam na resposta.',
  'FileImportOut.records': 'Registros do arquivo, contando as linhas em branco.',
  'HandleOut.value': 'Mascarado conforme o papel de quem chama.',
  LineageEvent: 'De onde veio um item; só metadados, o conteúdo fica atrás das rotas de histórico.',
  'LinkIn.role': '`buyer`, `driver`, `broker`, `technical_contact`, `owner` e outros.',
  'MediaUploadIn.subject': 'De quem é a mídia. O objeto fica guardado sob o handle do titular, então o `forget` o alcança mesmo que nenhum evento aponte para ele.',
  'MediaUploadResponse.upload_headers': 'Envie exatamente estes cabeçalhos com o upload; o armazenamento recusa qualquer outro conteúdo.',
  'MergeIn.force': 'Passa por cima das proteções de perfis verificados e de organizações.',
  'OpenItemOut.promise': 'Uma pendência da empresa: uma promessa feita ao cliente.',
  Origin: 'A procedência de um item: o evento de onde veio, a fonte (agente ou sistema), o canal e a hora.',
  ProfileListed: 'Uma linha da lista de clientes: o bastante para reconhecer um cliente e abrir a memória dele, nunca um identificador por inteiro.',
  'ProfileListed.handles': 'Os três com a atividade mais recente, o mais recente primeiro.',
  'ProfileListed.handle_count': 'Quantos identificadores o perfil tem.',
  'ProfileListed.last_seen': 'A atividade mais recente em qualquer identificador do perfil.',
  'ListedHandle.value': 'Sempre mascarado, seja qual for o papel de quem chama: um contato pelas pontas, um documento pelos dois últimos caracteres, um ID opaco pelos dois primeiros e os dois últimos.',
  'ProfileMatch.handles': 'Mascarados conforme o papel de quem chama.',
  'ProfileMatch.last_seen': 'A atividade mais recente em qualquer handle do perfil.',
  'ProfileMemory.audience': 'A classe de audiência com que o papel de quem lê enxerga a memória.',
  'ProfileMemory.timeline': 'Episódios, do mais recente para o mais antigo.',
  'ProfileMemory.withheld': 'Itens deixados de fora, por motivo.',
  'ProfileObject.actions': 'Do mais recente para o mais antigo.',
  'ProfileObject.state': 'Nulo quando a política mascara o estado para quem lê.',
  'ProfileObjects.actions': 'Ações sobre nenhum objeto da lista, como uma que fecha uma promessa.',
  'ProfileOut.partial': 'Conhecido só por ids privados do canal, como um BSUID.',
  ProfileSearchIn:
    'O valor vai no corpo. Um pseudônimo (`p_...`) ou um identificador: telefones, e-mails, ids do WhatsApp e documentos nacionais são reconhecidos pelo formato; ids opacos pedem `type` (e `scope`, quando têm escopo).',
  'RateOut.low': 'Limite inferior do intervalo de Wilson de 95%.',
  'RateOut.n': 'O denominador: amostras pequenas dão intervalos largos.',
  ReceiptLineage: 'Responde "por que o agente disse X?": comprovante, manifesto, itens e eventos de origem.',
  'ReceiptLineage.excluded': 'O que o manifesto deixou de fora de propósito, com o motivo.',
  'ReceiptOut.pseudonym': 'O pseudônimo do perfil, o nome a mostrar a uma pessoa no lugar de `profile_id`; não faz parte do comprovante encadeado.',
  'ReceiptOut.person_label':
    'O e-mail da pessoa que agiu, o nome a mostrar no lugar de `person_id`; não faz parte do comprovante encadeado, e é nulo para uma pessoa que o espaço ainda não viu agir desde que passou a guardar os nomes.',
  'ReceiptOut.detail':
    'Por que uma leitura `denied` foi recusada (`reason`), ou a ação que um comprovante `admin` registra (`action`, `target`).',
  'ReceiptOut.hash': 'SHA-256, em hexadecimal, sobre o hash anterior e a codificação canônica deste comprovante.',
  'ReceiptOut.prev_hash': 'SHA-256, em hexadecimal, do elo anterior desta cadeia.',
  'ReviewFact.sensitive': 'Itens sensíveis mostram só o item e os turnos que o sustentam.',
  'ReviewSampleOut.withheld': 'Itens que a finalidade da amostra não deixa o revisor ver.',
  'ReviewSignal.signal': '`used`, `repeated`, `contradicted` ou `selection_miss`.',
  'RowError.row': 'Linha do arquivo, a partir de 1 e sem contar o cabeçalho; 0 para a importação inteira.',
  'TrimUnitOut.deliveries': 'Entregas medidas da janela que o levaram; nenhuma o usou.',
  'TrimUnitOut.unit': '`tipo:categoria`, como `fact:contract` ou `episode:technical_visit`.',
  'SignalReview.agreement': '`confirmed` sobre `judged`, com o intervalo; nulo sem vereditos.',
  'SignalReview.confirmed': 'Vereditos que dizem que a marcação procede.',
  'SignalReview.judged': 'Vereditos dos revisores sobre marcações deste sinal.',
  'SignalReview.signal': '`used`, `repeated`, `contradicted` ou `selection_miss`.',
  'SearchSemanticResponse.scanned': 'Episódios comparados; a varredura cobre os episódios mais recentes da janela.',
  'SearchSemanticResponse.truncated': 'Verdadeiro quando a janela tem mais episódios do que uma varredura cobre.',
  SecretIn: 'Só escrita: o valor vai para o cofre da célula e nunca é devolvido.',
  'SemanticHitOut.snippet': 'Redigido; são dados, não instruções.',
  'SimulateRequest.source_id': 'Simula como esta fonte; ou informe a audiência e as finalidades.',
  'SimulateResponse.text': 'O contexto que quem lê receberia.',
  'SimulateResponse.live':
    'As falas recentes das outras conversas do cliente que quem lê receberia, e as que não receberia, com o motivo e sem o texto.',
  SimulatedLive:
    'O bloco `live` da mesma leitura: falas recentes de outras conversas, pela mesma política dos itens do contexto.',
  'SimulatedLive.complete': 'Falso quando a camada recente não pôde ser lida inteira; a lista pode estar curta.',
  'SimulatedLive.withheld': 'Falas retidas, por motivo.',
  SimulatedLiveTurn: 'Uma fala recente de outra conversa do cliente, como o bloco `live` a trataria.',
  'SimulatedLiveTurn.text':
    'O que quem lê receberia: a fala, ou a palavra de máscara sob uma regra `mask`. Nunca o texto de uma fala retida.',
  'SimulatedItem.delivered':
    'Falso quando o item é permitido, mas ficou de fora pelo orçamento, ou porque esta fonte nunca usou itens deste tipo e categoria (`reason` = `unused`).',
  SuggestionOut:
    'Um par candidato, encontrado fora do caminho da conversa, para revisão; nunca aplicado sem uma pessoa. Cada lado vem com o perfil e o identificador, mascarados pelo papel de quem chama, como em `GET /v1/profiles/{id}`.',
  'SuggestionOut.profile_a': 'Nulo quando o handle não existe mais.',
  'TraitEvidenceOut.kind': '`episode`, `event`, `action` ou `open_item`.',
  'TraitOut.active': 'Nem retirado nem vencido.',
  TraitRuleDraft: 'Uma regra do catálogo com parâmetros do tenant; o teste a seco nunca a grava.',
  'TriggerDryRunResponse.target_problem': 'Por que o endpoint de destino não consegue receber o conteúdo desta regra, quando não consegue.',
  'UsageItem.cost_micros': 'O nosso custo em milionésimos de dólar, quando a métrica tem custo.',
  'UsageItem.metric': '`session_billable`, `context_read`, `event_ingested` e outras.',
  'UsageItem.source_id':
    'Com `group_by=source`: o agente ou o sistema; nulo para o trabalho do próprio espaço (exportações, gatilhos, webhooks) e para as horas contadas antes de o uso ser guardado por fonte.',
  'UsageItem.start': 'Início da hora ou do dia em UTC.',
  niadra__contracts__common__SourceCoverage: 'A cobertura de uma fonte, calculada pelos batimentos que o SDK envia.',
  'niadra__contracts__common__SourceCoverage.status': '`ok`, ou `silent` quando a fonte parou de mandar eventos.',
  niadra__contracts__ingest__SourceCoverage:
    'Totais da janela e uma linha por dia (UTC) com tráfego; uma fonte configurada que não mandou nada aparece com zeros, porque mostrar a fonte muda é para isso que a cobertura existe.',
  'niadra__contracts__ingest__SourceCoverage.name': 'Nulo para uma fonte que não está mais na configuração.',
  ContextStamp:
    'Qual contexto o prompt do agente levava e quando ele entrou: o SDK carimba isso no turno do agente, e a medição distingue um contexto atrasado de um contexto não usado.',
  'EventItem.usage': 'A chamada ao modelo por trás de uma mensagem do `ai_agent`: tokens e cache de prompt.',
  ModelUsage:
    'O que o provedor do modelo informou sobre a chamada que produziu o turno do agente: o SDK lê isso da resposta de toda chamada que o `wrap()` vê, ou recebe de quem chama. Opcional; só os turnos do próprio agente levam.',
  'ModelUsage.provider': 'Quem atendeu a chamada, em minúsculas: `openai`, `anthropic`, um roteador ou uma nuvem.',
  'ModelUsage.model': 'O modelo que o provedor diz ter respondido, como `gpt-4.1-2025-04-14`.',
  'ModelUsage.prompt_tokens': 'Todos os tokens de entrada, os do cache inclusive.',
  'ModelUsage.cached_tokens': 'Tokens de entrada lidos do cache do provedor.',
  'ModelUsage.cache_write_tokens': 'Tokens de entrada gravados no cache (a criação de cache da Anthropic).',
  'ModelPriceOut.input': 'Dólares por milhão de tokens de entrada.',
  'ModelPriceOut.table': '`product`: a tabela de preços públicos da Niadra; `space`: as configurações do espaço.',
  PromptCacheReport:
    '`GET /v1/usage/prompt-cache`: o cache de prompt dos agentes, pelo que os provedores informaram.',
  'PromptCacheReport.estimate': 'Sempre verdadeiro: a economia é uma estimativa, nunca o que um provedor cobrou.',
  'PromptCacheReport.prices': 'O preço de cada modelo que a estimativa usou.',
  'PromptCacheReport.prices_as_of': 'Quando a tabela da Niadra foi tirada das listas de preços públicas.',
  'PromptCacheBucket.vendor': 'Vem da configuração da fonte; `""` quando não tem.',
  'PromptCacheBucket.calls': 'Turnos do agente cuja chamada ao modelo informou o uso.',
  'PromptCacheBucket.prompt_tokens': 'Todos os tokens de entrada dessas chamadas, os do cache inclusive.',
  'PromptCacheBucket.cached_tokens': 'Tokens de entrada que o provedor leu do cache de prompt.',
  'PromptCacheBucket.cache_write_tokens': 'Tokens de entrada gravados no cache.',
  'PromptCacheBucket.hit_rate': '`cached_tokens` sobre `prompt_tokens`; nulo sem tokens.',
  'PromptCacheBucket.estimated_savings_micros':
    'Uma estimativa, em milionésimos de dólar: tokens do cache pelo preço de entrada menos o preço de cache, descontado o adicional de gravação, pelo preço do modelo de cada chamada. Nulo quando nenhuma chamada do grupo tem preço.',
  'PromptCacheBucket.unpriced_calls': 'Chamadas de modelos sem preço em nenhuma das tabelas: ficam fora da estimativa.',
  'ProfileMemory.timeline_next': 'Onde começa a próxima página da linha do tempo (`timeline_cursor`); nulo quando não há mais.',
  'ProfileObjects.actions_next': 'Onde começa a próxima página das ações (`actions_cursor`); nulo quando não há mais.',
  'ProfileObjects.objects_next': 'Onde começa a próxima página dos objetos (`objects_cursor`); nulo quando não há mais.',
}
