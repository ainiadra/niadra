/**
 * What the webhook endpoint forms share (config/models.py WebhookEndpoint): the id, the signing secret, the
 * address and the secrets an endpoint names in the cell vault. A `standard` endpoint receives events and
 * warnings; a SIEM stream (`ocsf` or `hec`) receives the receipts of the space.
 */
import type { CellClient, ControlClient } from '../api/clients'
import type { WebhookEndpointDoc } from '../api/types'

/** The ids of the configuration documents (config/models.py Name). */
export const ENDPOINT_ID = /^[a-z][a-z0-9_.:-]{0,63}$/

export const ENDPOINT_ID_RULE = 'Letras minúsculas, números, ponto, dois-pontos, hífen e sublinhado, começando por letra, até 64.'

/** A starting endpoint id from a name: lower case, no accents, words joined by hyphens. */
export function suggestedEndpointId(name: string): string {
  const slug = name
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48)
  return /^[a-z]/.test(slug) ? slug : `endpoint-${slug}`.replace(/-+$/, '')
}

/** A Standard Webhooks secret: `whsec_` and 32 random bytes in base64, as domain/notify/webhooks.py reads it. */
export function newSigningSecret(): string {
  const bytes = new Uint8Array(32)
  crypto.getRandomValues(bytes)
  let bin = ''
  for (const b of bytes) bin += String.fromCharCode(b)
  return `whsec_${btoa(bin)}`
}

/** What is wrong with an endpoint address, in words, or null for an https address without credentials. */
export function urlProblem(raw: string): string | null {
  let u: URL
  try {
    u = new URL(raw.trim())
  } catch {
    return 'Use o endereço completo, começando por https://.'
  }
  if (u.protocol !== 'https:') return 'O endpoint precisa de HTTPS.'
  if (u.username || u.password) return 'Tire usuário e senha do endereço: a entrega é autenticada pela assinatura.'
  return null
}

/** Where the cell vault keeps a webhook secret of the space (app/privacy.py secret_ref); the endpoint names it by this path. */
export const webhookSecretRef = (spaceId: string, secretId: string) => `spaces/${spaceId}/webhook/${secretId}`

/** The collector token of a SIEM stream sits beside its signing secret, as the receipts docs name it. */
export const collectorTokenId = (endpointId: string) => `${endpointId}-token`

/**
 * Whether an endpoint of the document names this secret already. The vault keeps versions and reads the
 * latest, so writing it again would replace that endpoint's signing secret or token.
 */
export const secretInUse = (ref: string, endpoints: WebhookEndpointDoc[]) => endpoints.some((e) => e.secret_refs.includes(ref) || e.authorization_ref === ref)

export const SECRET_TAKEN = 'Outro endpoint já usa um segredo com este nome. Escolha outro ID.'

/** A SIEM stream carries receipts only; a warning or an event never reaches it (app/notify.py _subscribed). */
export const isSiemStream = (e: WebhookEndpointDoc) => e.format === 'ocsf' || e.format === 'hec'

/** What the control plane records of a secret, beside its version: a signing secret or a collector token. */
export type WebhookSecretKind = 'webhook' | 'collector_token'

/**
 * Writes a webhook secret straight into the cell vault and registers its version on the control plane,
 * which keeps only the version. A failure there is returned, not thrown: the secret is already in
 * the vault, where it works.
 */
export async function writeWebhookSecret(
  cell: CellClient,
  control: ControlClient,
  spaceId: string,
  secretId: string,
  value: string,
  kind: WebhookSecretKind = 'webhook',
): Promise<{ ref: string; refError: unknown }> {
  const written = await cell.putSecret('webhook', secretId, value)
  const ref = webhookSecretRef(spaceId, secretId)
  try {
    await control.putSecretRef(spaceId, ref, kind, written.version)
    return { ref, refError: null }
  } catch (refError) {
    return { ref, refError }
  }
}
