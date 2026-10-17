import type { ReactNode } from 'react'
import { td, th } from './styles'

/** A table that scrolls inside its card on a phone; `numeric` columns align right with tabular figures. */
/** `wide` tables scroll sideways on a phone instead of folding their cells into narrow columns. */
export function DataTable({ label: name, head, rows, numeric = [], wide = false }: { label: string; head: string[]; rows: ReactNode[][]; numeric?: number[]; wide?: boolean }) {
  return (
    <div className="overflow-x-auto" tabIndex={0} role="region" aria-label={`Tabela: ${name.toLowerCase()}`}>
      <table className={`w-full ${wide ? 'min-w-[52rem]' : 'min-w-[32rem]'} border-collapse text-[0.875rem]`}>
        <thead>
          <tr>
            {head.map((h, i) => (
              <th key={`${h}-${i}`} scope="col" className={`${th} ${numeric.includes(i) ? 'text-right' : ''}`}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r}>
              {row.map((cell, i) => (
                <td key={i} className={`${td} ${numeric.includes(i) ? 'text-right tabular-nums' : ''}`}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
