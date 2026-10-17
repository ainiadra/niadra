import { useT } from '../i18n'
import { Icon, IconChip, type IconName } from '../components/Icon'
import { H2, Lede, PageHead, Sec } from '../components/Shell'
import { h3cls } from '../ui'

// In the order of `about.principles` in pt.ts/en.ts: neutral, managed, security, data.
const PRINCIPLE_ICONS: IconName[] = ['hub', 'cloud', 'lock', 'archive']

/**
 * The "About" page: what Niadra is, why it exists, how it works and the rules of
 * what we publish. It is the page search engines and AI look for to confirm who is
 * behind the site. The company's details (legal name, CNPJ, founders) go here and in
 * the JSON-LD Organization when the owner provides them.
 */
export function About() {
  const { t } = useT()
  const a = t.about
  return (
    <>
      <PageHead a={a.h1a} b={a.h1b} sub={a.sub} />

      <Sec>
        <H2>{a.whatH2}</H2>
        <Lede>{a.what}</Lede>
        <ul className="mt-8 grid gap-4 lg:grid-cols-3 lg:gap-8">
          {a.whatItems.map((i) => (
            <li key={i} className="flex items-start gap-3 text-[1rem] leading-relaxed text-ink">
              <span className="mt-1 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand text-on-brand">
                <Icon name="check" size={12} className="[stroke-width:3]" />
              </span>
              {i}
            </li>
          ))}
        </ul>
      </Sec>

      <Sec tone="soft">
        <H2>{a.whyH2}</H2>
        <p className="mt-5 max-w-[68ch] text-[1.05rem] leading-relaxed text-ink2">{a.why}</p>
      </Sec>

      <Sec>
        <H2>{a.howH2}</H2>
        <div className="mt-9 grid gap-x-10 gap-y-9 sm:grid-cols-2 lg:grid-cols-4">
          {a.principles.map((p, i) => (
            <div key={p.t} className="flex gap-4 sm:block">
              <IconChip name={PRINCIPLE_ICONS[i]} />
              <div>
                <h3 className={`${h3cls} text-[1.08rem] sm:mt-4`}>{p.t}</h3>
                <p className="mt-2 text-[0.96rem] leading-relaxed text-ink2">{p.d}</p>
              </div>
            </div>
          ))}
        </div>
      </Sec>

      <Sec tone="soft">
        <H2>{a.editH2}</H2>
        <Lede>{a.editSub}</Lede>
        <ul className="mt-8 border-t border-edge">
          {a.editItems.map((i) => (
            <li key={i} className="border-b border-edge py-4 text-[1rem] leading-relaxed text-ink">
              {i}
            </li>
          ))}
        </ul>
      </Sec>
    </>
  )
}
