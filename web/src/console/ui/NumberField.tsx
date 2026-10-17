import { Field } from './forms'
import { danger, hint, input } from './styles'

export const fmtNumber = (n: number) => new Intl.NumberFormat('pt-BR').format(n)

/** Why a typed number cannot go out, in words, or null when it can. */
export function numberProblem(raw: string, p: { min: number; max: number; integer?: boolean }): string | null {
  const n = Number(raw)
  if (raw.trim() === '' || !Number.isFinite(n) || n < p.min || n > p.max || (p.integer && !Number.isInteger(n)))
    return `Use ${p.integer ? 'um número inteiro' : 'um número'} de ${fmtNumber(p.min)} a ${fmtNumber(p.max)}.`
  return null
}

/** A number of a rule: the hint, or the problem tied to the field while the value is out of range. */
export function NumberField({ label: text, hint: help, value, onChange, problem, min, max, step }: { label: string; hint?: string; value: string; onChange: (v: string) => void; problem: string | null; min: number; max: number; step?: number }) {
  return (
    <Field label={text}>
      {(id) => (
        <>
          <input
            id={id}
            type="number"
            inputMode="decimal"
            min={min}
            max={max}
            step={step ?? 1}
            className={problem ? input.replace('border-edge', danger.border) : input}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            aria-invalid={problem ? true : undefined}
            aria-describedby={problem || help ? `${id}-note` : undefined}
          />
          {(problem || help) && (
            <p id={`${id}-note`} className={problem ? `mt-1.5 text-[0.8125rem] font-semibold ${danger.text}` : hint}>
              {problem ?? help}
            </p>
          )}
        </>
      )}
    </Field>
  )
}
