import { Suspense } from 'react'
import { useParams } from 'react-router-dom'
import { useT } from '../i18n'
import { Link } from '../i18n/link'
import { findPost, formatDate, postPath, postsIn, preload, usePostHtml, type Post } from '../blog/posts'
import { Byline, PostCover } from '../components/PostCover'
import { QuickAccess } from '../components/QuickAccess'
import { h3cls, prose, textLink, wrap } from '../ui'

/**
 * A post's page, in the reference layout: centered header (title, subtitle,
 * author, date and category), wide cover and a narrow reading column. The text
 * arrives prerendered in the HTML, so search engines and AI read the whole post
 * without running anything.
 */
export function PostPage() {
  const { slug = '' } = useParams()
  const { t, lang } = useT()
  const b = t.blog
  const post = findPost(lang, slug)

  if (!post) {
    return (
      <section className={`${wrap} py-20 lg:py-32`}>
        <h1 className={`${h3cls} text-[2rem]`}>{t.notFound.h1}</h1>
        <p className="mt-4 text-ink2">{t.notFound.body}</p>
        <Link to="/blog" className={`${textLink} mt-6`}>{b.all}</Link>
      </section>
    )
  }

  // All the other posts of the blog, up to six: it is what links each post to the others on the same topic.
  const related = postsIn(lang).filter((p) => p.meta.id !== post.meta.id).slice(0, 6)

  return (
    <article className="pb-16 pt-8 sm:pt-12 lg:pb-24 lg:pt-16">
      <header className={`${wrap} text-center`}>
        <nav aria-label="Breadcrumb" className="text-[0.88rem] text-ink3">
          <Link to="/blog" className="inline-flex min-h-11 min-w-11 items-center justify-center font-medium text-ink2 underline decoration-line underline-offset-4 hover:decoration-accent">
            {t.nav.blog}
          </Link>
          <span aria-hidden="true" className="px-2">/</span>
          <span>{post.meta.category}</span>
        </nav>
        <h1 className="mx-auto mt-3 max-w-[22ch] text-balance font-display text-[clamp(2rem,6.4vw,3.35rem)] font-bold leading-[1.04] tracking-[-0.03em] [font-stretch:92%]">
          {post.meta.title}
        </h1>
        <p className="mx-auto mt-5 max-w-[58ch] text-[1.06rem] leading-relaxed text-ink2 sm:text-[1.14rem]">{post.meta.description}</p>
        <div className="mt-7 flex justify-center">
          <Byline name={b.author} />
        </div>
        <p className="mt-3 flex flex-wrap items-center justify-center gap-x-3 gap-y-1 text-[0.88rem] text-ink3">
          <time dateTime={post.meta.date}>{formatDate(post.meta.date, lang)}</time>
          <span aria-hidden="true" className="h-3.5 w-px bg-line max-sm:hidden" />
          <span>{post.meta.category}</span>
          <span aria-hidden="true" className="h-3.5 w-px bg-line max-sm:hidden" />
          <span>
            {post.minutes} {b.minRead}
          </span>
          {post.meta.updated && post.meta.updated.slice(0, 10) !== post.meta.date.slice(0, 10) && (
            <>
              <span aria-hidden="true" className="h-3.5 w-px bg-line max-sm:hidden" />
              <span>
                {b.updated} <time dateTime={post.meta.updated}>{formatDate(post.meta.updated, lang)}</time>
              </span>
            </>
          )}
        </p>
      </header>

      <div className={`${wrap} mt-10 lg:mt-14`}>
        <div className="mx-auto max-w-[64rem]">
          <PostCover post={post} size="hero" />
        </div>
      </div>

      {post.headings.length > 2 && (
        <nav aria-labelledby="neste-post" className={`${wrap} mt-12 lg:mt-16`}>
          <div className="mx-auto max-w-[42rem] rounded-xl border border-line bg-surface px-5 py-4 sm:px-6">
            <h2 id="neste-post" className="text-[0.84rem] font-semibold text-ink3">{b.toc}</h2>
            <ol className="mt-2 grid gap-0.5">
              {post.headings.map((h) => (
                <li key={h.id}>
                  <a href={`#${h.id}`} className="inline-flex min-h-11 items-center py-1 text-[0.96rem] leading-snug text-ink underline decoration-line underline-offset-4 hover:decoration-accent">
                    {h.text}
                  </a>
                </li>
              ))}
            </ol>
          </div>
        </nav>
      )}

      <div className={`${wrap} ${post.headings.length > 2 ? 'mt-10' : 'mt-12 lg:mt-16'}`}>
        {/* While the text of a post opened by navigation arrives, the column holds its place. */}
        <Suspense fallback={<div className="mx-auto min-h-[70vh] max-w-[42rem]" />}>
          <Body post={post} />
        </Suspense>
      </div>

      {/* The short early-access request, right after the text: the post's hook says what Niadra does, the form asks who is building. */}
      <QuickAccess />

      {related.length > 0 && (
        <aside className={`${wrap} mt-16 lg:mt-24`} aria-labelledby="leia-tambem">
          <div className="mx-auto max-w-[42rem]">
            <h2 id="leia-tambem" className={`${h3cls} text-[1.25rem]`}>{b.related}</h2>
            <ul className="mt-4 border-t border-line">
              {related.map((p) => (
                <li key={p.meta.id} className="border-b border-line">
                  <Link to={postPath(p)} onPointerEnter={() => preload(p)} className="flex min-h-14 flex-col justify-center gap-0.5 py-4 transition-colors hover:text-accent">
                    <span className="text-[1.02rem] font-semibold leading-snug text-ink">{p.meta.title}</span>
                    <span className="text-[0.88rem] text-ink3">
                      {p.meta.category} · {formatDate(p.meta.date, lang)}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
            <Link to="/blog" className={`${textLink} mt-4`}>{b.all}</Link>
          </div>
        </aside>
      )}
    </article>
  )
}

function Body({ post }: { post: Post }) {
  const html = usePostHtml(post)
  return <div className={`mx-auto max-w-[42rem] ${prose}`} dangerouslySetInnerHTML={{ __html: html }} />
}
