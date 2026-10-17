/**
 * The contact form's rules, apart from the HTTP server and the database so they can be tested:
 * which fields are required, which e-mails are refused (malformed, personal webmail, disposable
 * inbox) and whether the e-mail's domain receives mail. server/api.ts runs them on every request.
 */
import { resolve4, resolve6, resolveMx } from 'node:dns/promises'
import { EMAIL_RE, emailDomain, personalKind } from '../src/lib/email'

const INTERESTS = new Set(['conversa', 'producao', 'regulado', 'docs', 'seguranca'])
const HEARD = new Set(['ia', 'busca', 'linkedin', 'indicacao', 'evento', 'outro'])
/**
 * Which form sent the request. `full` is /enterprise (name, work e-mail, company, interest, what you
 * are building). `quick` is the short block at the end of the benchmark, the integration pages and the
 * posts: work e-mail and what you are building, nothing else, so a reader who is not ready to introduce
 * the company still leaves a way to be answered. Both land in the same table.
 */
const FORMS = new Set(['full', 'quick'])
const LIMITS = { name: 120, email: 254, company: 160, message: 5000, page: 200 }
export const str = (v: unknown) => (typeof v === 'string' ? v.trim() : '')

export type Clean = {
  name: string; email: string; company: string; interest: string; message: string; lang: string; page: string; heard: string
  form: 'full' | 'quick'
  referrer: string; landing: string; utm_source: string; utm_medium: string; utm_campaign: string
}

/** Why the e-mail was refused: format, personal webmail, disposable inbox or a domain that does not receive e-mail. */
export type EmailReason = 'format' | 'free' | 'disposable' | 'domain'

const withTimeout = <T,>(p: Promise<T>, ms = 3000) =>
  Promise.race([p, new Promise<never>((_, reject) => setTimeout(() => reject(Object.assign(new Error('timeout'), { code: 'ETIMEOUT' })), ms))])

/**
 * Does the e-mail domain receive mail? 'mx' = it has a mail server;
 * 'address' = no MX, but the domain exists (delivery is still possible);
 * 'none' = the domain does not exist or declares it receives no e-mail (null MX);
 * 'unknown' = DNS failed or took too long, and the request is not lost over it.
 */
export async function mailCheck(domain: string, dns: Dns = SYSTEM_DNS): Promise<MailCheck> {
  try {
    const mx = await withTimeout(dns.resolveMx(domain))
    return mx.some((r) => r.exchange && r.exchange !== '.') ? 'mx' : 'none'
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code
    if (code === 'ENOTFOUND') return 'none'
    if (code === 'ENODATA') {
      const found = await Promise.any([withTimeout(dns.resolve4(domain)), withTimeout(dns.resolve6(domain))]).then(() => true, () => false)
      return found ? 'address' : 'none'
    }
    return 'unknown'
  }
}

/** The lookups mailCheck needs; tests pass their own. */
export type Dns = {
  resolveMx: (domain: string) => Promise<{ exchange: string; priority: number }[]>
  resolve4: (domain: string) => Promise<string[]>
  resolve6: (domain: string) => Promise<string[]>
}
export type MailCheck = 'mx' | 'address' | 'none' | 'unknown'
const SYSTEM_DNS: Dns = { resolveMx, resolve4, resolve6 }

/**
 * What the API does with a request body once origin, size, honeypot and rate limit passed: 400 with
 * the fields to fix, or the clean request with its domain and whether that domain has a mail server
 * (null when DNS could not tell, and the request is kept).
 */
export async function screen(
  input: Record<string, unknown>,
  check: (domain: string) => Promise<MailCheck> = mailCheck,
): Promise<
  | { ok: false; status: 400; body: { error: 'fields'; fields: string[]; reasons: Record<string, EmailReason> } }
  | { ok: true; data: Clean; domain: string; mx: boolean | null }
> {
  const v = validate(input)
  if (!v.ok) return { ok: false, status: 400, body: { error: 'fields', fields: v.fields, reasons: v.reasons } }
  const domain = emailDomain(v.data.email)
  const found = await check(domain)
  // A domain that does not exist is not a company: it goes back for the person to fix the address.
  if (found === 'none') return { ok: false, status: 400, body: { error: 'fields', fields: ['email'], reasons: { email: 'domain' } } }
  return { ok: true, data: v.data, domain, mx: found === 'mx' ? true : found === 'address' ? false : null }
}

/** The origin of the visit (src/lib/origin.ts): the site it came from, without query string, and utm_*. */
function origin(raw: unknown) {
  const o = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  const ref = str(o.referrer).slice(0, 300)
  return {
    referrer: /^https?:\/\//.test(ref) ? ref.split('?')[0] : '',
    landing: str(o.landing).slice(0, 200),
    utm_source: str(o.utm_source).slice(0, 100),
    utm_medium: str(o.utm_medium).slice(0, 100),
    utm_campaign: str(o.utm_campaign).slice(0, 100),
  }
}

/** Validates and cleans. Returns the fields with problems, under the names the form uses. */
export function validate(input: Record<string, unknown>): { ok: true; data: Clean } | { ok: false; fields: string[]; reasons: Record<string, EmailReason> } {
  const form = str(input.form)
  const d: Clean = {
    name: str(input.name),
    email: str(input.email).toLowerCase(),
    company: str(input.company),
    interest: str(input.interest),
    message: str(input.message),
    lang: str(input.lang) === 'en' ? 'en' : 'pt',
    page: str(input.page).slice(0, LIMITS.page),
    heard: HEARD.has(str(input.heard)) ? str(input.heard) : '',
    form: FORMS.has(form) ? (form as Clean['form']) : 'full',
    ...origin(input.origin),
  }
  const fields: string[] = []
  const reasons: Record<string, EmailReason> = {}
  // The quick form has no name and no company field: the e-mail's domain says the company.
  const quick = d.form === 'quick'
  if ((!d.name && !quick) || d.name.length > LIMITS.name) fields.push('name')
  if (!EMAIL_RE.test(d.email) || d.email.length > LIMITS.email) {
    fields.push('email')
    reasons.email = 'format'
  } else {
    const kind = personalKind(d.email)
    if (kind) {
      fields.push('email')
      reasons.email = kind
    }
  }
  if ((!d.company && !quick) || d.company.length > LIMITS.company) fields.push('company')
  if (!d.message || d.message.length > LIMITS.message) fields.push('message')
  if (!INTERESTS.has(d.interest)) d.interest = 'conversa'
  return fields.length ? { ok: false, fields, reasons } : { ok: true, data: d }
}

