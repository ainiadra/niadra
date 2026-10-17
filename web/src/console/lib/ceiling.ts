import { audience } from './labels'

/**
 * A source's verification ceiling follows its audience when it is created (the server's
 * domain/control/rules.py check_ceiling): V4 only for a human desk or internal agents, no_customer only
 * for internal agents, every other audience up to V3. The control plane refuses anything else with the
 * code `verification_not_allowed`; this puts its two details in words. Any other message returns null,
 * and describeError says the refusal in general words.
 */
export function explainCeilingRefusal(detail: string | null | undefined): string | null {
  if (!detail) return null
  if (detail === 'only internal agents work without the customer') {
    return 'Sem cliente vale só para um agente interno, que trabalha sem o cliente na conversa. Escolha um nível de verificação para esta audiência.'
  }
  const m = /^an? (\w+) source's ceiling is at most (V\d)$/.exec(detail)
  if (!m) return null
  const top = m[2]!
  const v4 = top === 'V3' ? ' V4, documento conferido, fica para a mesa de atendimento humano e para agentes internos.' : ''
  return `Uma fonte com a audiência ${audience(m[1]!)} declara no máximo ${top}.${v4} Escolha ${top} ou um nível abaixo.`
}
