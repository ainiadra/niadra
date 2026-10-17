import { Link } from 'react-router-dom'
import type { ObjectMemoryOut, SaidAbout } from '../../api/types'
import { fmtDate, fmtDateTime } from '../../lib/format'
import { predicate } from '../../lib/labels'
import { paths } from '../../paths'
import { useSpace } from '../../session'
import { Chip } from '../../ui/bits'
import { small } from '../../ui/styles'

/** Who said it, by the role the object's events gave them; the owner is the customer. */
function sayer(role?: string | null): string {
  if (!role || role === 'participant') return 'Participante'
  if (role === 'owner') return 'Titular'
  return role.replace(/_/g, ' ')
}

const KINDS: Record<string, string> = { promise: 'Promessa', dispute: 'Contestação', request: 'Pedido' }

/** A value the system of record sent, as text. */
const shown = (v: unknown) => (v === null || v === undefined ? 'sem valor' : typeof v === 'string' ? v : JSON.stringify(v))

function Audience({ row }: { row: SaidAbout }) {
  return row.audience === 'internal' ? <Chip tone="dark">Só interno</Chip> : <Chip>Compartilhado</Chip>
}

function Said({ row }: { row: SaidAbout }) {
  return (
    <li className="py-2">
      <p className="flex flex-wrap items-center gap-2 text-ink">
        <span>{row.text}</span>
        <Audience row={row} />
        {row.diverges && <Chip tone="danger">Diverge do sistema</Chip>}
      </p>
      {row.observes && (
        <p className={`${small} mt-0.5`}>
          {predicate(row.observes).label.replace(/_/g, " ")} no sistema: {shown(row.recorded)}. O que foi dito não substitui o registrado nem pode ser afirmado como ele.
        </p>
      )}
      <p className={`${small} mt-0.5`}>
        {sayer(row.role)} · {fmtDateTime(row.said_at)}
      </p>
    </li>
  )
}

function Instruction({ row }: { row: SaidAbout }) {
  return (
    <li className="py-2">
      <p className="flex flex-wrap items-center gap-2 text-ink">
        <Chip tone={row.strength === 'must_not' ? 'danger' : 'ok'}>{row.strength === 'must_not' ? 'Não fazer' : 'Fazer'}</Chip>
        <span>{row.text}</span>
      </p>
      <p className={`${small} mt-0.5`}>
        {sayer(row.role)} · {fmtDateTime(row.said_at)} · {row.expires_at ? `vale até ${fmtDate(row.expires_at)}` : 'vale enquanto o objeto estiver aberto'}
        {row.attr && ` · campo ${row.attr}`}
      </p>
    </li>
  )
}

function Item({ row }: { row: SaidAbout }) {
  return (
    <li className="py-2">
      <p className="flex flex-wrap items-center gap-2 text-ink">
        <Chip tone={row.item_kind === 'dispute' ? 'danger' : 'neutral'}>{KINDS[row.item_kind ?? 'promise']}</Chip>
        <span>{row.text}</span>
        {row.overdue && <Chip tone="danger">Vencida</Chip>}
      </p>
      <p className={`${small} mt-0.5`}>
        {sayer(row.role)} · {fmtDateTime(row.said_at)}
        {row.due_at && ` · prazo ${fmtDate(row.due_at)}`}
      </p>
    </li>
  )
}

/**
 * What the owner and participants of an object said about it, on the object's card: what was
 * said in the conversations, the instructions in force and the open promises, disputes and requests, each with
 * who said it and when. Each row stays under the person who said it; the link opens the read receipts of this
 * customer, where the reads that carried it are.
 */
export function ObjectMemory({ memory, profileId }: { memory?: ObjectMemoryOut | null; profileId: string }) {
  const { space } = useSpace()
  if (!memory || (memory.said.length === 0 && memory.instructions.length === 0 && memory.items.length === 0)) return null
  const receipts = `${paths.receipts(space.space_id)}?profile=${encodeURIComponent(profileId)}&kind=read`
  return (
    <div className="mt-3 grid gap-3 border-t border-line2 pt-3">
      {memory.instructions.length > 0 && (
        <section aria-label="Instruções">
          <h4 className="text-[0.8125rem] font-semibold text-ink">Instruções</h4>
          <ul className="divide-y divide-line2">
            {memory.instructions.map((row) => (
              <Instruction key={row.id} row={row} />
            ))}
          </ul>
        </section>
      )}
      {memory.items.length > 0 && (
        <section aria-label="Pendências sobre o objeto">
          <h4 className="text-[0.8125rem] font-semibold text-ink">Pendências sobre o objeto</h4>
          <ul className="divide-y divide-line2">
            {memory.items.map((row) => (
              <Item key={row.id} row={row} />
            ))}
          </ul>
        </section>
      )}
      {memory.said.length > 0 && (
        <section aria-label="Dito nas conversas">
          <h4 className="text-[0.8125rem] font-semibold text-ink">Dito nas conversas</h4>
          <ul className="divide-y divide-line2">
            {memory.said.map((row) => (
              <Said key={row.id} row={row} />
            ))}
          </ul>
        </section>
      )}
      <p className={small}>
        <Link className="underline underline-offset-2" to={receipts}>
          Comprovantes das leituras deste cliente
        </Link>
      </p>
    </div>
  )
}
