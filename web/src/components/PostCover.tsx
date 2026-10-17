import type { Post } from '../blog/posts'
import { Icon, type IconName } from './Icon'
import { Mark } from './Mark'

/**
 * A post's cover: typographic, no generic illustration. The cover line in
 * Archivo, the topic's icon (the same set as the site) and the N in the corner. The
 * tone alternates between the dark block and full lime, and is fixed per post (it
 * comes from the id), so the cover is the same in the list and on the post's page.
 * No pale lime: the cover lives inside a white card.
 */
export function PostCover({ post, size = 'card' }: { post: Post; size?: 'card' | 'hero' }) {
  const lime = [...post.meta.id].reduce((n, c) => n + c.charCodeAt(0), 0) % 2 === 1
  const tone = lime ? 'brand-block' : 'invert-block'
  const big = size === 'hero'
  return (
    <div
      className={`${tone} relative flex flex-col justify-between overflow-hidden rounded-lg ${
        big ? 'aspect-[16/9] p-6 sm:aspect-[21/9] sm:p-10' : 'aspect-[16/9] p-5 sm:p-6'
      }`}
      aria-hidden="true"
    >
      <div className="flex items-center justify-between gap-4">
        <span className={`text-[0.82rem] font-semibold ${lime ? 'text-ink' : 'text-accent'}`}>{post.meta.category}</span>
        <Mark className={`h-4 w-auto ${lime ? 'text-ink' : 'text-brand'}`} />
      </div>
      <div className="flex items-end justify-between gap-6">
        <p
          className={`font-display font-bold leading-[1.02] tracking-[-0.03em] text-ink [font-stretch:92%] ${
            big ? 'max-w-[16ch] text-[clamp(1.7rem,5.4vw,3.4rem)]' : 'max-w-[14ch] text-[clamp(1.35rem,3.6vw,2rem)]'
          }`}
        >
          {post.meta.cover}
        </p>
        <span className={`flex shrink-0 items-center justify-center rounded-xl border ${lime ? 'border-ink/25' : 'border-line'} ${big ? 'h-20 w-20' : 'h-14 w-14'}`}>
          <Icon name={post.meta.icon as IconName} size={big ? 38 : 28} className={lime ? 'text-ink' : 'text-brand'} />
        </span>
      </div>
    </div>
  )
}

/** The author: the brand's N in a circle, in place of a person's photo. */
export function Byline({ name }: { name: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-[0.92rem] font-medium text-ink">
      <span className="flex h-7 w-7 items-center justify-center rounded-full bg-ink">
        <Mark className="h-3 w-auto text-brand" />
      </span>
      {name}
    </span>
  )
}
