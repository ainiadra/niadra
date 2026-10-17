/**
 * The one value an SSO sign-in keeps in the browser while the page goes to the identity provider and back.
 *
 * Before leaving, the Console draws a random value, sends only its SHA-256 to the control plane, and keeps the
 * value in this tab's sessionStorage. The provider's answer comes back with a one-time sign-in code in the URL
 * fragment; the Console exchanges the code together with the value, and removes the value at once. The value
 * is not a credential: it signs nobody in without the code, and it never holds a token.
 */

const KEY = 'niadra.sso.tab'
// A sign-in that takes longer than this at the provider has expired on the server too.
const MAX_AGE_MS = 15 * 60 * 1000

export interface KeptForTab {
  verifier: string
  email: string
  /** Where the person wanted to go before signing in. */
  from: string | null
  at: number
}

function b64url(bytes: Uint8Array): string {
  let bin = ''
  for (const b of bytes) bin += String.fromCharCode(b)
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/** The page leaves for the identity provider through here, so tests can watch the address it goes to. */
export const browser = {
  go(url: string): void {
    window.location.assign(url)
  },
}

export function newVerifier(): string {
  const bytes = new Uint8Array(32)
  crypto.getRandomValues(bytes)
  return b64url(bytes)
}

/** base64url, without padding, of the SHA-256 of the value: what the control plane stores. */
export async function bindingOf(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))
  return b64url(new Uint8Array(digest))
}

export function keepForTab(kept: Omit<KeptForTab, 'at'>): void {
  sessionStorage.setItem(KEY, JSON.stringify({ ...kept, at: Date.now() }))
}

/** Reads without removing, so a second render reads the same; `dropTab` removes it once used. */
export function readTab(): KeptForTab | null {
  try {
    const raw = sessionStorage.getItem(KEY)
    const kept = raw ? (JSON.parse(raw) as Partial<KeptForTab>) : null
    if (!kept || typeof kept.verifier !== 'string' || typeof kept.email !== 'string' || typeof kept.at !== 'number') return null
    if (Date.now() - kept.at > MAX_AGE_MS) return null
    return { verifier: kept.verifier, email: kept.email, from: typeof kept.from === 'string' ? kept.from : null, at: kept.at }
  } catch {
    return null
  }
}

export function dropTab(): void {
  try {
    sessionStorage.removeItem(KEY)
  } catch {
    // Storage blocked: there is nothing to remove.
  }
}

/** `#code=...` or `#error=...` of the address the control plane sent the browser back to. */
export function readFragment(hash: string): { code: string } | { error: string } | null {
  const params = new URLSearchParams(hash.replace(/^#/, ''))
  const code = params.get('code')
  if (code) return { code }
  const error = params.get('error')
  if (error) return { error }
  return null
}
