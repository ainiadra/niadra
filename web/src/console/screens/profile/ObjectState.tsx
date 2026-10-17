import { useId, useState, type ReactNode } from 'react'
import { fmtDateLike, fmtMoney } from '../../lib/format'
import { eventType, moneyField, objectType, stateField, stateValue, technicalField, type Labeled } from '../../lib/labels'
import { mono, small, td, th } from '../../ui/styles'

type State = Record<string, unknown>

/** A labeled value: the words, with the raw value in a tooltip when it was translated. */
export function Raw({ value, className = '' }: { value: Labeled; className?: string }) {
  return value.raw ? (
    <span title={value.raw} className={className}>
      {value.label}
    </span>
  ) : (
    <span className={`${mono} ${className}`}>{value.label}</span>
  )
}

/** The currency of an object, from its state, BRL when the system sent none. */
const currencyOf = (state: State) => (typeof state.currency === 'string' && /^[A-Za-z]{3}$/.test(state.currency) ? state.currency : 'BRL')

const isRecord = (v: unknown): v is State => typeof v === 'object' && v !== null && !Array.isArray(v)
const isObjectList = (v: unknown): v is State[] => Array.isArray(v) && v.length > 0 && v.every(isRecord)

/** `item_variant:loja:vestido-midi-linho-azul`: a reference to another object, said by its type and id. */
function refWords(v: string): Labeled | null {
  const m = /^([a-z_]+):([a-z0-9_-]+):(.+)$/i.exec(v)
  if (!m) return null
  return { label: `${objectType(m[1])} ${m[3]}`, raw: v }
}

/** One value of a state in words: money in the currency, dates in the local format, states and events translated. */
function scalar(key: string, v: unknown, currency: string): ReactNode {
  if (v === null || v === undefined || v === '') return <span className="text-ink2">sem valor</span>
  if (typeof v === 'boolean') return v ? 'sim' : 'não'
  if (typeof v === 'number') return moneyField(key) ? fmtMoney(v, currency) : new Intl.NumberFormat('pt-BR').format(v)
  if (typeof v !== 'string') return <span className={mono}>{JSON.stringify(v)}</span>
  if (moneyField(key) && /^-?\d+([.,]\d+)?$/.test(v)) return fmtMoney(v, currency)
  if (/^\d{4}-\d{2}-\d{2}/.test(v)) return fmtDateLike(v)
  if (key === 'state' || key === 'status') return <Raw value={stateValue(v)} />
  if (/event_type$|^event$/.test(key)) return <Raw value={eventType(v)} />
  if (key === 'ref' || /_ref$/.test(key)) {
    const r = refWords(v)
    return r ? <Raw value={r} /> : <span className={mono}>{v}</span>
  }
  if (/^[a-z]+(\.[a-z_]+)+$/.test(v)) return <Raw value={eventType(v)} />
  if (/^[a-z]+(_[a-z]+)*$/.test(v) && stateValue(v).raw) return <Raw value={stateValue(v)} />
  return v
}

/** The fields a person reads, in the order the system sent them, tokens and ids left out. */
const visibleEntries = (state: State) => Object.entries(state).filter(([k]) => !technicalField(k))

/** A list of records (the lines of an order) as a small table, one column per field the lines share. */
function Lines({ name, rows, currency }: { name: string; rows: State[]; currency: string }) {
  const columns = [...new Set(rows.flatMap((r) => Object.keys(r)))].filter((k) => !technicalField(k) || k === 'line_id')
  return (
    <div className="min-w-0 max-w-full overflow-x-auto rounded-lg border border-line">
      <table className="w-full border-collapse text-[0.8125rem]">
        <caption className="sr-only">{name}</caption>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c} scope="col" className={`${th} whitespace-nowrap`}>
                <Raw value={stateField(c)} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c} className={`${td} ${moneyField(c) ? 'whitespace-nowrap tabular-nums' : ''}`}>
                  {isRecord(r[c]) || Array.isArray(r[c]) ? <span className={mono}>{JSON.stringify(r[c])}</span> : scalar(c, r[c], currency)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Key and value pairs of a record, one level deep; deeper values show as JSON in the technical details. */
function Pairs({ state, currency, nested = false }: { state: State; currency: string; nested?: boolean }) {
  const entries = visibleEntries(state).filter(([, v]) => !isObjectList(v))
  if (entries.length === 0) return null
  return (
    <dl className={`grid gap-x-6 gap-y-1.5 ${nested ? '' : 'sm:grid-cols-2 xl:grid-cols-3'}`}>
      {entries.map(([k, v]) => (
        <div key={k} className="min-w-0">
          <dt className="text-[0.75rem] font-semibold text-ink2">
            <Raw value={stateField(k)} />
          </dt>
          <dd className="mt-0.5 break-words text-[0.875rem] text-ink">
            {isRecord(v) ? (
              nested ? (
                <span className={mono}>{JSON.stringify(v)}</span>
              ) : (
                <Pairs state={v} currency={currency} nested />
              )
            ) : Array.isArray(v) ? (
              v.length === 0 ? (
                <span className="text-ink2">nenhum</span>
              ) : (
                v.map((x, i) => (
                  <span key={i}>
                    {i > 0 && ', '}
                    {isRecord(x) ? JSON.stringify(x) : scalar(k, x, currency)}
                  </span>
                ))
              )
            ) : (
              scalar(k, v, currency)
            )}
          </dd>
        </div>
      ))}
    </dl>
  )
}

/**
 * The state of an object as the system last sent it, readable: pairs with labels, the lines as a table, money
 * in the currency, dates in the local format. The raw JSON, tokens included, stays behind "Detalhes técnicos".
 */
export function ObjectState({ state, name }: { state: State; name: string }) {
  const currency = currencyOf(state)
  const lists = Object.entries(state).filter(([k, v]) => !technicalField(k) && isObjectList(v)) as [string, State[]][]
  const [open, setOpen] = useState(false)
  const panel = useId()
  const hidden = Object.keys(state).filter(technicalField).length + lists.reduce((n, [, rows]) => n + rows.reduce((m, r) => m + Object.keys(r).filter((k) => technicalField(k) && k !== 'line_id').length, 0), 0)
  if (Object.keys(state).length === 0) return <p className={small}>O sistema não mandou nenhum campo.</p>
  return (
    <div className="grid min-w-0 grid-cols-1 gap-3">
      <Pairs state={state} currency={currency} />
      {lists.map(([k, rows]) => (
        <div key={k} className="min-w-0">
          <p className="mb-1.5 text-[0.75rem] font-semibold text-ink2">
            <Raw value={stateField(k)} /> <span className="font-normal">({rows.length})</span>
          </p>
          <Lines name={`${stateField(k).label} de ${name}`} rows={rows} currency={currency} />
        </div>
      ))}
      <div>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panel}
          onClick={() => setOpen((v) => !v)}
          className="inline-flex min-h-11 items-center text-[0.8125rem] font-semibold text-ink underline decoration-accent decoration-2 underline-offset-4 hover:decoration-ink lg:min-h-8"
        >
          {open ? 'Esconder os detalhes técnicos' : `Detalhes técnicos${hidden > 0 ? ` (${hidden} ${hidden === 1 ? 'campo interno' : 'campos internos'})` : ''}`}
        </button>
        {open && (
          <pre id={panel} className={`${mono} mt-1 max-h-[24rem] overflow-auto whitespace-pre-wrap break-all rounded-lg border border-line bg-bg p-3 text-[0.75rem] leading-[1.1rem] text-ink`} tabIndex={0}>
            {JSON.stringify(state, null, 2)}
          </pre>
        )}
      </div>
    </div>
  )
}
