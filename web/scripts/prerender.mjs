/**
 * Prerender: after the build, writes every page of the site as complete HTML
 * (Portuguese at the root, English under /en), each with its own <head>.
 * Search engines and AI read the whole content without running JavaScript; in
 * the browser, React only takes over the HTML that already arrived.
 *
 * Also writes what crawlers and AIs look for at the root of the site:
 *   sitemap.xml   every page, with the version in each language
 *   robots.txt    everything allowed, AI crawlers included
 *   llms.txt      the site map for AI, with the key facts
 *   llms-full.txt the whole text of the site in Markdown
 *   <page>.md     every page and every post in Markdown
 *   blog/feed.xml and en/blog/feed.xml  the blog's RSS feeds
 *
 * Usage: npm run build (runs after the client and server builds).
 */
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import TurndownService from 'turndown'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const DIST = join(ROOT, 'dist')
const ssr = await import(pathToFileURL(join(ROOT, 'dist-ssr', 'entry-server.js')).href)
const { SITE, dicts } = ssr
const template = readFileSync(join(DIST, 'index.html'), 'utf8')
const now = new Date().toISOString().replace(/\.\d{3}Z$/, 'Z')

// The real date of the last change of each fixed page. Bing uses the sitemap's
// lastmod as a freshness signal for AI answers, and Google only trusts it when it
// is true: the build date on every page would be a lie. We keep the hash of each
// page's content; the date moves forward only when it changes.
const LASTMOD_FILE = join(ROOT, 'src', 'content', 'lastmod.json')
const lastmods = existsSync(LASTMOD_FILE) ? JSON.parse(readFileSync(LASTMOD_FILE, 'utf8')) : {}
function lastmodFor(path, content) {
  const hash = createHash('sha256').update(content).digest('hex').slice(0, 16)
  const prev = lastmods[path]
  if (!prev || prev.hash !== hash) lastmods[path] = { hash, lastmod: now }
  return lastmods[path].lastmod
}
const PAGE_TYPES = new Set(['WebPage', 'ContactPage', 'CollectionPage', 'TechArticle'])

const fileFor = (path) => (path === '/' ? 'index.html' : `${path.slice(1)}.html`)
function write(rel, content) {
  const file = join(DIST, rel)
  mkdirSync(dirname(file), { recursive: true })
  writeFileSync(file, content)
}
const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

function renderPage(path) {
  const app = ssr.render(path)
  const meta = ssr.metaFor(path)
  // Fixed page: the date comes from the content hash. Post: from the frontmatter (date/updated).
  if (meta.post) {
    // Post: the sitemap date comes from the frontmatter; the hash only marks whether the text changed (for IndexNow).
    lastmodFor(path, readFileSync(join(ROOT, meta.post.file.replace(/^\.\.\//, 'src/')), 'utf8'))
  }
  if (!meta.post && !meta.noindex) {
    const main = /<main id="conteudo">([\s\S]*)<\/main>/.exec(app)?.[1] ?? app
    meta.modified = lastmodFor(path, `${meta.title}\n${meta.description}\n${main}`)
    for (const node of meta.jsonLd) if (PAGE_TYPES.has(node['@type'])) node.dateModified = meta.modified
  }
  // Replacement by function: "US$" and the like in the text do not become replace patterns.
  const html = template
    .replace('<html lang="pt-BR">', () => `<html lang="${ssr.htmlLang(meta.lang)}">`)
    .replace('<!--seo-->', () => ssr.headTags(meta))
    .replace('<!--app-->', () => app)
  if (html.includes('<!--app-->') || !app.includes('id="conteudo"')) throw new Error(`empty prerender at ${path}`)
  return { html, app, meta }
}

// Markdown of the pages: the <main> of each one, without interface controls.
const td = new TurndownService({ headingStyle: 'atx', codeBlockStyle: 'fenced', bulletListMarker: '-' })
td.remove(['button', 'svg', 'script', 'style', 'form', 'input', 'select', 'textarea', 'label', 'nav'])
// The benchmark's company logos: the system's name is already in the text beside each one.
td.addRule('logos', { filter: (node) => node.nodeName === 'IMG' && (node.getAttribute('src') ?? '').startsWith('/benchmark/logos/'), replacement: () => '' })
td.addRule('details',{ filter: 'summary', replacement: (content) => `\n\n### ${content.trim()}\n\n` })

function mainMarkdown(app) {
  const m = /<main id="conteudo">([\s\S]*)<\/main>/.exec(app)
  return td.turndown(m ? m[1] : app).replace(/\n{3,}/g, '\n\n').trim()
}

// Markdown of the posts: straight from the source, which is cleaner than the HTML.
const SRC = join(ROOT, 'src', 'content', 'blog')
const postSource = new Map()
for (const f of readdirSync(SRC).filter((f) => f.endsWith('.md'))) {
  const raw = readFileSync(join(SRC, f), 'utf8')
  const fm = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(raw)
  const data = Object.fromEntries(fm[1].split(/\r?\n/).map((l) => [l.slice(0, l.indexOf(':')).trim(), l.slice(l.indexOf(':') + 1).trim()]))
  postSource.set(`${data.lang}:${data.slug}`, raw.slice(fm[0].length).trim())
}

function header(meta) {
  const t = dicts[meta.lang]
  const lines = [`# ${meta.post ? meta.post.meta.title : meta.title}`, '', `> ${meta.description}`, '', `URL: ${SITE}${meta.path}`]
  if (meta.post) {
    lines.push(`${meta.lang === 'pt' ? 'Publicado em' : 'Published on'}: ${meta.post.meta.date.slice(0, 10)} · ${meta.post.meta.category} · ${t.blog.author}`)
  }
  return lines.join('\n')
}

// Every post exists in Portuguese and in English: the blog is bilingual, and a post
// without the version in the other language does not go live.
const byId = new Map()
for (const p of ssr.posts) byId.set(p.meta.id, [...(byId.get(p.meta.id) ?? []), p.meta.lang])
const missing = [...byId].filter(([, langs]) => !(langs.includes('pt') && langs.includes('en')))
if (missing.length) throw new Error(`post without both versions (pt and en): ${missing.map(([id, l]) => `${id} (only ${l.join(', ')})`).join('; ')}`)

const paths = ssr.allPaths()
const pages = []
// Broken internal links stop the build: a post written by a person or by an agent
// does not go live pointing at a page that does not exist.
// The Console (src/console) runs only in the browser: it is not prerendered, but links to it exist.
// The docs live at docs.niadra.com (Mintlify): an absolute link, outside this check.
const CLIENT_ONLY = ['/console']
const known = new Set([...paths, ...CLIENT_ONLY])
const broken = []
function checkLinks(from, html) {
  for (const m of html.matchAll(/href="(\/[^"#?]*)/g)) {
    const to = m[1]
    if (to.startsWith('/assets/') || /\.[a-z0-9]+$/i.test(to)) continue
    if (!known.has(to)) broken.push(`${from} -> ${to}`)
  }
}
for (const path of paths) {
  const { html, app, meta } = renderPage(path)
  checkLinks(path, html)
  write(fileFor(path), html)
  // Post: straight from the source, which is cleaner than the HTML.
  const body = meta.post ? postSource.get(`${meta.lang}:${meta.post.meta.slug}`) : mainMarkdown(app)
  const md = `${header(meta)}\n\n${body}\n`
  write(ssr.mdPath(path).slice(1), md)
  pages.push({ path, meta, md })
}

if (broken.length) throw new Error(`broken internal links:\n  ${[...new Set(broken)].join('\n  ')}`)

// Error page in each language, served by Caddy with status 404.
for (const [lang, path] of [['pt', '/pagina-que-nao-existe'], ['en', '/en/pagina-que-nao-existe']]) {
  const { html } = renderPage(path)
  write(lang === 'pt' ? '404.html' : 'en/404.html', html)
}

// The Console: HTML with no content and out of the search index. Caddy serves this
// file at /console and /console/* (deploy/Caddyfile); React builds the screen in the browser.
write(
  'console.html',
  template
    .replace('<!--seo-->', () => '<title>Console da Niadra</title>\n    <meta name="robots" content="noindex, nofollow" />\n    <meta name="referrer" content="no-referrer" />')
    .replace('<!--app-->', () => ''),
)

// sitemap.xml, with the language versions of each address.
const urls = pages
  .map(({ path, meta }) => {
    const alts = Object.entries(meta.alternates)
      .map(([l, p]) => `    <xhtml:link rel="alternate" hreflang="${ssr.htmlLang(l)}" href="${SITE}${p}"/>`)
      .concat(meta.alternates.en ?? meta.alternates.pt ? [`    <xhtml:link rel="alternate" hreflang="x-default" href="${SITE}${meta.alternates.en ?? meta.alternates.pt}"/>`] : [])
      .join('\n')
    const lastmod = meta.modified
    return `  <url>\n    <loc>${SITE}${path}</loc>\n    <lastmod>${lastmod}</lastmod>\n${alts}\n  </url>`
  })
  .join('\n')
write('sitemap.xml', `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">\n${urls}\n</urlset>\n`)

// robots.txt: the public content is meant to be read. The AI crawlers are listed by
// name so there is no doubt; only the API stays out.
const AI_BOTS = [
  'GPTBot', 'OAI-SearchBot', 'ChatGPT-User', 'ClaudeBot', 'Claude-User', 'Claude-SearchBot', 'anthropic-ai',
  'PerplexityBot', 'Perplexity-User', 'Google-Extended', 'Googlebot', 'Bingbot', 'Applebot', 'Applebot-Extended',
  'Amazonbot', 'meta-externalagent', 'DuckAssistBot', 'CCBot', 'cohere-ai', 'MistralAI-User', 'YouBot',
]
write(
  'robots.txt',
  [
    '# Niadra: todo o conteúdo público pode ser lido por buscadores e por IAs.',
    '# Resumo para IA: https://niadra.com/llms.txt · Texto completo: https://niadra.com/llms-full.txt',
    '',
    'User-agent: *',
    'Allow: /',
    'Disallow: /api/',
    '',
    ...AI_BOTS.map((b) => `User-agent: ${b}`),
    'Allow: /',
    'Disallow: /api/',
    '',
    `Sitemap: ${SITE}/sitemap.xml`,
    '',
  ].join('\n'),
)

// The docs (docs.niadra.com, Mintlify) have their own llms.txt; from here, only the address.
const docsHome = (lang) => ssr.localize(ssr.DOCS, lang)

// llms.txt (https://llmstxt.org): what Niadra is in a few lines, the facts
// an AI needs to answer about it, and the map in Markdown.
function facts(lang) {
  const t = dicts[lang]
  const h = t.home
  const pt = lang === 'pt'
  return [
    `- ${pt ? 'Categoria' : 'Category'}: ${h.category}`,
    `- ${pt ? 'O que é' : 'What it is'}: ${t.products.memory.sub}`,
    `- ${pt ? 'A plataforma' : 'The platform'}: ${h.h1a} ${h.h1b} ${h.sub}`,
    `- ${pt ? 'Como se conecta' : 'How it connects'}: ${h.arch.calls.map((c) => `\`${c.call}\` ${c.text.charAt(0).toLowerCase()}${c.text.slice(1)}`).join(' ')}`,
    `- ${pt ? 'Velocidade' : 'Speed'}: ${h.perf.map((p) => `${p.n}${p.unit ? ' ' + p.unit : ''}: ${p.label}`).join(' ')}`,
    `- ${pt ? 'Motor' : 'Engine'}: ${h.arch.parts.map((p) => `${p.n} (${p.d.replace(/\.$/, '')})`).join('; ')}.`,
    `- ${pt ? 'Produtos, cada um com a sua página' : 'Products, each with its own page'}: ${ssr.PRODUCTS.map((p) => `${t.products[p.key].name} (${t.products[p.key].menu.replace(/\.$/, '').toLowerCase()}) ${SITE}${ssr.withLang(ssr.productPath(p.slug), lang)}`).join('; ')}.`,
    `- SDK: ${t.products.sdkNote.d} ${docsHome(lang)}`,
    `- ${pt ? 'Integrações, cada uma com a sua página' : 'Integrations, each with its own page'}: ${ssr.INTEGRATIONS.map((i) => `${t.integrations.pages[i.key].name} ${SITE}${ssr.withLang(ssr.integrationPath(i.slug), lang)}`).join('; ')}. ${pt ? 'As 36 integrações estão na documentação:' : 'All 36 integrations are in the documentation:'} ${ssr.INTEGRATIONS_DOCS}`,
    `- ${pt ? 'Operação' : 'Operations'}: ${h.arch.base}`,
    `- ${pt ? 'Segurança' : 'Security'}: ${h.secBadges.join(', ')}. ${h.secNever.join(' ')}`,
    `- ${pt ? 'Preço' : 'Pricing'}: ${t.pricing.plans.map((p) => `${p.name}: ${p.price}, ${p.unit}`).join('; ')}.`,
    ...(ssr.benchmarkFact(lang) ? [`- Benchmark (${SITE}${ssr.withLang('/benchmark', lang)}): ${ssr.benchmarkFact(lang)}`] : []),
    `- ${pt ? 'Acesso antecipado' : 'Early access'}: ${pt ? 'a Niadra está abrindo para empresas por pedido, antes do lançamento; o pedido é pelo formulário em' : 'Niadra is opening to companies by request, before the public launch; the request goes through the form at'} ${SITE}${ssr.withLang('/enterprise', lang)}`,
  ].join('\n')
}

function section(lang) {
  const t = dicts[lang]
  const pt = lang === 'pt'
  const list = pages.filter((p) => p.meta.lang === lang)
  const main = list.filter((p) => !p.meta.post).map((p) => `- [${p.meta.title}](${SITE}${ssr.mdPath(p.path)}): ${p.meta.description}`)
  const blog = list.filter((p) => p.meta.post).map((p) => `- [${p.meta.post.meta.title}](${SITE}${ssr.mdPath(p.path)}): ${p.meta.description}`)
  return [
    `## ${pt ? 'Fatos principais' : 'Key facts'}`,
    '',
    facts(lang),
    '',
    `## ${pt ? 'Páginas (português)' : 'Pages (English)'}`,
    '',
    ...main,
    '',
    `## ${pt ? 'Documentação' : 'Documentation'}`,
    '',
    pt
      ? `- [Documentação da Niadra](${docsHome(lang)}): guias, referência da API, SDKs e MCP. O mapa em Markdown está em ${ssr.DOCS}/llms.txt.`
      : `- [Niadra documentation](${docsHome(lang)}): guides, API reference, SDKs and MCP. The Markdown map is at ${ssr.DOCS}/llms.txt.`,
    '',
    `## ${pt ? 'Blog (português)' : 'Blog (English)'}`,
    '',
    ...blog,
    '',
    `## ${pt ? 'Perguntas frequentes' : 'FAQ'}`,
    '',
    ...[...t.home.faq, ...t.products.memory.faq].map((f) => `- **${f.q}** ${f.a}`),
    '',
  ].join('\n')
}

write(
  'llms.txt',
  [
    '# Niadra',
    '',
    `> ${dicts.pt.home.description}`,
    '',
    `> ${dicts.en.home.description}`,
    '',
    `${dicts.pt.home.h1a} ${dicts.pt.home.h1b} ${dicts.pt.footer.tagline} Site: ${SITE}/ (português) · ${SITE}/en (English).`,
    '',
    section('pt'),
    section('en'),
    '## Optional',
    '',
    `- [Texto completo do site em Markdown / Full site text in Markdown](${SITE}/llms-full.txt)`,
    `- [Mapa do site / Sitemap](${SITE}/sitemap.xml)`,
    `- [Feed do blog (RSS)](${SITE}/blog/feed.xml) · [Blog feed (RSS)](${SITE}/en/blog/feed.xml)`,
    '',
  ].join('\n'),
)

write(
  'llms-full.txt',
  [
    '# Niadra: texto completo do site / full site text',
    '',
    `> ${dicts.pt.home.description}`,
    '',
    `> ${dicts.en.home.description}`,
    '',
    ...pages.map((p) => p.md.trim()),
  ].join('\n\n---\n\n') + '\n',
)

// RSS feeds, one per language, with the full text of every post.
const rfc822 = (d) => new Date(d.length > 10 ? d : `${d}T12:00:00Z`).toUTCString()
for (const lang of ['pt', 'en']) {
  const t = dicts[lang]
  const items = ssr.postsIn(lang).map((p) => {
    const url = `${SITE}${ssr.withLang(ssr.postPath(p), lang)}`
    return [
      '    <item>',
      `      <title>${esc(p.meta.title)}</title>`,
      `      <link>${url}</link>`,
      `      <guid isPermaLink="true">${url}</guid>`,
      `      <pubDate>${rfc822(p.meta.date)}</pubDate>`,
      `      <category>${esc(p.meta.category)}</category>`,
      `      <description>${esc(p.meta.description)}</description>`,
      `      <content:encoded><![CDATA[${ssr.htmlOf(p).replace(/]]>/g, ']]]]><![CDATA[>')}]]></content:encoded>`,
      '    </item>',
    ].join('\n')
  })
  const self = `${SITE}${ssr.feedPath(lang)}`
  write(
    ssr.feedPath(lang).slice(1),
    [
      '<?xml version="1.0" encoding="UTF-8"?>',
      '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:content="http://purl.org/rss/1.0/modules/content/">',
      '  <channel>',
      `    <title>${esc(t.blog.title)}</title>`,
      `    <link>${SITE}${ssr.withLang('/blog', lang)}</link>`,
      `    <description>${esc(t.blog.description)}</description>`,
      `    <language>${ssr.htmlLang(lang)}</language>`,
      `    <atom:link href="${self}" rel="self" type="application/rss+xml"/>`,
      ...items,
      '  </channel>',
      '</rss>',
      '',
    ].join('\n'),
  )
}

// Only the pages that exist stay in the dates file. The hash of each one goes to
// the deploy, which notifies IndexNow only of what changed since the last notice.
const live = new Set(paths)
for (const k of Object.keys(lastmods)) if (!live.has(k)) delete lastmods[k]
writeFileSync(join(ROOT, 'dist-ssr', 'hashes.json'), JSON.stringify(Object.fromEntries(paths.map((p) => [`${SITE}${p}`, lastmods[p].hash]))) + '\n')
writeFileSync(LASTMOD_FILE, JSON.stringify(Object.fromEntries(Object.entries(lastmods).sort()), null, 2) + '\n')

console.log(`prerendered ${pages.length} pages + 2 error pages; sitemap, robots, llms.txt, llms-full.txt, ${pages.length} .md and 2 feeds`)
