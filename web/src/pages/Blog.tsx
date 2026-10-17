import { useMemo, useState } from 'react'
import type { PostKind } from '../../build/markdown'
import { useT } from '../i18n'
import { Link } from '../i18n/link'
import { formatDate, postPath, postsIn, preload, type Post } from '../blog/posts'
import { Icon } from '../components/Icon'
import { Byline, PostCover } from '../components/PostCover'
import { feedPath } from '../seo'
import { h1cls, h3cls, lede, panel, textLink, wrap } from '../ui'

const norm = (s: string) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()

/** The order of the sections: what starts from a pain or a piece of news first, the reference material last. */
const KINDS: readonly PostKind[] = ['news', 'case', 'guide', 'measured', 'explainer']

/**
 * The blog list: title and search on the same line, the newest post featured with its
 * cover, and then one section per format (news with analysis, pains and real cases,
 * practical guides, what we measured, fundamentals), each a table of title, category and
 * date. The reader picks by what the post does for them, not by date alone. The search
 * filters title, description, category and keywords, without going to the server.
 */
export function Blog() {
  const { t, lang } = useT()
  const b = t.blog
  const all = postsIn(lang)
  const [q, setQ] = useState('')

  const found = useMemo(() => {
    const k = norm(q.trim())
    if (!k) return all
    return all.filter((p) => norm([p.meta.title, p.meta.description, p.meta.category, ...p.meta.keywords].join(' ')).includes(k))
  }, [q, all])

  const searching = q.trim().length > 0
  const featured = searching ? undefined : all[0]
  const sections = useMemo(
    () => KINDS.map((kind) => ({ kind, posts: all.filter((p) => p.meta.kind === kind && p !== featured) })).filter((s) => s.posts.length > 0),
    [all, featured],
  )

  return (
    <section className={`${wrap} pb-16 pt-10 sm:pt-14 lg:pb-24 lg:pt-20`}>
      <div className="flex flex-col gap-6 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className={h1cls}>{b.h1}</h1>
          <p className={`${lede} mt-4`}>{b.sub}</p>
        </div>
        <label className="relative block w-full sm:max-w-[20rem]">
          <span className="sr-only">{b.searchLabel}</span>
          <Icon name="search" size={18} className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-ink3" />
          <input
            id="blog-busca"
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={b.searchPh}
            className="min-h-12 w-full rounded-lg border border-edge bg-surface pl-11 pr-4 text-[1rem] text-ink placeholder:text-ink3 focus:outline-none focus-visible:outline-2 focus-visible:outline-ink"
          />
        </label>
      </div>

      {featured && <Featured post={featured} />}

      {searching ? (
        <div className="mt-10">
          <h2 className={`${h3cls} text-[1.3rem]`}>{b.results}</h2>
          {found.length > 0 ? (
            <Table posts={found} header />
          ) : (
            <p className="mt-4 border-t border-line pt-5 text-ink2" role="status">
              {b.empty}
            </p>
          )}
        </div>
      ) : (
        <div className="mt-14 grid gap-12 lg:mt-20 lg:gap-16">
          {sections.map(({ kind, posts }) => (
            <section key={kind} aria-labelledby={`blog-${kind}`}>
              <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)] md:gap-10">
                <h2 id={`blog-${kind}`} className={`${h3cls} text-[1.3rem] sm:text-[1.45rem]`}>{b.sections[kind].h}</h2>
                <p className="max-w-[60ch] text-[0.98rem] leading-relaxed text-ink2">{b.sections[kind].d}</p>
              </div>
              <Table posts={posts} />
            </section>
          ))}
        </div>
      )}

      <a href={feedPath(lang)} className={`${textLink} mt-10`}>
        {b.feed}
      </a>
    </section>
  )
}

/** The newest post, with its cover beside the title: the one piece the blog puts forward today. */
function Featured({ post }: { post: Post }) {
  const { t, lang } = useT()
  const b = t.blog
  return (
    <article className={`${panel} group relative mt-10 grid gap-5 p-2 transition-colors hover:border-edge md:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] md:gap-8 lg:mt-14`}>
      <PostCover post={post} />
      <div className="flex flex-col justify-center px-3 pb-4 md:py-6 md:pr-8">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.88rem] text-ink3">
          <span className="font-semibold text-accent">{b.featured}</span>
          <span aria-hidden="true" className="h-3.5 w-px bg-line" />
          <time dateTime={post.meta.date}>{formatDate(post.meta.date, lang)}</time>
          <span aria-hidden="true" className="h-3.5 w-px bg-line" />
          <span>{post.meta.category}</span>
        </div>
        <h2 className={`${h3cls} mt-3 text-balance text-[clamp(1.35rem,2.6vw,1.9rem)] leading-[1.12]`}>
          {/* The link covers the whole card, but the accessible name is only the title. */}
          <Link to={postPath(post)} onPointerEnter={() => preload(post)} className="after:absolute after:inset-0 after:rounded-xl focus:outline-none focus-visible:after:outline-2 focus-visible:after:outline-ink">
            {post.meta.title}
          </Link>
        </h2>
        <p className="mt-3 text-[1rem] leading-relaxed text-ink2">{post.meta.description}</p>
        <div className="mt-5 flex items-center justify-between gap-4">
          <Byline name={b.author} />
          <span className="text-[0.92rem] font-semibold text-ink underline decoration-accent decoration-2 underline-offset-[5px]">{b.read}</span>
        </div>
      </div>
    </article>
  )
}

function Table({ posts, header }: { posts: Post[]; header?: boolean }) {
  const { t } = useT()
  return (
    <ul className="mt-5 border-t border-line">
      {header && (
        <li className="hidden grid-cols-[minmax(0,1fr)_11rem_11rem] gap-6 border-b border-line py-3 text-[0.84rem] font-semibold text-ink3 md:grid" aria-hidden="true">
          {t.blog.cols.map((c) => (
            <span key={c}>{c}</span>
          ))}
        </li>
      )}
      {posts.map((p) => (
        <Row key={p.meta.id} post={p} />
      ))}
    </ul>
  )
}

function Row({ post }: { post: Post }) {
  const { lang } = useT()
  return (
    <li className="relative grid gap-1 border-b border-line py-5 transition-colors hover:bg-surface md:grid-cols-[minmax(0,1fr)_11rem_11rem] md:items-baseline md:gap-6 md:px-0">
      <Link to={postPath(post)} onPointerEnter={() => preload(post)} className="text-[1.02rem] font-semibold leading-snug text-ink after:absolute after:inset-0 focus:outline-none focus-visible:after:outline-2 focus-visible:after:outline-ink">
        {post.meta.title}
      </Link>
      <span className="text-[0.92rem] text-ink2">{post.meta.category}</span>
      <time dateTime={post.meta.date} className="text-[0.92rem] text-ink3">
        {formatDate(post.meta.date, lang)}
      </time>
    </li>
  )
}
