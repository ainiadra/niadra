import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { en } from './i18n/en'
import { pt } from './i18n/pt'

// RFC 9116: the file the build copies to /.well-known/security.txt.
const text = readFileSync(resolve(process.cwd(), 'public/.well-known/security.txt'), 'utf8')
const field = (name: string) => text.split('\n').find((l) => l.startsWith(`${name}: `))?.slice(name.length + 2)

describe('security.txt', () => {
  it('names a contact that reaches the security form, in both languages', () => {
    const contact = field('Contact')
    expect(contact).toBe('https://niadra.com/enterprise?interesse=seguranca')
    const interest = new URL(contact ?? '').searchParams.get('interesse')
    expect(pt.enterprise.interests.map((i) => i.v)).toContain(interest)
    expect(en.enterprise.interests.map((i) => i.v)).toContain(interest)
    expect(field('Preferred-Languages')).toBe('pt, en')
    expect(field('Canonical')).toBe('https://niadra.com/.well-known/security.txt')
  })

  it('has not expired, and expires within a year of being written', () => {
    const expires = Date.parse(field('Expires') ?? '')
    expect(expires).toBeGreaterThan(Date.now())
    expect(expires).toBeLessThan(Date.parse('2026-09-24T00:00:00Z') + 366 * 86_400_000)
  })
})
