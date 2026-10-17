/**
 * Repeated compositions, as constants (better than @apply in Tailwind).
 *
 * Finish: an enterprise product, so a 1px hairline, flat
 * surfaces and a soft shadow only on what really floats. The thick contour with a
 * hard shadow was tried and dropped: it read as a building-block toy.
 * The personality comes from the palette (lime, pale lime, ink) and the typography.
 */

export const wrap = 'mx-auto w-full max-w-[1180px] px-5 sm:px-8 lg:px-12'

/** Vertical rhythm with its own scale on mobile, not the desktop one shrunk. */
export const sec = 'py-12 sm:py-16 lg:py-24'

/** Archivo at 92% width: an engineering cut, and the headline grows without breaking. */
export const display = 'font-display [font-stretch:92%] font-bold tracking-[-0.03em] text-balance'

export const h1cls = `${display} text-[clamp(2.3rem,9.2vw,4.25rem)] leading-[1]`
export const h2cls = `${display} text-[clamp(1.7rem,5.4vw,2.7rem)] leading-[1.05] max-w-[22ch]`
export const h3cls = 'font-display [font-stretch:92%] font-semibold tracking-[-0.015em]'

export const lede = 'mt-4 max-w-[56ch] text-[1.02rem] leading-relaxed text-ink2 sm:text-[1.08rem]'

/** Product panel: flat, 1px hairline. */
export const panel = 'rounded-xl border border-line bg-surface'

/** The only panel with elevation: the hero stage, which is the focal point of the page. */
export const panelLift = `${panel} shadow-[0_1px_2px_rgba(24,28,18,0.05),0_24px_56px_-28px_rgba(24,28,18,0.28)]`

export const card = panel

const btnBase =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-lg border px-5 text-[0.98rem] font-semibold transition-colors duration-150 active:translate-y-px'

/** Primary action: full lime with near-black text. The olive border gives the 3:1 boundary. */
export const btnPrimary = `${btnBase} border-accent bg-brand text-on-brand hover:bg-brand-hi`
export const btnGhost = `${btnBase} border-edge bg-surface text-ink hover:bg-bg2`
/** Inside the lime block the button inverts: full ink with lime text. */
export const btnDark = `${btnBase} border-ink bg-ink text-bg hover:opacity-90`
export const btnSm = 'min-h-11 px-4 text-[0.92rem]'

/** 44px control (menu). */
export const iconBtn =
  'inline-flex h-11 min-w-11 items-center justify-center gap-2 rounded-lg border border-edge bg-surface px-3 text-ink transition-colors hover:bg-bg2'

/** The highlighter mark: marks what Niadra remembered. A pen's tone, not a sign's. */
export const hl = 'rounded-[3px] bg-brand/45 px-1 py-px text-ink [box-decoration-break:clone] [-webkit-box-decoration-break:clone]'

/** Source tag of a fact (which channel and what time it came from). */
export const srcChip = 'whitespace-nowrap rounded-md border border-line bg-surface px-1.5 py-0.5 font-mono text-[0.66rem] text-ink2'

/** Text link with an olive underline: full lime disappeared on pale lime sections. 44px tall for touch. */
export const textLink =
  'inline-flex min-h-11 items-center font-semibold text-ink underline decoration-accent decoration-2 underline-offset-[5px] transition-colors hover:decoration-ink'

/** Every early-access request goes to /enterprise. The interest, when there is one, comes already selected. */
export const contactTo = (interest?: string) => (interest ? `/enterprise?interesse=${interest}` : '/enterprise')

/**
 * A post's text (HTML from the Markdown). Only Tailwind utilities, with child
 * selectors: no hand-written CSS rule. A reading column of about 68
 * characters; the first paragraph opens larger, as in the reference.
 */
export const prose = [
  'text-[1.04rem] leading-[1.75] text-ink2 sm:text-[1.08rem]',
  '[&>p]:mt-5 [&>*:first-child]:mt-0 [&>p:first-child]:text-[1.16rem] [&>p:first-child]:leading-relaxed [&>p:first-child]:text-ink sm:[&>p:first-child]:text-[1.25rem]',
  '[&_h2]:mt-14 [&_h2]:scroll-mt-24 [&_h2]:font-display [&_h2]:text-[1.55rem] [&_h2]:font-bold [&_h2]:leading-[1.15] [&_h2]:tracking-[-0.02em] [&_h2]:text-ink [&_h2]:[font-stretch:92%] sm:[&_h2]:text-[1.8rem]',
  '[&_h3]:mt-9 [&_h3]:scroll-mt-24 [&_h3]:font-display [&_h3]:text-[1.18rem] [&_h3]:font-semibold [&_h3]:leading-snug [&_h3]:text-ink [&_h3]:[font-stretch:92%] [&_h3+p]:mt-2',
  '[&_ul]:mt-5 [&_ul]:list-disc [&_ul]:pl-6 [&_ol]:mt-5 [&_ol]:list-decimal [&_ol]:pl-6 [&_li]:mt-2 [&_li]:pl-1 [&_li::marker]:text-accent',
  '[&_strong]:font-semibold [&_strong]:text-ink',
  '[&_a]:font-medium [&_a]:text-ink [&_a]:underline [&_a]:decoration-accent [&_a]:decoration-2 [&_a]:underline-offset-4 hover:[&_a]:decoration-ink',
  '[&_code]:rounded-[5px] [&_code]:border [&_code]:border-line [&_code]:bg-surface [&_code]:px-1.5 [&_code]:py-px [&_code]:font-mono [&_code]:text-[0.84em] [&_code]:text-ink',
  '[&_pre]:mt-6 [&_pre]:overflow-x-auto [&_pre]:rounded-xl [&_pre]:border [&_pre]:border-line [&_pre]:bg-surface [&_pre]:p-5 [&_pre]:text-[0.86rem] [&_pre]:leading-relaxed',
  '[&_pre_code]:border-0 [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:text-[1em]',
  '[&_.md-table]:mt-6 [&_.md-table]:overflow-x-auto [&_.md-table]:rounded-xl [&_.md-table]:border [&_.md-table]:border-line [&_.md-table]:bg-surface',
  '[&_table]:w-full [&_table]:min-w-[34rem] [&_table]:text-left [&_table]:text-[0.94rem] [&_table]:leading-snug',
  '[&_th]:border-b [&_th]:border-line [&_th]:px-4 [&_th]:py-3 [&_th]:font-semibold [&_th]:text-ink',
  '[&_td]:border-b [&_td]:border-line [&_td]:px-4 [&_td]:py-3 [&_td]:align-top [&_tr:last-child_td]:border-b-0',
  '[&_blockquote]:mt-6 [&_blockquote]:border-l-2 [&_blockquote]:border-accent [&_blockquote]:pl-5 [&_blockquote]:text-ink',
  '[&_hr]:my-12 [&_hr]:border-line',
].join(' ')
