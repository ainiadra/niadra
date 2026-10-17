/**
 * Portuguese for the governance routes of the data API: privacy and audit, sources,
 * patterns, measurement and usage, review and assistant, triggers and webhooks, and
 * base-wide analysis.
 */
export const PT: Record<string, string> = {
  // Privacy and audit
  'op:post /v1/forget':
    'Apaga um perfil, um handle ou uma conversa por linhagem: o conteúdo dos eventos, os fatos que ficaram sem evidência, episódios, pendências, objetos cujo dono é o handle, o conteúdo pessoal das ações, padrões, vínculos, linhas da medição, amostras de revisão, payloads de disparo, contextos e arquivos, e as lápides para a exportação contínua. Só os eventos do cliente sustentam a memória: uma retratação, correção ou restauração da empresa nunca segura um fato, e todo evento de feedback que nomeou um fato apagado fica só com ids e tipos (`feedback_values` nas contagens), como a resolução de uma pendência apagada (`open_item_feedback`); uma nota de agente perde o id da conversa apagada (`agent_note_evidence`); uma conversa cujos eventos já foram para o arquivo frio é achada e apagada por inteiro. Apagar uma pessoa nunca apaga a conta a que ela está ligada. O apagamento roda em segundo plano; acompanhe por `GET /v1/forget/{request_id}`. Ao terminar, a resposta traz o hash do comprovante de apagamento e as execuções de exportação que já continham o dado, e o webhook `erasure.completed` é disparado.',
  'res:post /v1/forget:202': 'Aceito. O apagamento entra na fila; `status` vai de `pending` para `running` e `completed`.',
  'param:post /v1/forget:Idempotency-Key': 'Obrigatório. Fica guardado 24 horas com o hash do corpo; a mesma chave com outro corpo devolve 409.',
  'op:get /v1/forget':
    'Lista os pedidos de apagamento do espaço, do mais novo para o mais antigo, filtrados por `status`. O seu time de privacidade usa esta rota para mostrar cada pedido de titular e a prova dele: o que foi apagado, o hash do comprovante e as execuções de exportação que o seu lago de dados precisa purgar.',
  'res:get /v1/forget:200': 'Uma página de pedidos de apagamento.',
  'param:get /v1/forget:status': 'Só os pedidos neste estado: `pending`, `running`, `completed` ou `failed`.',
  'op:get /v1/forget/{request_id}':
    'Lê um pedido de apagamento. `erased` conta as linhas removidas ou reescritas por tipo de dado (entre eles `feedback_values`, `open_item_feedback` e `agent_note_evidence`: eventos de feedback e resoluções que ficaram só com ids e tipos, e notas de agente que perderam o id da conversa), `receipt_hash` é o hash do comprovante de apagamento na trilha de auditoria e `export_run_ids` lista as execuções da exportação contínua que já continham o dado, para você purgar a sua cópia.',
  'res:get /v1/forget/{request_id}:200': 'O pedido de apagamento e até onde ele chegou.',
  'op:post /v1/export':
    'Reúne os dados de um titular para acesso e portabilidade, por `profile_id` ou por handle: eventos, fatos, episódios, pendências, objetos, ações, padrões com as evidências e vínculos. A resposta sai na hora, com uma URL de download assinada, o SHA-256 do arquivo e a lista de arquivos dentro dele. Toda exportação fica registrada na trilha de auditoria.',
  'res:post /v1/export:201': 'A exportação está pronta: baixe antes de `download_expires_at`.',
  'op:get /v1/exports/{run_id}/download':
    'Baixa o arquivo de uma exportação pronta. O corpo é o próprio arquivo, não JSON; confira com o `sha256` devolvido quando a exportação foi criada. Cada download fica registrado na trilha de auditoria.',
  'res:get /v1/exports/{run_id}/download:200': 'O arquivo da exportação.',
  'op:get /v1/exports/runs':
    'Lista as execuções da exportação contínua para o seu bucket, da mais nova para a mais antiga: a faixa de sequência de mudanças que cada uma cobriu, o estado e o manifesto que ela escreveu. Filtre por `schedule_id` para acompanhar um destino.',
  'res:get /v1/exports/runs:200': 'Uma página de execuções de exportação.',
  'param:get /v1/exports/runs:schedule_id': 'Só as execuções deste agendamento de exportação.',
  'op:put /v1/exports/destinations/{schedule_id}/credential':
    'Grava a chave de acesso do destino da exportação (S3 ou armazenamento compatível com S3), um JSON com `access_key_id` e `secret_access_key`, direto no cofre da célula. A chave só deve gravar no prefixo do destino. O valor nunca volta na resposta e nunca chega ao plano de controle; a resposta só diz qual versão está em uso.',
  'res:put /v1/exports/destinations/{schedule_id}/credential:200': 'Gravada. `version` é a versão da credencial em uso.',
  'param:put /v1/exports/destinations/{schedule_id}/credential:schedule_id': 'O agendamento de exportação, com o nome dado na configuração `export-schedules`.',
  'op:get /v1/receipts':
    'Lista os comprovantes do espaço: cada leitura de contexto, chamada ao histórico, consulta analítica e ação administrativa, e cada leitura que a política recusou (`denied`, com o motivo em `detail`), com os ids dos itens entregues, quantos ficaram retidos e sob qual versão de política. O comprovante nunca guarda conteúdo. Os comprovantes são encadeados por SHA-256 por espaço, fatia e dia; filtre por perfil, fonte, conversa, tipo ou período. Prefira `POST /v1/receipts/search`, que leva os mesmos filtros no corpo: um id de conversa pode ser um telefone ou um e-mail, e a query string chega aos logs de acesso.',
  'res:get /v1/receipts:200': 'Uma página de comprovantes.',
  'op:post /v1/receipts/search':
    'O mesmo que `GET /v1/receipts`, com os filtros no corpo: um id de conversa pode ser um telefone ou um e-mail, e nunca viaja numa URL.',
  'res:post /v1/receipts/search:200': 'Uma página de comprovantes, como `GET /v1/receipts` devolve.',
  'op:get /v1/receipts/verify':
    'Recalcula a cadeia SHA-256 dos comprovantes de um dia e compara a raiz com a raiz ancorada naquele dia. `valid` é verdadeiro quando nenhum elo está quebrado; `breaks` lista a fatia e a sequência de cada quebra. `anchor_copy_matches` compara a raiz recalculada com a cópia guardada fora do banco, e `anchor_locked` diz se essa cópia está em armazenamento que só aceita uma gravação. Rode numa auditoria para provar que nenhum comprovante foi alterado ou removido.',
  'res:get /v1/receipts/verify:200': 'A verificação daquele dia: cabeças por fatia, quebras e se a raiz ancorada confere.',
  'param:get /v1/receipts/verify:day': 'O dia a verificar, em UTC.',
  'op:get /v1/lineage/receipt/{receipt_id}':
    'Responde "por que o agente disse aquilo?": de um comprovante aos itens entregues, e de cada item aos eventos de origem, com canal, fonte e hora. `excluded` lista o que a política reteve e por quê, e `manifest_found` diz se o manifesto de proveniência ainda está disponível.',
  'res:get /v1/lineage/receipt/{receipt_id}:200': 'O comprovante, os itens e os eventos de origem de cada um.',
  'op:get /v1/lineage/fact/{fact_id}':
    'Responde "quem viu este fato?": cada comprovante em que o fato foi entregue, com a fonte, a audiência e a hora de cada leitura.',
  'res:get /v1/lineage/fact/{fact_id}:200': 'Os comprovantes que entregaram o fato.',
  'param:get /v1/lineage/fact/{fact_id}:handle': 'Id do handle de origem.',
  'op:post /v1/policy/simulate':
    'Mostra o que uma fonte ou uma audiência receberia de um perfil, item a item, antes de você liberar o acesso: o efeito da política em cada item (`allow`, `mask` ou `deny`), a regra que decidiu e o texto que o agente receberia. Nada é entregue e nenhum comprovante de leitura é gravado; a simulação em si fica registrada como ação administrativa.',
  'res:post /v1/policy/simulate:200': 'Os itens simulados, os retidos por motivo e o texto resultante.',
  'op:put /v1/secrets/{kind}/{secret_id}':
    'Grava um segredo direto no cofre da célula: o segredo de assinatura de um webhook (`webhook`) ou a credencial de um provedor de modelo que você traz (`model`). Segredos são só de escrita; a resposta traz a versão nova, e na rotação do segredo de webhook a versão anterior continua valendo até você aposentá-la.',
  'res:put /v1/secrets/{kind}/{secret_id}:200': 'Gravado. `version` é a versão do segredo em uso.',
  'param:put /v1/secrets/{kind}/{secret_id}:kind': '`webhook` para o segredo de assinatura de webhook, `model` para a credencial de provedor de modelo.',
  // Sources
  'op:get /v1/sources/arrived':
    'Para qualquer papel do Console: se o espaço recebeu algum evento no período, e nada mais da cobertura, que fica com os papéis que a leem.',
  'res:get /v1/sources/arrived:200': 'Se chegou algum evento, e desde quando a pergunta vale.',
  'param:get /v1/sources/arrived:days': 'Quantos dias olhar, de 1 a 90; o padrão é 90.',
  EventsArrived: 'Se o espaço recebeu algum evento no período: a resposta inteira, sem fonte e sem contagem.',
  'op:get /v1/sources/coverage':
    'Mostra, por fonte e por dia, quantos eventos o SDK diz ter mandado, quantos chegaram, quantos foram recusados e quantos vieram com tipo não mapeado. Uma fonte cujo último evento é antigo está muda: o contexto marca isso em `coverage`, e o gatilho `source.silent` pode avisar você.',
  'res:get /v1/sources/coverage:200': 'A cobertura por fonte, com uma linha por dia.',
  'param:get /v1/sources/coverage:days': 'Quantos dias cobrir, de 1 a 90.',
  'op:post /v1/sources/{source_id}/revoke':
    'Todas as chaves da fonte param de autenticar nesta célula em segundos; a revogação no plano de controle, que o Console manda junto, torna o corte permanente.',
  'res:post /v1/sources/{source_id}/revoke:200': 'A fonte está cortada nesta célula; `revoked_key_ids` lista as chaves que pararam de funcionar.',
  // Patterns, measurement and usage
  'op:post /v1/traits/{trait_id}/retract':
    'Retira um padrão errado. A retirada deixa uma lápide, para a mesma evidência não trazer o padrão de volta. O padrão sai do contexto das próximas leituras.',
  'res:post /v1/traits/{trait_id}/retract:200': 'O padrão, agora com `retracted_at`.',
  'op:post /v1/traits/rules/dry-run':
    'Testa uma regra de padrão do catálogo, com os seus parâmetros, contra os últimos 90 dias antes de ligar: quantos perfis foram avaliados, quantos ganhariam o padrão e uma amostra por pseudônimo, com a evidência e o prazo de validade de cada um. Conta como a derivação: uma contagem abaixo de 2 vale 2 fora das regras de evidência única, uma regra sobre eventos de sistema roda dentro da janela quente do espaço, e um perfil que se opôs ao perfilamento não é contado nem mostrado. Nada é gravado na memória.',
  'res:post /v1/traits/rules/dry-run:200': 'Quantos perfis casariam com a regra, com uma amostra.',
  'op:post /v1/profiles/{profile_id}/traits/opt-out':
    'Registra a oposição de um titular ao perfilamento: a memória para de derivar padrões para este perfil e remove os que estão ativos. Fatos, histórico e contexto continuam funcionando.',
  'res:post /v1/profiles/{profile_id}/traits/opt-out:200': 'A oposição vale a partir de `opted_out_at`.',
  'op:get /v1/context-use':
    'Mede se cada agente usou o contexto que recebeu: entregas com ao menos um item usado, perguntas repetidas, contradições, falhas de seleção, transferências que o destino não leu, entregas atrasadas e sessões sem contexto. Agrupe com `group_by` por `day`, `source_id`, `vendor`, `channel`, `view` ou `experiment_group` (padrão `source_id`). As taxas vêm com intervalo de confiança, e uma taxa sem nada a medir é `null`, nunca zero. `review` diz, por sinal, quantas das marcações dele o seu time de revisão confirmou no mesmo período e nas mesmas fontes. Pessoas com o papel vendor só veem as próprias fontes. `slot_channels` diz, por view e canal de recuperação, o que cada canal trouxe aos encaixes por turno e o que os agentes fizeram com isso, e `retrieval_weights` traz os pesos de fusão aprendidos, com a versão e as últimas execuções noturnas.',
  'res:get /v1/context-use:200': 'Um grupo para cada combinação das dimensões de `group_by`.',
  'param:get /v1/context-use:group_by': 'Repita o parâmetro para agrupar por mais de uma entre `day`, `source_id`, `vendor`, `channel`, `view` e `experiment_group`.',
  'param:get /v1/context-use:experiment_group': 'Só um grupo de um experimento, como o grupo de controle, para comparar com o grupo que recebe a memória.',
  'op:get /v1/context-use/{conversation_id}':
    'O detalhe de uma conversa: uma entrada por entrega, com o manifesto de origem, os itens usados, as perguntas repetidas, as contradições e se uma transferência foi lida. Quando uma entrega não pôde ser medida, `not_measured_reason` diz por quê. Prefira `POST /v1/context-use/conversation`, que leva o id da conversa no corpo.',
  'res:get /v1/context-use/{conversation_id}:200': 'Uma entrada por entrega medida da conversa.',
  'op:post /v1/context-use/conversation': 'O mesmo que `GET /v1/context-use/{conversation_id}`, com o id da conversa no corpo.',
  'res:post /v1/context-use/conversation:200': 'Uma entrada por entrega medida da conversa.',
  'op:get /v1/usage/budget':
    'O gasto de IA de hoje contra o teto diário do espaço, por finalidade: o teto do contrato ou o padrão.',
  'res:get /v1/usage/budget:200': 'Uma linha por finalidade: `extraction` e `assistant`.',
  'op:get /v1/usage':
    'O uso do espaço por hora ou por dia: sessões cobradas, leituras de contexto, chamadas ao histórico, eventos recebidos e as demais métricas, com o custo em micros. Passe `group_by=source` para separar cada métrica por fonte, ou `source_id` para ver uma fonte só.',
  'res:get /v1/usage:200': 'Um item por métrica e período, e por fonte com `group_by=source`.',
  'param:get /v1/usage:metric': 'Só estas métricas, como `session_billable`, `context_read`, `history_call` ou `event_ingested`. Até 20.',
  'param:get /v1/usage:group_by': '`source` separa cada métrica por fonte.',
  'op:get /v1/usage/context-latency':
    'O tempo do servidor das leituras de contexto do espaço em 24 horas e em 7 dias, sem a rede: mediana, p95, a parte abaixo de 100 ms, se o objetivo de 99% valeu e quanto da margem de erro foi usada, as leituras e a parte servida degradada. Vem das métricas da própria região, atualizado no máximo uma vez por minuto.',
  ContextLatencyReport:
    'O tempo do servidor das leituras de contexto do espaço (`/v1/context`), sem a rede, pelas métricas da própria região, atualizado no máximo uma vez por minuto.',
  'ContextLatencyReport.available': 'Falso quando as métricas da região não responderam; as janelas vêm vazias.',
  'ContextLatencyReport.target_ms': 'O tempo de servidor publicado de uma leitura de contexto.',
  'ContextLatencyReport.slo': 'A parte das leituras que o objetivo espera abaixo de `target_ms`.',
  'ContextLatencyWindow.reads': 'As leituras de contexto do espaço na janela.',
  'ContextLatencyWindow.p50_ms': 'A mediana do tempo do servidor; nula sem leituras.',
  'ContextLatencyWindow.under_target': 'A parte das leituras que o servidor respondeu em `target_ms` ou menos.',
  'ContextLatencyWindow.within_slo': 'Se essa parte chegou ao objetivo, `slo` das leituras.',
  'ContextLatencyWindow.error_budget_used':
    'A parte da margem de erro do objetivo (as leituras acima de `target_ms` que ele admite) já usada; acima de 1, o objetivo não foi cumprido.',
  'ContextLatencyWindow.degraded':
    'A parte das leituras servida degradada: memória atrasada, ausente ou um bloco de fora.',
  'op:get /v1/usage/prompt-cache':
    'O cache de prompt dos agentes: tokens, acertos do cache e a economia estimada, pelo uso que o SDK leu de cada chamada ao modelo.',
  // Review and assistant
  'op:get /v1/review/queue':
    'As próximas amostras para o seu time revisar: os fatos extraídos de uma sessão, com a evidência de cada um, e os sinais da medição encontrados nela. O revisor nunca vê mais do que o agente daquela finalidade veria, e cada amostra aberta fica registrada. Uma sessão que não deixou nada em que se possa registrar um veredito não é sorteada, e uma amostra que ficou sem nada sai da fila.',
  'res:get /v1/review/queue:200': 'As próximas amostras a revisar.',
  'op:post /v1/review/{sample_id}/verdicts':
    'Registra os vereditos de um revisor sobre uma amostra: `correct`, `wrong` ou `missing` para um fato, `holds` ou `does_not_hold` para um sinal da medição ou um padrão. Só uma pessoa com o papel review pode chamar, nunca uma chave de fonte. O veredito não muda a memória sozinho: um fato errado sai por `POST /v1/feedback`, um padrão errado pela retirada dele.',
  'res:post /v1/review/{sample_id}/verdicts:200': 'Os vereditos foram registrados.',
  'op:get /v1/review/agreement':
    'Quanto os revisores concordam com o sistema, e entre si, por tipo de item e por sinal da medição (`signal:repeated` e os demais), nos últimos `days` dias. É o número que diz se a extração e a medição merecem confiança no seu espaço. Cada veredito leva a fonte da amostra, então uma pessoa com o papel vendor lê só a concordância sobre as próprias fontes.',
  'res:get /v1/review/agreement:200': 'Concordância por tipo de item e por sinal, com intervalos de confiança.',
  'param:get /v1/review/agreement:days': 'Janela do relatório, de 1 a 365 dias.',
  'param:get /v1/review/agreement:source_id': 'Só os vereditos sobre amostras desta fonte.',
  'op:post /v1/assist':
    'Pede uma proposta ao assistente de configuração: um mapeamento de webhook, um esquema de extração, uma política de fonte ou uma regra de gatilho ou de padrão. Ele trabalha com amostras mascaradas dentro da célula e produz um diff versionado com as verificações; uma pessoa aprova na API de controle. O assistente nunca muda a configuração sozinho.',
  'res:post /v1/assist:202': 'Na fila. Acompanhe a execução até `proposed` ou `delivered`.',
  'op:get /v1/assist':
    'Lista as propostas pedidas ao assistente de configuração, da mais nova para a mais antiga, com o estado e o diff que cada uma produziu.',
  'res:get /v1/assist:200': 'Uma página de execuções do assistente.',
  'op:get /v1/assist/{run_id}':
    'Lê uma execução do assistente: o estado, as verificações que ele rodou contra as suas amostras, os problemas encontrados e, depois de proposto, o diff mandado ao plano de controle para aprovação.',
  'res:get /v1/assist/{run_id}:200': 'A execução do assistente.',
  // Triggers and webhooks
  'op:post /v1/triggers/dry-run':
    'Testa uma regra de gatilho antes de ligar: para quantos clientes ativos nos últimos 30 dias a condição vale agora, quantos foram avaliados e uma amostra com as evidências. Também confere se o endpoint de destino pode receber o que a regra manda; `target_problem` diz por que não. Nada é enviado.',
  'res:post /v1/triggers/dry-run:200': 'Quantos disparos a regra teria tido, com uma amostra.',
  'op:get /v1/triggers/firings':
    'Lista os disparos das suas regras de gatilho, com a versão da regra e os ponteiros para as evidências, nunca o conteúdo de uma conversa. Um disparo cujas evidências foram separadas por uma separação de perfis mostra `retracted_at`.',
  'res:get /v1/triggers/firings:200': 'Uma página de disparos.',
  'op:get /v1/webhooks/deliveries':
    'Lista as entregas dos webhooks de saída, com as tentativas, o último status e a próxima tentativa. Entregas com falha tentam de novo com recuo exponencial por 24 horas, depois viram `dead` e ficam até 7 dias, contados da criação da entrega, para você reenviar.',
  'res:get /v1/webhooks/deliveries:200': 'Uma página de entregas.',
  'op:post /v1/webhooks/deliveries/{delivery_id}/redeliver':
    'Reenvia uma entrega com falha ou morta, com o mesmo `webhook-id`, para o seu consumidor reconhecer o mesmo evento.',
  'res:post /v1/webhooks/deliveries/{delivery_id}/redeliver:202': 'A entrega voltou para a fila.',
  'op:post /v1/webhooks/endpoints/{endpoint_id}/test':
    'Manda uma entrega marcada como teste, do tipo `webhook.test`, para conferir um endpoint ou um fluxo de SIEM antes de depender dele. O endpoint recebe o envelope de sempre com `data: {"test": true}`, qualquer que seja a lista de tipos que ele assina; o fluxo de SIEM recebe um evento OCSF de atividade de API com `unmapped.niadra.test`, fora da cadeia de comprovantes. A entrega é assinada como as outras, sai uma vez só, sem novas tentativas, e aparece na lista de entregas. Endpoint desligado responde 409.',
  'res:post /v1/webhooks/endpoints/{endpoint_id}/test:202': 'A entrega de teste está na fila; acompanhe em `GET /v1/webhooks/deliveries`.',
  'param:post /v1/webhooks/endpoints/{endpoint_id}/test:endpoint_id': 'O `endpoint_id` do endpoint ou do fluxo de SIEM, como está em `webhook-endpoints`.',
  // Base-wide analysis
  'op:get /v1/insights/schema':
    'Descreve o que a análise da base aceita: as entidades (episódios, pendências, padrões, ações, objetos e perfis), as dimensões e os filtros de cada uma, as métricas e o tamanho mínimo de grupo.',
  'res:get /v1/insights/schema:200': 'As entidades, dimensões e métricas consultáveis.',
  'op:get /v1/insights/overview':
    'Os números principais da memória do espaço, calculados toda noite por entidade e dimensão. Grupos menores que o tamanho mínimo ficam de fora e são contados em `suppressed_buckets`.',
  'res:get /v1/insights/overview:200': 'Contagens por dimensão, na última execução noturna.',
  'op:post /v1/insights/aggregate':
    'Contagens e taxas sobre todos os clientes do espaço: escolha uma entidade, um período, filtros, dimensões e métricas (`count`, `profiles`, `avg_sentiment`). Grupos menores que o tamanho mínimo são suprimidos, nunca mostrados, e cada consulta deixa um comprovante analítico.',
  'res:post /v1/insights/aggregate:200': 'Uma linha por grupo, com os grupos suprimidos contados à parte.',
  'op:post /v1/insights/profiles':
    'Encontra os perfis que atendem a um critério, como três reclamações da mesma categoria num período. Os perfis voltam por pseudônimo, nunca por handle.',
  'res:post /v1/insights/profiles:200': 'Os perfis encontrados, por pseudônimo.',
  'op:post /v1/insights/search':
    'Busca por significado nos episódios do espaço inteiro, para perguntas que um perfil só não responde. Cada episódio é ordenado pelo vetor guardado com a versão atual dele; na hora, só a pergunta e os episódios sem vetor do codificador dela são codificados, e o comprovante diz quantos foram. Os resultados voltam por pseudônimo, com trechos redigidos, e `truncated` diz se mais episódios casaram do que foram lidos.',
  'res:post /v1/insights/search:200': 'Os episódios encontrados, por pseudônimo, com trechos redigidos.',
  'op:post /v1/insights/episodes/sample':
    'Uma amostra redigida dos episódios que atendem a um filtro, para uma pessoa ou um LLM ler o que está por trás de um número.',
  'res:post /v1/insights/episodes/sample:200': 'Os episódios da amostra, por pseudônimo.',
  'op:post /v1/insights/context':
    'A memória de um cliente, pelo pseudônimo, como a audiência de analista pode ver: fatos, episódios, pendências e padrões, só dos handles confirmados: o que chegou por um vínculo não confirmado não conta em nada, e o pseudônimo lista esse handle à parte em `hidden_handle_ids`. Categorias sensíveis ficam de fora, a menos que a sua política as libere para analistas.',
  'res:post /v1/insights/context:200': 'A memória do cliente, por pseudônimo.',
  'op:post /v1/insights/lineage':
    'Explica de onde veio um item de um cliente pseudonimizado, até os eventos que o produziram; um item que chegou por um vínculo não confirmado não é encontrado.',
  'res:post /v1/insights/lineage:200': 'O item e os eventos por trás dele.',
  'op:post /v1/insights/reveal':
    'Troca um pseudônimo pelos handles do cliente. Exige um motivo de ao menos 10 caracteres e acesso elevado: uma chave de fonte com os escopos `analytics` e `admin`, ou uma pessoa com o papel security. Cada revelação fica registrada com o motivo. Os handles de um vínculo não confirmado ficam de fora.',
  'res:post /v1/insights/reveal:200': 'Os handles por trás do pseudônimo.',
  'op:post /mcp/insights':
    'JSON-RPC 2.0 sobre HTTP, uma mensagem por requisição; falhas de ferramenta voltam como resultados com `isError`.',
  'res:post /mcp/insights:200': 'A resposta JSON-RPC 2.0 à mensagem.',
  'FailedIntake': 'Um lote de dados da empresa que falhou em todas as tentativas de entrar: guardado de lado, cifrado como chegou, nunca apagado, até alguém com acesso de administração tentá-lo de novo. Que tipo de lote, de qual fonte e quando; nunca o conteúdo.',
  'FailedIntake.kind': '`events` para `POST /v1/events` e os webhooks, `turns` para `POST /v1/turns`.',
  'FailedIntakePage.items': 'Do mais antigo para o mais novo, até 500 de cada tipo.',
  'FailedIntakePage.kept': 'Todos os lotes guardados, listados ou não; além da lista, `POST /v1/intake/failed/retry` sem ids tenta todos de novo.',
  'FailedIntakeRetried.retried': 'Lotes devolvidos à fila do espaço para um worker guardar.',
  'FailedIntakeRetry.intake_ids': 'Os lotes a tentar de novo; sem nenhum, todos os lotes guardados.',
  'op:get /v1/intake/failed': 'Os lotes que falharam em todas as tentativas de entrar (`row_failed`): guardados de lado, cifrados, nunca apagados, do mais antigo para o mais novo. Só o tipo, a fonte e os horários, nunca o conteúdo.',
  'op:post /v1/intake/failed/retry': 'Devolve lotes guardados à fila do espaço para um worker guardar, todos quando nenhum id vem. Um lote que falhar de novo volta a ser guardado, com uma nova ocorrência de `row_failed`.',
}
