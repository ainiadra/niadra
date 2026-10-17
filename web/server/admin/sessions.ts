/** Admin sessions in Postgres (database niadra_site), so a deploy of the site API keeps the founder signed in. */
import type pg from 'pg'
import type { Session, SessionStore } from './auth'

export const SESSIONS_SCHEMA = `
  create table if not exists admin_sessions (
    token_hash bytea       primary key,
    email      text        not null,
    created_at timestamptz not null default now(),
    expires_at timestamptz not null
  );
`

export class PgSessions implements SessionStore {
  constructor(private readonly pool: Pick<pg.Pool, 'query'>) {}

  async create(tokenHash: Buffer, session: Session) {
    // Expired sessions go whenever a new one starts: there is one account, so the table stays tiny.
    await this.pool.query('delete from admin_sessions where expires_at < now()')
    await this.pool.query('insert into admin_sessions (token_hash, email, expires_at) values ($1, $2, $3)', [tokenHash, session.email, session.expiresAt])
  }

  async find(tokenHash: Buffer, now: Date) {
    const { rows } = await this.pool.query<{ email: string; expires_at: Date }>(
      'select email, expires_at from admin_sessions where token_hash = $1 and expires_at > $2',
      [tokenHash, now],
    )
    return rows[0] ? { email: rows[0].email, expiresAt: rows[0].expires_at } : null
  }

  async remove(tokenHash: Buffer) {
    await this.pool.query('delete from admin_sessions where token_hash = $1', [tokenHash])
  }
}
