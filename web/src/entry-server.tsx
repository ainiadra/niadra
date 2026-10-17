/**
 * Entry of the prerender (scripts/prerender.mjs). Renders a page as HTML and
 * exposes the data the script uses to write the <head>, the sitemap, the
 * llms.txt and the feeds.
 */
import { StrictMode } from 'react'
import { renderToString } from 'react-dom/server'
import { StaticRouter } from 'react-router-dom'
import { AppShell } from './App'

export function render(url: string): string {
  return renderToString(
    <StrictMode>
      <StaticRouter location={url}>
        <AppShell />
      </StaticRouter>
    </StrictMode>,
  )
}

export { allPaths, feedPath, headTags, htmlLang, mdPath, metaFor, SITE, withLang } from './seo'
export { htmlOf, posts, postsIn, postPath } from './blog/posts'
export { dicts, DOCS, localize } from './i18n'
export { PRODUCTS, productPath } from './products'
export { INTEGRATIONS, INTEGRATIONS_DOCS, integrationPath } from './integrations'
export { benchmarkFact } from './content/benchmark/llms'
