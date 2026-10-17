import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { allPaths } from './seo'

/**
 * The addresses Caddy sends elsewhere (deploy/Caddyfile) protect links already indexed and shared:
 * each one must stay, and each must lead to a page the site serves, never to a 404.
 */
const caddy = readFileSync(resolve(process.cwd(), 'deploy/Caddyfile'), 'utf8')
const REDIRECTS = [...caddy.matchAll(/^\s*redir (\/\S*) (\S+) (?:301|308|permanent)$/gm)].map(([, from, to]) => ({ from, to }))

/** Files the build writes (scripts/prerender.mjs) or public/ holds, which are not pages. */
const GENERATED = new Set(['/blog/feed.xml', '/en/blog/feed.xml', '/llms.txt'])
const served = (path: string) =>
  allPaths().includes(path) || GENERATED.has(path) || existsSync(resolve(process.cwd(), 'public', path.slice(1)))

describe('public redirects', () => {
  it.each([
    ['/produtos/afirmacao', '/produtos/conferencia'],
    ['/en/produtos/afirmacao', '/en/produtos/conferencia'],
    ['/comparativo-de-memoria', '/comparativo'],
    ['/glossario', '/recursos'],
    ['/en/glossario', '/en/recursos'],
    ['/glossary', '/en/recursos'],
    ['/en/glossary', '/en/recursos'],
  ])('keeps %s going to %s', (from, to) => {
    expect(REDIRECTS).toContainEqual({ from, to })
  })

  it('leads every redirect inside the site to a page or file the site serves', () => {
    expect(REDIRECTS.length).toBeGreaterThan(40)
    const broken = REDIRECTS.filter(({ to }) => to.startsWith('/') && !served(to.replace(/[#?].*$/, '')))
    expect(broken).toEqual([])
  })

  it('never shadows a page with a redirect', () => {
    const pages = new Set(allPaths())
    expect(REDIRECTS.filter(({ from }) => pages.has(from))).toEqual([])
  })
})
