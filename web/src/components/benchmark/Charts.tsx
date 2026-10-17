import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { useT } from '../../i18n'
import { h3cls, panel } from '../../ui'
import { VARIANTS, logoOf } from './logos'

/**
 * The benchmark's charts: inline SVG and Tailwind, no chart library, in the manner of Artificial
 * Analysis: one ranked horizontal bar chart per metric (best first, the rank beside each row, the value
 * at the tip, the range of the repetitions as a thin whisker, and which way is better stated on the
 * card), and a scatter of cost against accuracy. Coordinates are percentages of an SVG without a
 * viewBox, so bars and dots keep their shape at any width (390 px included) and every text is HTML at
 * the page's own size.
 *
 * Colors follow the system's family, never its rank, as a chart of many vendors colors by vendor: Niadra
 * takes the brand's olive, the Mem0 variants one blue, every other memory system one raspberry. Three
 * hues pass the dataviz validator on all pairs (the scatter's case: lightness band, chroma, color-blind
 * separation, 3:1 on the surface), which more hues would not. The references carry no hue, only a hatch.
 * Every row is named in text beside its bar and every chart has its table, so color is never the only cue.
 * Each name follows its company's logo (logos.ts); the logo is never the only cue either.
 */

const NIADRA = 'var(--color-accent)'
const MEM0 = '#2a6cb8'
const FIELD = '#b0456f'
const REFERENCE = '#8f938a'

/** Every system key the harness writes, with its family color; an unknown key takes the field's. */
export const SERIES: Record<string, string> = {
  niadra: NIADRA,
  mem0_oss: MEM0,
  mem0_oss_rerank: MEM0,
  mem0_platform: MEM0,
  ai_memory: FIELD,
  ai_memory_llm: FIELD,
  graphiti: FIELD,
  hindsight: FIELD,
  hindsight_reflect: FIELD,
  memobase: FIELD,
  supermemory: FIELD,
  memos: FIELD,
  redis_agent_memory: FIELD,
  honcho: FIELD,
  honcho_dialectic: FIELD,
  langmem: FIELD,
  cognee: FIELD,
  agentcore_memory: FIELD,
}

/** Lines that are not a memory system under test: the accuracy floor and ceiling, and the embedder alone. */
const REFERENCES = new Set(['no_memory', 'full_history', 'embedder'])
export const isReference = (system: string) => REFERENCES.has(system)
const colorOf = (system: string) => (isReference(system) ? REFERENCE : (SERIES[system] ?? FIELD))

export type Family = 'niadra' | 'mem0' | 'field' | 'reference'
export const familyOf = (system: string): Family =>
  system === 'niadra' ? 'niadra' : isReference(system) ? 'reference' : system.startsWith('mem0') ? 'mem0' : 'field'
const FAMILY_COLOR: Record<Family, string> = { niadra: NIADRA, mem0: MEM0, field: FIELD, reference: REFERENCE }

/** The icon of a line with no logo: a reference (dashed, as its hatched bar) or a system with no mark on file. */
function NeutralIcon({ reference, size }: { reference: boolean; size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" className="shrink-0" aria-hidden="true">
      {reference ? (
        <rect x="1.5" y="1.5" width="17" height="17" rx="4" fill="var(--color-surface)" stroke={REFERENCE} strokeWidth="1.5" strokeDasharray="3 2" />
      ) : (
        <>
          <rect x="1" y="1" width="18" height="18" rx="4" fill="var(--color-line2)" stroke="var(--color-line)" />
          <circle cx="10" cy="10" r="3.5" fill="var(--color-edge)" />
        </>
      )}
    </svg>
  )
}

/**
 * A system as every chart names it: its company's logo, then its name. The logo's text alternative is the
 * full name and the visible name is hidden from screen readers, so the name is read once. A variant shows
 * its base system's name and a short tag (rerank, reflect, dialectic), since it shares the base's logo.
 */
export function SystemLabel({ system, label, size = 20, className = '' }: { system: string; label: string; size?: number; className?: string }) {
  const { t } = useT()
  const src = logoOf(system)
  const variant = VARIANTS[system]
  const base = variant && t.benchmark.systems[variant.base]
  return (
    <span className={`inline-flex min-w-0 max-w-full items-center gap-2 align-middle ${className}`}>
      {src ? (
        <img src={src} alt={label} width={size} height={size} loading="eager" decoding="async" className="shrink-0 rounded-[4px] object-contain" style={{ width: size, height: size }} />
      ) : (
        <NeutralIcon reference={isReference(system)} size={size} />
      )}
      <span aria-hidden={src ? true : undefined} className="min-w-0">
        {base ? (
          <>
            {base} <span className="whitespace-nowrap rounded bg-line2 px-1.5 py-px font-mono text-[0.76em] font-medium text-ink2">{variant.tag}</span>
          </>
        ) : (
          label
        )}
      </span>
    </span>
  )
}

export type Bar = {
  key: string
  system: string
  label: string
  detail?: string
  value: number
  min?: number | null
  max?: number | null
  text: string
  /** Shown after the ranked rows, with no rank: a reference, a system without the mechanism, a value that never came. */
  unranked?: boolean
}

export type Better = 'lower' | 'higher'

function Hatch({ id }: { id: string }) {
  return (
    <pattern id={id} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <rect width="6" height="6" fill="var(--color-surface)" />
      <line x1="0" y1="0" x2="0" y2="6" stroke={REFERENCE} strokeWidth="2.4" />
    </pattern>
  )
}

function BarRow({ bar, scale, rank, best, bestLabel, limit }: { bar: Bar; scale: number; rank?: number; best: boolean; bestLabel: string; limit?: number }) {
  const hatch = useId().replace(/:/g, '')
  const pct = (v: number) => `${Math.max(0, Math.min(100, (v / scale) * 100))}%`
  const reference = isReference(bar.system)
  const fill = reference ? `url(#${hatch})` : colorOf(bar.system)
  const range = bar.min != null && bar.max != null && bar.max > bar.min
  return (
    <li className="grid grid-cols-[1.75rem_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 py-2 sm:grid-cols-[1.75rem_13rem_minmax(0,1fr)_7rem]">
      <span className="row-span-2 self-start pt-0.5 text-right font-mono text-[0.86rem] tabular-nums text-ink3 sm:row-span-1 sm:self-center sm:pt-0">{rank ?? ''}</span>
      <div className="col-span-2 min-w-0 sm:col-span-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
          <SystemLabel system={bar.system} label={bar.label} className="text-[0.93rem] font-semibold leading-snug text-ink" />
          {best && <span className="rounded-md border border-line px-1.5 text-[0.74rem] font-semibold leading-5 text-ink2">{bestLabel}</span>}
        </div>
        {bar.detail && <div className="text-[0.82rem] leading-snug text-ink3">{bar.detail}</div>}
      </div>
      {/* A row with no value (no mechanism, not measured, never arrived) has no track: its text says why. */}
      <svg className={`block h-5 w-full min-w-0 overflow-visible ${bar.unranked && bar.value === 0 ? 'invisible' : ''}`} aria-hidden="true">
        {reference && (
          <defs>
            <Hatch id={hatch} />
          </defs>
        )}
        <rect x="0" y="0" width="100%" height="20" fill="var(--color-line2)" rx="4" />
        {bar.value > 0 && (
          <>
            <rect x="0" y="0" width={pct(bar.value)} height="20" rx="4" fill={fill} stroke={reference ? REFERENCE : 'none'} />
            {/* Square at the baseline, rounded only at the data end. */}
            <rect x="0" y="0" width="6" height="20" fill={fill} />
          </>
        )}
        {range && (
          <g stroke="var(--color-ink)" strokeWidth="1.5">
            <line x1={pct(bar.min!)} x2={pct(bar.max!)} y1="10" y2="10" />
            <line x1={pct(bar.min!)} x2={pct(bar.min!)} y1="5" y2="15" />
            <line x1={pct(bar.max!)} x2={pct(bar.max!)} y1="5" y2="15" />
          </g>
        )}
        {limit != null && <line x1={pct(limit)} x2={pct(limit)} y1="-3" y2="23" stroke="var(--color-ink)" strokeWidth="1.5" strokeDasharray="3 2" />}
      </svg>
      <div className="text-right font-mono text-[0.9rem] tabular-nums text-ink">{bar.text}</div>
    </li>
  )
}

/**
 * Ranks rows by value, best first (competition ranking: ties share a rank), and puts the unranked rows
 * after them in their given order. The rank is a position among this file's rows, never a new measurement.
 */
export function rankBars(bars: Bar[], better: Better): { bar: Bar; rank?: number }[] {
  const ranked = bars.filter((b) => !b.unranked).sort((a, b) => (better === 'lower' ? a.value - b.value : b.value - a.value))
  const out: { bar: Bar; rank?: number }[] = []
  ranked.forEach((bar, i) => {
    const prev = out[i - 1]
    out.push({ bar, rank: prev && prev.bar.value === bar.value ? prev.rank : i + 1 })
  })
  return [...out, ...bars.filter((b) => b.unranked).map((bar) => ({ bar }))]
}

function Table({ caption, head, rows }: { caption: string; head: string[]; rows: ReactNode[][] }) {
  return (
    <div className="mt-3 overflow-x-auto rounded-lg border border-line bg-surface">
      <table className="w-full min-w-[30rem] text-left text-[0.88rem] leading-snug">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {head.map((h) => (
              <th key={h} scope="col" className="border-b border-line px-3 py-2.5 font-semibold text-ink">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-line last:border-b-0">
              {r.map((c, j) =>
                j === 0 ? (
                  <th key={j} scope="row" className="px-3 py-2.5 font-medium text-ink">
                    {c}
                  </th>
                ) : (
                  <td key={j} className="px-3 py-2.5 font-mono tabular-nums text-ink2">
                    {c}
                  </td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export type TableProps = { caption: string; head: string[]; rows: ReactNode[][] }

/** One chart: title, what it shows and which way is better, the bars, a note, and the table behind it. */
export function ChartCard({
  id,
  title,
  sub,
  children,
  table,
  tableLabel,
  note,
  direction,
}: {
  id: string
  title: string
  sub: string
  children: ReactNode
  table: TableProps
  tableLabel: string
  note?: ReactNode
  /** Which way is better, with its words ("Lower is better"), shown beside the title. */
  direction?: { better: Better; text: string }
}) {
  return (
    <figure id={id} className={`${panel} min-w-0 scroll-mt-24 p-5 sm:p-7`} aria-labelledby={`${id}-title`}>
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <h3 id={`${id}-title`} className={`${h3cls} min-w-0 max-w-[40ch] text-[1.2rem] leading-snug sm:text-[1.3rem]`}>
          {title}
        </h3>
        {direction && (
          <span className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-line bg-bg px-2 py-0.5 text-[0.82rem] font-semibold text-ink2">
            <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
              <path
                d={direction.better === 'lower' ? 'M6 1.5v9M2.5 7 6 10.5 9.5 7' : 'M6 10.5v-9M2.5 5 6 1.5 9.5 5'}
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            {direction.text}
          </span>
        )}
      </div>
      <p className="mt-1.5 max-w-[70ch] text-[0.93rem] leading-relaxed text-ink2">{sub}</p>
      <div className="mt-5">{children}</div>
      {note && <figcaption className="mt-4 border-t border-line pt-3 text-[0.9rem] leading-relaxed text-ink2">{note}</figcaption>}
      <details className="mt-4 group">
        <summary className="inline-flex min-h-11 cursor-pointer items-center gap-2 text-[0.92rem] font-semibold text-ink underline decoration-accent decoration-2 underline-offset-[5px]">
          {tableLabel}
        </summary>
        <Table {...table} />
      </details>
    </figure>
  )
}

/**
 * Ranked bars of one group. `limit` draws a dashed line on every track at that value (a turn's budget), with
 * its label under the group's, and the scale always reaches it.
 */
export function Bars({ bars, groupLabel, better, bestLabel, limit }: { bars: Bar[]; groupLabel?: string; better: Better; bestLabel: string; limit?: { value: number; label: string } }) {
  const scale = Math.max(...bars.map((b) => Math.max(b.value, b.max ?? 0)), limit?.value ?? 0, 0) * 1.05 || 1
  const rows = rankBars(bars, better)
  const rankedCount = rows.filter((r) => r.rank != null).length
  // The best-value mark goes to one line only: a tie for first (many systems at zero) marks none.
  const firsts = rows.filter((r) => r.rank === 1).length
  if (!rows.length) return null
  return (
    <div className="mt-1 first:mt-0">
      {groupLabel && <h4 className="mt-3 text-[0.86rem] font-semibold text-ink3">{groupLabel}</h4>}
      {limit && (
        <p className="mt-1 flex items-center gap-2 text-[0.82rem] text-ink3">
          <svg width="10" height="14" viewBox="0 0 10 14" aria-hidden="true">
            <line x1="5" x2="5" y1="0" y2="14" stroke="var(--color-ink)" strokeWidth="1.5" strokeDasharray="3 2" />
          </svg>
          {limit.label}
        </p>
      )}
      <ol className="divide-y divide-line2">
        {rows.map(({ bar, rank }) => (
          <BarRow key={bar.key} bar={bar} scale={scale} rank={rankedCount > 1 ? rank : undefined} best={rank === 1 && rankedCount > 1 && firsts === 1} bestLabel={bestLabel} limit={limit?.value} />
        ))}
      </ol>
    </div>
  )
}

/** One measure of one arm: its share, the bounds of its interval and the text the row shows. */
export type ArmValue = { value: number; lo: number; hi: number; text: string }
export type ArmRow = { key: string; label: string; without: ArmValue; with: ArmValue }

const WITHOUT = 'var(--color-edge)'

/**
 * The same system on two arms, one row per category: the arm without the blocks in grey, the arm with them
 * in Niadra's olive, each bar with its interval as a whisker and its value at the tip. Both arms are named
 * in the legend and the value text names each one, so color is never the only cue.
 */
export function ArmBars({ rows, arms }: { rows: ArmRow[]; arms: { without: string; with: string } }) {
  const pct = (v: number) => `${Math.max(0, Math.min(100, v * 100))}%`
  const bar = (v: ArmValue, fill: string, name: string) => (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3">
      <svg className="block h-4 w-full min-w-0 overflow-visible" aria-hidden="true">
        <rect x="0" y="0" width="100%" height="16" fill="var(--color-line2)" rx="3" />
        {v.value > 0 && (
          <>
            <rect x="0" y="0" width={pct(v.value)} height="16" rx="3" fill={fill} />
            <rect x="0" y="0" width="5" height="16" fill={fill} />
          </>
        )}
        {v.hi > v.lo && (
          <g stroke="var(--color-ink)" strokeWidth="1.5">
            <line x1={pct(v.lo)} x2={pct(v.hi)} y1="8" y2="8" />
            <line x1={pct(v.lo)} x2={pct(v.lo)} y1="3" y2="13" />
            <line x1={pct(v.hi)} x2={pct(v.hi)} y1="3" y2="13" />
          </g>
        )}
      </svg>
      <span className="whitespace-nowrap text-right font-mono text-[0.86rem] tabular-nums text-ink">
        <span className="sr-only">{name}: </span>
        {v.text}
      </span>
    </div>
  )
  return (
    <div>
      <ul className="flex flex-wrap gap-x-5 gap-y-2 text-[0.88rem] text-ink2">
        {[
          { label: arms.without, fill: WITHOUT },
          { label: arms.with, fill: NIADRA },
        ].map((a) => (
          <li key={a.label} className="inline-flex items-center gap-2">
            <svg width="14" height="14" className="shrink-0" aria-hidden="true">
              <rect x="1" y="1" width="12" height="12" rx="3" fill={a.fill} />
            </svg>
            {a.label}
          </li>
        ))}
      </ul>
      <ol className="mt-3 divide-y divide-line2">
        {rows.map((r) => (
          <li key={r.key} className="grid gap-x-4 gap-y-1.5 py-3 sm:grid-cols-[13rem_minmax(0,1fr)] sm:items-center">
            <span className="text-[0.93rem] font-semibold leading-snug text-ink">{r.label}</span>
            <div className="grid gap-1.5">
              {bar(r.without, WITHOUT, arms.without)}
              {bar(r.with, NIADRA, arms.with)}
            </div>
          </li>
        ))}
      </ol>
    </div>
  )
}

/** One swatch per family present in the run: the systems themselves are named beside every bar. */
export function Legend({ items }: { items: { family: Family; label: string }[] }) {
  return (
    <ul className="flex flex-wrap gap-x-5 gap-y-2 text-[0.88rem] text-ink2">
      {items.map((i) => (
        <li key={i.family} className="inline-flex items-center gap-2">
          <svg width="14" height="14" className="shrink-0" aria-hidden="true">
            {i.family === 'reference' ? (
              <rect x="1" y="1" width="12" height="12" rx="3" fill="var(--color-surface)" stroke={REFERENCE} strokeDasharray="3 2" />
            ) : (
              <rect x="1" y="1" width="12" height="12" rx="3" fill={FAMILY_COLOR[i.family]} />
            )}
          </svg>
          {i.label}
        </li>
      ))}
    </ul>
  )
}

export type Point = { key: string; system: string; label: string; x: number; y: number; text: string; xTo?: number }

type Box = { left: number; right: number; top: number; bottom: number; side?: 'left' | 'right'; anchor?: number }
const overlaps = (a: Box, b: Box) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom

/**
 * Name labels beside their points, in plot percentages. Each label tries the right of its point, then the
 * left, then a little above or below either side, and takes the first place that covers no other label
 * and no point; Niadra is placed first. A label that finds no free place keeps the right side: the list
 * under the plot names every point with its values anyway.
 */
function placeLabels(points: Point[], x: (v: number) => number, y: (v: number) => number, plotPx: { w: number; h: number }, logo: boolean) {
  // A label's height and a step up or down, in percent of the plot's height.
  const H = (18 / plotPx.h) * 100
  // The label's width in percent of the plot, from its length at the label's size (a little generous), and its logo.
  const width = (p: Point) => ((p.label.length * 7.4 + 10 + (logo ? 22 : 0)) / plotPx.w) * 100
  // What a label must not cover: every point, and Niadra's price range between its two points.
  const dots: Box[] = points.flatMap((p) => [
    ...[p.x, ...(p.xTo != null ? [p.xTo] : [])].map((v) => ({ left: x(v) - 1, right: x(v) + 1, top: y(p.y) - 1.5, bottom: y(p.y) + 1.5 })),
    ...(p.xTo != null ? [{ left: x(p.x), right: x(p.xTo), top: y(p.y) - 1, bottom: y(p.y) + 1 }] : []),
  ])
  const placed: Box[] = []
  const order = [...points].sort((a, b) => Number(b.system === 'niadra') - Number(a.system === 'niadra') || b.y - a.y)
  return order.map((p) => {
    const w = width(p)
    const cy = y(p.y)
    const rightOf = x(p.xTo ?? p.x) + 1.5
    const leftOf = x(p.x) - 1.5 - w
    const candidates: Box[] = (
      [
        [rightOf, cy, 'right'],
        [leftOf, cy, 'left'],
        [rightOf, cy - H, 'right'],
        [rightOf, cy + H, 'right'],
        [leftOf, cy - H, 'left'],
        [leftOf, cy + H, 'left'],
        [rightOf, cy - 2 * H, 'right'],
        [rightOf, cy + 2 * H, 'right'],
        [leftOf, cy - 2 * H, 'left'],
        [leftOf, cy + 2 * H, 'left'],
      ] as const
    )
      // A label may reach a little into the card's padding, never past it.
      .filter(([left, top]) => left >= -2 && left + w <= 102 && top >= 0 && top <= 100)
      .map(([left, top, side]) => ({ left, right: left + w, top: top - H / 2, bottom: top + H / 2, side, anchor: side === 'left' ? left + w : left }))
    if (!candidates.length) candidates.push({ left: Math.max(0, 100 - w), right: 100, top: cy - H / 2, bottom: cy + H / 2, side: 'left', anchor: x(p.x) - 1.5 })
    // The first free place; when none is free, the one that covers the fewest labels and points.
    const covered = (c: Box) => [...placed, ...dots].filter((q) => overlaps(c, q)).length
    const box = candidates.find((c) => covered(c) === 0) ?? [...candidates].sort((a, b) => covered(a) - covered(b))[0]
    placed.push(box)
    return { p, box }
  })
}

/**
 * Cost against accuracy. No numeric ticks: the accuracy axis spans the run's own values (with room at
 * both ends), every point is named beside it, and the list under the plot and the table give every value.
 * Lower cost is to the left, higher accuracy up; the upper left quarter is shaded.
 */
export function Scatter({ points, xTitle, yTitle, better }: { points: Point[]; xTitle: string; yTitle: string; better: string }) {
  const xMax = Math.max(...points.map((p) => Math.max(p.x, p.xTo ?? 0)), 0) * 1.08 || 1
  const ys = points.map((p) => p.y)
  const lo = Math.max(0, Math.min(...ys) - 0.06)
  const hi = Math.min(1, Math.max(...ys) + 0.06)
  const x = (v: number) => 3 + (v / xMax) * 94
  const y = (v: number) => 92 - ((v - lo) / (hi - lo || 1)) * 84
  // The plot's width in pixels, for the label widths: a desktop width until the browser measures it.
  const plot = useRef<HTMLDivElement>(null)
  const [plotPx, setPlotPx] = useState({ w: 1000, h: 625 })
  useEffect(() => {
    const el = plot.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => el.clientWidth && setPlotPx({ w: el.clientWidth, h: el.clientHeight }))
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  // Wide enough, every point carries its name; narrower, a number that the list under the plot repeats.
  const numbered = plotPx.w < 800
  const marks = numbered ? points.map((p, i) => ({ ...p, label: String(i + 1) })) : points
  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-[0.8rem] font-semibold text-ink3">
        <span>{yTitle} ↑</span>
        <span>↖ {better}</span>
      </div>
      <div ref={plot} className="relative mt-2 aspect-[4/3] w-full lg:aspect-[16/10]">
        <svg className="absolute inset-0 h-full w-full overflow-visible" aria-hidden="true">
          <rect x="3%" y="4%" width="47%" height="46%" fill="var(--color-line2)" />
          <line x1="3%" x2="3%" y1="4%" y2="96%" stroke="var(--color-edge)" strokeWidth="1" />
          <line x1="3%" x2="97%" y1="96%" y2="96%" stroke="var(--color-edge)" strokeWidth="1" />
          {points.map((p) =>
            p.xTo != null ? (
              <line key={`${p.key}-range`} x1={`${x(p.x)}%`} x2={`${x(p.xTo)}%`} y1={`${y(p.y)}%`} y2={`${y(p.y)}%`} stroke={colorOf(p.system)} strokeWidth="4" strokeLinecap="round" />
            ) : null,
          )}
          {points.map((p) => (
            <g key={p.key}>
              <circle cx={`${x(p.x)}%`} cy={`${y(p.y)}%`} r="7" fill="var(--color-surface)" />
              <circle cx={`${x(p.x)}%`} cy={`${y(p.y)}%`} r="5" fill={colorOf(p.system)} />
              {p.xTo != null && <circle cx={`${x(p.xTo)}%`} cy={`${y(p.y)}%`} r="5" fill={colorOf(p.system)} />}
            </g>
          ))}
        </svg>
        {placeLabels(marks, x, y, plotPx, !numbered).map(({ p, box }) => (
          <div
            key={`${p.key}-label`}
            // A point's number is an identifier, not a measurement (the numbers test skips data-id).
            {...(numbered ? { 'data-id': true } : {})}
            className={`absolute whitespace-nowrap leading-none text-ink ${numbered ? 'font-mono text-[0.78rem] font-semibold' : 'text-[0.8rem] font-semibold'}`}
            // A label on the left ends at its point, whatever the width of its text.
            style={{ ...(box.side === 'left' ? { right: `${100 - box.anchor!}%` } : { left: `${box.anchor}%` }), top: `${(box.top + box.bottom) / 2}%`, transform: 'translateY(-50%)' }}
          >
            {numbered ? p.label : <SystemLabel system={p.system} label={p.label} size={16} className="gap-1.5" />}
          </div>
        ))}
      </div>
      <div className="mt-2 text-right text-[0.8rem] font-semibold text-ink3">{xTitle} →</div>
      {/* Every point with its values, at every width, numbered as the plot is when it has no room for names. */}
      <ul className="mt-4 grid gap-x-6 gap-y-2 sm:grid-cols-2 lg:grid-cols-3">
        {points.map((p, i) => (
          <li key={`${p.key}-item`} className="flex items-start gap-2 text-[0.88rem] leading-snug">
            {numbered && (
              <span data-id className="w-5 shrink-0 text-right font-mono text-[0.8rem] font-semibold text-ink3">
                {i + 1}
              </span>
            )}
            <svg width="12" height="12" className="mt-1 shrink-0" aria-hidden="true">
              <circle cx="6" cy="6" r="5" fill={colorOf(p.system)} />
            </svg>
            <span className="min-w-0">
              <SystemLabel system={p.system} label={p.label} size={16} className="font-semibold text-ink" /> <span className="font-mono text-ink2">{p.text}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
