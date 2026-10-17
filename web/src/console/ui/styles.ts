/**
 * Console compositions as constants, like src/ui.ts on the site.
 *
 * Density follows Agno's dashboard (their open-source agent-ui, MIT): 14px body
 * with tight tracking, 12px mono for system values, 36px controls on desktop.
 * On touch widths every control grows to 44px.
 * Colors are the site tokens; the one addition is `danger` (#b42318, 6.3:1 on
 * paper and 6.6:1 on white), for destructive actions and overdue promises,
 * which the marketing palette never needed.
 */

export const danger = {
  text: 'text-[#b42318]',
  border: 'border-[#b42318]',
  bg: 'bg-[#b42318]',
  wash: 'bg-[#fdf1ef]',
}

export const pad = 'px-4 sm:px-6 lg:px-8'

export const h1 = 'font-display [font-stretch:92%] text-[1.5rem] font-bold leading-tight tracking-[-0.02em] text-ink sm:text-[1.7rem]'
export const h2 = 'font-display [font-stretch:92%] text-[1.06rem] font-semibold leading-snug tracking-[-0.01em] text-ink'
export const body = 'text-[0.875rem] leading-5 tracking-[-0.01em]'
export const small = 'text-[0.8125rem] leading-5 text-ink2'
export const mono = 'font-mono text-[0.75rem] leading-[1.125rem]'

export const card = 'rounded-xl border border-line bg-surface'

const btnShape =
  'inline-flex items-center justify-center gap-2 rounded-lg border px-4 text-[0.875rem] font-semibold transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-55'
const btn = `${btnShape} h-11 shrink-0 whitespace-nowrap lg:h-9`
export const btnPrimary = `${btn} border-accent bg-brand text-on-brand hover:bg-brand-hi`
export const btnSecondary = `${btn} border-edge bg-surface text-ink hover:bg-bg`
/** For a long label: on a phone the text wraps inside the button instead of running out of the card. */
const btnWrap = `${btnShape} min-h-11 max-w-full py-2 text-center lg:min-h-9`
export const btnPrimaryWrap = `${btnWrap} border-accent bg-brand text-on-brand hover:bg-brand-hi`
export const btnSecondaryWrap = `${btnWrap} border-edge bg-surface text-ink hover:bg-bg`
export const btnDanger = `${btn} ${danger.border} ${danger.bg} text-white hover:bg-[#9a1d14]`
export const btnDangerGhost = `${btn} ${danger.border} bg-surface ${danger.text} hover:bg-[#fdf1ef]`
export const btnQuiet = `${btn} border-transparent bg-transparent px-2 text-ink2 hover:bg-bg hover:text-ink`

export const input =
  'h-11 w-full min-w-0 rounded-lg border border-edge bg-surface px-3 text-[0.875rem] text-ink placeholder:text-ink3 disabled:bg-bg lg:h-9'
/** The trigger of the dropdown (src/components/Select.tsx): the input look, the chevron inside. */
export const select = input
export const label = 'mb-1.5 block text-[0.8125rem] font-semibold text-ink'
export const hint = 'mt-1.5 text-[0.8125rem] leading-5 text-ink2'

export const th = 'border-b border-line bg-bg px-3 py-2 text-left text-[0.75rem] font-semibold text-ink2 whitespace-nowrap'
export const td = 'border-b border-line2 px-3 py-2.5 align-top'

/** Text link: olive underline, the same as the site (lime disappears on pale backgrounds). */
export const link = 'font-semibold text-ink underline decoration-accent decoration-2 underline-offset-4 hover:decoration-ink'
