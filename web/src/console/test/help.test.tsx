import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { GLOSSARY } from '../lib/glossary'
import { NAV_GROUPS, SCREENS, screenOf } from '../lib/screens'
import { Term } from '../ui/Term'
import { at, baseRoutes, mockApi, signInAt, SP_PROD } from './harness'

describe('Term', () => {
  it('shows the definition on focus, keeps it on click, and closes it with Escape, described for a screen reader', async () => {
    const user = userEvent.setup()
    render(
      <p>
        Cada <Term k="comprovante">comprovante</Term> fica na cadeia.
      </p>,
    )
    const term = screen.getByRole('button', { name: 'comprovante' })
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
    await user.tab()
    expect(term).toHaveFocus()
    const tip = screen.getByRole('tooltip')
    expect(tip).toHaveTextContent(GLOSSARY.comprovante.means)
    expect(term).toHaveAttribute('aria-describedby', tip.id)
    await user.click(term)
    await user.tab()
    expect(screen.getByRole('tooltip')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  })
})

describe('the help of a screen', () => {
  it('leaves the head of a screen to its name and the line under it, with the help in the rail', async () => {
    mockApi(baseRoutes())
    await signInAt(at('agentes'))
    const title = await screen.findByRole('heading', { level: 1, name: SCREENS.agents.label })
    const head = title.closest('header')!
    expect(head).toHaveTextContent(SCREENS.agents.lead!)
    expect(head).not.toHaveTextContent(SCREENS.agents.read[0]!)
    expect(screen.queryByRole('button', { name: /Como ler/ })).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Ajuda desta tela' }).length).toBeGreaterThan(0)
  })

  it('says what every term of every screen means, in words a person reads', () => {
    for (const [id, s] of Object.entries(SCREENS)) {
      expect(s.read.length, id).toBeGreaterThan(0)
      for (const k of s.terms) expect(GLOSSARY[k].means.length, `${id}:${k}`).toBeGreaterThan(40)
      expect(s.short.length, id).toBeLessThan(70)
    }
    const listed = new Set(NAV_GROUPS.flatMap((g) => g.screens))
    for (const id of ['customers', 'receipts', 'usage', 'data', 'agents', 'config', 'people']) expect(listed.has(id as never), id).toBe(true)
  })

  it('knows which screen an address belongs to', () => {
    expect(screenOf(`/console/e/${SP_PROD}/clientes`)).toBe('customers')
    expect(screenOf(`/console/e/${SP_PROD}/clientes/0192a000-0000-7000-8000-00000000c001`)).toBe('profile')
    expect(screenOf(`/console/e/${SP_PROD}/recibos/rct_1`)).toBe('receipt')
    expect(screenOf(`/console/e/${SP_PROD}/uso`)).toBe('usage')
    expect(screenOf('/console/espacos')).toBeNull()
  })
})
