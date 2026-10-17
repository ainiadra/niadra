import { Fragment } from 'react'
import { useT } from '../i18n'
import { ExternalArrow, Link } from '../i18n/link'
import { PRICE, ROWS, SYSTEMS, type Cell, type Mark, type SystemKey } from '../content/comparison'
import { Icon } from '../components/Icon'
import { QuickAccess } from '../components/QuickAccess'
import { H2, Lede, PageHead, Sec } from '../components/Shell'
import { h3cls, panel, textLink, wrap } from '../ui'

/**
 * /comparativo: the feature matrix, Niadra against the memory systems a buyer meets, every cell with the
 * page it was read on and the date. The marks are the vendor's own documentation, never a measurement;
 * the measured comparison is /benchmark, linked at the top and at the bottom. Where a competitor has
 * what Niadra does not (open source, self-hosting), the row says so: the page is only worth reading if
 * it is honest in both directions.
 */

// The mark as a sign: full lime for "yes", a hairline ring for "partial", ink for "no", nothing for "undocumented".
const MARK_STYLE: Record<Mark, string> = {
  yes: 'bg-brand text-on-brand border-accent',
  partial: 'bg-surface text-ink border-edge',
  no: 'bg-ink text-bg border-ink',
  undocumented: 'bg-bg text-ink3 border-line border-dashed',
}

const fmtDate = (iso: string, lang: 'pt' | 'en') => {
  const [y, m, d] = iso.split('-')
  return lang === 'pt' ? `${d}/${m}/${y}` : `${y}-${m}-${d}`
}

function MarkChip({ cell, system }: { cell: Cell; system: string }) {
  const { t, lang } = useT()
  const c = t.comparison
  const title = `${system}: ${c.marks[cell.v]}${cell.note ? `. ${cell.note[lang]}` : ''} (${c.readOn} ${fmtDate(cell.read, lang)})`
  return (
    <a
      href={cell.src}
      target="_blank"
      rel="noopener noreferrer"
      title={title}
      aria-label={title}
      className={`inline-flex min-h-8 min-w-[4.6rem] items-center justify-center gap-1 rounded-md border px-2 text-[0.78rem] font-semibold leading-none transition-opacity hover:opacity-80 ${MARK_STYLE[cell.v]}`}
    >
      {cell.v === 'yes' && <Icon name="check" size={12} className="[stroke-width:3]" />}
      {c.marks[cell.v]}
    </a>
  )
}

const systemName = (s: (typeof SYSTEMS)[number], lang: 'pt' | 'en') => ('nameEn' in s && lang === 'en' ? s.nameEn : s.name)

export function Comparison() {
  const { t, lang } = useT()
  const c = t.comparison
  const read = fmtDate(ROWS[0].cells.niadra.read, lang)

  return (
    <>
      <PageHead a={c.h1a} b={c.h1b} sub={c.sub} />

      <Sec className="!pt-8 lg:!pt-12">
        {/* The legend first: the four marks are the whole vocabulary of the table. */}
        <ul className="flex flex-wrap gap-x-6 gap-y-2 text-[0.92rem] text-ink2">
          {(['yes', 'partial', 'no', 'undocumented'] as const).map((m) => (
            <li key={m} className="flex items-center gap-2">
              <span className={`inline-flex min-h-7 min-w-[4.6rem] items-center justify-center gap-1 rounded-md border px-2 text-[0.76rem] font-semibold ${MARK_STYLE[m]}`}>
                {m === 'yes' && <Icon name="check" size={12} className="[stroke-width:3]" />}
                {c.marks[m]}
              </span>
              {c.legend[m]}
            </li>
          ))}
        </ul>
        <p className="mt-3 max-w-[72ch] text-[0.92rem] leading-relaxed text-ink2">{c.sourceNote.replace('{date}', read)}</p>

        {/* The matrix: rows are the capabilities, columns the systems; the first column stays put while the rest scroll on a phone. */}
        <div className={`${panel} mt-8 overflow-x-auto`}>
          <table className="w-full min-w-[72rem] border-collapse text-left">
            <thead>
              <tr className="border-b border-line">
                <th scope="col" className="sticky left-0 z-10 w-[11rem] min-w-[11rem] bg-surface sm:w-[15rem] sm:min-w-[15rem] px-4 py-3 text-[0.82rem] font-semibold text-ink3 sm:px-5">
                  {c.thCapability}
                </th>
                {SYSTEMS.map((s) => (
                  <th key={s.key} scope="col" className={`px-2 py-3 align-bottom ${s.key === 'niadra' ? 'bg-bg2' : ''}`}>
                    <span className="flex flex-col items-start gap-1.5">
                      <img src={s.logo} alt="" width={20} height={20} className="h-5 w-5 rounded-[4px] object-contain" loading="eager" />
                      <span className="text-[0.8rem] font-semibold leading-snug text-ink">{systemName(s, lang)}</span>
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ROWS.map((row) => (
                <tr key={row.key} className="border-b border-line align-top">
                  <th scope="row" className="sticky left-0 z-10 w-[11rem] min-w-[11rem] bg-surface sm:w-[15rem] sm:min-w-[15rem] px-4 py-3.5 sm:px-5">
                    <span className="block text-[0.94rem] font-semibold leading-snug text-ink">{row.label[lang]}</span>
                    <span className="mt-1 block text-[0.8rem] font-normal leading-snug text-ink2">{row.means[lang]}</span>
                  </th>
                  {SYSTEMS.map((s) => (
                    <td key={s.key} className={`px-2 py-3.5 ${s.key === 'niadra' ? 'bg-bg2' : ''}`}>
                      <MarkChip cell={row.cells[s.key]} system={systemName(s, lang)} />
                    </td>
                  ))}
                </tr>
              ))}
              <tr className="align-top">
                <th scope="row" className="sticky left-0 z-10 w-[11rem] min-w-[11rem] bg-surface sm:w-[15rem] sm:min-w-[15rem] px-4 py-3.5 sm:px-5">
                  <span className="block text-[0.94rem] font-semibold leading-snug text-ink">{c.priceRow}</span>
                  <span className="mt-1 block text-[0.8rem] font-normal leading-snug text-ink2">{c.priceMeans}</span>
                </th>
                {SYSTEMS.map((s) => {
                  const p = PRICE[s.key as SystemKey]
                  return (
                    <td key={s.key} className={`px-2 py-3.5 text-[0.8rem] leading-snug text-ink ${s.key === 'niadra' ? 'bg-bg2' : ''}`}>
                      {p.text[lang]}
                      <a href={p.src} target="_blank" rel="noopener noreferrer" className="mt-1 block text-[0.76rem] font-medium text-ink2 underline decoration-line underline-offset-2 hover:decoration-accent">
                        {c.source} · {fmtDate(p.read, lang)}
                      </a>
                    </td>
                  )
                })}
              </tr>
            </tbody>
          </table>
        </div>
        <p className="mt-4 text-[0.88rem] leading-relaxed text-ink3">{c.trademarks}</p>
        <Link to="/benchmark" className={`${textLink} mt-4`}>{c.benchmarkLink}</Link>
      </Sec>

      {/* The evidence behind every mark, system by system: the note and the page, so a reader can check each one. */}
      <Sec tone="soft">
        <H2>{c.notesH2}</H2>
        <Lede>{c.notesSub}</Lede>
        <div className="mt-8 grid gap-4 lg:grid-cols-2">
          {SYSTEMS.map((s) => (
            <details key={s.key} className={`${panel} group p-5 sm:p-6`}>
              <summary className="flex min-h-11 cursor-pointer list-none items-center gap-3 marker:content-none">
                <img src={s.logo} alt="" width={24} height={24} className="h-6 w-6 rounded-[5px] object-contain" loading="eager" />
                <span className={`${h3cls} text-[1.08rem] text-ink`}>{systemName(s, lang)}</span>
                <span className="ml-auto text-[0.82rem] font-medium text-ink2 group-open:hidden">{c.open}</span>
                <span className="ml-auto hidden text-[0.82rem] font-medium text-ink2 group-open:inline">{c.close}</span>
              </summary>
              <dl className="mt-4 grid gap-3 border-t border-line pt-4 text-[0.9rem] leading-snug">
                {ROWS.map((row) => {
                  const cell = row.cells[s.key as SystemKey]
                  return (
                    <Fragment key={row.key}>
                      <div className="grid gap-1 sm:grid-cols-[minmax(0,1fr)_auto] sm:gap-4">
                        <div>
                          <dt className="font-semibold text-ink">
                            {row.label[lang]}: <span className="font-normal text-ink2">{c.marks[cell.v]}</span>
                          </dt>
                          {cell.note && <dd className="mt-0.5 text-ink2">{cell.note[lang]}</dd>}
                        </div>
                        <dd className="sm:text-right">
                          <a href={cell.src} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-8 items-center gap-1 whitespace-nowrap text-[0.8rem] font-medium text-ink2 underline decoration-line underline-offset-2 hover:decoration-accent">
                            {c.source} · {fmtDate(cell.read, lang)}
                            <ExternalArrow to={cell.src} />
                          </a>
                        </dd>
                      </div>
                    </Fragment>
                  )
                })}
              </dl>
            </details>
          ))}
        </div>
        <div className={`${wrap} !px-0`}>
          <p className="mt-8 max-w-[72ch] text-[0.96rem] leading-relaxed text-ink2">{c.honesty}</p>
          <Link to="/benchmark" className={`${textLink} mt-3`}>{c.benchmarkLink}</Link>
        </div>
      </Sec>

      <QuickAccess />
    </>
  )
}
