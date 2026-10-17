import { useEffect, useRef, useState } from 'react'
import { Select } from '../components/Select'
import { useSearchParams } from 'react-router-dom'
import { Link } from '../i18n/link'
import { useT } from '../i18n'
import { IconChip, type IconName } from '../components/Icon'
import { EMAIL_RE, personalKind } from '../lib/email'
import { readOrigin } from '../lib/origin'
import { H2, Sec } from '../components/Shell'
import { btnGhost, btnPrimary, h1cls, h3cls, lede, panel, textLink, wrap } from '../ui'

// In the order of `enterprise.features` in pt.ts/en.ts.
const FEATURE_ICONS: IconName[] = ['archive', 'cloud', 'user', 'globe', 'shield', 'clock']

type Values = { name: string; email: string; company: string; interest: string; heard: string; what: string }
type Errors = Partial<Record<keyof Values, string>>
type Status = 'idle' | 'sending' | 'sent' | 'failed' | 'limited'

const field = (bad: boolean) =>
  `w-full rounded-lg border bg-bg px-4 py-3 text-[1rem] text-ink placeholder:text-ink3 transition-colors focus:outline-none focus-visible:outline-2 focus-visible:outline-ink ${
    bad ? 'border-2 border-ink' : 'border-edge'
  }`
const labelCls = 'mb-1.5 block text-[0.88rem] font-medium text-ink2'

export function Enterprise() {
  const { t, lang } = useT()
  const e = t.enterprise
  const [params] = useSearchParams()

  const [v, setV] = useState<Values>({ name: '', email: '', company: '', interest: 'conversa', heard: '', what: '' })
  const [errors, setErrors] = useState<Errors>({})
  const [status, setStatus] = useState<Status>('idle')
  // Honeypot field: invisible to people, filled in by spam bots.
  const [trap, setTrap] = useState('')
  const formRef = useRef<HTMLFormElement>(null)

  // The interest coming from the link (?interesse=regulado) comes in after hydration:
  // the prerendered page does not know the query string.
  useEffect(() => {
    const wanted = params.get('interesse') ?? ''
    if (e.interests.some((i) => i.v === wanted)) setV((s) => ({ ...s, interest: wanted }))
  }, [params, e.interests])

  const set = (k: keyof Values) => (ev: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    setV((s) => ({ ...s, [k]: ev.target.value }))
    if (errors[k]) setErrors((s) => ({ ...s, [k]: undefined }))
  }

  const submit = async (ev: React.FormEvent) => {
    ev.preventDefault()
    if (status === 'sending') return
    const next: Errors = {}
    if (!v.name.trim()) next.name = e.errRequired
    if (!v.email.trim()) next.email = e.errRequired
    else if (!EMAIL_RE.test(v.email.trim())) next.email = e.errEmail
    else if (personalKind(v.email)) next.email = e.errCorporate
    if (!v.company.trim()) next.company = e.errRequired
    if (!v.what.trim()) next.what = e.errRequired
    setErrors(next)

    const first = (Object.keys(next) as (keyof Values)[])[0]
    if (first) {
      formRef.current?.querySelector<HTMLElement>(`[name="${first}"]`)?.focus()
      return
    }

    // The request goes to the site API (server/api.ts), which saves it in Postgres.
    setStatus('sending')
    try {
      const res = await fetch('/api/contato', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: v.name.trim(),
          email: v.email.trim(),
          company: v.company.trim(),
          interest: v.interest,
          message: v.what.trim(),
          heard: v.heard,
          lang,
          page: window.location.pathname,
          origin: readOrigin(),
          website: trap,
          form: 'full',
        }),
      })
      if (res.ok) setStatus('sent')
      else if (res.status === 429) setStatus('limited')
      else if (res.status === 400) {
        const body = (await res.json().catch(() => ({}))) as { fields?: string[]; reasons?: { email?: string } }
        const bad: Errors = {}
        const emailMsg = { free: e.errCorporate, disposable: e.errCorporate, domain: e.errDomain }[body.reasons?.email ?? ''] ?? e.errEmail
        for (const f of body.fields ?? []) {
          const k = (f === 'message' ? 'what' : f) as keyof Values
          if (k in v) bad[k] = k === 'email' ? emailMsg : e.errRequired
        }
        setErrors(bad)
        setStatus(Object.keys(bad).length ? 'idle' : 'failed')
      } else setStatus('failed')
    } catch {
      setStatus('failed')
    }
  }

  const again = () => {
    setV({ name: '', email: '', company: '', interest: v.interest, heard: '', what: '' })
    setStatus('idle')
  }

  const err = (k: keyof Values) =>
    errors[k] ? (
      <p id={`err-${k}`} className="mt-1.5 text-[0.88rem] font-medium text-ink">
        {errors[k]}
      </p>
    ) : null

  return (
    <>
      <section className={`${wrap} pb-12 pt-10 sm:pt-14 lg:pb-16 lg:pt-20`}>
        {/* On mobile: headline, form, plans. On desktop the plans move up, below the headline. */}
        <div className="grid items-start gap-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,0.92fr)] lg:grid-rows-[auto_1fr] lg:gap-x-16">
          <div>
            <h1 className={h1cls}>
              {e.h1a} <span className="text-ink3">{e.h1b}</span>
            </h1>
            <p className={`${lede} mt-6`}>{e.sub}</p>
          </div>

          <div id="contato" className={`${panel} scroll-mt-24 p-5 sm:p-7 lg:col-start-2 lg:row-span-2 lg:row-start-1`}>
            <h2 className={`${h3cls} text-[1.25rem]`}>{e.formTitle}</h2>

            {status === 'sent' ? (
              <div className="mt-5" role="status">
                <p className="flex items-center gap-2.5 text-[1.06rem] font-semibold text-ink">
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand text-on-brand">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M4.5 12.5l5 5L20 7" />
                    </svg>
                  </span>
                  {e.sentTitle}
                </p>
                <p className="mt-2 leading-relaxed text-ink2">{e.sentBody}</p>
                <button type="button" className={`${btnGhost} mt-5`} onClick={again}>{e.again}</button>
              </div>
            ) : (
              <form ref={formRef} className="relative mt-5 grid gap-4" onSubmit={submit} noValidate>
                <div>
                  <label className={labelCls} htmlFor="f-name">{e.name}</label>
                  <input id="f-name" name="name" className={field(!!errors.name)} value={v.name} onChange={set('name')}
                    autoComplete="name" aria-invalid={!!errors.name} aria-describedby={errors.name ? 'err-name' : undefined} />
                  {err('name')}
                </div>
                <div>
                  <label className={labelCls} htmlFor="f-email">{e.email}</label>
                  <input id="f-email" name="email" type="email" inputMode="email" className={field(!!errors.email)} value={v.email}
                    onChange={set('email')} placeholder={e.emailPh} autoComplete="email" aria-invalid={!!errors.email} aria-describedby={errors.email ? 'err-email' : undefined} />
                  {err('email')}
                </div>
                <div>
                  <label className={labelCls} htmlFor="f-company">{e.company}</label>
                  <input id="f-company" name="company" className={field(!!errors.company)} value={v.company} onChange={set('company')}
                    autoComplete="organization" aria-invalid={!!errors.company} aria-describedby={errors.company ? 'err-company' : undefined} />
                  {err('company')}
                </div>
                <div>
                  <label className={labelCls} htmlFor="f-interest">{e.interest}</label>
                  <Select id="f-interest" triggerClassName={`${field(false)} min-h-12`} value={v.interest} onChange={(value) => setV((s) => ({ ...s, interest: value }))}
                    options={e.interests.map((i) => ({ value: i.v, label: i.l }))} />
                </div>
                <div>
                  <label className={labelCls} htmlFor="f-what">{e.what}</label>
                  <textarea id="f-what" name="what" rows={4} className={`${field(!!errors.what)} resize-y`} value={v.what}
                    onChange={set('what')} placeholder={e.whatPh} aria-invalid={!!errors.what} aria-describedby={errors.what ? 'err-what' : undefined} />
                  {err('what')}
                </div>
                <div>
                  <label className={labelCls} htmlFor="f-heard">{e.heard}</label>
                  {/* Some visits coming from AI arrive without an origin in the browser: the person tells us. */}
                  <Select id="f-heard" triggerClassName={`${field(false)} min-h-12`} value={v.heard} onChange={(value) => setV((s) => ({ ...s, heard: value }))}
                    options={e.heardOptions.map((o) => ({ value: o.v, label: o.l }))} />
                </div>
                {/* Off screen and out of the tab order: whoever fills it in is a bot. */}
                <div className="absolute -left-[9999px] h-px w-px overflow-hidden" aria-hidden="true">
                  <label htmlFor="f-website">Website</label>
                  <input id="f-website" name="website" tabIndex={-1} autoComplete="off" value={trap} onChange={(ev) => setTrap(ev.target.value)} />
                </div>
                <button className={`${btnPrimary} w-full disabled:cursor-wait disabled:opacity-70`} type="submit" disabled={status === 'sending'}>
                  {status === 'sending' ? e.sending : e.submit}
                </button>
                {(status === 'failed' || status === 'limited') && (
                  <p className="rounded-lg border border-edge bg-bg px-4 py-3 text-[0.94rem] leading-relaxed text-ink" role="alert">
                    {status === 'limited' ? e.errRate : e.errSend}
                  </p>
                )}
                <p className="text-[0.86rem] leading-relaxed text-ink3">{e.hint}</p>
              </form>
            )}
          </div>

          <div className="lg:col-start-1 lg:row-start-2">
            <ul className="border-t border-line">
              {t.pricing.plans.map((pl) => (
                <li key={pl.name} className="grid gap-1 border-b border-line py-4 sm:grid-cols-[7.5rem_1fr] sm:gap-6">
                  <span className={`${h3cls} text-[1.02rem]`}>{pl.name}</span>
                  <span>
                    <span className="block text-[0.96rem] text-ink">{pl.blurb}</span>
                    <span className="mt-0.5 block text-[0.88rem] text-ink3">
                      {pl.price} · {pl.unit}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
            <Link to="/preco" className={`${textLink} mt-2`}>
              {e.plansLink}
            </Link>
          </div>
        </div>
      </section>

      <Sec tone="soft">
        <H2>{e.featuresH2}</H2>
        <div className="mt-9 grid gap-x-10 gap-y-9 sm:grid-cols-2 lg:grid-cols-3">
          {e.features.map((f, i) => (
            <div key={f.t} className="flex gap-4 sm:block">
              <IconChip name={FEATURE_ICONS[i]} tone="plain" />
              <div>
                <h3 className={`${h3cls} text-[1.08rem] sm:mt-4`}>{f.t}</h3>
                <p className="mt-2 leading-relaxed text-ink2">{f.d}</p>
              </div>
            </div>
          ))}
        </div>
      </Sec>
    </>
  )
}
