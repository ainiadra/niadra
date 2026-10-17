import { readdirSync, readFileSync } from 'node:fs'
import { join, relative, sep } from 'node:path'
import { describe, expect, it } from 'vitest'

/** Every file of the Console, as written: screens, labels, UI, the API layer, the tests and the README. */
const root = join(__dirname, '..')
const walk = (dir: string): string[] =>
  readdirSync(dir, { withFileTypes: true }).flatMap((entry) => (entry.isDirectory() ? walk(join(dir, entry.name)) : /\.(tsx?|md)$/.test(entry.name) ? [join(dir, entry.name)] : []))
const files = walk(root).map((file) => [relative(root, file).split(sep).join('/'), readFileSync(file, 'utf8')] as const)
/** What a person reads: every file but the tests, which name what they must not find. */
const texts = files.filter(([path]) => !path.startsWith('test/'))

describe('Console copy', () => {
  it('reads every file of the Console', () => {
    expect(texts.map(([path]) => path)).toEqual(expect.arrayContaining(['screens/Review.tsx', 'screens/Usage.tsx', 'lib/labels.ts', 'README.md']))
  })

  it('never promises that a verdict moves a threshold or changes memory', () => {
    // A verdict runs as a test case and corrects the count it judges; it tunes no threshold and never
    // touches memory, which changes only through feedback or retract.
    const promise = /\blimiar(es)?\b|veredito (muda|altera|corrige) a mem[oó]ria/i
    expect(texts.filter(([, text]) => promise.test(text)).map(([path]) => path)).toEqual([])
  })

  it('uses the words of the glossary: comprovante, never recibo; agente, never bot', () => {
    // "Recibo" is an internal codename; the address /recibos stays, the text does not.
    // Letters with accents are word characters here: "botão" is not "bot".
    const codename = /(?<!\p{L})recibos?(?!\p{L})/iu
    const forbidden = /(?<!\p{L})(chat)?bots?(?!\p{L})/iu
    const prose = texts.filter(([path]) => path !== 'paths.ts' && path !== 'README.md')
    expect(prose.filter(([, text]) => codename.test(text.replace(/\/recibos\b|['"`]recibos\b|recibos\//g, ''))).map(([path]) => path)).toEqual([])
    expect(prose.filter(([, text]) => forbidden.test(text)).map(([path]) => path)).toEqual([])
  })

  it('has no em dash or en dash, in the tests too', () => {
    // En dash and em dash, by code, so this file never holds one.
    const dash = new RegExp(`[${String.fromCharCode(0x2013, 0x2014)}]`)
    expect(files.filter(([, text]) => dash.test(text)).map(([path]) => path)).toEqual([])
  })
})
