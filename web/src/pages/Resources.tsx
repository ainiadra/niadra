import { ExternalArrow, Link } from '../i18n/link'
import { DOCS, useT } from '../i18n'
import { IconChip, type IconName } from '../components/Icon'
import { H2, Lede, PageHead, Sec } from '../components/Shell'
import { btnPrimary, card, display, h3cls, textLink } from '../ui'

function Blocks({ items }: { items: { t: string; d: string }[] }) {
  return (
    <div className="mt-8 grid gap-x-12 gap-y-8 md:grid-cols-2">
      {items.map((b) => (
        <div key={b.t} className="border-t border-line pt-5">
          <h3 className={`${h3cls} text-[1.1rem]`}>{b.t}</h3>
          <p className="mt-2.5 max-w-[58ch] leading-relaxed text-ink2">{b.d}</p>
        </div>
      ))}
    </div>
  )
}

// In the order of `resources.levels` in pt.ts/en.ts, from the infrastructure up to the audit.
const LEVEL_ICONS: IconName[] = ['cloud', 'lock', 'key', 'target', 'user', 'eye']

/**
 * Architecture, security and privacy: what the buyer's technical, security and
 * compliance teams need to read. The security layers are numbered because they are
 * real layers, from the infrastructure up to the audit: the order is information,
 * not decoration.
 */
export function Resources() {
  const { t } = useT()
  const r = t.resources

  return (
    <>
      <PageHead a={r.h1a} b={r.h1b} sub={r.sub} />

      <Sec id="arquitetura" tone="soft">
        <H2>{r.archH2}</H2>
        <Blocks items={r.arch} />
      </Sec>

      <Sec id="seguranca">
        <H2>{r.secH2}</H2>
        <Lede>{r.secSub}</Lede>
        <ol className="mt-9 grid gap-4 md:grid-cols-2">
          {r.levels.map((l, i) => (
            <li key={l.t} className={`${card} flex gap-4 p-5 sm:p-6`}>
              <IconChip name={LEVEL_ICONS[i]} tone="plain" />
              <div>
                <div className="flex items-baseline gap-2.5">
                  <span className={`${display} text-[1.05rem] tabular-nums text-ink3`}>{i + 1}</span>
                  <h3 className={`${h3cls} text-[1.1rem]`}>{l.t}</h3>
                </div>
                <p className="mt-2 text-[0.96rem] leading-relaxed text-ink2">{l.d}</p>
              </div>
            </li>
          ))}
        </ol>

        {/* The documents a buyer's security review asks for, published in the docs: one link each, nothing summarized here. */}
        <div className="mt-12">
          <h3 className={`${h3cls} text-[1.3rem]`}>{r.secDocsH3}</h3>
          <p className="mt-2.5 max-w-[64ch] leading-relaxed text-ink2">{r.secDocsSub}</p>
          <ul className="mt-5 grid gap-x-8 sm:grid-cols-2">
            {r.secDocs.map((d) => (
              <li key={d.to}>
                <Link className={`${textLink} gap-1.5`} to={d.to}>
                  {d.t}
                  <ExternalArrow to={d.to} />
                </Link>
              </li>
            ))}
          </ul>
        </div>
      </Sec>

      <Sec id="privacidade" tone="soft">
        <H2>{r.privH2}</H2>
        <Blocks items={r.priv} />
      </Sec>

      <Sec id="docs">
        <H2>{r.docsH2}</H2>
        <div className={`${card} mt-8 grid gap-6 p-6 sm:p-8 md:grid-cols-[1fr_auto] md:items-center md:gap-10`}>
          <p className="max-w-[64ch] text-[1.02rem] leading-relaxed text-ink2">{r.docsBody}</p>
          <Link className={btnPrimary} to={DOCS}>{r.docsCta}<ExternalArrow to={DOCS} /></Link>
        </div>
      </Sec>
    </>
  )
}
