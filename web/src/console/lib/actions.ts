/**
 * What an `admin` receipt records (the server's app/admin_audit.py, control_sync.py), in words: what the
 * person, the agent's key or the control plane did, and on what. The receipt carries only codes and ids
 * (`detail.action`, `detail.target`); the words live here, so the screen never prints a code.
 */

/** The verb of each action, third person, as the receipt list reads it: "demo@empresa.com gravou vereditos". */
const ACTIONS: Record<string, string> = {
  'profiles.listed': 'Listou os clientes',
  'profile.memory.read': 'Abriu a memória do cliente',
  'profile.objects.read': 'Abriu os objetos e as ações do cliente',
  'profile.fact_history.read': 'Abriu o histórico de um fato',
  'profile.agent_state.read': 'Abriu a memória de trabalho dos agentes sobre o cliente',
  'profile.inferences.read': 'Abriu as inferências sobre o cliente',
  'profile.inference.corrected': 'Corrigiu uma inferência',
  'profile.inference.deleted': 'Apagou uma inferência',
  'policy.simulate': 'Simulou o contexto que um agente receberia',
  'lineage.receipt.read': 'Abriu um comprovante',
  'lineage.fact.read': 'Consultou quem leu um fato',
  'review.queue.read': 'Abriu a fila de revisão',
  'review.verdicts': 'Gravou vereditos',
  'review_request.created': 'Pediu uma revisão',
  'review_request.resolved': 'Resolveu um pedido de revisão',
  'privacy.forget': 'Pediu o apagamento de um cliente',
  'privacy.object': 'Registrou a oposição a uma finalidade',
  'privacy.export': 'Gerou o arquivo do titular',
  'privacy.export_download': 'Baixou o arquivo do titular',
  'privacy.destination_credential': 'Gravou a credencial de um destino de exportação',
  'privacy.secret': 'Gravou um segredo no cofre',
  'memory.retract_fact': 'Retirou um fato a pedido do titular',
  'memory.correct_fact': 'Corrigiu um fato a pedido do titular',
  'memory.resolve_open_item': 'Resolveu uma pendência a pedido do titular',
  'memory.restore_fact': 'Restaurou um fato retirado',
  'traits.retract': 'Retirou um padrão',
  'traits.opt_out': 'Desligou os padrões do cliente',
  'traits.rule_dry_run': 'Testou uma regra de padrão',
  'triggers.rule_dry_run': 'Testou uma regra de aviso',
  'notify.redeliver': 'Reenviou uma entrega de webhook',
  'notify.test': 'Testou um endpoint',
  'agent_memory.note_created': 'Guardou uma nota de trabalho',
  'agent_memory.note_revised': 'Revisou uma nota de trabalho',
  'agent_memory.note_retired': 'Retirou uma nota de trabalho',
  'agent_memory.export': 'Exportou as notas de um agente',
  'agent_memory.erase': 'Apagou as notas de um agente',
  'agent_memory.distill': 'Pediu uma proposta de nota a partir de uma conversa',
  'agent_memory.proposal_created': 'Propôs uma nota de trabalho',
  'agent_memory.proposal_approved': 'Aprovou uma proposta de nota',
  'agent_memory.proposal_rejected': 'Recusou uma proposta de nota',
  'legal_hold.created': 'Criou uma retenção legal',
  'legal_hold.released': 'Liberou uma retenção legal',
  'data_issue.acknowledged': 'Deu ciência de um problema de dados',
  'operations.replay_dead_tasks': 'Mandou de novo as tarefas que esgotaram as tentativas',
  'source.revoked': 'Cortou o acesso de uma fonte',
  'content.released': 'Liberou um conteúdo retido',
  'assist.request': 'Pediu uma proposta ao assistente',
  'key.issued': 'Emitiu uma chave de API',
  'key.updated': 'Girou uma chave de API',
  'key.revoked': 'Revogou uma chave de API',
}

/** The kind of each target, in words, with the article the sentence needs: "sobre a amostra …". */
const TARGETS: Record<string, string> = {
  sample: 'amostra',
  source: 'agente',
  erasure: 'pedido de apagamento',
  export_run: 'arquivo',
  export_schedule: 'agendamento',
  webhook_secret: 'assinatura de webhook',
  model_secret: 'credencial de modelo',
  agent_note: 'nota',
  agent_note_proposal: 'proposta de nota',
  legal_hold: 'retenção legal',
  data_issue: 'problema de dados',
  receipt: 'comprovante',
  fact: 'fato',
  open_item: 'pendência',
  trait: 'padrão',
  trait_rule: 'regra de padrão',
  trigger_rule: 'regra de aviso',
  delivery: 'entrega',
  endpoint: 'endpoint',
  key: 'chave',
  task: 'tarefa',
  inference: 'inferência',
  review_request: 'pedido de revisão',
  assist_run: 'proposta do assistente',
  purpose: 'finalidade',
  audience: 'audiência',
}

/** A target that is a count, folded into the verb: `profiles:50` reads "Listou 50 clientes". */
const COUNTS: Record<string, (n: string) => string> = {
  profiles: (n) => `Listou ${n} ${n === '1' ? 'cliente' : 'clientes'}`,
  samples: (n) => `Abriu a fila de revisão, com ${n} ${n === '1' ? 'amostra' : 'amostras'}`,
}

interface ActionTarget {
  /** `source` names an agent of the space, shown by name; any other kind shows its id. */
  kind: string
  /** The kind in words, or the raw kind when the Console does not know it. */
  label: string
  id: string
  /** The agent a key belongs to: a key receipt's target is `key:<key id>:source:<source id>`. */
  sourceId: string | null
}

export interface ActionWords {
  /** The action in words, or null when the Console does not know the code (the code is then shown as it came). */
  did: string | null
  code: string | null
  on: ActionTarget | null
}

/** The action of an `admin` receipt in words: who did what, on what. The subject is rendered beside it. */
export function actionWords(detail: Record<string, string> | null | undefined): ActionWords {
  const code = detail?.action ?? null
  const target = detail?.target ?? null
  const colon = target?.indexOf(':') ?? -1
  const kind = target && colon > 0 ? target.slice(0, colon) : null
  const id = target && colon > 0 ? target.slice(colon + 1) : target
  if (kind && COUNTS[kind] && id) return { did: COUNTS[kind]!(id), code, on: null }
  const did = code ? (ACTIONS[code] ?? null) : null
  const [own, sourceId] = kind === 'key' && id ? id.split(':source:') : [id]
  const on = kind && own ? { kind, label: TARGETS[kind] ?? kind, id: own, sourceId: sourceId ?? null } : null
  return { did, code, on }
}

/** Who did it when no person and no agent key signed the receipt: a change the control plane made. */
export const isControlPlane = (detail: Record<string, string> | null | undefined) => detail?.actor === 'control_plane'
