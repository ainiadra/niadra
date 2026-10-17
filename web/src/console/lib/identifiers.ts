/**
 * What a typed value looks like, by the same rules the cell uses to classify a search
 * (domain/identity/search.py, domain/identity/handles.py classify). The Console checks before it asks, so
 * a name or a loose word never leaves the browser as a search: the memory keeps identifiers as keyed hashes
 * and finds only what the person already knows.
 */

export type Recognized = 'pseudonym' | 'phone_e164' | 'email' | 'wa_jid' | 'wa_lid'

const PSEUDONYM = /^p_[a-z2-7]{16}$/
const EMAIL = /^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$/
const PHONE_LIKE = /^\+?[\d\s().\-/]{8,24}$/

export function recognize(query: string): Recognized | null {
  const text = query.trim().toLowerCase()
  if (!text) return null
  if (PSEUDONYM.test(text)) return 'pseudonym'
  if (text.endsWith('@lid')) return 'wa_lid'
  if (text.endsWith('@s.whatsapp.net') || text.endsWith('@c.us')) return 'wa_jid'
  if (EMAIL.test(text)) return 'email'
  if (PHONE_LIKE.test(text)) {
    const digits = text.replace(/\D/g, '').length
    if (digits >= 8 && digits <= 15) return 'phone_e164'
  }
  return null
}

/** What the person reads when the value is not one the search can take without a type. */
export function unrecognizedHint(query: string): string {
  const shown = query.trim().length > 40 ? `${query.trim().slice(0, 40)}…` : query.trim()
  return `“${shown}” não é um telefone, um e-mail, um identificador do WhatsApp nem um pseudônimo. A busca encontra um cliente por um dado exato, nunca por nome ou texto livre. Se for um ID de app ou de sistema, escolha o tipo ao lado. Para abrir um cliente sem saber um dado dele, use a lista de clientes abaixo.`
}
