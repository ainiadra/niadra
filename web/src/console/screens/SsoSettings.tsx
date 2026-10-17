import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { ApiError } from '../api/http'
import type { Role, RoleMapping, ServiceProviderOut, SsoConnectionIn, SsoConnectionOut, SsoOut, SsoProtocol, SsoTestOut, SsoTestResultOut } from '../api/types'
import { fmtDate, fmtDateTime } from '../lib/format'
import { role, ssoFailure } from '../lib/labels'
import { useResource } from '../lib/useResource'
import { useSpace } from '../session'
import { Chip, PageHeader, Props } from '../ui/bits'
import { Dialog } from '../ui/Dialog'
import { Field, ReasonField, reasonOk, Section, Select, TextInput, useAction } from '../ui/forms'
import { Icon } from '../ui/Icon'
import { useTitle } from '../ui/layout'
import { Empty, ErrorState, Loading } from '../ui/States'
import { btnDangerGhost, btnDanger, btnPrimary, btnQuiet, btnSecondary, danger, hint, input, link, mono, small } from '../ui/styles'

/** The roles a group may grant: the vendor role names its sources, which no group can say. */
const GRANTABLE: Role[] = ['admin', 'security', 'integration', 'review', 'analysis']
const NO_DEFAULT = 'none'
const POLL_MS = 2000

/**
 * The tenant's single sign-on: one connection, OIDC or SAML, the e-mail domains it
 * answers for, and how groups become roles. The screen also says what to paste into the identity provider,
 * runs a test sign-in in another tab and explains how the second factor works with SSO.
 */
export function SsoSettings() {
  const { control, hasRole, space } = useSpace()
  useTitle('Login único (SSO)', space.environment === 'sandbox')
  const admin = hasRole('admin')
  const sso = useResource((signal) => control.sso({ signal }), [control], admin)
  const [saved, setSaved] = useState<SsoOut | null>(null)
  const view = saved ?? sso.data ?? null
  // Lives here: a save gives the form a new connection, and the form starts over from it.
  const [note, setNote] = useState<string | null>(null)
  const noteRef = useRef<HTMLParagraphElement>(null)
  useEffect(() => {
    // The save button is at the end of a long form: bring the answer into view and give it the focus.
    if (note) {
      noteRef.current?.scrollIntoView?.({ block: 'center' })
      noteRef.current?.focus()
    }
  }, [note])

  if (!admin)
    return (
      <>
        <PageHeader screen="sso" />
        <Empty title="Esta tela exige o papel Administração.">Quem administra o Console da sua empresa configura o SSO.</Empty>
      </>
    )

  return (
    <>
      <PageHeader
        screen="sso"
        actions={view?.connection ? <ConnectionChip connection={view.connection} /> : undefined}
      />
      {sso.status === 'loading' && !view && <Loading label="Carregando o SSO da empresa…" />}
      {sso.status === 'error' && !view && <ErrorState error={sso.error} onRetry={sso.reload} />}
      {view && (
        <div className="grid gap-6">
          {note && (
            <p ref={noteRef} tabIndex={-1} role="status" className="rounded-lg border border-accent/40 bg-accent-wash px-3 py-2.5 text-[0.875rem] text-ink outline-none">
              <strong>SSO salvo.</strong> {note}
            </p>
          )}
          <ConnectionForm
            key={view.connection?.updated_at ?? 'new'}
            view={view}
            onChange={() => setNote(null)}
            onSaved={(next) => {
              setSaved(next)
              setNote(next.connection?.enabled ? 'O login já oferece o SSO para estes domínios.' : 'Está desligado: teste o login e depois ligue.')
            }}
          />
          {view.connection && <TestSection />}
          <MfaSection />
          {view.connection && <RemoveSection onRemoved={() => { setSaved(null); sso.reload() }} />}
        </div>
      )}
    </>
  )
}

function ConnectionChip({ connection }: { connection: SsoConnectionOut }) {
  return connection.enabled ? <Chip tone="ok">Ligado · {connection.protocol === 'oidc' ? 'OIDC' : 'SAML 2.0'}</Chip> : <Chip>Desligado</Chip>
}

/** What the tenant pastes into its identity provider, for the protocol chosen in the form. */
function ProviderDetails({ protocol, sp }: { protocol: SsoProtocol; sp: ServiceProviderOut }) {
  const rows: [string, string, string][] =
    protocol === 'oidc'
      ? [['URI de redirecionamento (callback)', sp.oidc_redirect_uri, 'Na aplicação do provedor, do tipo web, com o fluxo de código de autorização.']]
      : [
          ['Entity ID do provedor de serviço (público)', sp.saml_entity_id, 'O identificador que o provedor põe no público da asserção.'],
          ['URL do ACS (HTTP-POST)', sp.saml_acs_url, 'Para onde o provedor envia a resposta assinada.'],
          ['Metadados do provedor de serviço', sp.saml_metadata_url, 'Disponível depois de salvar a conexão SAML; alguns provedores leem tudo daqui.'],
        ]
  return (
    <div className="grid gap-3">
      {rows.map(([name, value, help]) => (
        <CopyValue key={name} name={name} value={value} help={help} />
      ))}
      {protocol === 'oidc' ? (
        <p className={small}>
          Escopos pedidos: <span className={mono}>openid email profile</span>, mais os que você acrescentar. O provedor precisa mandar a declaração <span className={mono}>email</span> (no Microsoft Entra ID,
          como declaração opcional) e, para mapear papéis, a dos grupos.
        </p>
      ) : (
        <p className={small}>
          Formato do NameID: e-mail. A asserção precisa vir assinada (a resposta inteira pode vir assinada também) e sem criptografia. Mande os grupos num atributo, como <span className={mono}>groups</span>.
        </p>
      )}
    </div>
  )
}

function CopyValue({ name, value, help }: { name: string; value: string; help: string }) {
  const [copied, setCopied] = useState<'idle' | 'ok' | 'fail'>('idle')
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied('ok')
    } catch {
      setCopied('fail')
    }
  }
  return (
    <div className="rounded-lg border border-line bg-bg px-3 py-2.5">
      <p className="text-[0.75rem] font-semibold text-ink2">{name}</p>
      <div className="mt-1 flex flex-wrap items-center justify-between gap-2">
        <span className={`${mono} min-w-0 select-all break-all text-[0.8125rem] text-ink`} translate="no">
          {value}
        </span>
        <button type="button" className={btnQuiet} onClick={copy} aria-label={`Copiar: ${name}`}>
          <Icon name={copied === 'ok' ? 'check' : 'copy'} size={16} />
          <span aria-live="polite">{copied === 'ok' ? 'Copiado' : 'Copiar'}</span>
        </button>
      </div>
      <p className={`${small} mt-1`}>{copied === 'fail' ? 'O navegador não deixou copiar. Selecione o texto e copie.' : help}</p>
    </div>
  )
}

interface FormState {
  protocol: SsoProtocol
  domains: string
  enabled: boolean
  enforced: boolean
  requireSecondFactor: boolean
  defaultRole: Role | typeof NO_DEFAULT
  mappings: RoleMapping[]
  groupsAttribute: string
  issuer: string
  clientId: string
  clientSecret: string
  scopes: string
  idpEntityId: string
  idpSsoUrl: string
  idpCertificate: string
}

function initial(c: SsoConnectionOut | null): FormState {
  return {
    protocol: c?.protocol ?? 'oidc',
    domains: c?.domains.join('\n') ?? '',
    // A new connection is saved off, tested, then turned on.
    enabled: c?.enabled ?? false,
    enforced: c?.enforced ?? false,
    requireSecondFactor: c?.require_second_factor ?? false,
    defaultRole: c ? (c.default_role ?? NO_DEFAULT) : NO_DEFAULT,
    mappings: c?.role_mappings.map((m) => ({ ...m })) ?? [],
    groupsAttribute: c?.groups_attribute ?? 'groups',
    issuer: c?.oidc?.issuer ?? '',
    clientId: c?.oidc?.client_id ?? '',
    clientSecret: '',
    scopes: c?.oidc?.scopes.filter((s) => !['openid', 'email', 'profile'].includes(s)).join(' ') ?? '',
    idpEntityId: c?.saml?.idp_entity_id ?? '',
    idpSsoUrl: c?.saml?.idp_sso_url ?? '',
    // The signing certificate is public: the server returns it, so an update does not ask to paste it again.
    idpCertificate: c?.saml?.certificate.pem ?? '',
  }
}

const isHttps = (v: string) => {
  try {
    return new URL(v.trim()).protocol === 'https:'
  } catch {
    return false
  }
}

function problems(f: FormState, existing: SsoConnectionOut | null): Partial<Record<keyof FormState, string>> {
  const out: Partial<Record<keyof FormState, string>> = {}
  const domains = splitList(f.domains)
  if (domains.length === 0) out.domains = 'Informe ao menos um domínio de e-mail da empresa.'
  else if (domains.some((d) => !/^[a-z0-9-]+(\.[a-z0-9-]+)+$/i.test(d.replace(/^@/, '')))) out.domains = 'Cada linha é um domínio, como empresa.com.br.'
  if (f.mappings.some((m) => !m.group.trim())) out.mappings = 'Cada linha de grupo precisa do nome ou do id do grupo.'
  else if (f.enabled && f.mappings.length === 0 && f.defaultRole === NO_DEFAULT) out.mappings = 'Sem papel padrão e sem grupo mapeado, ninguém entra. Mapeie um grupo ou escolha um papel padrão.'
  if (!f.groupsAttribute.trim()) out.groupsAttribute = 'Informe onde vêm os grupos, como groups.'
  if (f.protocol === 'oidc') {
    if (!isHttps(f.issuer)) out.issuer = 'O emissor é um endereço https, como https://login.microsoftonline.com/<tenant>/v2.0.'
    if (!f.clientId.trim()) out.clientId = 'Informe o client ID da aplicação no provedor.'
    if (!f.clientSecret && !existing?.oidc) out.clientSecret = 'Informe o client secret da aplicação.'
  } else {
    if (!f.idpEntityId.trim()) out.idpEntityId = 'Informe o entity ID (emissor) do provedor.'
    if (!isHttps(f.idpSsoUrl)) out.idpSsoUrl = 'O endereço de login do provedor é https.'
    if (!f.idpCertificate.trim()) out.idpCertificate = 'Cole o certificado de assinatura do provedor.'
  }
  return out
}

const splitList = (text: string) => [...new Set(text.split(/[\s,;]+/).map((d) => d.trim().toLowerCase()).filter(Boolean))]

/** What the server said, in the tenant's language, for the refusals an admin can fix in the form. */
function saveError(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null
  const d = err.detail ?? ''
  if (err.status === 409) return 'Um dos domínios já é de outra empresa na Niadra. Fale com o time da Niadra se o domínio é seu.'
  const known: [string, string][] = [
    ['public e-mail domain', 'Domínios de e-mail público (gmail.com, outlook.com e parecidos) não identificam uma empresa.'],
    ['invalid e-mail domain', 'Um dos domínios não é válido.'],
    ['no OpenID discovery document', 'O emissor não publica o documento de descoberta (/.well-known/openid-configuration). Confira o endereço.'],
    ['names another issuer', 'O documento de descoberta aponta outro emissor. Use o endereço do emissor exatamente como o provedor mostra.'],
    ['no https', 'O documento de descoberta do provedor tem endereços sem https.'],
    ['not a public https address', 'O emissor precisa ser um endereço público com https.'],
    ['not a valid X.509', 'O certificado não é um X.509 válido. Cole o PEM ou o conteúdo em base64 dos metadados.'],
    ['vendor role', 'O papel Fornecedor não vem de grupo: ele é dado por convite, com as fontes do fornecedor.'],
    ['client secret is required', 'Informe o client secret da aplicação.'],
    ['nobody could sign in', 'Sem papel padrão e sem grupo mapeado, ninguém entra.'],
  ]
  const hit = known.find(([k]) => d.includes(k))
  if (hit) return hit[1]
  if (err.status === 503) return 'O provedor de identidade não respondeu a tempo. Tente salvar de novo em instantes.'
  return null
}

function ConnectionForm({ view, onSaved, onChange }: { view: SsoOut; onSaved: (v: SsoOut) => void; onChange: () => void }) {
  const { control } = useSpace()
  const existing = view.connection
  const [f, setF] = useState<FormState>(() => initial(existing))
  const [reason, setReason] = useState('')
  const [touched, setTouched] = useState(false)
  const act = useAction<SsoOut>()
  const errs = touched ? problems(f, existing) : {}
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => {
    setF((cur) => ({ ...cur, [k]: v }))
    onChange()
  }
  const ids = { enabled: useId(), enforced: useId(), second: useId() }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    setTouched(true)
    if (Object.keys(problems(f, existing)).length > 0 || !reasonOk(reason)) return
    const body: SsoConnectionIn = {
      protocol: f.protocol,
      domains: splitList(f.domains).map((d) => d.replace(/^@/, '')),
      enabled: f.enabled,
      enforced: f.enforced,
      default_role: f.defaultRole === NO_DEFAULT ? null : f.defaultRole,
      role_mappings: f.mappings.map((m) => ({ group: m.group.trim(), role: m.role })),
      groups_attribute: f.groupsAttribute.trim(),
      require_second_factor: f.requireSecondFactor,
      oidc: f.protocol === 'oidc' ? { issuer: f.issuer.trim(), client_id: f.clientId.trim(), client_secret: f.clientSecret || null, scopes: splitList(f.scopes) } : null,
      saml:
        f.protocol === 'saml'
          ? { idp_entity_id: f.idpEntityId.trim(), idp_sso_url: f.idpSsoUrl.trim(), idp_certificate: f.idpCertificate.trim() }
          : null,
      reason: reason.trim(),
    }
    act.run(() => control.saveSso(body), onSaved)
  }

  const friendly = saveError(act.error)
  return (
    <form onSubmit={submit} noValidate className="grid gap-6">
      <Section
        tour="sso.connection"
        title="Conexão com o provedor de identidade"
        description={
          existing ? (
            <>
              Atualizada em {fmtDateTime(existing.updated_at)}. Domínios: {existing.domains.join(', ')}.
            </>
          ) : (
            'Nenhuma conexão ainda. Escolha o protocolo que o seu provedor oferece, cole os dados dele e salve desligado; depois teste o login e ligue.'
          )
        }
      >
        <div className="grid gap-4">
          <fieldset>
            <legend className="mb-1.5 text-[0.8125rem] font-semibold text-ink">Protocolo</legend>
            <div className="flex flex-wrap gap-2">
              {(['oidc', 'saml'] as const).map((p) => (
                <label key={p} className={`flex min-h-11 cursor-pointer items-center gap-2 rounded-lg border px-3 text-[0.875rem] font-semibold lg:min-h-9 ${f.protocol === p ? 'border-accent bg-accent-wash text-ink' : 'border-edge bg-surface text-ink2'}`}>
                  <input type="radio" name="protocol" value={p} checked={f.protocol === p} onChange={() => set('protocol', p)} className="h-4 w-4 accent-[#5e7c00]" />
                  {p === 'oidc' ? 'OpenID Connect (OIDC)' : 'SAML 2.0'}
                </label>
              ))}
            </div>
          </fieldset>

          <div>
            <h3 className="mb-2 text-[0.875rem] font-semibold text-ink">No provedor, cole</h3>
            <ProviderDetails protocol={f.protocol} sp={view.service_provider} />
          </div>

          <h3 className="text-[0.875rem] font-semibold text-ink">Do provedor, traga</h3>
          {f.protocol === 'oidc' ? (
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="sm:col-span-2">
                <ErrText error={errs.issuer}>
                  <TextInput label="Emissor (issuer)" value={f.issuer} onChange={(v) => set('issuer', v)} mono placeholder="https://login.microsoftonline.com/…/v2.0" aria-invalid={Boolean(errs.issuer)} />
                </ErrText>
              </div>
              <ErrText error={errs.clientId}>
                <TextInput label="Client ID" value={f.clientId} onChange={(v) => set('clientId', v)} mono aria-invalid={Boolean(errs.clientId)} />
              </ErrText>
              <ErrText error={errs.clientSecret}>
                <TextInput
                  label="Client secret"
                  type="password"
                  value={f.clientSecret}
                  onChange={(v) => set('clientSecret', v)}
                  hint={existing?.oidc ? 'Guardado cifrado e nunca mostrado. Deixe em branco para manter o atual.' : 'Guardado cifrado no plano de controle e nunca mostrado de novo.'}
                  aria-invalid={Boolean(errs.clientSecret)}
                  autoComplete="new-password"
                />
              </ErrText>
              <div className="sm:col-span-2">
                <TextInput label="Escopos a mais (opcional)" value={f.scopes} onChange={(v) => set('scopes', v)} mono hint="Separados por espaço, como groups. openid, email e profile vão sempre." />
              </div>
            </div>
          ) : (
            <div className="grid gap-4">
              <ErrText error={errs.idpEntityId}>
                <TextInput label="Entity ID do provedor (emissor)" value={f.idpEntityId} onChange={(v) => set('idpEntityId', v)} mono aria-invalid={Boolean(errs.idpEntityId)} />
              </ErrText>
              <ErrText error={errs.idpSsoUrl}>
                <TextInput label="URL de login do provedor (HTTP-Redirect)" value={f.idpSsoUrl} onChange={(v) => set('idpSsoUrl', v)} mono aria-invalid={Boolean(errs.idpSsoUrl)} />
              </ErrText>
              <Field
                label="Certificado de assinatura"
                hint="O PEM, ou o conteúdo em base64 de X509Certificate nos metadados do provedor. Quando o provedor trocar o certificado, cole o novo aqui."
              >
                {(id) => (
                  <>
                    <textarea
                      id={id}
                      rows={5}
                      className={`${input} h-auto min-h-32 py-2 font-mono text-[0.75rem] leading-5`}
                      value={f.idpCertificate}
                      onChange={(e) => set('idpCertificate', e.target.value)}
                      spellCheck={false}
                      aria-invalid={Boolean(errs.idpCertificate)}
                    />
                    {errs.idpCertificate && <p className={`mt-1.5 text-[0.8125rem] font-semibold ${danger.text}`}>{errs.idpCertificate}</p>}
                  </>
                )}
              </Field>
              {existing?.saml && (
                <Props
                  items={[
                    ['Certificado guardado', existing.saml.certificate.subject],
                    ['Vence em', fmtDate(existing.saml.certificate.not_after)],
                    ['Impressão digital SHA-256', <span className={`${mono} break-all`}>{existing.saml.certificate.sha256_fingerprint}</span>],
                  ]}
                />
              )}
            </div>
          )}
        </div>
      </Section>

      <Section tour="sso.roles" title="Quem entra e com que papel" description="O provedor diz quem é a pessoa e os grupos dela. Cada grupo mapeado dá um papel em toda a empresa, e os papéis são conferidos a cada login pelo SSO.">
        <div className="grid gap-4">
          <Field label="Domínios de e-mail" hint={errs.domains ? undefined : 'Um por linha. Quem digita um e-mail destes domínios no login vê a opção de entrar pelo SSO. Cada domínio é de uma empresa só.'}>
            {(id) => (
              <>
                <textarea id={id} rows={3} className={`${input} h-auto min-h-24 py-2 font-mono text-[0.8125rem]`} value={f.domains} onChange={(e) => set('domains', e.target.value)} spellCheck={false} aria-invalid={Boolean(errs.domains)} />
                {errs.domains && <p className={`mt-1.5 text-[0.8125rem] font-semibold ${danger.text}`}>{errs.domains}</p>}
              </>
            )}
          </Field>
          <ErrText error={errs.groupsAttribute}>
            <TextInput
              label={f.protocol === 'oidc' ? 'Declaração dos grupos' : 'Atributo dos grupos'}
              value={f.groupsAttribute}
              onChange={(v) => set('groupsAttribute', v)}
              mono
              hint={f.protocol === 'oidc' ? 'Normalmente groups. O Microsoft Entra ID manda os ids dos grupos.' : 'Como o provedor nomeia o atributo: groups, ou um URI.'}
            />
          </ErrText>
          <MappingsEditor mappings={f.mappings} onChange={(m) => set('mappings', m)} error={errs.mappings} />
          <Select
            label="Papel padrão"
            value={f.defaultRole}
            onChange={(v) => set('defaultRole', v)}
            options={[{ value: NO_DEFAULT, label: 'Nenhum: só entra quem estiver num grupo mapeado' }, ...GRANTABLE.map((r) => ({ value: r, label: role(r) }))]}
            hint="Para quem entra pelo provedor sem estar em nenhum grupo mapeado."
          />
        </div>
      </Section>

      <Section tour="sso.rules" title="Regras do login">
        <div className="grid gap-1">
          <Toggle id={ids.enabled} checked={f.enabled} onChange={(v) => set('enabled', v)} label="SSO ligado" hint="Desligado, o Console não oferece o SSO no login; o teste funciona mesmo assim." />
          <Toggle
            id={ids.second}
            checked={f.requireSecondFactor}
            onChange={(v) => set('requireSecondFactor', v)}
            label="Pedir também o código da Niadra"
            hint="Depois do provedor, o Console pede o código do aplicativo autenticador, que a pessoa configura no primeiro login."
          />
          <Toggle
            id={ids.enforced}
            checked={f.enforced}
            onChange={(v) => set('enforced', v)}
            label="Só pelo SSO"
            hint="Pessoas destes domínios não entram com senha. Quem tem Administração em todos os espaços continua com a senha, para o caso de o provedor falhar."
          />
        </div>
      </Section>

      <div className="grid gap-4">
        <ReasonField value={reason} onChange={setReason} />
        {touched && Object.keys(errs).length > 0 && (
          <p role="alert" className={`text-[0.875rem] font-semibold ${danger.text}`}>
            Revise os campos marcados antes de salvar.
          </p>
        )}
        {Boolean(act.error) &&
          (friendly ? (
            <p role="alert" className={`text-[0.875rem] font-semibold ${danger.text}`}>
              {friendly}
            </p>
          ) : (
            <ErrorState error={act.error} compact />
          ))}
        <div className="flex justify-end">
          <button type="submit" className={btnPrimary} disabled={act.busy || !reasonOk(reason)}>
            {act.busy ? 'Salvando…' : existing ? 'Salvar a conexão' : 'Criar a conexão'}
          </button>
        </div>
      </div>
    </form>
  )
}

function ErrText({ error, children }: { error?: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      {children}
      {error && <p className={`mt-1.5 text-[0.8125rem] font-semibold ${danger.text}`}>{error}</p>}
    </div>
  )
}

function Toggle({ id, checked, onChange, label: text, hint: help }: { id: string; checked: boolean; onChange: (v: boolean) => void; label: string; hint: string }) {
  return (
    <div className="py-1">
      <label htmlFor={id} className="flex min-h-11 cursor-pointer items-center gap-2.5 text-[0.875rem] font-semibold text-ink lg:min-h-8">
        <input id={id} type="checkbox" className="h-5 w-5 shrink-0 accent-[#5e7c00]" checked={checked} onChange={(e) => onChange(e.target.checked)} aria-describedby={`${id}-hint`} />
        {text}
      </label>
      <p id={`${id}-hint`} className={`${small} pl-[1.875rem]`}>
        {help}
      </p>
    </div>
  )
}

function MappingsEditor({ mappings, onChange, error }: { mappings: RoleMapping[]; onChange: (m: RoleMapping[]) => void; error?: string }) {
  return (
    <fieldset className="grid gap-3">
      <legend className="mb-1 text-[0.8125rem] font-semibold text-ink">Grupos que dão papéis</legend>
      {mappings.length === 0 && <p className={small}>Nenhum grupo mapeado.</p>}
      {mappings.map((m, i) => (
        <div key={i} className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,14rem)_auto] sm:items-end">
          <TextInput label={`Grupo ${i + 1}`} value={m.group} onChange={(v) => onChange(mappings.map((x, j) => (j === i ? { ...x, group: v } : x)))} mono />
          <Select label={`Papel do grupo ${i + 1}`} value={m.role} onChange={(r: Role) => onChange(mappings.map((x, j) => (j === i ? { ...x, role: r } : x)))} options={GRANTABLE.map((r) => ({ value: r, label: role(r) }))} />
          <button type="button" className={`${btnQuiet} ${danger.text}`} onClick={() => onChange(mappings.filter((_, j) => j !== i))} aria-label={`Tirar o grupo ${i + 1}`}>
            Tirar
          </button>
        </div>
      ))}
      <div>
        <button type="button" className={btnSecondary} onClick={() => onChange([...mappings, { group: '', role: 'review' }])}>
          <Icon name="plus" size={16} />
          Adicionar grupo
        </button>
      </div>
      {error ? <p className={`text-[0.8125rem] font-semibold ${danger.text}`}>{error}</p> : <p className={hint}>A pessoa recebe o papel de cada grupo mapeado em que está. A comparação ignora maiúsculas.</p>}
    </fieldset>
  )
}

/**
 * A test sign-in in another tab: the provider's answer only says what would happen (e-mail, groups, roles),
 * and the Console asks the control plane for the result every two seconds, for ten minutes at most.
 */
function TestSection() {
  const { control } = useSpace()
  const start = useAction<SsoTestOut>()
  const [result, setResult] = useState<SsoTestResultOut | null>(null)
  const [pollError, setPollError] = useState<unknown>(null)
  const started = start.result

  useEffect(() => {
    if (!started) return
    let stop = false
    let timer: number | undefined
    const deadline = new Date(started.expires_at).getTime()
    const tick = async () => {
      try {
        const r = await control.ssoTest(started.test_id)
        if (stop) return
        setResult(r)
        setPollError(null)
        if (r.status === 'pending' && Date.now() < deadline) timer = window.setTimeout(tick, POLL_MS)
      } catch (e) {
        if (stop) return
        setPollError(e)
        if (Date.now() < deadline) timer = window.setTimeout(tick, POLL_MS)
      }
    }
    void tick()
    return () => {
      stop = true
      window.clearTimeout(timer)
    }
  }, [started, control])

  const begin = () => {
    setResult(null)
    setPollError(null)
    start.run(() => control.startSsoTest())
  }

  return (
    <Section
      tour="sso.test"
      title="Testar o login"
      description="Entre com uma conta do provedor numa aba nova. O teste mostra o e-mail, os grupos e os papéis que o login daria, sem criar ninguém e sem mudar papel."
      actions={
        <button type="button" className={btnSecondary} onClick={begin} disabled={start.busy}>
          {start.busy ? 'Preparando…' : started ? 'Testar de novo' : 'Testar o login'}
        </button>
      }
    >
      {Boolean(start.error) && <ErrorState error={start.error} compact />}
      {started && (
        <div className="grid gap-3">
          <p className="text-[0.875rem] text-ink">
            <a href={started.authorize_url} target="_blank" rel="noopener noreferrer" className={`${link} inline-flex min-h-11 items-center gap-1.5 lg:min-h-0`}>
              Abrir o provedor em outra aba
              <span className="sr-only">(abre em outra aba)</span>
            </a>
          </p>
          {(!result || result.status === 'pending') && (
            <p role="status" className={small}>
              Esperando a resposta do provedor. O teste vale até {fmtDateTime(started.expires_at)}.
            </p>
          )}
          {Boolean(pollError) && <ErrorState error={pollError} compact />}
          {result && result.status !== 'pending' && <TestResult result={result} />}
        </div>
      )}
    </Section>
  )
}

function TestResult({ result }: { result: SsoTestResultOut }) {
  return (
    <div role="status" className={`rounded-lg border px-3 py-3 ${result.ok ? 'border-accent/40 bg-accent-wash' : `${danger.border} ${danger.wash}`}`}>
      <p className="font-semibold text-ink">{result.ok ? 'O login funcionaria.' : 'O login seria recusado.'}</p>
      {!result.ok && <p className="mt-1 text-[0.875rem] text-ink">{ssoFailure(result.failure)}</p>}
      {(result.email || result.groups.length > 0) && (
        <div className="mt-3">
          <Props
            items={[
              ['E-mail do provedor', result.email ?? 'não enviado'],
              ['Grupos recebidos', result.groups.length ? <span className={`${mono} break-all`}>{result.groups.join(', ')}</span> : 'nenhum'],
              ['Papéis que daria', result.roles.length ? result.roles.map(role).join(', ') : 'nenhum'],
              ...(result.ok ? ([['No Console', result.person_exists ? 'A pessoa já existe; os papéis seriam atualizados' : 'Pessoa nova: seria criada no primeiro login']] as [string, ReactNode][]) : []),
            ]}
          />
        </div>
      )}
      {!result.ok && result.detail && (
        <p className={`${small} mt-2`}>
          Detalhe técnico: <span className={mono}>{result.detail}</span>
        </p>
      )}
    </div>
  )
}

function MfaSection() {
  return (
    <Section title="Como fica o segundo fator com SSO">
      <div className="grid max-w-[76ch] gap-2 text-[0.875rem] leading-6 text-ink">
        <p>
          Quem entra pelo SSO passa pelo MFA do provedor de identidade da empresa, com as regras que a empresa já definiu lá. A Niadra confere a resposta assinada do provedor, mas não enxerga qual
          fator a pessoa usou.
        </p>
        <p>
          Se a sua política pede mais, ligue <strong>Pedir também o código da Niadra</strong>: depois do provedor, o Console pede o código do aplicativo autenticador, com códigos de recuperação, como no
          login com senha.
        </p>
        <p>Quem entra com e-mail e senha continua com o segundo fator obrigatório da Niadra.</p>
      </div>
    </Section>
  )
}

function RemoveSection({ onRemoved }: { onRemoved: () => void }) {
  const { control } = useSpace()
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const act = useAction<void>()
  const submit = (e: FormEvent) => {
    e.preventDefault()
    act.run(
      () => control.removeSso(reason.trim()),
      () => {
        setOpen(false)
        onRemoved()
      },
    )
  }
  return (
    <section aria-labelledby="h-sso-remove" className={`rounded-xl border ${danger.border} bg-surface p-4 sm:p-5`}>
      <h2 id="h-sso-remove" className="font-display text-[1.06rem] font-semibold text-ink">
        Remover o SSO
      </h2>
      <p className={`${small} mt-1 max-w-[70ch]`}>
        O login deixa de oferecer o SSO. Quem foi criado pelo SSO continua no Console, com os papéis de agora, e entra com senha depois de receber um convite em Pessoas e papéis.
      </p>
      <button type="button" className={`${btnDangerGhost} mt-4`} onClick={() => setOpen(true)}>
        Remover o SSO
      </button>
      {open && (
        <Dialog open onClose={() => setOpen(false)} title="Remover o SSO">
          <form className="grid gap-4" onSubmit={submit}>
            <p className="text-ink">A conexão, os domínios e os logins em andamento pelo SSO são apagados.</p>
            <ReasonField value={reason} onChange={setReason} />
            {Boolean(act.error) && <ErrorState error={act.error} compact />}
            <div className="flex flex-wrap justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setOpen(false)}>
                Cancelar
              </button>
              <button type="submit" className={btnDanger} disabled={act.busy || !reasonOk(reason)}>
                {act.busy ? 'Removendo…' : 'Remover o SSO'}
              </button>
            </div>
          </form>
        </Dialog>
      )}
    </section>
  )
}
