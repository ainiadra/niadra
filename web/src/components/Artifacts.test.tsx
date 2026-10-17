import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement } from 'react'
import { describe, expect, it } from 'vitest'
import { I18n, dicts, type Lang } from '../i18n'
import examples from '../content/pack-example.json'
import { Code, ContextDoc } from './Artifacts'

const inLang = (lang: Lang, ui: ReactElement) =>
  render(<I18n.Provider value={{ lang, t: dicts[lang], setLang: () => {} }}>{ui}</I18n.Provider>)

describe('Code', () => {
  it.each(['pt', 'en'] as const)('copies exactly the code it shows and announces it (%s)', async (lang) => {
    const user = userEvent.setup()
    const c = dicts[lang].demo.code
    const { container } = inLang(lang, <Code />)
    const shown = container.querySelector('pre')?.textContent ?? ''

    for (const comment of [c.before, c.during, c.after]) expect(shown).toContain(`# ${comment}`)
    expect([...container.querySelectorAll('mark')].map((m) => m.textContent)).toEqual(['context', 'tools', 'track'])

    const button = screen.getByRole('button', { name: c.copy })
    expect(button).toHaveAttribute('aria-live', 'polite')
    await user.click(button)
    expect(await navigator.clipboard.readText()).toBe(shown)
    expect(button).toHaveTextContent(c.copied)
  })
})

describe('ContextDoc', () => {
  it.each(['pt', 'en'] as const)('shows the exact text the compiler produced for the sample customer, line by line (%s)', (lang) => {
    const pack = examples.packs[lang].voice
    const { container } = inLang(lang, <ContextDoc />)
    const lines = [...container.querySelectorAll('p.font-mono')].map((p) => p.textContent)
    expect(lines.join('\n')).toBe(pack.text)
    expect(container).toHaveTextContent(`${pack.tokens} tokens`)
    // What came from the history, another channel or another agent is highlighted; the stable part is not.
    // The voice skeleton carries the earlier conversation, the one on WhatsApp, the other agent's credit and the open item.
    const marked = [...container.querySelectorAll('mark')].map((m) => m.textContent ?? '')
    expect(marked.length).toBeGreaterThanOrEqual(1)
    expect(marked.every((t) => pack.text.includes(t))).toBe(true)
    expect(examples.compiler_version).toMatch(/^compiler-/)
  })
})
