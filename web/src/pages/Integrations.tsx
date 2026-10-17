import { Fragment, useId, useRef, useState, type ReactNode } from 'react'
import { useParams } from 'react-router-dom'
import { useT } from '../i18n'
import { ExternalArrow, Link } from '../i18n/link'
import { ContextDoc } from '../components/Artifacts'
import { QuickAccess } from '../components/QuickAccess'
import { VendorLogo } from '../components/VendorLogo'
import { Bullets, Faq, H2, Lede, Sec } from '../components/Shell'
import { CODE, type CodeSample } from '../content/integrations/code'
import { GROUP_ORDER, INTEGRATIONS, INTEGRATIONS_DOCS, integrationBySlug, integrationDocs, integrationPath, type Integration } from '../integrations'
import { btnGhost, btnPrimary, btnSm, contactTo, display, h1cls, h3cls, lede, panel, srcChip, textLink, wrap } from '../ui'

const productH1 = `${display} mt-5 max-w-[24ch] text-[clamp(2.1rem,6.4vw,3.6rem)] leading-[1.03]`

/** Marks the SDK calls of a line (the same rule as the home snippet): what Niadra does, highlighted. */
function CodeLine({ line }: { line: string }) {
  const at = line.search(/\s(#|\/\/)\s/)
  const code = at >= 0 ? line.slice(0, at) : line
  const comment = at >= 0 ? line.slice(at) : ''
  const trimmed = code.trimStart()
  if (trimmed.startsWith('#') || trimmed.startsWith('//') || trimmed.startsWith('"""') || trimmed.startsWith('/**') || trimmed.startsWith('* ')) {
    return <span className="text-ink3">{line}</span>
  }
  const m = /^(.*?\b)(Niadra(?:Agent|Memory|MemoryProcessor|Crew|AgentsMemory|Middleware|Session|CallbackHandler)?|AsyncNiadra|niadra(?:Instructions|Tools|RunHooks|Middleware|Context)?|conversation_for(?:_call)?|history_tools|context_runnable|tool_(?:configs|definitions)|vapiTools|withNiadraContext|readWhatsApp|recordInbound|recordOutbound|whatsAppChallenge|readTwilio|recordTwilioInbound|verifyTwilio|attestationProof|sipSubject|sipConversationId|parse_(?:webhook|call|message)|subscribe|sent|elevenLabs|retell|vapi)(\b.*)$/.exec(code)
  return (
    <>
      {m ? (
        <>
          {m[1]}
          <mark className="rounded-[3px] bg-brand/45 px-0.5 text-ink">{m[2]}</mark>
          {m[3]}
        </>
      ) : (
        code
      )}
      {comment && <span className="text-ink3">{comment}</span>}
    </>
  )
}

/**
 * The adapter's minimal example, in the languages it exists in. Both code blocks are in the HTML (search
 * engines and AIs read both); the tabs only hide one. The Copy button copies the visible one.
 */
function CodeTabs({ sample, title }: { sample: CodeSample; title: string }) {
  const { t } = useT()
  const i = t.integrations
  const langs = (['python', 'typescript'] as const).filter((l) => sample[l])
  const [active, setActive] = useState<(typeof langs)[number]>(langs[0])
  const [copied, setCopied] = useState(false)
  const pre = useRef<HTMLPreElement>(null)
  const base = useId()
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(sample[active] ?? '')
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1800)
    } catch {
      // No clipboard permission: selects the code for the person to copy by hand.
      if (pre.current) {
        const r = document.createRange()
        r.selectNodeContents(pre.current)
        const s = window.getSelection()
        s?.removeAllRanges()
        s?.addRange(r)
      }
    }
  }
  return (
    <div className={`${panel} min-w-0 max-w-full overflow-hidden`}>
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-line bg-surface py-1.5 pl-4 pr-2 sm:pl-5 lg:py-2 lg:pr-3">
        <span className="py-1 text-[0.8rem] font-semibold leading-snug text-ink">{title}</span>
        <div className="flex items-center gap-2">
          {langs.length > 1 && (
            <div role="tablist" aria-label={title} className="inline-flex rounded-lg border border-edge bg-surface p-0.5">
              {langs.map((l) => (
                <button
                  key={l}
                  type="button"
                  role="tab"
                  id={`${base}-tab-${l}`}
                  aria-selected={l === active}
                  aria-controls={`${base}-panel-${l}`}
                  onClick={() => setActive(l)}
                  className={`min-h-9 rounded-md px-3 text-[0.84rem] font-semibold transition-colors ${l === active ? 'bg-ink text-bg' : 'text-ink2 hover:bg-bg hover:text-ink'}`}
                >
                  {i.langs[l]}
                </button>
              ))}
            </div>
          )}
          <button type="button" className={`${btnGhost} ${btnSm} lg:min-h-9 lg:px-3.5 lg:text-[0.86rem]`} onClick={copy} aria-live="polite">
            {copied ? i.copied : i.copy}
          </button>
        </div>
      </div>
      {langs.map((l) => (
        <div key={l} role="tabpanel" id={`${base}-panel-${l}`} aria-labelledby={`${base}-tab-${l}`} hidden={l !== active} className="min-w-0 max-w-full">
          {sample.install[l] && (
            <pre className="overflow-x-auto border-b border-line bg-bg px-4 py-3 font-mono text-[0.78rem] leading-relaxed text-ink2 sm:px-5" tabIndex={0}>
              <code>{sample.install[l]}</code>
            </pre>
          )}
          <pre ref={l === active ? pre : undefined} className="overflow-x-auto px-4 py-5 font-mono text-[0.8rem] leading-[1.75] text-ink sm:px-5" tabIndex={0}>
            <code>
              {(sample[l] ?? '').split('\n').map((ln, n) => (
                <Fragment key={n}>
                  {n > 0 && '\n'}
                  <CodeLine line={ln} />
                </Fragment>
              ))}
            </code>
          </pre>
        </div>
      ))}
      <ul className="flex flex-wrap gap-1.5 border-t border-line px-4 py-3 sm:px-5">
        {langs.map((l) => (
          <li key={l} className={srcChip}>{i.langs[l]}</li>
        ))}
      </ul>
    </div>
  )
}

/** One integration, named: the platform's own mark, the name and the one line under it, as a link to its page. */
function IntegrationCard({ item }: { item: Integration }) {
  const { t } = useT()
  const p = t.integrations.pages[item.key]
  return (
    <li>
      <Link to={integrationPath(item.slug)} className={`${panel} flex h-full min-h-14 flex-col gap-3 p-5 transition-colors hover:border-edge`}>
        <span className="flex items-center gap-3">
          <VendorLogo vendor={item.logo} />
          <span className={`${h3cls} text-[1.08rem] text-ink`}>{p.name}</span>
        </span>
        <span className="text-[0.94rem] leading-relaxed text-ink2">{p.sub.split('. ')[0]}.</span>
        <span className="mt-auto inline-flex items-center text-[0.9rem] font-semibold text-ink underline decoration-accent decoration-2 underline-offset-4">
          {t.integrations.cardCta}
        </span>
      </Link>
    </li>
  )
}

/** /integracoes: every page, by group, and the way to the 36 in the docs. */
export function Integrations() {
  const { t } = useT()
  const i = t.integrations
  return (
    <>
      <section className={`${wrap} pt-10 sm:pt-14 lg:pt-20`}>
        <h1 className={h1cls}>
          {i.h1a} <span className="text-ink3">{i.h1b}</span>
        </h1>
        <p className={`${lede} mt-6`}>{i.sub}</p>
      </section>
      {GROUP_ORDER.map((g, n) => (
        <Sec key={g} tone={n % 2 === 1 ? 'soft' : undefined} className={n === 0 ? '!pt-10 lg:!pt-14' : ''}>
          <H2>{i.groups[g]}</H2>
          <ul className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {INTEGRATIONS.filter((x) => x.group === g).map((x) => (
              <IntegrationCard key={x.slug} item={x} />
            ))}
          </ul>
          {n === GROUP_ORDER.length - 1 && (
            <div className="mt-10 max-w-[72ch]">
              <p className="leading-relaxed text-ink2">{i.allDocsNote}</p>
              <Link to={INTEGRATIONS_DOCS} className={`${textLink} mt-2`}>
                {i.allDocs}
                <ExternalArrow to={INTEGRATIONS_DOCS} />
              </Link>
            </div>
          )}
        </Sec>
      ))}
      <QuickAccess />
    </>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1.5 border-b border-line py-5 lg:grid-cols-[11rem_minmax(0,1fr)] lg:gap-8">
      <dt className={`${h3cls} text-[1.02rem] text-ink`}>{label}</dt>
      <dd className="leading-relaxed text-ink2">{children}</dd>
    </div>
  )
}

/** /integracoes/<slug>: the problem, the code, the five primitives, what the agent receives, the limits, the questions. */
export function IntegrationPage({ notFound }: { notFound: ReactNode }) {
  const { t } = useT()
  const item = integrationBySlug(useParams().slug)
  if (!item) return <>{notFound}</>
  const i = t.integrations
  const p = i.pages[item.key]
  const docs = integrationDocs(item)
  const sample = CODE[item.key]
  const hasCode = Boolean(sample.python || sample.typescript)
  const others = INTEGRATIONS.filter((x) => x.slug !== item.slug)
  return (
    <>
      <section className={`${wrap} pb-12 pt-10 sm:pt-14 lg:pb-16 lg:pt-20`}>
        <nav aria-label="Breadcrumb" className="flex flex-wrap items-center gap-x-2 text-[0.88rem] text-ink3">
          <Link to="/integracoes" className="inline-flex min-h-11 items-center font-medium text-ink2 underline decoration-line underline-offset-4 hover:decoration-accent">
            {i.crumb}
          </Link>
          <span aria-hidden="true">/</span>
          <span className="inline-flex min-h-11 items-center">{p.name}</span>
        </nav>
        <div className="mt-2 flex items-center gap-3">
          <VendorLogo vendor={item.logo} size="lg" />
          <span className="text-[1.05rem] font-semibold text-ink">{i.tag} · {p.name}</span>
        </div>
        <h1 className={productH1}>
          {p.h1a} <span className="text-ink3">{p.h1b}</span>
        </h1>
        <p className={`${lede} mt-6`}>{p.sub}</p>
        <div className="mt-8 grid gap-3 sm:flex">
          <Link className={btnPrimary} to={contactTo()}>{t.nav.cta}</Link>
          <Link className={btnGhost} to={docs}>
            {i.docsCta}
            <ExternalArrow to={docs} />
          </Link>
        </div>
      </section>

      <Sec tone="soft" className="!pt-10 lg:!pt-14">
        <div className="grid grid-cols-[minmax(0,1fr)] gap-10 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] lg:items-start lg:gap-14">
          <div className="min-w-0">
            {p.problem.map((para, n) => (
              <p key={n} className={`max-w-[58ch] text-[1.04rem] leading-relaxed sm:text-[1.08rem] ${n === 0 ? 'text-ink' : 'mt-5 text-ink2'}`}>
                {para}
              </p>
            ))}
          </div>
          {hasCode ? (
            <div className="min-w-0">
              <CodeTabs sample={sample} title={i.codeH} />
              <p className="mt-3 text-[0.9rem] leading-relaxed text-ink2">{i.codeNote.replace('{files}', item.examples)}</p>
            </div>
          ) : (
            <div className={`${panel} p-5 sm:p-6`}>
              <h2 className={`${h3cls} text-[1.08rem]`}>{i.installH}</h2>
              <p className="mt-3 leading-relaxed text-ink2">{p.install}</p>
            </div>
          )}
        </div>
      </Sec>

      <Sec>
        <H2>{i.howH}</H2>
        <Lede>{i.howSub}</Lede>
        <dl className="mt-8 border-t border-line">
          <Row label={i.prim.context}>{p.how.context}</Row>
          <Row label={i.prim.turns}>{p.how.turns}</Row>
          <Row label={i.prim.tools}>{p.how.tools}</Row>
          <Row label={i.prim.verify}>{p.how.verify}</Row>
          <Row label={i.prim.handoff}>{p.how.handoff}</Row>
        </dl>
      </Sec>

      <Sec tone="soft">
        <H2>{i.receivesH}</H2>
        <Lede>{i.receivesSub}</Lede>
        <div className="mt-8 grid grid-cols-[minmax(0,1fr)] gap-8 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)] lg:items-start lg:gap-12">
          {item.group === 'voice' || item.group === 'channels' ? <ContextDoc /> : <div className={`${panel} p-5 sm:p-6`}><Bullets items={i.receives} single /></div>}
          <div>
            {item.group === 'voice' || item.group === 'channels' ? <Bullets items={i.receives} single /> : null}
            <Link to="/produtos/contexto" className={`${textLink} mt-6`}>{i.receivesLink}</Link>
          </div>
        </div>
      </Sec>

      <Sec>
        <H2>{i.limitsH}</H2>
        <ul className="mt-6 grid max-w-[76ch] gap-3">
          {p.limits.map((l) => (
            <li key={l} className="flex gap-3 text-[1rem] leading-relaxed text-ink2">
              <span className="mt-[0.6rem] h-1.5 w-1.5 shrink-0 rounded-full bg-accent" aria-hidden="true" />
              {l}
            </li>
          ))}
        </ul>
      </Sec>

      <Sec tone="soft">
        <H2>{i.faqH}</H2>
        <Faq items={[...p.faq, ...i.sharedFaq]} />
      </Sec>

      <Sec>
        <H2>{i.othersH}</H2>
        <ul className="mt-6 flex flex-wrap gap-2">
          {others.map((x) => (
            <li key={x.slug}>
              <Link to={integrationPath(x.slug)} className={`${btnGhost} ${btnSm} gap-2 pl-2.5`}>
                <VendorLogo vendor={x.logo} size="sm" />
                {i.pages[x.key].name}
              </Link>
            </li>
          ))}
          <li>
            <Link to={INTEGRATIONS_DOCS} className={`${btnGhost} ${btnSm}`}>
              {i.allDocs}
              <ExternalArrow to={INTEGRATIONS_DOCS} />
            </Link>
          </li>
        </ul>
      </Sec>

      <QuickAccess />
    </>
  )
}
