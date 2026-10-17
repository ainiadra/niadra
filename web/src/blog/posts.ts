import { use } from 'react'
import type { PostInfo } from '../../build/markdown'
import type { Lang } from '../i18n'

export type Post = PostInfo & { file: string }

// The data of each post goes into the main bundle (build/markdown.ts, "?meta").
const infos = import.meta.glob<PostInfo>('../content/blog/*.md', { eager: true, query: '?meta', import: 'default' })

// The text as HTML: bundled in the prerender, on demand in the browser. This way the
// JavaScript of every page does not carry the text of every post.
const eagerHtml = import.meta.env.SSR ? import.meta.glob<string>('../content/blog/*.md', { eager: true, import: 'html' }) : {}
const lazyHtml = import.meta.env.SSR ? {} : import.meta.glob<string>('../content/blog/*.md', { import: 'html' })

/** Newest to oldest; on the same date, by the `order` field and then by id. */
export const posts: Post[] = Object.entries(infos)
  .map(([file, info]) => ({ ...info, file }))
  .sort((a, b) => b.meta.date.localeCompare(a.meta.date) || a.meta.order - b.meta.order || a.meta.id.localeCompare(b.meta.id))

export const postsIn = (lang: Lang) => posts.filter((p) => p.meta.lang === lang)

export const findPost = (lang: Lang, slug: string) => posts.find((p) => p.meta.lang === lang && p.meta.slug === slug)

export const translationOf = (p: Post, lang: Lang) => posts.find((q) => q.meta.id === p.meta.id && q.meta.lang === lang)

/** Path without the language prefix; the site's Link adds /en when needed. */
export const postPath = (p: Post) => `/blog/${p.meta.slug}`

/** A post's HTML in the prerender (feeds and page). */
export const htmlOf = (p: Post): string => eagerHtml[p.file] ?? ''

type Pending = Promise<string> & { status?: 'pending' | 'fulfilled' | 'rejected'; value?: string }
const loading = new Map<string, Pending>()

/** Starts downloading a post's text (on hovering the link, for example). */
export function preload(p: Post): Pending | undefined {
  if (import.meta.env.SSR) return undefined
  let job = loading.get(p.file)
  if (!job) {
    const started = lazyHtml[p.file]() as Pending
    started.status = 'pending'
    started.then(
      (v) => { started.status = 'fulfilled'; started.value = v },
      () => { started.status = 'rejected' },
    )
    job = started
    loading.set(p.file, job)
  }
  return job
}

/**
 * The post's text inside a <Suspense>. During hydration React keeps the prerendered
 * HTML on screen while the text arrives; nothing flickers.
 */
export function usePostHtml(p: Post): string {
  if (import.meta.env.SSR) return htmlOf(p)
  // The status set in preload() lets use() return the already downloaded text at once.
  return use(preload(p) as Promise<string>)
}

export function formatDate(date: string, lang: Lang) {
  return new Intl.DateTimeFormat(lang === 'pt' ? 'pt-BR' : 'en-US', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(`${date.slice(0, 10)}T12:00:00Z`))
}
