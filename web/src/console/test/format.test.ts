import { describe, expect, it } from 'vitest'
import { dayInput, utcDayEndsAt, utcDayInput, utcDayToISO } from '../lib/format'

// The suite runs in America/Sao_Paulo (vitest.config.ts): 22:30 of 30/09 there is already 01/10 in UTC.
const NIGHT = new Date('2026-10-01T01:30:00Z')

describe('UTC days', () => {
  it('builds the period from UTC days, so the current server day is never cut at night in Brazil', () => {
    expect(dayInput(0, NIGHT)).toBe('2026-09-30')
    expect(utcDayInput(0, NIGHT)).toBe('2026-10-01')
    expect(utcDayInput(29, NIGHT)).toBe('2026-09-02')
    expect(utcDayInput(1, NIGHT)).toBe('2026-09-30')
  })

  it('turns a UTC day into the instants of its start and its end', () => {
    expect(utcDayToISO('2026-10-01')).toBe('2026-10-01T00:00:00.000Z')
    expect(utcDayToISO('2026-10-01', true)).toBe('2026-10-01T23:59:59.999Z')
    expect(utcDayToISO('hoje')).toBeUndefined()
  })

  it('says when the UTC day ends in the local zone', () => {
    expect(utcDayEndsAt(NIGHT)).toBe('21:00')
  })
})
