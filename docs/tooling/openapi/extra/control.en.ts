/**
 * English descriptions of the control API routes, where the contract has none.
 * Keys follow model.ts (Translate), with the `control:` prefix that build.ts adds to control API keys.
 */
export const EN: Record<string, string> = {
  // Access
  'control:op:post /v1/auth/login':
    'Signs a person in with e-mail, password and the TOTP code, and returns the token the Console and the control API use. The token lasts 15 minutes and is bound to one space; a person whose authenticator is not confirmed yet gets 401 "second factor enrollment required" and sets it up with POST /v1/auth/second-factor. The login that confirms the authenticator also returns ten recovery codes, once; a person who lost the app sends one of them in `recovery_code`, in place of `totp`. People of a domain with the tenant\'s single sign-on sign in through POST /v1/auth/sso/start instead; when the tenant enforces it, the password stays only for tenant-wide admins, and the others get 401 "single sign-on required".',
  'control:res:post /v1/auth/login:200':
    'The token, its lifetime in seconds and the space it opens, with the recovery codes when this login confirmed the authenticator.',
  'control:op:post /v1/auth/token':
    'Issues a new token for the signed-in person, to renew the session silently or to switch to another space. Roles are read again, so a role removed a moment ago is gone from the new token.',
  'control:res:post /v1/auth/token:200': 'A new token.',
  'control:op:get /.well-known/jwks.json':
    'The public keys (Ed25519) that sign person tokens. Cells verify tokens offline with these keys, so a Console read never waits for the control plane. Cached for 5 minutes.',
  'control:res:get /.well-known/jwks.json:200': 'A JWKS document.',
  'control:op:get /v1/users/me':
    'The signed-in person: tenant, e-mail and every space they can open, with the roles they hold in each. The Console builds its menus from this answer.',
  'control:res:get /v1/users/me:200': 'The person and their spaces.',
  'control:res:get /v1/users/me/preferences:200': 'The preferences of the person, by key.',
  'control:res:put /v1/users/me/preferences/{key}:200': 'Every preference of the person, with the key written.',
  'control:param:put /v1/users/me/preferences/{key}:key': 'The name of the preference: 1 to 64 characters of a-z, 0-9, `_`, `.` and `-`, starting with a letter.',
  'control:res:get /v1/users/me/second-factor:200': 'The second factor of the person.',
  'control:res:post /v1/users/me/recovery-codes:200': 'The new recovery codes, shown once.',
  'control:res:post /v1/users/me/second-factor:200': 'The new secret, pending until its first code.',
  'control:res:post /v1/users/me/second-factor/confirm:200': 'The new recovery codes, shown once.',
  'control:res:get /v1/users:200': 'The people of the tenant.',
  'control:res:post /v1/users:201': 'The person, with the invitation (shown once) when created without a password.',
  'control:res:put /v1/users/{user_id}/roles:200': 'The bindings now in force.',
  'control:res:post /v1/users/{user_id}/deactivate:200': 'The person, deactivated.',
  'control:res:post /v1/users/{user_id}/reactivate:200': 'The person, active again.',
  'control:res:post /v1/users/{user_id}/invitation:201': 'The new link, shown once.',
  'control:res:get /v1/users/{user_id}/history:200': 'The changes, newest first.',
  'control:res:post /v1/auth/invitation/preview:200': 'Whose invitation it is and until when it works.',
  'control:res:post /v1/auth/invitation:200': 'The e-mail, for the sign-in that follows.',
  // Single sign-on
  'control:res:post /v1/auth/sso/discover:200': 'Whether the domain signs in through SSO.',
  'control:res:post /v1/auth/sso/start:200': 'The address of the identity provider, where the browser goes.',
  'control:res:get /v1/auth/sso/oidc/callback:200':
    'For a test sign-in, a short page that says it is done. A sign-in answers 303 to the Console instead, with `#code=` or `#error=` in the fragment.',
  'control:res:post /v1/auth/sso/saml/{tenant_id}/acs:200':
    'For a test sign-in, a short page that says it is done. A sign-in answers 303 to the Console instead, with `#code=` or `#error=` in the fragment.',
  'control:res:get /v1/auth/sso/saml/{tenant_id}/metadata:200': 'The SAML metadata of the service provider (application/samlmetadata+xml).',
  'control:res:post /v1/auth/sso/exchange:200': 'The token, as at the password login.',
  'control:res:post /v1/auth/sso/second-factor:200': 'The secret to set up the authenticator app.',
  'control:res:get /v1/sso:200': 'The connection, or null, and what to paste into the identity provider.',
  'control:res:put /v1/sso:200': 'The connection as stored, and what to paste into the identity provider.',
  'control:res:delete /v1/sso:204': 'The connection is gone.',
  'control:res:post /v1/sso/test:201': 'The address of the test sign-in.',
  'control:res:get /v1/sso/tests/{test_id}:200': 'The outcome of the test, or pending.',
  'control:res:post /v1/users/{user_id}/second-factor/reset:200': 'The reset, with who did it and why.',
  'control:op:get /v1/roles':
    'The six Console roles and what each one covers: admin, security, integration, review, analysis and vendor. Roles combine.',
  'control:res:get /v1/roles:200': 'The roles.',
  // Tenant and projects
  'control:op:post /v1/tenants':
    'Creates a customer company. Used by Niadra operations when a contract starts; customer code never calls it.',
  'control:res:post /v1/tenants:201': 'The tenant.',
  'control:op:get /v1/tenants/{tenant_id}': 'The tenant of the signed-in person. Any other tenant id answers 404.',
  'control:res:get /v1/tenants/{tenant_id}:200': 'The tenant.',
  'control:op:get /v1/projects': 'The projects of the tenant, each with its sandbox and production spaces.',
  'control:res:get /v1/projects:200': 'The projects.',
  'control:op:post /v1/projects':
    'Creates a project in the given region, one of the regions in your contract. It comes with two spaces, sandbox and production, which never share data. The name becomes the slug of the address of each space, so the names the edge already serves (`mtls`, `api`, `www`, `control`, `cells`, `grafana`) are refused. Needs the admin role.',
  'control:res:post /v1/projects:201': 'The project and its two spaces.',
  'control:op:get /v1/spaces/{space_id}':
    'One space: project, environment, region, slug and the cell that serves it. The address of the space is `https://<slug>.<region>.api.niadra.com`.',
  'control:res:get /v1/spaces/{space_id}:200': 'The space.',
  'control:res:delete /v1/spaces/{space_id}:202': 'The deleted space, with `status` `deleting` until the cell finishes.',
  'control:op:get /v1/spaces/{space_id}/deletion-receipt':
    'A deleted space and what its cell deleted: `status` is `deleting` until the cell reports, then `deleted`, with the receipt and its SHA-256. A space that is not deleted answers 404. Needs the admin or security role.',
  'control:res:get /v1/spaces/{space_id}/deletion-receipt:200': 'The deleted space, with the receipt once the cell reported.',
  // Sources and keys
  'control:res:get /v1/sources:200': 'The sources of the space. A person with the vendor role sees only their own.',
  'control:res:post /v1/sources:201': 'The source.',
  'control:op:get /v1/sources/{source_id}/keys':
    'The keys of a source with their scopes, status and dates. The secret is never returned after creation. Needs the admin or security role.',
  'control:res:get /v1/sources/{source_id}/keys:200': 'The keys.',
  'control:res:post /v1/sources/{source_id}/keys:201': 'The key and its secret, shown this once.',
  'control:op:post /v1/sources/{source_id}/keys/{key_id}/rotate':
    'Issues a new key with the same scopes. The old key keeps working for `grace_seconds` (24 hours by default) and then expires; with 0 it is revoked at once. Needs the admin role.',
  'control:res:post /v1/sources/{source_id}/keys/{key_id}/rotate:201': 'The new key and its secret, shown this once.',
  'control:res:post /v1/sources/{source_id}/keys/{key_id}/revoke:200': 'The revocation, with its position in the revocation feed.',
  'control:op:post /v1/sources/{source_id}/revoke':
    'Cuts a vendor or system at once: every active key of the source is revoked and the source stops appearing in the configuration sent to the cells. Cells apply the cut within seconds. Needs the admin or security role.',
  'control:res:post /v1/sources/{source_id}/revoke:200': 'The revocation of the source.',
  'control:op:get /v1/secret-refs':
    'Where each secret of a space lives in the cell vault, with version and who rotated it last. Values never pass through the control plane.',
  'control:res:get /v1/secret-refs:200': 'The secret references.',
  'control:op:post /v1/secret-refs':
    'Registers the reference of a secret the Console wrote straight into the cell vault, such as a webhook signing secret. Only metadata travels here. Needs the admin, integration or security role.',
  'control:res:post /v1/secret-refs:201': 'The reference.',
  // Configuration
  'control:op:get /v1/config/types':
    'The configuration types a diff can change (policies, schemas, mappings, views, trait-rules, trigger-rules, webhook-endpoints, export-schedules, review-sampling, experiments, settings, extraction-guidance, the words of the company on what extraction should focus on and leave out, and ingest-masking, what ingestion holds back before storing: cards, security codes, passwords and secrets, security role only), with the roles that may write and approve each one besides admin.',
  'control:res:get /v1/config/types:200': 'The configuration types.',
  'control:op:get /v1/config/{type}':
    'The current document of one configuration type in a space, with its version. Version 0 means the type still has its default.',
  'control:res:get /v1/config/{type}:200': 'The document and its version.',
  'control:op:get /v1/config/diffs': 'The diffs of a space, newest first, optionally filtered by status.',
  'control:res:get /v1/config/diffs:200': 'The diffs.',
  'control:res:post /v1/config/diffs:201': 'The diff, pending approval.',
  'control:res:post /v1/config/diffs/{diff_id}/approve:200': 'The applied diff, with the version it created.',
  'control:op:post /v1/config/diffs/{diff_id}/reject':
    'Rejects a pending or proposed diff. Nothing changes in the space. Needs a role that may write the type.',
  'control:res:post /v1/config/diffs/{diff_id}/reject:200': 'The rejected diff.',
  'control:op:post /v1/config/diffs/{diff_id}/rollback':
    'Puts back the document as it was before an applied diff. The rollback is recorded as a new diff with `rollback_of` and applies at once, because it restores a state that was already approved. It is refused when a later diff changed the same document.',
  'control:res:post /v1/config/diffs/{diff_id}/rollback:200': 'The rollback diff, already applied.',
  'control:op:get /v1/config/history': 'Every applied version of the configuration of a space, optionally for one type.',
  'control:res:get /v1/config/history:200': 'The versions, newest first.',
  'control:op:post /v1/config/proposals':
    'Where a cell delivers the diff proposed by the configuration assistant. The diff arrives with status `proposed` and waits for a person to approve it. Only configuration travels: samples and customer values stay in the cell. Used by Niadra cells, not by customer code.',
  'control:res:post /v1/config/proposals:201': 'The proposed diff.',
  // Usage
  'control:res:get /v1/usage:200': 'Usage per space, period and metric, with the quotas of the tenant.',
  // Platform operations
  'control:op:get /v1/cells': 'The registered cells with their region, address, capacity and status. Used by Niadra cells and operations.',
  'control:res:get /v1/cells:200': 'The cells.',
  'control:op:post /v1/cells': 'Registers a new cell in a region. Used by Niadra operations.',
  'control:res:post /v1/cells:201': 'The cell.',
  'control:op:get /v1/spaces/{space_id}/placement':
    'Which cell serves a space, and the move in progress if there is one. During a move the old cell answers 421 and the SDK retries at once, at the same address: the address of the space never changes.',
  'control:res:get /v1/spaces/{space_id}/placement:200': 'The placement.',
  'control:op:post /v1/spaces/{space_id}/placement':
    'Starts moving a space to another cell: the cells copy and write to both sides until the move completes. Used by Niadra operations.',
  'control:res:post /v1/spaces/{space_id}/placement:202': 'The move, copying.',
  'control:op:post /v1/spaces/{space_id}/placement/complete':
    'Completes a move: the address of the space points to the new cell. Used by Niadra operations.',
  'control:res:post /v1/spaces/{space_id}/placement/complete:200': 'The completed move.',
  'control:op:put /v1/quotas':
    "Sets a usage limit for a tenant, per metric and period. Used by Niadra operations. A quota named `llm_daily_micros:<purpose>` with period `day` sets the contract's daily AI spend ceiling of that purpose (`extraction` or `assistant`) in every space of the tenant, in millionths of a US dollar: setting it republishes the tenant's cells, and each space reads it as its ceiling, shown by `GET /v1/usage/budget` in the space. The other quotas are recorded and returned with the usage; the AI spend ceiling is the one the cells apply.",
  'control:res:put /v1/quotas:200': 'The quota in force.',
  'control:res:get /v1/snapshot/{cell_id}:200': 'The signed snapshot.',
  'control:op:get /v1/revocations':
    'The signed feed of revoked keys and sources for one cell, from a sequence number on. Cells poll it and cut access within seconds. Used by Niadra cells.',
  'control:res:get /v1/revocations:200': 'The signed feed.',
  'control:op:post /v1/usage': 'Cells send their usage aggregates for billing. Used by Niadra cells.',
  'control:res:post /v1/usage:202': 'How many aggregates were accepted.',
  'control:res:post /v1/spaces/{space_id}/deletion-receipt:200': 'The stored receipt.',
  // Parameters
  'control:param:get /v1/sources:space_id': 'The space whose sources you want.',
  'control:param:get /v1/secret-refs:space_id': 'The space whose secret references you want.',
  'control:param:get /v1/config/{type}:type': 'A configuration type from `GET /v1/config/types`, such as `policies` or `trigger-rules`.',
  'control:param:get /v1/config/{type}:space_id': 'The space whose configuration you want.',
  'control:param:get /v1/config/diffs:space_id': 'The space whose diffs you want.',
  'control:param:get /v1/config/diffs:status': 'Only diffs with this status: `pending`, `proposed`, `applied` or `rejected`.',
  'control:param:get /v1/config/history:space_id': 'The space whose history you want.',
  'control:param:get /v1/config/history:type': 'Only versions of this configuration type.',
  'control:param:get /v1/config/history:limit': 'Up to 200 versions. Defaults to 50.',
  'control:param:get /v1/snapshot/{cell_id}:after': 'The version the cell already has. With it, the call waits until a newer version exists, or answers 304 at the deadline.',
  'control:param:get /v1/snapshot/{cell_id}:wait': 'How long to wait for a newer version, up to 30 seconds. Defaults to 25.',
  'control:param:get /v1/revocations:cell_id': 'The cell asking for the feed.',
  'control:param:get /v1/revocations:after': 'The last sequence number the cell applied. Pass the `seq` of the previous answer.',
  'control:param:get /v1/usage:since': 'Start of the period, inclusive.',
  'control:param:get /v1/usage:until': 'End of the period, exclusive.',
  // Fields
  'control:LoginRequest.totp': 'The current code of the authenticator app. Required when the person enrolled a second factor.',
  'control:LoginRequest.space_id': 'The space the token opens. Defaults to the first space the person can reach.',
  'control:TokenRequest.space_id': 'Switch the new token to this space. Defaults to the space of the current token.',
  'control:TokenOut.expires_in': 'Seconds until the token expires.',
  'control:SecondFactorResetOut.reset_id': 'The id of this reset.',
  'control:SecondFactorResetOut.user_id': 'The person whose second factor was reset.',
  'control:SecondFactorResetOut.reason': 'Why, as the admin wrote it.',
  'control:SecondFactorResetOut.at': 'When the reset happened.',
  'control:BindingIn.space_id': 'The role holds on this space only. Empty means every space of the tenant.',
  'control:BindingIn.source_ids': 'For the vendor role: the sources this person may see.',
  'control:SourceCreate.audience': 'What the source may read, by audience class: a customer-facing agent, an internal agent, a human desk and others.',
  'control:SourceCreate.purposes': 'Purposes the policy grants access by, such as `customer_service` or `billing`.',
  'control:SourceCreate.verification_ceiling': 'The highest verification level this source may declare, V2 by default. A customer-facing agent source can go up to V3, the level of the OTP it runs itself; V4 is only for `human` and `internal_agent`, and `no_customer` only for `internal_agent`. A ceiling above what the audience allows is refused with 422 `verification_not_allowed`.',
  'control:SourceCreate.confirms_before_acting': 'The agent confirms values before acting, so measurement does not count those confirmations as repeated questions.',
  'control:SourceCreate.navigation_enabled': 'Whether the source may use the history navigation tools.',
  'control:SourceCreate.trusted_action_ops': 'Operations whose declared actions this source may use to close open items before the system of record confirms them.',
  'control:SourceCreate.critical': 'Sessions of this source stay out of control groups and always receive the memory.',
  'control:KeyRotate.grace_seconds': 'How long the old key keeps working. Defaults to 24 hours; 0 revokes it at once.',
  'control:RevokeRequest.reason': 'Why, recorded in the audit trail.',
  'control:DiffCreate.document': 'The whole new document of the type, in the format `GET /v1/config/{type}` returns.',
  'control:DiffCreate.reason': 'Why, recorded with the diff.',
  'control:DiffOut.base_version': 'The configuration version the diff was written against. A diff written against an outdated document is refused at approval.',
  'control:DiffOut.applied_version': 'The version the diff created when it was applied.',
  'control:DiffOut.rollback_of': 'The diff this one rolled back.',
  'control:SecretRefIn.ref': 'The path of the secret in the cell vault, such as `spaces/<space_id>/webhook/whsec-crm`.',
  'control:SecretRefIn.kind': '`webhook` for a webhook signing secret, `model` for a model credential.',
  'control:CellCreate.capacity': 'How many spaces the cell may serve.',
  'control:UsageItem.cost_micros': 'Cost in millionths of a dollar, when the metric has one.',
  'control:QuotaIn.period': 'The period the limit counts over. Defaults to `month`.',
  'control:SpaceDeletionOut.deleted_at': 'When an admin deleted the space.',
  'control:SpaceDeletionOut.receipt': 'What the cell deleted; null while `status` is `deleting`.',
  'control:DeletionReceiptOut.cell_id': 'The cell that deleted the space.',
  'control:DeletionReceiptOut.dropped_at': "When the space's database was dropped.",
  'control:DeletionReceiptOut.received_at': 'When the control plane stored the receipt.',
  'control:DeletionReceiptIn.cell_id': 'The cell that deleted the space.',
  'control:DeletionReceiptIn.receipt_hash': 'SHA-256 of the canonical JSON of `receipt` (sorted keys, no spaces, UTF-8), in hexadecimal.',
  // Overrides of descriptions carried from the first draft, to match the contract
  'control:op:get /v1/sources':
    'Every agent, vendor, channel or system that talks to a space is a source, with its own keys, purposes and audience class. A person with the vendor role sees only the sources bound to them.',
  'control:op:post /v1/sources':
    'Creates a source in a space. Access to memory is denied by default and granted by purpose in the policy; the verification ceiling caps what the source may declare. Needs the admin role.',
  'control:op:post /v1/sources/{source_id}/keys':
    'Issues a key for the source. Without `scopes`, the scopes follow the audience of the source. The secret is returned once; Niadra keeps only two hashes of it. Needs the admin role.',
  'control:op:post /v1/sources/{source_id}/keys/{key_id}/revoke':
    'Cuts one key at once. The revocation enters the signed feed the cells apply within seconds, and the SDK drops the context it cached for that key on the next 401 or 403. Needs the admin or security role.',
  'control:op:post /v1/config/diffs':
    'Configuration is never written in place: policies, views, mappings, pattern and trigger rules, webhook endpoints, export schedules and experiments change through versioned diffs. The diff carries the whole new document and waits for approval.',
  'control:op:post /v1/config/diffs/{diff_id}/approve':
    'Applies a pending or proposed diff and creates a new configuration version, which the cells receive through the signed snapshot. The author cannot approve their own diff unless the tenant has a single admin, and a diff written against an outdated document is refused.',
}
