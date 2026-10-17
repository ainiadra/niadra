import { useId, useState, type ReactNode } from 'react'
import { Select as Dropdown } from '../../components/Select'
import type { HandleIn, HandleType } from '../api/types'
import { HANDLE_TYPES, TYPABLE_HANDLES } from '../lib/labels'
import { card, danger, h2, hint, input, label, select, small } from './styles'

/** A labelled control. The label is always visible; the hint says what goes in, not what the label already says. */
export function Field({ label: text, hint: help, children, id }: { label: ReactNode; hint?: ReactNode; children: (id: string) => ReactNode; id?: string }) {
  const auto = useId()
  const fid = id ?? auto
  return (
    <div className="min-w-0">
      <label htmlFor={fid} className={label}>
        {text}
      </label>
      {children(fid)}
      {help && <p className={hint}>{help}</p>}
    </div>
  )
}

export function TextInput({
  label: text,
  value,
  onChange,
  hint: help,
  mono = false,
  ...rest
}: {
  label: ReactNode
  value: string
  onChange: (v: string) => void
  hint?: ReactNode
  mono?: boolean
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange'>) {
  return (
    <Field label={text} hint={help}>
      {(id) => <input id={id} className={`${input} ${mono ? 'font-mono' : ''}`} value={value} onChange={(e) => onChange(e.target.value)} autoComplete="off" spellCheck={false} {...rest} />}
    </Field>
  )
}

/** A labelled dropdown. `problem` takes the place of the hint, marks the field invalid and describes it. */
export function Select<T extends string>({
  label: text,
  value,
  onChange,
  options,
  hint: help,
  disabled,
  id,
  problem,
}: {
  label: ReactNode
  value: T
  onChange: (v: T) => void
  options: { value: T; label: string }[]
  hint?: ReactNode
  disabled?: boolean
  id?: string
  problem?: string | null
}) {
  return (
    <Field id={id} label={text} hint={problem ? undefined : help}>
      {(fid) => (
        <>
          <Dropdown
            id={fid}
            triggerClassName={problem ? select.replace('border-edge', danger.border) : select}
            value={value}
            onChange={onChange}
            options={options}
            disabled={disabled}
            aria-invalid={problem ? true : undefined}
            aria-describedby={problem ? `${fid}-problem` : undefined}
          />
          {problem && (
            <p id={`${fid}-problem`} className={`mt-1.5 text-[0.8125rem] font-semibold ${danger.text}`}>
              {problem}
            </p>
          )}
        </>
      )}
    </Field>
  )
}

/** Reason for a governance action: it goes to the receipt, with the person's name. */
export function ReasonField({ value, onChange, required = true, min = 3 }: { value: string; onChange: (v: string) => void; required?: boolean; min?: number }) {
  return (
    <Field label={required ? 'Motivo' : 'Motivo (opcional)'} hint="Fica no comprovante da ação, com o seu nome.">
      {(id) => <textarea id={id} className={`${input} h-auto min-h-20 py-2`} value={value} onChange={(e) => onChange(e.target.value)} required={required} minLength={required ? min : undefined} maxLength={500} />}
    </Field>
  )
}
export const reasonOk = (r: string, min = 3) => r.trim().length >= min

/**
 * An identifier typed by a person. It goes only in request bodies, never in a URL.
 * `system_id` needs the system it belongs to, which is the handle's scope.
 */
export function HandleInput({
  legend,
  value,
  onChange,
  types = TYPABLE_HANDLES,
}: {
  legend: string
  value: HandleIn
  onChange: (h: HandleIn) => void
  /** The identifier types this field takes; every typable one unless a route takes fewer. */
  types?: HandleType[]
}) {
  return (
    <fieldset className="grid min-w-0 gap-3 sm:grid-cols-[minmax(0,12rem)_minmax(0,1fr)]">
      <legend className="mb-2 text-[0.8125rem] font-semibold text-ink">{legend}</legend>
      <Select
        label="Tipo"
        value={value.type}
        onChange={(t: HandleType) => onChange({ ...value, type: t })}
        options={types.map((t) => ({ value: t, label: HANDLE_TYPES[t] }))}
      />
      <TextInput label="Valor" value={value.value} onChange={(v) => onChange({ ...value, value: v })} mono />
      {value.type === 'system_id' && (
        <div className="sm:col-span-2">
          <TextInput label="Sistema" value={value.scope ?? ''} onChange={(v) => onChange({ ...value, scope: v })} hint="O nome do sistema de origem do ID, como está no mapeamento: crm, erp." />
        </div>
      )}
    </fieldset>
  )
}
export const emptyHandle = (type: HandleType = 'phone_e164'): HandleIn => ({ type, value: '' })
export const handleOk = (h: HandleIn) => h.value.trim().length > 0 && (h.type !== 'system_id' || Boolean(h.scope?.trim()))
export const cleanHandle = (h: HandleIn): HandleIn => ({ type: h.type, value: h.value.trim(), ...(h.scope?.trim() ? { scope: h.scope.trim() } : {}) })

/** JSON typed by a person, parsed before it leaves the browser. */
export function JsonField({ label: text, value, onChange, rows = 8, hint: help }: { label: ReactNode; value: string; onChange: (v: string) => void; rows?: number; hint?: ReactNode }) {
  const error = jsonError(value)
  return (
    <Field label={text} hint={error ? undefined : help}>
      {(id) => (
        <>
          <textarea
            id={id}
            rows={rows}
            className={`${input} h-auto py-2 font-mono text-[0.8125rem] leading-5`}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            spellCheck={false}
            aria-invalid={Boolean(error)}
          />
          {error && <p className={`mt-1.5 text-[0.8125rem] font-semibold ${danger.text}`}>JSON inválido: {error}</p>}
        </>
      )}
    </Field>
  )
}
export function jsonError(text: string): string | null {
  if (!text.trim()) return null
  try {
    JSON.parse(text)
    return null
  } catch (e) {
    return e instanceof Error ? e.message : 'erro de sintaxe'
  }
}

/** A titled block of one screen. Screens are built from these, each around one job. */
export function Section({ title, description, children, actions, id, tour }: { title: string; description?: ReactNode; children: ReactNode; actions?: ReactNode; id?: string; /** The anchor a tutorial step points at. */ tour?: string }) {
  const hid = useId()
  return (
    <section aria-labelledby={hid} id={id} data-tour={tour} className={`${card} scroll-mt-20 p-4 sm:p-5`}>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 id={hid} className={h2}>
            {title}
          </h2>
          {description && <div className={`${small} mt-0.5 max-w-[72ch]`}>{description}</div>}
        </div>
        {actions}
      </div>
      {children}
    </section>
  )
}

/** Runs one action at a time and keeps its result and error for the view. */
export function useAction<T>() {
  const [state, setState] = useState<{ busy: boolean; error: unknown; result: T | null }>({ busy: false, error: null, result: null })
  const run = async (fn: () => Promise<T>, then?: (r: T) => void) => {
    setState((s) => ({ ...s, busy: true, error: null }))
    try {
      const r = await fn()
      setState({ busy: false, error: null, result: r })
      then?.(r)
    } catch (error) {
      setState((s) => ({ ...s, busy: false, error }))
    }
  }
  const reset = () => setState({ busy: false, error: null, result: null })
  return { ...state, run, reset }
}
