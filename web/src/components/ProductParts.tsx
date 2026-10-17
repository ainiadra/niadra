import { DOCS, useT } from '../i18n'
import { ExternalArrow, Link } from '../i18n/link'
import { ExampleTag, Snippet } from './Artifacts'
import { Icon, IconChip, type IconName } from './Icon'
import { h3cls, hl, panel } from '../ui'

/**
 * The artifacts of the product pages. Each one shows the mechanism with Marina's
 * example (22/09, 14:02 to 14:07), the same as the home stage and the memory's
 * context, so the example stays consistent with itself.
 */

const label = 'mb-1 text-[0.74rem] font-semibold text-ink3'
const chip = 'whitespace-nowrap rounded-md border border-line bg-surface px-1.5 py-0.5 text-[0.72rem] font-semibold text-ink2'
const head = 'flex items-center justify-between gap-3 border-b border-line bg-surface px-4 py-3 sm:px-5'
const codeBox = 'overflow-x-auto rounded-lg border border-line bg-bg px-3 py-2.5 font-mono text-[0.76rem] leading-relaxed text-ink'

/** The seven MCP tools, by the name the model sees. */
export function McpTools() {
  const { t } = useT()
  const s = t.demo.mcp
  return (
    <div className={`${panel} px-4 py-4 sm:px-5`}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <h3 className={`${h3cls} text-[0.98rem]`}>{s.title}</h3>
        <p className="text-[0.9rem] leading-snug text-ink2">{s.note}</p>
      </div>
      <ul className="mt-3 flex flex-wrap gap-1.5">
        {s.tools.map((x) => (
          <li key={x} className="rounded-md border border-line bg-bg px-2 py-1 font-mono text-[0.78rem] text-ink">
            {x}
          </li>
        ))}
      </ul>
    </div>
  )
}

/** From event to memory: what the ERP sends on the left, what the memory keeps on the right. */
export function SystemFlow() {
  const { t } = useT()
  const s = t.products.systems
  return (
    <div className={`${panel} overflow-hidden`}>
      <div className={head}>
        <h3 className={`${h3cls} text-[0.98rem]`}>{s.flowTitle}</h3>
        <ExampleTag />
      </div>
      <div className="grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
        <div className="border-b border-line p-4 sm:p-5 lg:border-b-0 lg:border-r">
          <div className={label}>{s.inLabel}</div>
          <pre className={codeBox} tabIndex={0}>
            <code>{s.inCode}</code>
          </pre>
        </div>
        <div className="p-4 sm:p-5">
          <div className={label}>{s.outLabel}</div>
          <dl className="border-t border-line">
            {s.out.map((o) => (
              <div key={o.k} className="grid gap-1 border-b border-line py-3 last:border-b-0 sm:grid-cols-[6.5rem_minmax(0,1fr)] sm:gap-4">
                <dt className="text-[0.82rem] font-semibold text-ink3">{o.k}</dt>
                <dd>
                  <span className="block text-[0.95rem] font-semibold leading-snug text-ink">{o.v}</span>
                  <span className="mt-0.5 block text-[0.86rem] leading-snug text-ink2">{o.note}</span>
                </dd>
              </div>
            ))}
          </dl>
        </div>
      </div>
      <p className="border-t border-line px-4 py-3.5 text-[0.9rem] leading-relaxed text-ink2 sm:px-5">{s.flowFoot}</p>
    </div>
  )
}

/** People linked to organizations: role, organization type, validity and origin of the link. */
export function OrgLinks() {
  const { t } = useT()
  const s = t.products.identity
  return (
    <div className={`${panel} overflow-hidden`}>
      <div className={head}>
        <h3 className={`${h3cls} text-[0.98rem]`}>{s.linksTitle}</h3>
        <ExampleTag />
      </div>
      <ul>
        {s.links.map((l) => (
          <li key={`${l.who}-${l.org}`} className="grid gap-1.5 border-b border-line px-4 py-3.5 sm:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] sm:gap-4 sm:px-5">
            <span>
              <span className="block text-[0.95rem] font-semibold text-ink">{l.who}</span>
              <span className="block text-[0.86rem] text-ink2">{l.role}</span>
            </span>
            <span>
              <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="text-[0.95rem] font-semibold text-ink">{l.org}</span>
                <span className={chip}>{l.kind}</span>
              </span>
              <span className="mt-0.5 block font-mono text-[0.74rem] text-ink3">
                {l.when} · {l.from}
              </span>
            </span>
          </li>
        ))}
      </ul>
      <p className="px-4 py-4 text-[0.92rem] leading-relaxed text-ink2 sm:px-5">{s.linksNote}</p>
    </div>
  )
}

/**
 * The status of an item. An open pending item gets a hollow ring; a fulfilled one gets
 * the lime check, the brand's "done" sign. The text goes with it: a state shown only
 * by color disappears for people who cannot tell hues apart.
 */
function Status({ status, text }: { status: string; text: string }) {
  if (!status) return null
  return (
    <span className="inline-flex items-center gap-1.5 text-[0.78rem] font-semibold text-ink2">
      {status === 'done' ? (
        <span className="flex h-4 w-4 items-center justify-center rounded-full bg-brand text-on-brand">
          <Icon name="check" size={10} className="[stroke-width:3.2]" />
        </span>
      ) : (
        <span className="h-3.5 w-3.5 rounded-full border-2 border-edge" aria-hidden="true" />
      )}
      {text}
    </span>
  )
}

/** A customer's memory: each item with type, origin and evidence. */
export function MemoryRecord() {
  const { t } = useT()
  const s = t.products.memory
  return (
    <div className={`${panel} overflow-hidden`}>
      <div className={head}>
        <h3 className={`${h3cls} text-[0.98rem]`}>{s.recTitle}</h3>
        <ExampleTag />
      </div>
      <ol>
        {s.items.map((it) => (
          <li key={it.text} className="border-b border-line px-4 py-4 last:border-b-0 sm:px-5">
            <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
              <span className="text-[0.78rem] font-semibold text-ink3">{it.kind}</span>
              <Status status={it.status} text={it.statusText} />
            </div>
            <p className="mt-1 text-[0.98rem] font-medium leading-snug text-ink">{it.text}</p>
            <p className="mt-1.5 font-mono text-[0.74rem] text-ink3">{it.src}</p>
            <p className="mt-1 text-[0.86rem] leading-snug text-ink2">{it.ev}</p>
          </li>
        ))}
      </ol>
    </div>
  )
}

/**
 * A pattern found: the sentence that goes into the context, the rule that found it, the
 * evidence, the expiry and who it reaches. The highlight marks what the memory knows.
 */
export function PatternCard() {
  const { t } = useT()
  const c = t.products.patterns.card
  return (
    <div className={`${panel} overflow-hidden`}>
      <div className={head}>
        <span className="text-[0.8rem] font-semibold text-ink">{c.title}</span>
        <ExampleTag />
      </div>
      <div className="px-4 py-5 sm:px-5">
        <p className="text-[1.12rem] font-semibold leading-snug text-ink">
          <mark className={hl}>{c.name}</mark>
        </p>

        <div className="mt-5">
          <div className={label}>{c.ruleLabel}</div>
          <p className="text-[0.94rem] leading-snug text-ink">{c.rule}</p>
        </div>

        <div className="mt-5">
          <div className={label}>{c.evLabel}</div>
          <ul className="border-t border-line">
            {c.ev.map((e) => (
              <li key={e.when} className="grid gap-1 border-b border-line py-2.5 sm:grid-cols-[10.5rem_minmax(0,1fr)] sm:gap-4">
                <span className="font-mono text-[0.74rem] leading-relaxed text-ink3">
                  {e.when} · {e.channel}
                </span>
                <span className="text-[0.92rem] leading-snug text-ink">{e.t}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="mt-5 grid gap-5 sm:grid-cols-[minmax(0,0.7fr)_minmax(0,1.3fr)]">
          <div>
            <div className={label}>{c.validLabel}</div>
            <p className="font-mono text-[0.86rem] text-ink">{c.valid}</p>
          </div>
          <div>
            <div className={label}>{c.reachLabel}</div>
            <ul className="grid gap-1.5">
              {c.reach.map((r) => (
                <li key={r.who} className="flex items-start gap-2 text-[0.9rem] leading-snug">
                  <Icon name={r.ok ? 'check' : 'block'} size={16} className={`mt-0.5 ${r.ok ? 'text-accent' : 'text-ink3'}`} />
                  <span>
                    <span className="font-semibold text-ink">{r.who}</span> <span className="text-ink2">{r.note}</span>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </div>
  )
}

/** A trigger: the rule on top, the outgoing webhook below, and who acts in the footer. */
export function AlertHook() {
  const { t } = useT()
  const a = t.products.alerts.hook
  return (
    <div className={`${panel} overflow-hidden`}>
      <div className={head}>
        <h3 className={`${h3cls} text-[0.98rem]`}>{a.ruleTitle}</h3>
        <ExampleTag />
      </div>
      <dl className="px-4 sm:px-5">
        {a.rule.map((r) => (
          <div key={r.k} className="grid gap-0.5 border-b border-line py-2.5 last:border-b-0 sm:grid-cols-[7.5rem_minmax(0,1fr)] sm:gap-4">
            <dt className="text-[0.82rem] font-semibold text-ink3">{r.k}</dt>
            <dd className="text-[0.94rem] leading-snug text-ink">{r.v}</dd>
          </div>
        ))}
      </dl>
      <div className="border-t border-line px-4 py-4 sm:px-5">
        <div className={label}>{a.sentTitle}</div>
        <pre className={codeBox} tabIndex={0}>
          <code>{a.request}</code>
        </pre>
      </div>
      <p className="border-t border-line px-4 py-3.5 text-[0.92rem] font-semibold leading-snug text-ink sm:px-5">{a.foot}</p>
    </div>
  )
}

/**
 * Context use per agent. A real table (header and row cells with scope), numbers
 * right-aligned in tabular figures. An illustrative example, marked as such: never
 * a real customer's number.
 */
export function UseTable() {
  const { t } = useT()
  const m = t.products.measure
  return (
    <div className={`${panel} overflow-hidden`}>
      <div className={head}>
        <h3 className={`${h3cls} text-[0.98rem]`}>{m.tableTitle}</h3>
        <ExampleTag />
      </div>
      {/* If a translation does not fit on mobile, the table scrolls inside the panel; it never cuts. */}
      <div className="overflow-x-auto">
      <table className="w-full text-left">
        <thead>
          <tr className="border-b border-line align-bottom text-[0.72rem] text-ink3 sm:text-[0.78rem]">
            {m.cols.map((c, i) => (
              <th key={c} scope="col" className={`px-2 py-2.5 font-semibold first:pl-3 last:pr-3 sm:px-5 ${i ? 'w-[16%] text-right' : ''}`}>
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {m.rows.map((r) => (
            <tr key={r.agent} className="border-b border-line last:border-b-0">
              <th scope="row" className="py-3.5 pl-3 pr-2 align-top font-normal sm:px-5">
                <span className="block text-[0.95rem] font-semibold leading-snug text-ink">{r.agent}</span>
                <span className="block font-mono text-[0.72rem] text-ink3">{r.vendor}</span>
              </th>
              {[r.used, r.repeated, r.contradicted].map((v, i) => (
                <td key={i} className="px-2 py-3.5 text-right align-top font-mono text-[0.92rem] tabular-nums text-ink last:pr-3 sm:px-5">
                  {v}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      <p className="border-t border-line px-4 py-3.5 text-[0.9rem] leading-relaxed text-ink2 sm:px-5">{m.tableFoot}</p>
    </div>
  )
}

/** The four signals of the measurement: what counts and what does not, side by side. */
export function Signals() {
  const { t } = useT()
  const m = t.products.measure
  return (
    <div className="mt-12">
      <h3 className={`${h3cls} text-[1.2rem]`}>{m.signalsTitle}</h3>
      <dl className="mt-4 grid gap-x-10 border-t border-edge sm:grid-cols-2">
        {m.signals.map((s) => (
          <div key={s.t} className="border-b border-edge py-5">
            <dt className="text-[1.02rem] font-semibold text-ink">{s.t}</dt>
            <dd className="mt-1.5 text-[0.95rem] leading-relaxed text-ink2">{s.d}</dd>
            <dd className="mt-2 text-[0.9rem] leading-relaxed text-ink2">
              <span className="font-semibold text-ink">{m.notLabel}:</span> {s.not}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

// In the order of `products.console.blocks`: governance, review, export, configuration.
const CONSOLE_ICONS: IconName[] = ['eye', 'doc', 'archive', 'sliders']

/** What the Console does, in four short blocks with the icon of each function. */
export function ConsoleBlocks() {
  const { t } = useT()
  const c = t.products.console
  return (
    <div className="mt-12 grid gap-x-8 gap-y-10 sm:grid-cols-2 xl:grid-cols-4">
      {c.blocks.map((b, i) => (
        <div key={b.t}>
          {/* White frame with a hairline: the blocks sit in the pale lime section of the Console page. */}
          <IconChip name={CONSOLE_ICONS[i]} tone="plain" />
          <h3 className={`${h3cls} mt-4 text-[1.1rem]`}>{b.t}</h3>
          <ul className="mt-3 border-t border-line">
            {b.items.map((x) => (
              <li key={x} className="border-b border-line py-2.5 text-[0.94rem] leading-snug text-ink2">
                {x}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}

/* ---------- the memory that operates: state, claims, coordination, turn record ---------- */

/** The four logical values of a state field, as text and as a sign. Never a color alone. */
function LogicChip({ logic, text }: { logic: string; text: string }) {
  const tone = logic === 'unobserved' || logic === 'defect' ? 'text-ink3' : 'text-ink2'
  return <span className={`${chip} ${tone}`}>{text}</span>
}

/** Whether a value may be stated now: the lime check when it may, the hollow ring when it may not. */
function ClaimSign({ ok }: { ok: boolean }) {
  return ok ? (
    <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-brand text-on-brand" aria-hidden="true">
      <Icon name="check" size={10} className="[stroke-width:3.2]" />
    </span>
  ) : (
    <span className="mt-0.5 h-3.5 w-3.5 shrink-0 rounded-full border-2 border-edge" aria-hidden="true" />
  )
}

/**
 * The state of now: two of Marina's objects, field by field, with the age and the source of each value, its
 * logical value and whether the agent may state it. What it may not state is listed at the end, as the read
 * returns it. The header row exists from the tablet width up; on the phone each row stacks.
 */
export function StateRead() {
  const { t } = useT()
  const s = t.products.state.read
  const cols = 'sm:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,1.3fr)]'
  return (
    <div className={`${panel} overflow-hidden`}>
      <div className={head}>
        <h3 className={`${h3cls} text-[0.98rem]`}>{s.title}</h3>
        <span className="flex items-center gap-2">
          <span className={chip}>{s.purpose}</span>{' '}
          <ExampleTag />
        </span>
      </div>
      <div className={`hidden gap-4 border-b border-line px-5 py-2 text-[0.78rem] text-ink3 sm:grid ${cols}`}>
        <span>
          {s.cols[0]} · {s.cols[1]}
        </span>
        <span>{s.cols[2]}</span>
        <span>{s.cols[3]}</span>
      </div>
      {s.objects.map((o) => (
        <div key={o.t} className="border-b border-line last:border-b-0">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 bg-bg px-4 py-2.5 sm:px-5">
            <span className="text-[0.95rem] font-semibold text-ink">{o.t}</span>{' '}
            <span className="font-mono text-[0.72rem] text-ink3">{o.src}</span>
          </div>
          <ul>
            {o.rows.map((r) => (
              <li key={r.field} className={`grid gap-x-4 gap-y-1.5 border-t border-line px-4 py-3 sm:items-start sm:px-5 ${cols}`}>
                <span className="min-w-0">
                  <span className="block text-[0.82rem] font-semibold text-ink3">{r.field}</span>{' '}
                  <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="font-mono text-[0.9rem] text-ink">{r.value}</span>{' '}
                    <LogicChip logic={r.logic} text={s.logic[r.logic as keyof typeof s.logic]} />
                  </span>
                </span>{' '}
                <span className="font-mono text-[0.74rem] leading-relaxed text-ink3">{r.age}</span>{' '}
                <span className="flex items-start gap-2 text-[0.88rem] leading-snug text-ink2">
                  <ClaimSign ok={r.claim} />
                  {r.note}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}
      <div className="border-t border-line px-4 py-3.5 sm:px-5">
        <div className={label}>{s.prohibitionsLabel}</div>
        <ul className="flex flex-wrap gap-1.5">
          {s.prohibitions.map((p) => (
            <li key={p} className={chip}>{p}</li>
          ))}
        </ul>
      </div>
      <p className="border-t border-line px-4 py-3.5 text-[0.9rem] leading-relaxed text-ink2 sm:px-5">{s.foot}</p>
    </div>
  )
}

/** The draft cut at each claim, so every number the agent said shows what the check made of it. */
function segments(text: string, claims: readonly { span: string; ok: boolean }[]) {
  const out: { t: string; ok?: boolean }[] = []
  let rest = text
  for (const c of claims) {
    const i = rest.indexOf(c.span)
    if (i < 0) continue
    if (i > 0) out.push({ t: rest.slice(0, i) })
    out.push({ t: c.span, ok: c.ok })
    rest = rest.slice(i + c.span.length)
  }
  if (rest) out.push({ t: rest })
  return out
}

/**
 * The claim contract at work: what the agent wrote, each number it said with its evidence, verdict and
 * action, and what went out. The highlighter marks the numbers the memory backs; a dotted underline, the
 * one nothing backs. The state is always said in words beside the sign.
 */
export function ClaimGuard() {
  const { t } = useT()
  const g = t.products.claims.guard
  return (
    <div className={`${panel} overflow-hidden`}>
      <div className={head}>
        <h3 className={`${h3cls} text-[0.98rem]`}>{g.title}</h3>
        <ExampleTag />
      </div>
      <div className="px-4 py-4 sm:px-5">
        <div className={label}>{g.draftLabel}</div>
        <p className="text-[0.98rem] leading-relaxed text-ink">
          {segments(g.draft, g.claims).map((s, i) =>
            s.ok === undefined ? (
              <span key={i}>{s.t}</span>
            ) : s.ok ? (
              <mark key={i} className={hl}>{s.t}</mark>
            ) : (
              <span key={i} className="underline decoration-ink3 decoration-dotted decoration-2 underline-offset-4">{s.t}</span>
            ),
          )}
        </p>
      </div>
      <div className="border-t border-line px-4 py-4 sm:px-5">
        <div className={label}>{g.claimsLabel}</div>
        <ul>
          {g.claims.map((c) => (
            <li key={c.span} className="grid gap-x-4 gap-y-1 border-t border-line py-3 first:border-t-0 sm:grid-cols-[7rem_minmax(0,1fr)] sm:gap-4">
              <span>
                <span className="block font-mono text-[0.9rem] font-semibold text-ink">{c.span}</span>{' '}
                <span className="block text-[0.78rem] text-ink3">{c.kind}</span>
              </span>{' '}
              <span className="min-w-0">
                <span className="block text-[0.9rem] leading-snug text-ink2">{c.evidence}</span>{' '}
                <span className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.84rem]">
                  <span className="inline-flex items-center gap-1.5 font-semibold text-ink">
                    <ClaimSign ok={c.ok} />
                    {g.verdictLabel}: {c.verdict}
                  </span>{' '}
                  <span className="text-ink2">
                    {g.actionLabel}: {c.action}
                  </span>
                </span>
              </span>
            </li>
          ))}
        </ul>
      </div>
      <div className="border-t border-line bg-bg px-4 py-4 sm:px-5">
        <div className={label}>{g.outLabel}</div>
        <p className="text-[0.98rem] font-medium leading-relaxed text-ink">{g.out}</p>
      </div>
      <p className="border-t border-line px-4 py-3.5 text-[0.9rem] leading-relaxed text-ink2 sm:px-5">{g.foot}</p>
    </div>
  )
}

/** A coordination decision as a sign and a word: the check for "now", the ring for "wait", the crossed circle for "no". */
function Decision({ kind, text }: { kind: string; text: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[0.86rem] font-semibold text-ink">
      {kind === 'allow' ? (
        <span className="flex h-4 w-4 items-center justify-center rounded-full bg-brand text-on-brand" aria-hidden="true">
          <Icon name="check" size={10} className="[stroke-width:3.2]" />
        </span>
      ) : kind === 'defer' ? (
        <span className="h-3.5 w-3.5 rounded-full border-2 border-edge" aria-hidden="true" />
      ) : (
        <Icon name="block" size={16} className="text-ink3" />
      )}
      {text}
    </span>
  )
}

/**
 * Four agents ask before acting on the same customer, at 14:07 to 14:09: who asks, what it wants to do and
 * the decision with its reason. Niadra answers and records; the company's agent sends and its gateway bars.
 */
export function CoordinationCheck() {
  const { t } = useT()
  const c = t.products.coordination.check
  const cols = 'md:grid-cols-[minmax(0,0.9fr)_minmax(0,1fr)_minmax(0,1.3fr)]'
  return (
    <div className={`${panel} overflow-hidden`}>
      <div className={head}>
        <h3 className={`${h3cls} text-[0.98rem]`}>{c.title}</h3>
        <ExampleTag />
      </div>
      <div className={`hidden gap-4 border-b border-line px-5 py-2 text-[0.78rem] text-ink3 md:grid ${cols}`}>
        {c.cols.map((x) => (
          <span key={x}>{x}</span>
        ))}
      </div>
      <ul>
        {c.rows.map((r) => (
          <li key={`${r.who}-${r.what}`} className={`grid gap-x-4 gap-y-1.5 border-b border-line px-4 py-3.5 last:border-b-0 sm:px-5 ${cols}`}>
            <span className="text-[0.95rem] font-semibold leading-snug text-ink">{r.who}</span>{' '}
            <span className="font-mono text-[0.76rem] leading-relaxed text-ink2">{r.what}</span>{' '}
            <span className="min-w-0">
              <Decision kind={r.decision} text={c.decisions[r.decision as keyof typeof c.decisions]} />{' '}
              <span className="mt-1 block text-[0.9rem] leading-snug text-ink2">{r.answer}</span>
            </span>
          </li>
        ))}
      </ul>
      <p className="border-t border-line px-4 py-3.5 text-[0.92rem] font-semibold leading-snug text-ink sm:px-5">{c.foot}</p>
    </div>
  )
}

/** One turn record: what the agent read, called, decided, stated and said, with its build, content mode, flags and replay. */
export function TurnRecord() {
  const { t } = useT()
  const r = t.products.turns.record
  return (
    <div className={`${panel} overflow-hidden`}>
      <div className={head}>
        <h3 className={`${h3cls} text-[0.98rem]`}>{r.title}</h3>
        <ExampleTag />
      </div>
      <p className="border-b border-line bg-bg px-4 py-2.5 font-mono text-[0.74rem] text-ink2 sm:px-5">{r.id}</p>
      <dl className="px-4 sm:px-5">
        {r.rows.map((row) => (
          <div key={row.k} className="grid gap-0.5 border-b border-line py-2.5 last:border-b-0 sm:grid-cols-[7.5rem_minmax(0,1fr)] sm:gap-4">
            <dt className="text-[0.82rem] font-semibold text-ink3">{row.k}</dt>
            <dd className="text-[0.92rem] leading-snug text-ink">{row.v}</dd>
          </div>
        ))}
      </dl>
      <p className="border-t border-line px-4 py-3.5 text-[0.92rem] font-semibold leading-snug text-ink sm:px-5">{r.foot}</p>
    </div>
  )
}

/**
 * The SDK samples of the memory that operates: the essential lines of the SDK's own examples folder (niadra
 * 0.7.0 on PyPI: object_state.py, claim_guard.py, coordination.py, turn_records.py). Code and comments in
 * English, as the code is; no country's phone number in a literal.
 */
const SAMPLES = {
  state: `from niadra import Niadra, phone
from niadra.resolvers import Resolved

def requote(ref, fields):
    # your own read of the quote, inside your company
    quote = quoting_system.read(ref.id)
    picked = {k: v for k, v in quote.items() if not fields or k in fields}
    return Resolved(picked, version=7)

niadra = Niadra(channel="whatsapp")
niadra.resolvers.register("health_quote", requote)
customer = phone(customer_phone)

with niadra.conversation(
    thread_id, subject=customer, agent_id="sales"
) as conversation, conversation.turn():
    verdict = conversation.verify_claim(
        "health_quote:op:q-77", "price_full", 511.06
    )
    if verdict.claim_safe:
        # said only because it may be claimed now
        reply = f"The plan is {verdict.value:.2f} a month."
    else:
        reply = "Let me confirm the current price and get back to you."`,
  claims: `from niadra import Niadra, phone

def shown(product):
    fields = {"price_sale": product["price_sale"]}
    return [{"ref": f"product:store:{product['sku']}", "fields": fields}]

@Niadra.tool("check_price", provenance=shown)
def check_price(sku: str) -> dict:
    return CATALOG[sku]

niadra = Niadra(channel="whatsapp")
# fingerprints of your own prompt, computed here; the text never leaves
niadra.internal_text.register("prompts@v16", PROMPT)
customer = phone(customer_phone)

with niadra.conversation(
    thread_id, subject=customer, agent_id="store"
) as conversation:
    build = Niadra.build(prompts={"store": "v16"}, model="gpt-4.1-mini")
    with conversation.turn(build=build):
        check_price("PX-4471")  # recorded, with the price it showed
        # the contract's actions, at the last point before the customer
        guarded = conversation.claims.guard_text(draft)
        conversation.agent(guarded.text)`,
  coordination: `from niadra import Niadra, phone

def farewell_once(conversation, send):
    key = f"farewell:{conversation.id}"
    decision = conversation.check(
        "farewell", purpose="service", effect_key=key
    )
    if decision.decision != "allow":
        # already said by another agent, or the customer is held
        return False
    send("Thanks for reaching out! Anything else, just call.")
    conversation.declare.effect(key, "done")
    return True

niadra = Niadra(channel="whatsapp")
customer = phone(customer_phone)
with niadra.conversation(
    thread_id, subject=customer, agent_id="closing"
) as conversation, conversation.turn():
    farewell_once(conversation, conversation.agent)

# the suppression list, applied from the local copy
niadra.may_contact(customer, "marketing", channel="whatsapp")`,
  turns: `from niadra import Niadra, phone

def shown(product):
    fields = {"price_sale": product["price_sale"]}
    return [{"ref": f"product:store:{product['sku']}", "fields": fields}]

@Niadra.tool("search_products", provenance=shown)
def search_products(sku: str) -> dict:
    return CATALOG[sku]

niadra = Niadra(channel="whatsapp")
customer = phone(customer_phone)
with niadra.conversation(
    thread_id, subject=customer, agent_id="store"
) as conversation:
    conversation.customer(text)
    build = Niadra.build(prompts={"store": "v3"}, model="gpt-4.1-mini")
    with conversation.turn(build=build):
        context = conversation.context(include=["constraints"])
        # recorded: arguments, result, latency, the objects it showed
        product = search_products("PX-4471")
        reply = model(prompt_with(context, product))
        # the record points at this event; the text is never repeated
        conversation.agent(reply)

# Later, in your CI: the recorded turns run again with the pinned build
# niadra replay --agent app.agent:build_agent --scenario <id> --runs 5`,
} as const

export type SampleKey = keyof typeof SAMPLES

/** One product's SDK sample, with the note that says which example it comes from. */
export function SdkSample({ product }: { product: SampleKey }) {
  const { t } = useT()
  const c = t.products[product].code
  return (
    <div>
      <Snippet title={c.title} code={SAMPLES[product]} langs={t.products.codeLangs} />
      <p className="mt-3 text-[0.9rem] leading-relaxed text-ink2">{c.note}</p>
    </div>
  )
}

/** The docs pages a product page points to, beyond the one in the hero: each one at docs.niadra.com, in the visitor's language. */
export function DocsLinks({ items }: { items: readonly { t: string; to: string }[] }) {
  const { t } = useT()
  return (
    <div>
      <h3 className={`${h3cls} text-[1.12rem]`}>{t.products.docsH}</h3>
      <ul className="mt-3 border-t border-line">
        {items.map((d) => (
          <li key={d.to} className="border-b border-line">
            <Link
              to={`${DOCS}${d.to}`}
              className="inline-flex min-h-12 items-center gap-1.5 py-2 text-[0.96rem] font-semibold text-ink underline decoration-accent decoration-2 underline-offset-4 hover:decoration-ink"
            >
              {d.t}
              <ExternalArrow to={DOCS} />
            </Link>
          </li>
        ))}
      </ul>
    </div>
  )
}
