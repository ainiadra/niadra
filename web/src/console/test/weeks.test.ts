import { describe, expect, it } from 'vitest'
import type { ContextUseBucket } from '../api/types'
import { byWeek, mondayOf, weekLabel, wilson } from '../lib/weeks'

const day = (d: string, b: Partial<ContextUseBucket>): ContextUseBucket => ({
  day: d,
  sessions: 0,
  no_context: 0,
  deliveries: 0,
  not_measured: 0,
  late_deliveries: 0,
  deliveries_used: 0,
  questions: 0,
  repeated: 0,
  contradicted: 0,
  selection_misses: 0,
  transfers: 0,
  transfers_unread: 0,
  recontacts: 0,
  trimmed_deliveries: 0,
  tokens_saved: 0,
  usage_rate: { value: 0.99, low: 0.9, high: 1, n: 1 },
  repetition_rate: null,
  transfer_unread_rate: null,
  recontact_rate: null,
  ...b,
})

describe('weekly context use', () => {
  it('computes the same Wilson interval as the server', () => {
    // Values of the server's domain/measure/aggregate.py wilson() for the same inputs.
    expect(wilson(9, 12)).toEqual({ value: 0.75, low: expect.closeTo(0.46769, 5), high: expect.closeTo(0.91106, 5), n: 12 })
    expect(wilson(3, 30)).toEqual({ value: 0.1, low: expect.closeTo(0.0346, 4), high: expect.closeTo(0.25621, 5), n: 30 })
    expect(wilson(0, 5)).toEqual({ value: 0, low: 0, high: expect.closeTo(0.43449, 5), n: 5 })
    expect(wilson(5, 5)).toEqual({ value: 1, low: expect.closeTo(0.56551, 5), high: 1, n: 5 })
    expect(wilson(0, 0)).toBeNull()
  })

  it('starts a week on Monday', () => {
    expect(mondayOf('2026-09-24')).toBe('2026-09-21') // a Thursday
    expect(mondayOf('2026-09-21')).toBe('2026-09-21')
    expect(mondayOf('2026-09-27')).toBe('2026-09-21') // a Sunday
    expect(mondayOf('2026-10-01')).toBe('2026-09-28')
  })

  it('sums the days of each week and computes the rates on the sums, never averaging them', () => {
    const weeks = byWeek(
      [
        day('2026-09-15', { sessions: 10, deliveries: 10, deliveries_used: 9, questions: 20, repeated: 1, recontacts: 1, tokens_saved: 300, trimmed_deliveries: 2 }),
        day('2026-09-19', { sessions: 2, deliveries: 2, not_measured: 2 }),
        day('2026-09-22', { sessions: 30, deliveries: 30, deliveries_used: 3, questions: 10, repeated: 5, recontacts: 3 }),
      ],
      '2026-09-12',
      '2026-09-24',
    )
    expect(weeks.map((w) => [w.week_start, w.week_end])).toEqual([
      ['2026-09-12', '2026-09-13'],
      ['2026-09-14', '2026-09-20'],
      ['2026-09-21', '2026-09-24'],
    ])
    const [quiet, second, third] = weeks
    expect(quiet!.sessions).toBe(0)
    expect(quiet!.usage_rate).toBeNull()
    expect(second).toMatchObject({ sessions: 12, deliveries: 12, not_measured: 2, deliveries_used: 9, questions: 20, repeated: 1, tokens_saved: 300, trimmed_deliveries: 2 })
    // 9 used of the 10 measured deliveries, whatever the days' own rates said.
    expect(second!.usage_rate).toEqual(wilson(9, 10))
    expect(second!.repetition_rate).toEqual(wilson(1, 20))
    expect(second!.recontact_rate).toEqual(wilson(1, 12))
    expect(second!.transfer_unread_rate).toBeNull()
    expect(third!.usage_rate).toEqual(wilson(3, 30))
  })

  it('names a week by the days it covers', () => {
    expect(weekLabel({ week_start: '2026-09-14', week_end: '2026-09-20' })).toBe('14/09 a 20/09')
    expect(weekLabel({ week_start: '2026-09-24', week_end: '2026-09-24' })).toBe('24/09')
  })
})
