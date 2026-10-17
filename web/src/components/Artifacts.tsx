import { Fragment, useRef, useState, type ReactNode } from 'react'
import { useT } from '../i18n'
import { Icon } from './Icon'
import { btnGhost, btnSm, hl, panel, srcChip } from '../ui'

export function ExampleTag() {
  const { t } = useT()
  return (
    <span className="rounded-md border border-line bg-surface px-1.5 py-0.5 font-mono text-[0.62rem] text-ink3">
      {t.meta.example}
    </span>
  )
}

const label = 'mb-1 text-[0.74rem] font-semibold text-ink3'

/**
 * The context the agent receives, as the exact text the server's compiler produced for a sample
 * customer (i18n/pack.ts). The highlighter marks what came from the history, from another
 * channel or from another agent; each line says where from and when. Below, the layers in the
 * order they enter the prompt.
 */
export function ContextDoc() {
  const { t } = useT()
  const c = t.demo.ctx
  return (
    <div className={`${panel} overflow-hidden`}>
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1.5 border-b border-line bg-surface px-4 py-3 sm:px-5">
        <span className="flex flex-wrap items-center gap-2 text-[0.8rem] font-semibold text-ink">
          {c.badge}
          <ExampleTag />
        </span>
        <span className="font-mono text-[0.74rem] tabular-nums text-ink2">{c.meta}</span>
      </div>

      <div className="grid gap-1.5 px-4 py-5 sm:px-5">
        {c.lines.map((ln, i) => (
          <p key={i} className="break-words font-mono text-[0.84rem] leading-[1.7] text-ink">
            {ln.hot ? <mark className={hl}>{ln.t}</mark> : ln.t}
          </p>
        ))}
        <p className={`${label} mt-2 font-normal`}>{c.source}</p>
      </div>

      <div className="border-t border-line bg-surface px-4 py-4 sm:px-5">
        {/* A ruler, not a control: a thin bar + a label, so it does not look like a clickable tab. */}
        <ol className="grid grid-cols-4 gap-1.5">
          {c.layers.map((l, i) => (
            <li key={l} className="min-w-0">
              {/* Lime on the stable part: that is the part the AI provider reuses. */}
              <span className={`block h-1.5 rounded-full ${i < 2 ? 'bg-brand' : 'bg-ink/15'}`} aria-hidden="true" />
              <span className="mt-2 block text-[0.72rem] font-semibold leading-tight text-ink sm:text-[0.78rem]">{l}</span>
            </li>
          ))}
        </ol>
        <p className="mt-3 text-[0.82rem] leading-relaxed text-ink2">{c.layersLabel}</p>
      </div>
    </div>
  )
}

// The three calls with the published Python SDK (niadra on PyPI). The Copy button copies
// this text and the block below renders the same text, so the two never drift apart.
// verify() records the carrier's attestation for this call first: a read proves only what
// its conversation proved, and the starter policy releases nothing below V1.
// tools() returns a kit: its definitions go to the model, and kit.call() answers the
// model's tool calls. track() takes one item per turn.
const SNIPPET = (before: string, during: string, after: string) => `from niadra import Niadra, phone

niadra = Niadra(channel="voice")
caller = phone(caller_id)

# ${before}
niadra.verify("network_attestation", "V1", handle=caller, conversation_id=call_id)
ctx = niadra.context(
    caller,
    view="voice",
    verification="V1",
    conversation_id=call_id,
)

# ${during}
kit = niadra.tools(caller, verification="V1", conversation_id=call_id)
reply = model.chat(
    system=[YOUR_PROMPT, ctx.system_block],
    tools=kit.definitions,
    messages=history,
)

# ${after}
turns = [("customer", question), ("ai_agent", reply)]
for role, text in turns:
    niadra.track({
        "conversation_id": call_id,
        "handles": [caller],
        "speaker": {"role": role},
        "content": {"text": text},
    })`

/** One line of the snippet: comments dimmed, and the three calls marked. */
function CodeLine({ line }: { line: string }) {
  const indent = line.slice(0, line.length - line.trimStart().length)
  const body = line.slice(indent.length)
  if (body.startsWith('#')) {
    return (
      <>
        {indent}
        <span className="text-ink3">{body}</span>
      </>
    )
  }
  const call = /^(.*niadra\.)(context|tools|track)(\(.*)$/.exec(body)
  if (!call) return <>{line}</>
  return (
    <>
      {indent}
      {call[1]}
      <mark className={hl}>{call[2]}</mark>
      {call[3]}
    </>
  )
}

/** The Copy button's state: the clipboard when allowed, the selection when not. */
function useCopy(code: string) {
  const [copied, setCopied] = useState(false)
  const pre = useRef<HTMLPreElement>(null)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code)
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
  return { copied, copy, pre }
}

/** A code panel: title and Copy button, the code line by line through `line`, and the languages it exists in. */
function CodePanel({ title, code, langs, line }: { title: string; code: string; langs: readonly string[]; line: (l: string) => ReactNode }) {
  const { t } = useT()
  const c = t.demo.code
  const { copied, copy, pre } = useCopy(code)
  return (
    <div className={`${panel} overflow-hidden`}>
      <div className="flex items-center justify-between gap-3 border-b border-line bg-surface py-1.5 pl-4 pr-2 sm:pl-5 lg:py-2 lg:pr-3">
        <span className="py-1 text-[0.8rem] font-semibold leading-snug text-ink">{title}</span>
        <button type="button" className={`${btnGhost} ${btnSm} lg:min-h-9 lg:px-3.5 lg:text-[0.86rem]`} onClick={copy} aria-live="polite">
          {copied ? c.copied : c.copy}
        </button>
      </div>
      <pre ref={pre} className="overflow-x-auto px-4 py-5 font-mono text-[0.8rem] leading-[1.75] text-ink sm:px-5" tabIndex={0}>
        <code>
          {code.split('\n').map((l, i) => (
            <Fragment key={i}>
              {i > 0 && '\n'}
              {line(l)}
            </Fragment>
          ))}
        </code>
      </pre>
      <ul className="flex flex-wrap gap-1.5 border-t border-line px-4 py-3 sm:px-5">
        {langs.map((l) => (
          <li key={l} className={srcChip}>{l}</li>
        ))}
      </ul>
    </div>
  )
}

/** The three calls: context before, search during, record after. */
export function Code() {
  const { t } = useT()
  const c = t.demo.code
  return <CodePanel title={c.title} code={SNIPPET(c.before, c.during, c.after)} langs={c.langs} line={(l) => <CodeLine line={l} />} />
}

/**
 * One line of an SDK sample: comments dimmed, and the SDK call the line makes (conversation.check,
 * niadra.may_contact, conversation.claims.guard_text) under the highlighter, as the memory's own part.
 */
function SnippetLine({ line }: { line: string }) {
  const at = line.indexOf('  # ')
  const code = at >= 0 ? line.slice(0, at) : line
  const comment = at >= 0 ? line.slice(at) : ''
  const body = code.trimStart()
  const indent = code.slice(0, code.length - body.length)
  const call = body.startsWith('#') ? null : /^(.*?\b(?:conversation|niadra|Niadra)\.)([A-Za-z_.]+)(\(.*)$/.exec(body)
  return (
    <>
      {body.startsWith('#') ? (
        <span className="text-ink3">{code}</span>
      ) : call ? (
        <>
          {indent}
          {call[1]}
          <mark className={hl}>{call[2]}</mark>
          {call[3]}
        </>
      ) : (
        code
      )}
      {comment && <span className="text-ink3">{comment}</span>}
    </>
  )
}

/** A sample from the SDK's examples folder, as a code panel with the same Copy button as the three calls. */
export function Snippet({ title, code, langs }: { title: string; code: string; langs: readonly string[] }) {
  return <CodePanel title={title} code={code} langs={langs} line={(l) => <SnippetLine line={l} />} />
}

/**
 * History search, staged: the customer claims something, the agent looks it up,
 * Niadra returns what happened with date, channel and outcome. The highlight sits on
 * the pattern Niadra saw, which is what the agent uses to answer.
 */
export function HistorySearch() {
  const { t } = useT()
  const s = t.demo.hist
  return (
    <div className={`${panel} overflow-hidden`}>
      <div className="flex items-center justify-between gap-3 border-b border-line bg-surface px-4 py-3 sm:px-5">
        <span className="flex items-center gap-2 text-[0.8rem] font-semibold text-ink">
          <Icon name="search" size={16} className="text-accent" />
          {s.title}
        </span>
        <ExampleTag />
      </div>

      {/* minmax(0,1fr): without it the long code line widens the column and the panel cuts the text of the answers. */}
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 px-4 py-5 sm:px-5">
        <div>
          <div className={label}>{s.saysLabel}</div>
          <p className="text-[0.98rem] font-medium leading-snug text-ink">{s.says}</p>
        </div>

        <div>
          <div className={label}>{s.askLabel}</div>
          <pre className="overflow-x-auto rounded-lg border border-line bg-bg px-3 py-2.5 font-mono text-[0.8rem] leading-relaxed text-ink" tabIndex={0}>
            <code>{s.query}</code>
          </pre>
        </div>

        <div>
          <div className={label}>{s.answerLabel}</div>
          <ul className="border-t border-line">
            {s.rows.map((r) => (
              <li key={r.when} className="grid gap-1 border-b border-line py-3 sm:grid-cols-[9.5rem_1fr] sm:gap-4">
                <span className="font-mono text-[0.74rem] leading-relaxed text-ink3">
                  {r.when}
                  <span className="sm:hidden"> · </span>
                  <span className="sm:block">{r.channel}</span>
                </span>
                <span className="text-[0.92rem] leading-snug text-ink">
                  {r.t} <span className={`${srcChip} ml-1 align-middle`}>{r.state}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="border-t border-line bg-surface px-4 py-3.5 text-[0.92rem] font-semibold leading-snug text-ink sm:px-5">
        <mark className={hl}>{s.pattern}</mark>
      </div>
    </div>
  )
}
