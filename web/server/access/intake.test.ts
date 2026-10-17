// The counter end to end with a stand-in database: datagrams in, one upsert a minute out.
import { afterEach, describe, expect, it } from 'vitest'
import type pg from 'pg'
import { startCounter, type Counter } from './intake'

const CHROME = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36'
const line = (uri: string, ua = CHROME) =>
  JSON.stringify({ ts: Date.now() / 1000, logger: 'http.log.access.site', request: { host: 'niadra.com', uri, client_ip: '203.0.113.0', headers: { 'User-Agent': [ua] } } })

/** Records every statement; `failing` makes the next transaction fail. */
function fakePool() {
  const statements: { sql: string; params?: unknown[] }[] = []
  const state = { failing: false }
  const query = async (sql: string, params?: unknown[]) => {
    statements.push({ sql, params })
    if (state.failing && sql.includes('site_access_daily')) throw new Error('database is down')
    if (sql.includes('from site_visitor_salts')) return { rows: [] }
    return { rows: [] }
  }
  const pool = { query, connect: async () => ({ query, release: () => {} }) } as unknown as pg.Pool
  return { pool, statements, state }
}

let counter: Counter | null = null
afterEach(async () => {
  await counter?.stop()
  counter = null
})

describe('access counter', () => {
  it('counts datagrams from the cluster and ignores the rest', async () => {
    const db = fakePool()
    counter = await startCounter({ pool: db.pool, port: 0, host: '127.0.0.1', siteHost: 'niadra.com' })
    counter.receive(`${line('/preco')}\n${line('/preco')}\n`, '10.42.0.224')
    counter.receive(line('/preco'), '203.0.113.9')
    counter.receive('garbage', '10.42.0.224')
    expect(counter.aggregator.size).toBe(1)
    await counter.flushNow()
    const upsert = db.statements.find((s) => s.sql.includes('insert into site_access_daily'))!
    expect(upsert.params).toEqual([[expect.any(String)], ['/preco'], ['human'], [''], [2]])
    expect(db.statements.filter((s) => s.sql.includes('insert into site_access_daily'))).toHaveLength(1)
  })

  it('keeps the counts for the next minute when the database refuses them', async () => {
    const db = fakePool()
    counter = await startCounter({ pool: db.pool, port: 0, host: '127.0.0.1', siteHost: 'niadra.com' })
    counter.receive(line('/blog'), '10.42.0.1')
    db.state.failing = true
    await counter.flushNow()
    expect(counter.aggregator.size).toBe(1)
    db.state.failing = false
    counter.receive(line('/blog'), '10.42.0.1')
    await counter.flushNow()
    const upserts = db.statements.filter((s) => s.sql.includes('insert into site_access_daily'))
    expect(upserts.at(-1)!.params![4]).toEqual([2])
    expect(counter.aggregator.size).toBe(0)
  })
})
