import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { I18n, dicts } from '../i18n'
import { QuickAccess } from './QuickAccess'

const q = dicts.pt.quick
const e = dicts.pt.enterprise

function renderForm() {
  const user = userEvent.setup()
  render(
    <MemoryRouter initialEntries={['/benchmark']}>
      <I18n.Provider value={{ lang: 'pt', t: dicts.pt, setLang: () => {} }}>
        <QuickAccess />
      </I18n.Provider>
    </MemoryRouter>,
  )
  return user
}

async function fill(user: ReturnType<typeof userEvent.setup>, email: string) {
  await user.type(screen.getByLabelText(q.email), email)
  await user.type(screen.getByLabelText(q.what), 'Agente de voz no LiveKit e WhatsApp em outro fornecedor.')
  await user.click(screen.getByRole('button', { name: q.submit }))
}

describe('the short early-access request', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('refuses a personal e-mail before sending anything', async () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    const user = renderForm()
    await fill(user, 'marina@gmail.com')
    expect(await screen.findByText(e.errCorporate)).toBeInTheDocument()
    expect(fetch).not.toHaveBeenCalled()
  })

  it('sends only the e-mail and the message, marked as the quick form, with the page it came from', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 201 }))
    vi.stubGlobal('fetch', fetch)
    const user = renderForm()
    await fill(user, 'marina@acme.com.br')
    expect(await screen.findByText(q.sentTitle)).toBeInTheDocument()
    const [url, init] = fetch.mock.calls[0]!
    expect(url).toBe('/api/contato')
    const body = JSON.parse(init.body)
    expect(body).toMatchObject({ form: 'quick', email: 'marina@acme.com.br', interest: 'conversa', lang: 'pt', website: '' })
    expect(body.message).toContain('LiveKit')
    expect(body).not.toHaveProperty('name')
    expect(body).not.toHaveProperty('company')
    expect(typeof body.page).toBe('string')
  })

  it('shows why the API refused the domain', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: 'fields', fields: ['email'], reasons: { email: 'domain' } }), { status: 400 })))
    const user = renderForm()
    await fill(user, 'marina@acme-nao-existe.com.br')
    expect(await screen.findByText(e.errDomain)).toBeInTheDocument()
  })

  it('links to the full form for whoever wants to say more', () => {
    renderForm()
    expect(screen.getByRole('link', { name: q.full })).toHaveAttribute('href', '/enterprise')
  })
})
