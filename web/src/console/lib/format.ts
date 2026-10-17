/** Dates and numbers in the tenant's language (pt-BR), in the browser's time zone. */

const two = (n: number) => String(n).padStart(2, '0')

function parts(iso: string) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  return { d, day: two(d.getDate()), month: two(d.getMonth() + 1), year: d.getFullYear(), hh: two(d.getHours()), mm: two(d.getMinutes()) }
}

/** 22/09/2026 14:02 */
export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return 'sem data'
  const p = parts(iso)
  return p ? `${p.day}/${p.month}/${p.year} ${p.hh}:${p.mm}` : iso
}

/** 22/09/2026 */
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return 'sem data'
  // A calendar day (2026-09-22) is not an instant: reading it as UTC would show the day before in the Americas.
  const day = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso)
  if (day) return `${day[3]}/${day[2]}/${day[1]}`
  const p = parts(iso)
  return p ? `${p.day}/${p.month}/${p.year}` : iso
}

const intFmt = new Intl.NumberFormat('pt-BR')
export const fmtInt = (n: number) => intFmt.format(n)

const pctFmt = new Intl.NumberFormat('pt-BR', { style: 'percent', minimumFractionDigits: 1, maximumFractionDigits: 1 })
/** 0.423 -> 42,3% */
export const fmtPct = (r: number) => pctFmt.format(r)

/** YYYY-MM-DD for <input type="date">, `days` before today in local time. */
export function dayInput(daysAgo = 0, now = new Date()): string {
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() - daysAgo)
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}`
}

/**
 * YYYY-MM-DD of the UTC day `daysAgo` days before the current UTC day. The server counts its days in UTC
 * (a context-use bucket, a receipt chain, a usage day), so a period built from the browser's local day
 * would cut the current UTC day between 21:00 and midnight in Brazil.
 */
export function utcDayInput(daysAgo = 0, now = new Date()): string {
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - daysAgo))
  return d.toISOString().slice(0, 10)
}

/** A UTC day (YYYY-MM-DD) as the ISO instant of its start, or of its last millisecond. */
export function utcDayToISO(day: string, endOfDay = false): string | undefined {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return undefined
  return `${day}T${endOfDay ? '23:59:59.999' : '00:00:00.000'}Z`
}

/** When the current UTC day ends, as a local time (21:00 in Brasília): what "até hoje, em UTC" means to the person. */
export function utcDayEndsAt(now = new Date()): string {
  const end = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1))
  return `${two(end.getHours())}:${two(end.getMinutes())}`
}

/** A date input value as the ISO instant of the start (or end) of that local day. */
export function dayToISO(day: string, endOfDay = false): string | undefined {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day)
  if (!m) return undefined
  const d = endOfDay
    ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 23, 59, 59, 999)
    : new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
  return d.toISOString()
}

/** First characters of a hash, enough to compare by eye; the full value is in the title. */
export const shortHash = (h: string | null | undefined, n = 12) => (h ? (h.length > n ? `${h.slice(0, n)}…` : h) : 'sem hash')

/** R$ 289,90: money as the systems send it, a number or a string of digits, in the object's currency (BRL when it says none). */
export function fmtMoney(value: number | string | null | undefined, currency = 'BRL'): string {
  if (value === null || value === undefined || value === '') return 'sem valor'
  const n = typeof value === 'number' ? value : Number(String(value).replace(',', '.'))
  if (!Number.isFinite(n)) return String(value)
  try {
    return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: currency.toUpperCase() }).format(n)
  } catch {
    return `${new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)} ${currency}`
  }
}

/** A state value that looks like a date (2026-09-27) or an instant, in the local format; anything else as it came. */
export function fmtDateLike(value: string): string {
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return fmtDate(value)
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value)) return fmtDateTime(value)
  return value
}
