/**
 * Blog posts in Markdown, converted to HTML at build time (`marked` never
 * reaches the browser). Each `src/content/blog/<id>.<lang>.md` becomes a
 * module with the metadata, the HTML, the FAQ and the headings.
 *
 * The frontmatter is validated here: a post without a title, date or
 * description breaks the build instead of going live half done.
 */
import { Marked, type Tokens } from 'marked'
import type { Plugin } from 'vite'

type PostMeta = {
  id: string
  lang: 'pt' | 'en'
  slug: string
  title: string
  description: string
  date: string
  updated?: string
  /** Tiebreaker for posts with the same date: the lowest comes first (and is featured). */
  order: number
  category: string
  icon: string
  cover: string
  keywords: string[]
  /** The post's format, which groups the blog index: news with analysis, a pain told through a case, a guide, a measurement, an explainer. */
  kind: PostKind
}

export type PostKind = 'news' | 'case' | 'guide' | 'measured' | 'explainer'
const KINDS = new Set<string>(['news', 'case', 'guide', 'measured', 'explainer'])

type Post = {
  meta: PostMeta
  html: string
  faq: { q: string; a: string }[]
  headings: { id: string; text: string }[]
  words: number
  minutes: number
}

const REQUIRED = ['id', 'lang', 'slug', 'title', 'description', 'date', 'category', 'icon', 'cover', 'keywords', 'kind'] as const
const ICONS = new Set(['bolt', 'hub', 'shield', 'code', 'lock', 'key', 'target', 'eye', 'doc', 'globe', 'user', 'layers', 'search', 'archive', 'sliders', 'clock', 'cloud', 'check'])

function splitFrontmatter(src: string): { data: Record<string, string>; body: string } {
  const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(src)
  if (!m) return { data: {}, body: src }
  const data: Record<string, string> = {}
  for (const line of m[1].split(/\r?\n/)) {
    const i = line.indexOf(':')
    if (i < 1) continue
    data[line.slice(0, i).trim()] = line.slice(i + 1).trim().replace(/^(['"])(.*)\1$/, '$2')
  }
  return { data, body: src.slice(m[0].length) }
}

const slugify = (s: string) =>
  s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
const plain = (html: string) =>
  html.replace(/<[^>]+>/g, '').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').trim()

const FAQ_TITLE = /^(perguntas frequentes|frequently asked questions)$/i

function compilePost(src: string, file: string): Post {
  const { data, body } = splitFrontmatter(src)
  for (const k of REQUIRED) if (!data[k]) throw new Error(`${file}: missing "${k}" in the frontmatter`)
  if (data.lang !== 'pt' && data.lang !== 'en') throw new Error(`${file}: lang must be pt or en`)
  // Date with time and offset, as Google asks for BlogPosting: 2026-09-21T21:49:00-03:00.
  const ISO = /^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2}))?$/
  if (!ISO.test(data.date)) throw new Error(`${file}: date must be YYYY-MM-DD or YYYY-MM-DDTHH:MM-03:00`)
  if (data.updated && !ISO.test(data.updated)) throw new Error(`${file}: updated must use the same format as date`)
  if (!ICONS.has(data.icon)) throw new Error(`${file}: icon "${data.icon}" does not exist in Icon.tsx`)
  if (!KINDS.has(data.kind)) throw new Error(`${file}: kind "${data.kind}" is not one of ${[...KINDS].join(', ')}`)
  if (/[—–]/.test(src)) throw new Error(`${file}: em dash in the text`)
  // A "# ..." comment inside a code block does not count as a heading.
  if (/^# /m.test(body.replace(/```[\s\S]*?```/g, ''))) throw new Error(`${file}: the title comes from the frontmatter, do not use "# "`)

  const headings: Post['headings'] = []
  const marked = new Marked({ gfm: true })
  marked.use({
    renderer: {
      heading({ tokens, depth }: Tokens.Heading) {
        const inner = this.parser.parseInline(tokens)
        const id = slugify(plain(inner))
        if (depth === 2) headings.push({ id, text: plain(inner) })
        return `<h${depth} id="${id}">${inner}</h${depth}>\n`
      },
      // An external source opens in another tab; an internal link stays in the same one.
      link({ href, title, tokens }: Tokens.Link) {
        const inner = this.parser.parseInline(tokens)
        const ext = /^https?:\/\//.test(href)
        return `<a href="${esc(href)}"${title ? ` title="${esc(title)}"` : ''}${ext ? ' target="_blank" rel="noopener"' : ''}>${inner}</a>`
      },
    },
  })

  // A wide table scrolls inside its own frame, never the page.
  const html = (marked.parse(body) as string).replace(/<table>/g, '<div class="md-table"><table>').replace(/<\/table>/g, '</table></div>')

  const faq: Post['faq'] = []
  let inFaq = false
  for (const tok of marked.lexer(body)) {
    if (tok.type === 'heading' && tok.depth === 2) inFaq = FAQ_TITLE.test(tok.text.trim())
    else if (inFaq && tok.type === 'heading' && tok.depth === 3) faq.push({ q: plain(marked.parseInline(tok.text) as string), a: '' })
    else if (inFaq && tok.type === 'paragraph' && faq.length) {
      const last = faq[faq.length - 1]
      last.a = `${last.a} ${plain(marked.parseInline(tok.text) as string)}`.trim()
    }
  }

  // Counts the words of the text that is read: no code blocks and no link addresses.
  const text = body.replace(/```[\s\S]*?```/g, ' ').replace(/\]\([^)]*\)/g, ']').replace(/[#>*_`|[\]()]/g, ' ')
  const words = text.split(/\s+/).filter(Boolean).length

  const meta: PostMeta = {
    id: data.id,
    lang: data.lang,
    slug: data.slug,
    title: data.title,
    description: data.description,
    date: data.date,
    order: Number(data.order ?? 99),
    ...(data.updated ? { updated: data.updated } : {}),
    category: data.category,
    icon: data.icon,
    cover: data.cover,
    keywords: data.keywords.split(',').map((k) => k.trim()).filter(Boolean),
    kind: data.kind as PostKind,
  }
  return { meta, html, faq, headings, words, minutes: Math.max(1, Math.round(words / 220)) }
}

/** A post's data without the text: what the list, the <head> and the sitemap use. */
export type PostInfo = Omit<Post, 'html'>

/**
 * `post.md?meta` brings only the data (it goes into the main bundle, it is small);
 * `post.md` brings the text as HTML (`export const html`), loaded only when the
 * post is opened. In the prerender both come together.
 */
export function markdown(): Plugin {
  return {
    name: 'niadra-markdown',
    transform(code, id) {
      const [file, query = ''] = id.split('?')
      if (!file.endsWith('.md')) return null
      const { html, ...info } = compilePost(code, file)
      if (query.includes('meta')) return { code: `export default ${JSON.stringify(info)}`, map: null }
      return { code: `export const html = ${JSON.stringify(html)}`, map: null }
    },
  }
}
