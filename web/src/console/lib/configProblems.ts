import { targetProblem } from './labels'

/**
 * The control plane refuses a change to the rule documents that would leave a rule accepted and silent
 * (the server's app/control_config.py, semantic_problems): each problem names the document, the rule or
 * endpoint id and the field, never a typed value. This puts them in words; an unknown message returns
 * null, and the screen shows the server's own text.
 */
export function explainConfigProblems(detail: string | null | undefined): string[] | null {
  if (!detail) return null
  const out: string[] = []
  for (const part of detail.split('; ')) {
    const said = explainOne(part.trim())
    if (!said) return null
    out.push(said)
  }
  return out.length > 0 ? out : null
}

const RULE_KIND: Record<string, string> = { 'trait-rules': 'regras de padrão', 'trigger-rules': 'regras de aviso' }

function explainOne(part: string): string | null {
  let m = /^(trait-rules|trigger-rules): (\d+) rules, at most (\d+) per space$/.exec(part)
  if (m) return `A lista ficaria com ${m[2]} ${RULE_KIND[m[1]!]}, e um espaço tem até ${m[3]}. Desligar não libera lugar: tire uma regra do documento.`
  m = /^views: (\d+) task views, at most (\d+) per space$/.exec(part)
  if (m) return `A lista ficaria com ${m[1]} views de tarefa, e um espaço tem até ${m[2]}: cada uma é um contexto que a Niadra mantém pronto para cada cliente.`
  m = /^export-schedules (\S+): region is not the space's region$/.exec(part)
  if (m) return `O agendamento ${m[1]} grava fora da região do espaço, e a exportação só sai para a região do espaço.`
  m = /^export-schedules (\S+): another schedule has its bucket and prefix$/.exec(part)
  if (m) return `O agendamento ${m[1]} grava no mesmo bucket e prefixo de outro agendamento: cada destino recebe um agendamento só.`
  m = /^trigger-rules (\S+): endpoint (\S+) cannot receive it \((\w+)\)$/.exec(part)
  if (m) return `A regra de aviso ${m[1]} não chegaria ao endpoint ${m[2]}: ${targetProblem(m[3]!)}`
  m = /^trigger-rules (\S+): params\.name names no pattern rule in use$/.exec(part)
  if (m) return `A regra de aviso ${m[1]} usa um padrão que não está ligado neste espaço.`
  m = /^webhook-endpoints (\S+): event_types\.(\d+) is not an event type Niadra sends$/.exec(part)
  if (m) return `O endpoint ${m[1]} assina, na posição ${Number(m[2]) + 1} da lista, um tipo de evento que a Niadra não envia.`
  m = /^trait-rules (\S+): (.+)$/.exec(part)
  if (m) return `A regra de padrão ${m[1]} ${traitProblem(m[2]!)}`
  return null
}

function traitProblem(detail: string): string {
  let m = /^window_days must be between 1 and (\d+)$/.exec(detail)
  if (m) return `tem uma janela fora do limite: de 1 a ${m[1]} dias.`
  m = /^window_days (\d+) counts system events, which stay (\d+) days in the space \(settings\.events_hot_days\)$/.exec(detail)
  if (m) return `conta eventos de sistema numa janela de ${m[1]} dias, e eles ficam ${m[2]} dias no espaço: use uma janela de até ${m[2]} dias.`
  m = /^unknown rule parameters: (.+)$/.exec(detail)
  if (m) return `tem parâmetro que o catálogo não conhece: ${m[1]}.`
  if (/names a category that is sensitive or not allowed$/.test(detail)) return 'usa uma categoria sensível ou fora da lista liberada para padrões.'
  if (detail === 'unknown trait catalog entry') return 'usa um padrão que não está no catálogo.'
  return `não passa na conferência (${detail}).`
}
