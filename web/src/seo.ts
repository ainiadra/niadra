/**
 * Everything search engines and AI read besides the page text: title, description,
 * canonical address, language versions, Open Graph and structured data
 * (schema.org in JSON-LD). The same module serves the prerender (which writes
 * each page's <head>) and the browser (which updates the title on navigation).
 */
import { dicts, langOfPath, stripLang, type Lang } from './i18n'
import { findPost, postPath, postsIn, translationOf, type Post } from './blog/posts'
import { PRODUCTS, PRODUCT_PATHS, productPath } from './products'
import { INTEGRATIONS, INTEGRATIONS_PATH, INTEGRATION_PATHS, integrationPath } from './integrations'
import { BENCHMARK_LIVE } from './content/benchmark'

export const SITE = 'https://niadra.com'
const OG_IMAGE = `${SITE}/og.png`
const LOGO = `${SITE}/logo-niadra.png`

/** The fixed pages, by their Portuguese path. Each one also exists under /en. */
const STATIC: readonly string[] = [
  '/', '/produtos', ...PRODUCT_PATHS, INTEGRATIONS_PATH, ...INTEGRATION_PATHS, '/recursos', '/preco', '/enterprise', '/blog', '/sobre',
  // The benchmark joins the prerender, the sitemap and llms.txt with its first run measured in the region.
  ...(BENCHMARK_LIVE ? ['/benchmark'] : []),
  // The feature comparison: what Niadra does against what each memory system documents, with sources.
  '/comparativo',
]

export const withLang = (base: string, lang: Lang) => (lang === 'en' ? (base === '/' ? '/en' : `/en${base}`) : base)
const abs = (path: string) => `${SITE}${path}`
/** The Markdown version of each page, for agents and AIs that prefer plain text. */
export const mdPath = (path: string) => (path === '/' ? '/index.md' : `${path}.md`)
export const feedPath = (lang: Lang) => withLang('/blog/feed.xml', lang)

const HTML_LANG: Record<Lang, string> = { pt: 'pt-BR', en: 'en' }
const OG_LOCALE: Record<Lang, string> = { pt: 'pt_BR', en: 'en_US' }

export type Meta = {
  lang: Lang
  path: string
  title: string
  description: string
  alternates: Partial<Record<Lang, string>>
  type: 'website' | 'article'
  noindex: boolean
  jsonLd: object[]
  published?: string
  modified?: string
  keywords?: string[]
  section?: string
  post?: Post
}

/** The equivalent address in the other language (a post has its own slug in each language). */
export function altPath(pathname: string, target: Lang): string {
  const lang = langOfPath(pathname)
  const base = stripLang(pathname)
  const m = /^\/blog\/([^/]+)$/.exec(base)
  if (m) {
    const post = findPost(lang, m[1])
    const tr = post && translationOf(post, target)
    return withLang(tr ? postPath(tr) : '/blog', target)
  }
  return withLang(STATIC.includes(base) || base === '/benchmark' ? base : '/', target)
}

/** Every page that exists, in both languages, in sitemap order. */
export function allPaths(): string[] {
  const out: string[] = []
  for (const lang of ['pt', 'en'] as Lang[]) {
    for (const base of STATIC) out.push(withLang(base, lang))
    for (const p of postsIn(lang)) out.push(withLang(postPath(p), lang))
  }
  return out
}

const org = (lang: Lang) => ({
  '@type': 'Organization',
  '@id': `${SITE}/#org`,
  name: 'Niadra',
  url: `${SITE}/`,
  logo: { '@type': 'ImageObject', url: LOGO, width: 512, height: 512 },
  description: dicts[lang].footer.tagline,
  slogan: `${dicts[lang].home.h1a} ${dicts[lang].home.h1b}`,
  // The topics Niadra is a reference on: the category and the products, by name.
  knowsAbout: [dicts[lang].home.category, ...PRODUCTS.map((p) => dicts[lang].products[p.key].name)],
  // Sold to companies anywhere; the data of each customer stays in one region, stated in the contract.
  areaServed: 'Worldwide',
  contactPoint: {
    '@type': 'ContactPoint',
    contactType: 'sales',
    url: abs(withLang('/enterprise', lang)),
    availableLanguage: ['Portuguese', 'English'],
  },
})

const website = (lang: Lang) => ({
  '@type': 'WebSite',
  '@id': `${SITE}/#site`,
  url: `${SITE}/`,
  name: 'Niadra',
  description: dicts[lang].home.description,
  inLanguage: [HTML_LANG.pt, HTML_LANG.en],
  publisher: { '@id': `${SITE}/#org` },
})

/** The product as software: what it is, what it does and how much it costs. It answers "what is Niadra". */
function app(lang: Lang) {
  const t = dicts[lang]
  return {
    '@type': 'SoftwareApplication',
    '@id': `${SITE}/#app`,
    name: 'Niadra',
    url: abs(withLang('/', lang)),
    description: `${t.home.description} ${t.home.sub}`,
    applicationCategory: 'BusinessApplication',
    applicationSubCategory: lang === 'pt' ? 'Camada de memória omnichannel para agentes de IA de atendimento e internos' : 'Omnichannel memory layer for customer-facing and internal AI agents',
    operatingSystem: lang === 'pt' ? 'Nuvem gerenciada; SDK em Python e TypeScript, API HTTP e MCP' : 'Managed cloud; SDK for Python and TypeScript, HTTP API and MCP',
    inLanguage: HTML_LANG[lang],
    publisher: { '@id': `${SITE}/#org` },
    featureList: [
      ...PRODUCTS.map((p) => `${t.products[p.key].name}: ${t.products[p.key].menu}`),
      ...t.home.arch.calls.map((c) => `${c.call}: ${c.text}`),
      ...t.home.secBadges,
    ],
    offers: {
      '@type': 'Offer',
      url: abs(withLang('/preco', lang)),
      areaServed: 'Worldwide',
      priceCurrency: 'USD',
      description: `${t.pricing.plans[0].name}: ${t.pricing.plans[0].price} ${t.pricing.plans[0].unit}. ${t.pricing.plans[1].name}: ${t.pricing.plans[1].price}, ${t.pricing.plans[1].unit}.`,
      priceSpecification: {
        '@type': 'UnitPriceSpecification',
        priceCurrency: 'USD',
        minPrice: 2,
        maxPrice: 3,
        referenceQuantity: { '@type': 'QuantitativeValue', value: 1000, unitText: lang === 'pt' ? 'conversas ou tarefas' : 'conversations or tasks' },
      },
    },
  }
}

const faqPage = (items: { q: string; a: string }[], url: string) => ({
  '@type': 'FAQPage',
  '@id': `${url}#faq`,
  mainEntity: items.map((f) => ({ '@type': 'Question', name: f.q, acceptedAnswer: { '@type': 'Answer', text: f.a } })),
})

function crumbs(lang: Lang, trail: readonly { name: string; path: string }[]) {
  const home = { name: 'Niadra', path: withLang('/', lang) }
  return {
    '@type': 'BreadcrumbList',
    itemListElement: [home, ...trail].map((c, i) => ({ '@type': 'ListItem', position: i + 1, name: c.name, item: abs(c.path) })),
  }
}

const webPage = (type: string, m: { path: string; title: string; description: string; lang: Lang }) => ({
  '@type': type,
  '@id': abs(m.path),
  url: abs(m.path),
  name: m.title,
  description: m.description,
  inLanguage: HTML_LANG[m.lang],
  isPartOf: { '@id': `${SITE}/#site` },
  about: { '@id': `${SITE}/#app` },
  primaryImageOfPage: OG_IMAGE,
})

export function metaFor(pathname: string): Meta {
  const lang = langOfPath(pathname)
  const base = stripLang(pathname)
  const t = dicts[lang]
  const other: Lang = lang === 'pt' ? 'en' : 'pt'
  const pages: Record<string, { title: string; description: string; type: string; crumb?: string; parent?: { name: string; base: string } }> = {
    '/': { title: t.home.title, description: t.home.description, type: 'WebPage' },
    '/produtos': { title: t.products.title, description: t.products.description, type: 'WebPage', crumb: t.nav.products },
    ...Object.fromEntries(
      PRODUCTS.map((p) => {
        const c = t.products[p.key]
        return [productPath(p.slug), { title: c.title, description: c.description, type: 'WebPage', crumb: c.name, parent: { name: t.nav.products, base: '/produtos' } }]
      }),
    ),
    [INTEGRATIONS_PATH]: { title: t.integrations.title, description: t.integrations.description, type: 'CollectionPage', crumb: t.integrations.crumb },
    ...Object.fromEntries(
      INTEGRATIONS.map((i) => {
        const c = t.integrations.pages[i.key]
        return [integrationPath(i.slug), { title: `${c.title} | Niadra`, description: c.description, type: 'WebPage', crumb: c.name, parent: { name: t.integrations.crumb, base: INTEGRATIONS_PATH } }]
      }),
    ),
    '/recursos': { title: t.resources.title, description: t.resources.description, type: 'WebPage', crumb: t.nav.resources },
    '/preco': { title: t.pricing.title, description: t.pricing.description, type: 'WebPage', crumb: t.nav.pricing },
    '/enterprise': { title: t.enterprise.title, description: t.enterprise.description, type: 'ContactPage', crumb: t.nav.enterprise },
    '/blog': { title: t.blog.title, description: t.blog.description, type: 'CollectionPage', crumb: t.nav.blog },
    '/sobre': { title: t.about.title, description: t.about.description, type: 'AboutPage', crumb: t.about.h1a.replace(/\.$/, '') },
    '/benchmark': { title: t.benchmark.title, description: t.benchmark.description, type: 'WebPage', crumb: 'Benchmark' },
    '/comparativo': { title: t.comparison.title, description: t.comparison.description, type: 'WebPage', crumb: t.comparison.crumb },
  }

  const page = pages[base]
  if (page) {
    const path = withLang(base, lang)
    const jsonLd: object[] = [org(lang), website(lang), webPage(page.type, { path, title: page.title, description: page.description, lang })]
    const trail = page.parent ? [{ name: page.parent.name, path: withLang(page.parent.base, lang) }] : []
    if (base !== '/') jsonLd.push(crumbs(lang, [...trail, { name: page.crumb ?? page.title, path }]))
    if (base === '/' || base === '/produtos' || base === productPath('memoria') || base === '/preco') jsonLd.push(app(lang))
    if (base === '/') jsonLd.push(faqPage(t.home.faq, abs(path)))
    if (base === productPath('memoria')) jsonLd.push(faqPage(t.products.memory.faq, abs(path)))
    if (base === '/preco') jsonLd.push(faqPage(t.pricing.faq, abs(path)))
    // Each integration page shows its own questions plus the two every buyer asks: the same list, as data.
    const integration = INTEGRATIONS.find((i) => integrationPath(i.slug) === base)
    if (integration) jsonLd.push(faqPage([...t.integrations.pages[integration.key].faq, ...t.integrations.sharedFaq], abs(path)))
    if (base === '/blog') {
      jsonLd.push({
        '@type': 'Blog',
        '@id': `${abs(path)}#blog`,
        name: t.blog.title,
        description: t.blog.description,
        inLanguage: HTML_LANG[lang],
        publisher: { '@id': `${SITE}/#org` },
        blogPost: postsIn(lang).map((p) => ({ '@id': `${abs(withLang(postPath(p), lang))}#post` })),
      })
    }
    // Before its first published run the benchmark page shows no numbers and stays out of the index.
    const noindex = base === '/benchmark' && !BENCHMARK_LIVE
    return {
      lang, path, title: page.title, description: page.description, type: 'website', noindex, jsonLd,
      alternates: { [lang]: path, [other]: withLang(base, other) },
    }
  }

  const m = /^\/blog\/([^/]+)$/.exec(base)
  const post = m ? findPost(lang, m[1]) : undefined
  if (post) {
    const path = withLang(postPath(post), lang)
    const tr = translationOf(post, other)
    const url = abs(path)
    const jsonLd: object[] = [
      org(lang),
      website(lang),
      {
        '@type': 'BlogPosting',
        '@id': `${url}#post`,
        mainEntityOfPage: url,
        url,
        headline: post.meta.title,
        description: post.meta.description,
        datePublished: post.meta.date,
        dateModified: post.meta.updated ?? post.meta.date,
        inLanguage: HTML_LANG[lang],
        articleSection: post.meta.category,
        keywords: post.meta.keywords.join(', '),
        wordCount: post.words,
        timeRequired: `PT${post.minutes}M`,
        image: OG_IMAGE,
        author: { '@type': 'Organization', name: t.blog.author, url: `${SITE}/` },
        publisher: { '@id': `${SITE}/#org` },
        isPartOf: { '@id': `${abs(withLang('/blog', lang))}#blog` },
        about: { '@id': `${SITE}/#app` },
        ...(tr ? { workTranslation: { '@id': `${abs(withLang(postPath(tr), other))}#post` } } : {}),
      },
      crumbs(lang, [
        { name: t.nav.blog, path: withLang('/blog', lang) },
        { name: post.meta.title, path },
      ]),
    ]
    if (post.faq.length) jsonLd.push(faqPage(post.faq, url))
    return {
      lang, path, title: `${post.meta.title} | Niadra`, description: post.meta.description, type: 'article', noindex: false, jsonLd,
      alternates: tr ? { [lang]: path, [other]: withLang(postPath(tr), other) } : { [lang]: path },
      published: post.meta.date, modified: post.meta.updated ?? post.meta.date, keywords: post.meta.keywords, section: post.meta.category, post,
    }
  }

  return {
    lang, path: pathname, title: `${t.notFound.h1} | Niadra`, description: t.notFound.body, type: 'website', noindex: true,
    jsonLd: [], alternates: {},
  }
}

const attr = (s: string) => s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')
const text = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;')

/** A page's <head>, as text, for the prerender. */
export function headTags(m: Meta): string {
  const t = dicts[m.lang]
  const url = abs(m.path)
  const tags = [
    `<title>${text(m.title)}</title>`,
    `<meta name="description" content="${attr(m.description)}" />`,
    `<meta name="robots" content="${m.noindex ? 'noindex, follow' : 'index, follow, max-snippet:-1, max-image-preview:large'}" />`,
  ]
  if (!m.noindex) {
    tags.push(`<link rel="canonical" href="${url}" />`)
    for (const [l, p] of Object.entries(m.alternates)) tags.push(`<link rel="alternate" hreflang="${HTML_LANG[l as Lang]}" href="${abs(p!)}" />`)
    // x-default: the page for someone who reads neither language. English serves that reader better.
    const xdefault = m.alternates.en ?? m.alternates.pt
    if (xdefault) tags.push(`<link rel="alternate" hreflang="x-default" href="${abs(xdefault)}" />`)
    tags.push(`<link rel="alternate" type="text/markdown" href="${mdPath(m.path)}" title="Markdown" />`)
  }
  tags.push(
    `<link rel="alternate" type="application/rss+xml" href="${feedPath(m.lang)}" title="${attr(t.blog.title)}" />`,
    `<meta property="og:site_name" content="Niadra" />`,
    `<meta property="og:type" content="${m.type}" />`,
    `<meta property="og:title" content="${attr(m.title)}" />`,
    `<meta property="og:description" content="${attr(m.description)}" />`,
    `<meta property="og:url" content="${url}" />`,
    `<meta property="og:image" content="${OG_IMAGE}" />`,
    `<meta property="og:image:width" content="1200" />`,
    `<meta property="og:image:height" content="630" />`,
    `<meta property="og:image:alt" content="${attr(t.home.h1a + ' ' + t.home.h1b)}" />`,
    `<meta property="og:locale" content="${OG_LOCALE[m.lang]}" />`,
    `<meta property="og:locale:alternate" content="${OG_LOCALE[m.lang === 'pt' ? 'en' : 'pt']}" />`,
    `<meta name="twitter:card" content="summary_large_image" />`,
    `<meta name="twitter:title" content="${attr(m.title)}" />`,
    `<meta name="twitter:description" content="${attr(m.description)}" />`,
    `<meta name="twitter:image" content="${OG_IMAGE}" />`,
  )
  if (m.type === 'article') {
    tags.push(`<meta property="article:published_time" content="${m.published}" />`)
    tags.push(`<meta property="article:modified_time" content="${m.modified}" />`)
    if (m.section) tags.push(`<meta property="article:section" content="${attr(m.section)}" />`)
    for (const k of m.keywords ?? []) tags.push(`<meta property="article:tag" content="${attr(k)}" />`)
  }
  if (m.jsonLd.length) {
    // "<" escaped: a text with "</script>" does not close the tag early.
    const json = JSON.stringify({ '@context': 'https://schema.org', '@graph': m.jsonLd }).replace(/</g, '\\u003c')
    tags.push(`<script type="application/ld+json">${json}</script>`)
  }
  return tags.join('\n    ')
}

export const htmlLang = (l: Lang) => HTML_LANG[l]
