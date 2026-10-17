import { useId, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Select as Dropdown } from '../../components/Select'
import { ApiError } from '../api/http'
import type { AccessChangeOut, BindingIn, InvitationOut, Role, Space, UserOut } from '../api/types'
import { fmtDate, fmtDateTime } from '../lib/format'
import { accessChange, role, roleReach, ROLES } from '../lib/labels'
import { useResource } from '../lib/useResource'
import { paths } from '../paths'
import { useConsole, useSpace } from '../session'
import { Chip, PageHeader } from '../ui/bits'
import { Dialog } from '../ui/Dialog'
import { Field, ReasonField, reasonOk, useAction } from '../ui/forms'
import { Icon } from '../ui/Icon'
import { useTitle } from '../ui/layout'
import { Empty, ErrorState, Loading } from '../ui/States'
import { btnDanger, btnPrimary, btnQuiet, btnSecondary, card, danger, hint, input, label, mono, select, small } from '../ui/styles'
import { loadSpaces, spaceLabel } from './SpacePicker'

type Opened =
  | { kind: 'add' }
  | { kind: 'roles'; person: UserOut }
  | { kind: 'invite'; person: UserOut }
  | { kind: 'reset'; person: UserOut }
  | { kind: 'deactivate'; person: UserOut }
  | { kind: 'reactivate'; person: UserOut }
  | { kind: 'history'; person: UserOut }

/**
 * The tenant's people: roles, how each one signs in and the state of the second factor.
 * Admins add a person by e-mail and roles; the person gets a single-use link to choose a password and sets
 * up the authenticator at the first sign-in, or, in a domain of the tenant's SSO, simply signs in through it.
 */
export function People() {
  const { control, hasRole, space } = useSpace()
  const { me } = useConsole()
  useTitle('Pessoas e papéis', space.environment === 'sandbox')
  const admin = hasRole('admin')
  const people = useResource((signal) => control.users({ signal }), [control], admin)
  const spaces = useResource((signal) => loadSpaces(control, signal).then((r) => r.spaces), [control], admin)
  const [opened, setOpened] = useState<Opened | null>(null)

  const sorted = useMemo(
    () => [...(people.data ?? [])].sort((a, b) => Number(b.active) - Number(a.active) || a.email.localeCompare(b.email)),
    [people.data],
  )
  const byId = useMemo(() => new Map((people.data ?? []).map((p) => [p.user_id, p])), [people.data])
  const close = (changed = false) => {
    setOpened(null)
    if (changed) people.reload()
  }

  if (!admin)
    return (
      <>
        <PageHeader screen="people" />
        <Empty title="Esta tela exige o papel Administração.">Quem administra o Console da sua empresa adiciona pessoas e define os papéis de cada uma.</Empty>
      </>
    )

  return (
    <>
      <PageHeader
        screen="people"
        actions={
          <button type="button" data-tour="people.add" className={btnPrimary} onClick={() => setOpened({ kind: 'add' })}>
            <Icon name="plus" size={16} />
            Adicionar pessoa
          </button>
        }
      />
      {people.status === 'loading' && <Loading label="Carregando as pessoas da empresa…" />}
      {people.status === 'error' && <ErrorState error={people.error} onRetry={people.reload} />}
      {people.status === 'success' && sorted.length === 0 && <Empty title="Nenhuma pessoa na empresa." />}
      {people.status === 'success' && sorted.length > 0 && (
        <ul aria-label="Pessoas da empresa" data-tour="people.list" className={`${card} divide-y divide-line2`}>
          {sorted.map((p) => (
            <PersonRow key={p.user_id} person={p} self={p.user_id === me?.user_id} spaces={spaces.data ?? []} onOpen={setOpened} />
          ))}
        </ul>
      )}

      {opened?.kind === 'add' && <AddPersonDialog spaces={spaces.data ?? []} onClose={close} />}
      {opened?.kind === 'roles' && <RolesDialog person={opened.person} spaces={spaces.data ?? []} onClose={close} />}
      {opened?.kind === 'invite' && <InviteDialog person={opened.person} onClose={close} />}
      {opened?.kind === 'reset' && <ResetDialog person={opened.person} onClose={close} />}
      {(opened?.kind === 'deactivate' || opened?.kind === 'reactivate') && <StateDialog person={opened.person} deactivate={opened.kind === 'deactivate'} onClose={close} />}
      {opened?.kind === 'history' && <HistoryDialog person={opened.person} people={byId} onClose={() => close()} />}
    </>
  )
}

function PersonRow({ person: p, self, spaces, onOpen }: { person: UserOut; self: boolean; spaces: Space[]; onOpen: (o: Opened) => void }) {
  const titleId = useId()
  return (
    <li aria-labelledby={titleId} className="grid gap-3 px-4 py-4 sm:px-5 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1.1fr)] lg:gap-x-6">
      <div className="min-w-0">
        <p id={titleId} className="break-all font-semibold text-ink">
          {p.email}
        </p>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {self && <Chip tone="dark">Você</Chip>}
          {p.active ? <Chip tone="ok">Ativa</Chip> : <Chip tone="danger">Desativada {p.deactivated_at ? `em ${fmtDate(p.deactivated_at)}` : ''}</Chip>}
          {p.sso && <Chip>SSO</Chip>}
        </div>
      </div>
      <div className="min-w-0">
        <p className="text-[0.75rem] font-semibold text-ink2">Papéis</p>
        <ul className="mt-0.5 grid gap-0.5 text-[0.875rem] text-ink">
          {p.roles.map((b, i) => (
            <li key={`${b.role}-${b.space_id ?? 'all'}-${i}`}>
              {role(b.role)} <span className="text-ink2">· {scopeLabel(b, spaces)}</span>
            </li>
          ))}
        </ul>
      </div>
      <dl className="grid min-w-0 gap-1.5 text-[0.875rem]">
        <div>
          <dt className="text-[0.75rem] font-semibold text-ink2">Entrada</dt>
          <dd className="text-ink">{signInLabel(p)}</dd>
        </div>
        <div>
          <dt className="text-[0.75rem] font-semibold text-ink2">Segundo fator</dt>
          <dd className="text-ink">{secondFactorLabel(p)}</dd>
        </div>
        <div>
          <dt className="text-[0.75rem] font-semibold text-ink2">Último acesso</dt>
          <dd className="text-ink">{p.last_login_at ? fmtDateTime(p.last_login_at) : 'Nunca entrou'}</dd>
        </div>
      </dl>
      <div className="flex flex-wrap gap-1 lg:col-span-3">
        <button type="button" className={btnSecondary} onClick={() => onOpen({ kind: 'roles', person: p })} aria-label={`Papéis de ${p.email}`} disabled={!p.active}>
          Papéis
        </button>
        {p.active && !(p.sso && !p.password_set) && (
          <button type="button" className={btnQuiet} onClick={() => onOpen({ kind: 'invite', person: p })} aria-label={`${p.password_set ? 'Link para trocar a senha de' : 'Novo convite para'} ${p.email}`}>
            {p.password_set ? 'Link para trocar a senha' : 'Novo convite'}
          </button>
        )}
        {p.active && p.second_factor_confirmed_at && (
          <button type="button" className={btnQuiet} onClick={() => onOpen({ kind: 'reset', person: p })} aria-label={`Redefinir o segundo fator de ${p.email}`}>
            Redefinir segundo fator
          </button>
        )}
        <button type="button" className={btnQuiet} onClick={() => onOpen({ kind: 'history', person: p })} aria-label={`Histórico de ${p.email}`}>
          Histórico
        </button>
        {p.active && !self && (
          <button type="button" className={`${btnQuiet} ${danger.text}`} onClick={() => onOpen({ kind: 'deactivate', person: p })} aria-label={`Desativar ${p.email}`}>
            Desativar
          </button>
        )}
        {!p.active && (
          <button type="button" className={btnQuiet} onClick={() => onOpen({ kind: 'reactivate', person: p })} aria-label={`Reativar ${p.email}`}>
            Reativar
          </button>
        )}
      </div>
    </li>
  )
}

function signInLabel(p: UserOut): string {
  if (p.sso && !p.password_set) return 'Pelo SSO da empresa'
  if (p.sso) return 'Pelo SSO da empresa, ou com senha'
  if (p.password_set) return 'E-mail e senha'
  if (p.invitation_expires_at) return `Convite aberto até ${fmtDateTime(p.invitation_expires_at)}`
  return 'Sem senha: envie um convite'
}

function secondFactorLabel(p: UserOut): string {
  if (p.second_factor_confirmed_at) {
    const codes = p.recovery_codes_left === 1 ? '1 código de recuperação' : `${p.recovery_codes_left} códigos de recuperação`
    return `Aplicativo ativo desde ${fmtDate(p.second_factor_confirmed_at)} · ${codes}`
  }
  if (p.sso && !p.password_set) return 'O do provedor de identidade'
  return 'Configura no primeiro acesso'
}

const ALL = 'all'

function scopeLabel(b: BindingIn, spaces: Space[]): string {
  if (!b.space_id) return 'todos os espaços'
  const s = spaces.find((x) => x.space_id === b.space_id)
  const where = s ? `${s.project_name} · ${spaceLabel(s)}` : 'um espaço'
  return b.role === 'vendor' ? `${where}, ${b.source_ids.length === 1 ? '1 fonte' : `${b.source_ids.length} fontes`}` : where
}

function rolesError(bindings: BindingIn[]): string | null {
  if (bindings.length === 0) return 'Dê ao menos um papel.'
  if (bindings.some((b) => b.role === 'vendor' && (!b.space_id || b.source_ids.length === 0))) return 'O papel Fornecedor precisa de um espaço e de ao menos uma fonte.'
  return null
}

/**
 * The roles of one person, as bindings: a role on the whole tenant or on one space. The vendor role names the
 * sources of one space the person may see.
 */
function RolesEditor({ value, onChange, spaces, error }: { value: BindingIn[]; onChange: (v: BindingIn[]) => void; spaces: Space[]; error: string | null }) {
  const { space } = useSpace()
  const [adding, setAdding] = useState<Role>('review')
  const addId = useId()
  const errId = useId()
  const scopeOptions = [{ value: ALL, label: 'Todos os espaços' }, ...spaces.map((s) => ({ value: s.space_id, label: `${s.project_name} · ${spaceLabel(s)}` }))]
  const set = (i: number, b: BindingIn) => onChange(value.map((x, j) => (j === i ? b : x)))
  const add = () => {
    const b: BindingIn = adding === 'vendor' ? { role: 'vendor', space_id: space.space_id, source_ids: [] } : { role: adding, space_id: null, source_ids: [] }
    if (!value.some((x) => x.role === b.role && x.space_id === b.space_id)) onChange([...value, b])
  }
  return (
    <fieldset className="grid gap-3" aria-describedby={error ? errId : undefined}>
      <legend className="mb-1 text-[0.8125rem] font-semibold text-ink">Papéis</legend>
      {value.length === 0 && <p className={small}>Nenhum papel ainda. Escolha abaixo e toque em Adicionar papel.</p>}
      {value.map((b, i) => (
        <BindingRow key={`${b.role}-${i}`} binding={b} scopeOptions={b.role === 'vendor' ? scopeOptions.filter((o) => o.value !== ALL) : scopeOptions} onChange={(n) => set(i, n)} onRemove={() => onChange(value.filter((_, j) => j !== i))} />
      ))}
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-[12rem] flex-1">
          <label htmlFor={addId} className={label}>
            Papel a adicionar
          </label>
          <Dropdown id={addId} triggerClassName={select} value={adding} onChange={setAdding} options={ROLES.map((r) => ({ value: r, label: role(r) }))} />
        </div>
        <button type="button" className={btnSecondary} onClick={add}>
          <Icon name="plus" size={16} />
          Adicionar papel
        </button>
      </div>
      <p className={hint}>{roleReach(adding)}.</p>
      {error && (
        <p id={errId} className={`text-[0.8125rem] font-semibold ${danger.text}`}>
          {error}
        </p>
      )}
    </fieldset>
  )
}

function BindingRow({ binding: b, scopeOptions, onChange, onRemove }: { binding: BindingIn; scopeOptions: { value: string; label: string }[]; onChange: (b: BindingIn) => void; onRemove: () => void }) {
  const { control } = useSpace()
  const scopeId = useId()
  const vendorSources = useResource((signal) => control.sources(b.space_id!, { signal }), [b.space_id, control], b.role === 'vendor' && Boolean(b.space_id))
  return (
    <div className="grid gap-2 rounded-lg border border-line px-3 py-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] sm:items-end">
      <div className="min-w-0">
        <p className="font-semibold text-ink">{role(b.role)}</p>
        <p className={small}>{roleReach(b.role)}</p>
      </div>
      <div className="min-w-0">
        <label htmlFor={scopeId} className={label}>
          Onde vale
        </label>
        <Dropdown
          id={scopeId}
          triggerClassName={select}
          value={b.space_id ?? ALL}
          onChange={(v) => onChange({ ...b, space_id: v === ALL ? null : v, source_ids: [] })}
          options={scopeOptions}
          aria-describedby={undefined}
        />
      </div>
      <button type="button" className={`${btnQuiet} ${danger.text}`} onClick={onRemove} aria-label={`Tirar o papel ${role(b.role)}`}>
        Tirar
      </button>
      {b.role === 'vendor' && (
        <fieldset className="sm:col-span-3">
          <legend className="mb-1 text-[0.8125rem] font-semibold text-ink">Fontes do fornecedor</legend>
          {vendorSources.status === 'loading' && <Loading label="Carregando as fontes do espaço…" className="py-2" />}
          {vendorSources.status === 'error' && <ErrorState error={vendorSources.error} onRetry={vendorSources.reload} compact />}
          {vendorSources.status === 'success' && (vendorSources.data ?? []).length === 0 && <p className={small}>Este espaço não tem fontes.</p>}
          <div className="grid gap-0.5 sm:grid-cols-2">
            {(vendorSources.data ?? []).map((s) => (
              <label key={s.source_id} className="flex min-h-11 cursor-pointer items-center gap-2.5 text-[0.875rem] text-ink lg:min-h-8">
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-[#5e7c00]"
                  checked={b.source_ids.includes(s.source_id)}
                  onChange={(e) => onChange({ ...b, source_ids: e.target.checked ? [...b.source_ids, s.source_id] : b.source_ids.filter((x) => x !== s.source_id) })}
                />
                {s.name}
                {s.vendor && <span className="text-ink2">· {s.vendor}</span>}
              </label>
            ))}
          </div>
        </fieldset>
      )}
    </div>
  )
}

function AddPersonDialog({ spaces, onClose }: { spaces: Space[]; onClose: (changed?: boolean) => void }) {
  const { control } = useSpace()
  const [email, setEmail] = useState('')
  const [roles, setRoles] = useState<BindingIn[]>([])
  const [touched, setTouched] = useState(false)
  const act = useAction<UserOut>()
  const field = useRef<HTMLInputElement>(null)
  const errId = useId()
  const emailError = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim()) ? null : 'Digite o e-mail corporativo da pessoa.'
  const problem = rolesError(roles)
  const submit = (e: FormEvent) => {
    e.preventDefault()
    setTouched(true)
    if (emailError || problem) return
    act.run(() => control.createUser(email.trim(), roles))
  }
  const created = act.result
  return (
    <Dialog open onClose={() => onClose(Boolean(created))} title={created ? 'Pessoa adicionada' : 'Adicionar pessoa'} wide initialFocus={field}>
      {created ? (
        <div className="grid gap-4">
          {created.invitation ? (
            <>
              <p className="text-ink">
                <strong>{created.email}</strong> recebe este convite: um link que entra uma vez, onde a pessoa cria a senha. No primeiro login, ela configura o aplicativo autenticador.
              </p>
              <InvitationLink invitation={created.invitation} />
            </>
          ) : created.sso ? (
            <p className="text-ink">
              <strong>{created.email}</strong> entra pelo SSO da empresa, sem convite. No primeiro login, o provedor de identidade confirma quem é, e os papéis passam a vir dos grupos dele.
            </p>
          ) : (
            <p className="text-ink">
              <strong>{created.email}</strong> foi adicionada.
            </p>
          )}
          <div className="flex justify-end">
            <button type="button" className={btnPrimary} onClick={() => onClose(true)}>
              Concluir
            </button>
          </div>
        </div>
      ) : (
        <form className="grid gap-5" onSubmit={submit} noValidate>
          <Field label="E-mail corporativo" hint="A pessoa entra no Console com este e-mail. Se o domínio for do SSO da empresa, ela entra pelo provedor.">
            {(id) => (
              <>
                <input
                  id={id}
                  ref={field}
                  type="email"
                  autoComplete="off"
                  className={input}
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  aria-invalid={touched && Boolean(emailError)}
                  aria-describedby={touched && emailError ? errId : undefined}
                />
                {touched && emailError && (
                  <p id={errId} className={`mt-1.5 text-[0.8125rem] font-semibold ${danger.text}`}>
                    {emailError}
                  </p>
                )}
              </>
            )}
          </Field>
          <RolesEditor value={roles} onChange={setRoles} spaces={spaces} error={touched ? problem : null} />
          {Boolean(act.error) && <ErrorState error={act.error} compact />}
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" className={btnSecondary} onClick={() => onClose()}>
              Cancelar
            </button>
            <button type="submit" className={btnPrimary} disabled={act.busy}>
              {act.busy ? 'Adicionando…' : 'Adicionar pessoa'}
            </button>
          </div>
        </form>
      )}
    </Dialog>
  )
}

/** The invitation link, shown once: the token lives only in this dialog's state. */
function InvitationLink({ invitation }: { invitation: InvitationOut }) {
  const url = `${window.location.origin}${paths.invitation}#${invitation.token}`
  const [copied, setCopied] = useState<'idle' | 'ok' | 'fail'>('idle')
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url)
      setCopied('ok')
    } catch {
      setCopied('fail')
    }
  }
  return (
    <div>
      <div className="rounded-xl border border-line bg-bg p-3">
        <p className="text-[0.75rem] font-semibold text-ink2">Link do convite, vale até {fmtDateTime(invitation.expires_at)}</p>
        <p className={`${mono} mt-1 select-all break-all text-[0.8125rem] text-ink`} data-testid="invitation-link" translate="no">
          {url}
        </p>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button type="button" className={btnSecondary} onClick={copy}>
          <Icon name={copied === 'ok' ? 'check' : 'copy'} size={16} />
          <span aria-live="polite">{copied === 'ok' ? 'Link copiado' : 'Copiar o link'}</span>
        </button>
        {copied === 'fail' && <span className={`text-[0.8125rem] font-semibold ${danger.text}`}>O navegador não deixou copiar. Selecione o link e copie.</span>}
      </div>
      <p className={hint}>Envie o link à pessoa por um canal da empresa. Ele aparece só agora; se perder, peça um novo, e este deixa de valer.</p>
    </div>
  )
}

function RolesDialog({ person, spaces, onClose }: { person: UserOut; spaces: Space[]; onClose: (changed?: boolean) => void }) {
  const { control } = useSpace()
  const [roles, setRoles] = useState<BindingIn[]>(person.roles.map((b) => ({ role: b.role, space_id: b.space_id, source_ids: [...b.source_ids] })))
  const [reason, setReason] = useState('')
  const [touched, setTouched] = useState(false)
  const act = useAction<unknown>()
  const problem = rolesError(roles)
  const lastAdmin = act.error instanceof ApiError && act.error.status === 409
  const submit = (e: FormEvent) => {
    e.preventDefault()
    setTouched(true)
    if (problem || !reasonOk(reason)) return
    act.run(() => control.setRoles(person.user_id, roles, reason.trim()), () => onClose(true))
  }
  return (
    <Dialog open onClose={() => onClose()} title={`Papéis de ${person.email}`} wide>
      <form className="grid gap-5" onSubmit={submit} noValidate>
        {person.sso && (
          <p className="rounded-lg border border-line bg-bg px-3 py-2.5 text-[0.875rem] text-ink">
            Esta pessoa entra pelo SSO: no próximo login, os papéis voltam a ser os dos grupos dela no provedor de identidade. Para mudar de vez, mude os grupos ou o mapeamento em Login único.
          </p>
        )}
        <RolesEditor value={roles} onChange={setRoles} spaces={spaces} error={touched ? problem : null} />
        <ReasonField value={reason} onChange={setReason} />
        {lastAdmin ? (
          <p role="alert" className={`text-[0.875rem] font-semibold ${danger.text}`}>
            A empresa precisa manter ao menos uma pessoa com Administração em todos os espaços.
          </p>
        ) : (
          Boolean(act.error) && <ErrorState error={act.error} compact />
        )}
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" className={btnSecondary} onClick={() => onClose()}>
            Cancelar
          </button>
          <button type="submit" className={btnPrimary} disabled={act.busy || !reasonOk(reason)}>
            {act.busy ? 'Salvando…' : 'Salvar papéis'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}

function InviteDialog({ person, onClose }: { person: UserOut; onClose: (changed?: boolean) => void }) {
  const { control } = useSpace()
  const act = useAction<InvitationOut>()
  return (
    <Dialog open onClose={() => onClose(Boolean(act.result))} title={person.password_set ? 'Link para trocar a senha' : 'Novo convite'} wide>
      {act.result ? (
        <div className="grid gap-4">
          <InvitationLink invitation={act.result} />
          <div className="flex justify-end">
            <button type="button" className={btnPrimary} onClick={() => onClose(true)}>
              Concluir
            </button>
          </div>
        </div>
      ) : (
        <div className="grid gap-4">
          <p className="text-ink">
            {person.password_set ? (
              <>
                Um link que entra uma vez, para <strong>{person.email}</strong> criar outra senha. A senha atual continua valendo até o link ser usado. O segundo fator não muda.
              </>
            ) : (
              <>
                Um link novo para <strong>{person.email}</strong> criar a senha. O convite anterior deixa de valer.
              </>
            )}
          </p>
          {Boolean(act.error) && <ErrorState error={act.error} compact />}
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" className={btnSecondary} onClick={() => onClose()}>
              Cancelar
            </button>
            <button type="button" className={btnPrimary} disabled={act.busy} onClick={() => act.run(() => control.newInvitation(person.user_id))}>
              {act.busy ? 'Gerando…' : 'Gerar o link'}
            </button>
          </div>
        </div>
      )}
    </Dialog>
  )
}

function ResetDialog({ person, onClose }: { person: UserOut; onClose: (changed?: boolean) => void }) {
  const { control } = useSpace()
  const [reason, setReason] = useState('')
  const act = useAction<unknown>()
  const submit = (e: FormEvent) => {
    e.preventDefault()
    act.run(() => control.resetSecondFactor(person.user_id, reason.trim()), () => onClose(true))
  }
  return (
    <Dialog open onClose={() => onClose()} title="Redefinir o segundo fator">
      <form className="grid gap-4" onSubmit={submit}>
        <p className="text-ink">
          Para quem perdeu o aplicativo autenticador e os códigos de recuperação. O aplicativo e os códigos de <strong>{person.email}</strong> deixam de valer, e a pessoa configura um aplicativo novo
          no próximo login.
        </p>
        <p className={small}>Confirme quem pediu por um canal da empresa antes: esta ação abre a conta para um aplicativo novo.</p>
        <ReasonField value={reason} onChange={setReason} />
        {Boolean(act.error) && <ErrorState error={act.error} compact />}
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" className={btnSecondary} onClick={() => onClose()}>
            Cancelar
          </button>
          <button type="submit" className={btnDanger} disabled={act.busy || !reasonOk(reason)}>
            {act.busy ? 'Redefinindo…' : 'Redefinir o segundo fator'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}

function StateDialog({ person, deactivate, onClose }: { person: UserOut; deactivate: boolean; onClose: (changed?: boolean) => void }) {
  const { control } = useSpace()
  const [reason, setReason] = useState('')
  const act = useAction<UserOut>()
  const lastAdmin = act.error instanceof ApiError && act.error.status === 409
  const submit = (e: FormEvent) => {
    e.preventDefault()
    const call = deactivate ? control.deactivateUser : control.reactivateUser
    act.run(() => call(person.user_id, reason.trim()), () => onClose(true))
  }
  return (
    <Dialog open onClose={() => onClose()} title={deactivate ? `Desativar ${person.email}` : `Reativar ${person.email}`}>
      <form className="grid gap-4" onSubmit={submit}>
        {deactivate ? (
          <p className="text-ink">
            A pessoa deixa de entrar no Console na hora, e o plano de controle recusa a sessão dela na próxima chamada. Na API de dados, uma sessão já aberta vale até vencer, 15 minutos no máximo.
            Convites abertos deixam de valer. Papéis e segundo fator ficam guardados para uma reativação.
          </p>
        ) : (
          <p className="text-ink">A pessoa volta a entrar, com os mesmos papéis, a mesma senha e o mesmo segundo fator.</p>
        )}
        <ReasonField value={reason} onChange={setReason} />
        {lastAdmin ? (
          <p role="alert" className={`text-[0.875rem] font-semibold ${danger.text}`}>
            Não dá para desativar: a empresa ficaria sem ninguém com Administração em todos os espaços.
          </p>
        ) : (
          Boolean(act.error) && <ErrorState error={act.error} compact />
        )}
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" className={btnSecondary} onClick={() => onClose()}>
            Cancelar
          </button>
          <button type="submit" className={deactivate ? btnDanger : btnPrimary} disabled={act.busy || !reasonOk(reason)}>
            {act.busy ? (deactivate ? 'Desativando…' : 'Reativando…') : deactivate ? 'Desativar' : 'Reativar'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}

function HistoryDialog({ person, people, onClose }: { person: UserOut; people: Map<string, UserOut>; onClose: () => void }) {
  const { control } = useSpace()
  const history = useResource((signal) => control.userHistory(person.user_id, { signal }), [person.user_id, control])
  return (
    <Dialog open onClose={onClose} title={`Histórico de ${person.email}`} wide>
      {history.status === 'loading' && <Loading label="Carregando o histórico…" />}
      {history.status === 'error' && <ErrorState error={history.error} onRetry={history.reload} compact />}
      {history.status === 'success' && (history.data ?? []).length === 0 && <p className={small}>Nada mudou no acesso desta pessoa desde que o histórico existe.</p>}
      {history.status === 'success' && (history.data ?? []).length > 0 && (
        <ol aria-label="Mudanças de acesso" className="grid gap-3">
          {(history.data ?? []).map((c) => (
            <li key={c.change_id} className="rounded-lg border border-line px-3 py-2.5">
              <p className="font-semibold text-ink">{accessChange(c.kind)}</p>
              <p className={small}>
                {fmtDateTime(c.at)} · {c.actor_id ? `por ${people.get(c.actor_id)?.email ?? 'uma pessoa que não está mais na empresa'}` : 'pelo provedor de identidade (SSO)'}
              </p>
              <ChangeDetail change={c} />
              {c.reason && <p className="mt-1 text-[0.875rem] text-ink">Motivo: {c.reason}</p>}
            </li>
          ))}
        </ol>
      )}
      <div className="mt-5 flex justify-end">
        <button type="button" className={btnPrimary} onClick={onClose}>
          Fechar
        </button>
      </div>
    </Dialog>
  )
}

function ChangeDetail({ change }: { change: AccessChangeOut }): ReactNode {
  const names = (v: unknown) =>
    Array.isArray(v)
      ? v
          .map((x) => (typeof x === 'string' ? x : typeof x === 'object' && x && 'role' in x ? String((x as { role: unknown }).role) : ''))
          .filter(Boolean)
          .map(role)
          .join(', ') || 'nenhum'
      : null
  const before = names(change.detail.before)
  const after = names(change.detail.after)
  if (before !== null && after !== null)
    return (
      <p className="mt-1 text-[0.875rem] text-ink">
        Antes: {before}. Depois: {after}.{change.detail.kept_admin === true && ' Administração mantida: era a última pessoa com esse papel.'}
      </p>
    )
  const roles = names(change.detail.roles)
  if (roles) return <p className="mt-1 text-[0.875rem] text-ink">Papéis: {roles}.</p>
  return null
}
