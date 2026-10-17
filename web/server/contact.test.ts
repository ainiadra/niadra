// The contact form's rules as the API runs them: work e-mail only, and a domain that receives e-mail.
import { describe, expect, it, vi } from 'vitest'
import { mailCheck, screen, validate, type Dns, type MailCheck } from './contact'

const request = (over: Record<string, unknown> = {}) => ({
  name: 'Marina Souza',
  email: 'marina@acme.com.br',
  company: 'Acme',
  interest: 'producao',
  message: 'Queremos a memória para o agente de voz.',
  lang: 'pt',
  ...over,
})

const failing = (code: string) => () => Promise.reject(Object.assign(new Error(code), { code }))
const dns = (over: Partial<Dns>): Dns => ({ resolveMx: failing('ENODATA'), resolve4: failing('ENODATA'), resolve6: failing('ENODATA'), ...over })

describe('contact form validation', () => {
  it('accepts a work e-mail, trimmed and in lower case', () => {
    const v = validate(request({ email: '  Marina@ACME.com.br ', heard: 'linkedin' }))
    expect(v).toMatchObject({ ok: true, data: { email: 'marina@acme.com.br', interest: 'producao', lang: 'pt', heard: 'linkedin' } })
  })

  it.each([
    ['marina@gmail.com', 'free'],
    ['marina@hotmail.com.br', 'free'],
    ['marina@outlook.com', 'free'],
    ['marina@icloud.com', 'free'],
    ['marina@uol.com.br', 'free'],
    ['MARINA@GMAIL.COM', 'free'],
    ['marina@mailinator.com', 'disposable'],
    ['marina@yopmail.com', 'disposable'],
    ['marina', 'format'],
    ['marina@acme', 'format'],
    ['mari na@acme.com', 'format'],
  ])('refuses %s as %s', (email, reason) => {
    expect(validate(request({ email }))).toEqual({ ok: false, fields: ['email'], reasons: { email: reason } })
  })

  it('refuses an address longer than an e-mail can be', () => {
    expect(validate(request({ email: `${'a'.repeat(250)}@acme.com` }))).toMatchObject({ ok: false, fields: ['email'], reasons: { email: 'format' } })
  })

  it('names every field to fix, under the names the form uses', () => {
    expect(validate({ email: 'x@gmail.com' })).toEqual({ ok: false, fields: ['name', 'email', 'company', 'message'], reasons: { email: 'free' } })
    expect(validate(request({ name: 'n'.repeat(121), company: 'c'.repeat(161), message: 'm'.repeat(5001) }))).toMatchObject({
      ok: false,
      fields: ['name', 'company', 'message'],
    })
  })

  it('accepts the quick form without a name or a company, and keeps the page it came from', () => {
    const quick = { form: 'quick', email: 'marina@acme.com.br', message: 'Agente de voz no LiveKit.', lang: 'en', page: '/en/integracoes/livekit' }
    expect(validate(quick)).toMatchObject({ ok: true, data: { form: 'quick', name: '', company: '', interest: 'conversa', lang: 'en', page: '/en/integracoes/livekit' } })
    // The message and a work e-mail are still required.
    expect(validate({ form: 'quick', email: 'marina@gmail.com', message: '' })).toEqual({ ok: false, fields: ['email', 'message'], reasons: { email: 'free' } })
    // Any other form name is the full form, with every field required.
    expect(validate({ ...quick, form: 'other' })).toMatchObject({ ok: false, fields: ['name', 'company'] })
    expect(validate(request())).toMatchObject({ ok: true, data: { form: 'full' } })
  })

  it('falls back to a conversation, and to Portuguese, for anything it does not know', () => {
    expect(validate(request({ interest: 'drop table', lang: 'fr', heard: 'tv' }))).toMatchObject({ ok: true, data: { interest: 'conversa', lang: 'pt', heard: '' } })
    expect(validate(request({ lang: 'en' }))).toMatchObject({ ok: true, data: { lang: 'en' } })
  })
})

describe('refusal by domain', () => {
  it('says whether the domain receives e-mail', async () => {
    const mx = [{ exchange: 'mx.acme.com.br', priority: 10 }]
    expect(await mailCheck('acme.com.br', dns({ resolveMx: () => Promise.resolve(mx) }))).toBe<MailCheck>('mx')
    // A null MX ("." as the exchange) declares that the domain receives no e-mail.
    expect(await mailCheck('acme.com.br', dns({ resolveMx: () => Promise.resolve([{ exchange: '.', priority: 0 }]) }))).toBe('none')
    expect(await mailCheck('acme-nao-existe.com.br', dns({ resolveMx: failing('ENOTFOUND') }))).toBe('none')
    // No MX, but the domain has an address: delivery is still possible.
    expect(await mailCheck('acme.com.br', dns({ resolve4: () => Promise.resolve(['203.0.113.7']) }))).toBe('address')
    expect(await mailCheck('acme.com.br', dns({ resolve6: () => Promise.resolve(['2001:db8::7']) }))).toBe('address')
    expect(await mailCheck('acme.com.br', dns({}))).toBe('none')
    // DNS failing is not a reason to lose a request.
    expect(await mailCheck('acme.com.br', dns({ resolveMx: failing('ESERVFAIL') }))).toBe('unknown')
  })

  it('sends a domain that receives no e-mail back to the person, and keeps the others', async () => {
    const check = vi.fn<(domain: string) => Promise<MailCheck>>()
    check.mockResolvedValueOnce('none')
    expect(await screen(request({ email: 'marina@acme-nao-existe.com.br' }), check)).toEqual({
      ok: false,
      status: 400,
      body: { error: 'fields', fields: ['email'], reasons: { email: 'domain' } },
    })
    expect(check).toHaveBeenLastCalledWith('acme-nao-existe.com.br')

    check.mockResolvedValueOnce('mx')
    expect(await screen(request(), check)).toMatchObject({ ok: true, domain: 'acme.com.br', mx: true })
    check.mockResolvedValueOnce('address')
    expect(await screen(request(), check)).toMatchObject({ ok: true, mx: false })
    check.mockResolvedValueOnce('unknown')
    expect(await screen(request(), check)).toMatchObject({ ok: true, mx: null })
  })

  it('never looks up the domain of a personal or malformed address', async () => {
    const check = vi.fn<(domain: string) => Promise<MailCheck>>()
    expect(await screen(request({ email: 'marina@gmail.com' }), check)).toMatchObject({ ok: false, body: { reasons: { email: 'free' } } })
    expect(await screen(request({ email: 'marina' }), check)).toMatchObject({ ok: false, body: { reasons: { email: 'format' } } })
    expect(check).not.toHaveBeenCalled()
  })
})
