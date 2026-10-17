/**
 * Where the person who asked for contact came from: the referring site (without the
 * query string), the first page seen and the utm_* parameters. Saved on the first page
 * of the visit (sessionStorage, gone when the tab closes) and sent with the request.
 * It is what tells whether a request came from an AI answer, a search engine or direct.
 */
const KEY = 'niadra.origin'

export type Origin = { referrer: string; landing: string; utm_source: string; utm_medium: string; utm_campaign: string }

export function rememberOrigin() {
  try {
    if (sessionStorage.getItem(KEY)) return
    const ref = document.referrer ? new URL(document.referrer) : null
    const external = ref && ref.host !== location.host
    const q = new URLSearchParams(location.search)
    const origin: Origin = {
      referrer: external ? `${ref.origin}${ref.pathname}` : '',
      landing: location.pathname,
      utm_source: q.get('utm_source') ?? '',
      utm_medium: q.get('utm_medium') ?? '',
      utm_campaign: q.get('utm_campaign') ?? '',
    }
    sessionStorage.setItem(KEY, JSON.stringify(origin))
  } catch { /* no sessionStorage: the request goes without an origin */ }
}

export function readOrigin(): Partial<Origin> {
  try {
    return JSON.parse(sessionStorage.getItem(KEY) ?? '{}') as Partial<Origin>
  } catch {
    return {}
  }
}
