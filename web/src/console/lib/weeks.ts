import type { ContextUseBucket, RateOut } from '../api/types'

/** The counters of a context-use bucket: they add up across days, the rates do not. */
const COUNTERS = [
  'sessions',
  'no_context',
  'deliveries',
  'not_measured',
  'late_deliveries',
  'deliveries_used',
  'questions',
  'repeated',
  'contradicted',
  'selection_misses',
  'transfers',
  'transfers_unread',
  'recontacts',
  'trimmed_deliveries',
  'tokens_saved',
] as const

/** A week of context use, Monday to Sunday, cut to the period asked for. */
export interface WeekBucket extends ContextUseBucket {
  week_start: string
  week_end: string
}

/** The 95% Wilson score interval, as the server computes it for every rate (domain/measure/aggregate.py, wilson). */
export function wilson(successes: number, n: number, z = 1.96): RateOut | null {
  if (n <= 0) return null
  const p = successes / n
  const denominator = 1 + (z * z) / n
  const centre = (p + (z * z) / (2 * n)) / denominator
  const margin = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / denominator
  return { value: p, low: Math.max(0, centre - margin), high: Math.min(1, centre + margin), n }
}

function utc(day: string): Date {
  const [y, m, d] = day.split('-').map(Number)
  return new Date(Date.UTC(y!, m! - 1, d!))
}

const iso = (date: Date) => date.toISOString().slice(0, 10)

function addDays(day: string, days: number): string {
  const date = utc(day)
  date.setUTCDate(date.getUTCDate() + days)
  return iso(date)
}

/** The Monday of the week of a calendar day (YYYY-MM-DD). */
export function mondayOf(day: string): string {
  const date = utc(day)
  return addDays(day, -((date.getUTCDay() + 6) % 7))
}

/**
 * Buckets grouped by `day` summed into weeks, Monday to Sunday, every week of the period listed, oldest
 * first, the first and last cut to the period. Rates come from the sums with the server's interval,
 * never from averaging the days' rates.
 */
export function byWeek(days: ContextUseBucket[], since: string, until: string): WeekBucket[] {
  const weeks = new Map<string, WeekBucket>()
  for (let start = mondayOf(since); start <= until; start = addDays(start, 7)) {
    const week: WeekBucket = {
      day: null,
      week_start: start < since ? since : start,
      week_end: addDays(start, 6) > until ? until : addDays(start, 6),
      usage_rate: null,
      repetition_rate: null,
      transfer_unread_rate: null,
      recontact_rate: null,
      ...Object.fromEntries(COUNTERS.map((k) => [k, 0])),
    } as WeekBucket
    weeks.set(start, week)
  }
  for (const b of days) {
    if (!b.day) continue
    const week = weeks.get(mondayOf(b.day))
    if (!week) continue
    for (const k of COUNTERS) week[k] += b[k]
  }
  return [...weeks.values()].map((w) => ({
    ...w,
    usage_rate: wilson(w.deliveries_used, w.deliveries - w.not_measured),
    repetition_rate: wilson(w.repeated, w.questions),
    transfer_unread_rate: wilson(w.transfers_unread, w.transfers),
    recontact_rate: wilson(w.recontacts, w.sessions),
  }))
}

/** 15/09 a 21/09 */
export function weekLabel(w: Pick<WeekBucket, 'week_start' | 'week_end'>): string {
  const short = (day: string) => `${day.slice(8, 10)}/${day.slice(5, 7)}`
  return w.week_start === w.week_end ? short(w.week_start) : `${short(w.week_start)} a ${short(w.week_end)}`
}
