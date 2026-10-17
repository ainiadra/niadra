/**
 * The access counter of the site API. Caddy, in the site pod, sends a copy of every access log entry
 * to this process over UDP (deploy/Caddyfile, the `counter` log); each datagram is one JSON entry, with
 * the address already masked. This classifies it, adds it to the minute's counters and forgets it.
 * Once a minute the counters go to Postgres in one transaction (store.ts).
 *
 * UDP because Caddy must never wait on the count: a datagram nobody reads is dropped, and a site API
 * that restarts loses at most the minute it held. Datagrams from outside the cluster's private
 * networks are ignored, and the port is not in any ingress.
 */
import { createSocket, type Socket } from 'node:dgram'
import type pg from 'pg'
import { Aggregator, parseLine, toEvent } from './aggregate'
import { loadGeo } from './geo'
import { compact, flush, loadSalts } from './store'

const INTERNAL = /^(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/
const FLUSH_MS = 60_000
const COMPACT_EVERY = 60 // flushes, so about once an hour

export interface Counter {
  aggregator: Aggregator
  /** Handles one datagram's text; exposed for the tests. */
  receive: (text: string, from: string) => void
  flushNow: () => Promise<void>
  stop: () => Promise<void>
}

export async function startCounter({ pool, port, host = '0.0.0.0', siteHost }: { pool: pg.Pool; port: number; host?: string; siteHost: string }): Promise<Counter> {
  const aggregator = new Aggregator()
  let salts = await loadSalts(pool)
  let flushes = 0
  // The country table (geo-country.bin next to api.mjs). Without it the report simply has no country.
  const geo = loadGeo()
  console.log(geo ? `access count: country table with ${geo.size} ranges` : 'access count: no country table (geo-country.bin), countries not counted')
  const country = geo ? geo.countryOf : () => null

  const receive = (text: string, from: string) => {
    if (!INTERNAL.test(from)) return
    for (const line of text.split('\n')) {
      if (!line.trim()) continue
      const parsed = parseLine(line)
      if (!parsed) continue
      const event = toEvent(parsed, siteHost, (day) => salts.get(day), country)
      if (event) aggregator.add(event)
    }
  }

  let running: Promise<void> | null = null
  const flushNow = () => {
    running ??= (async () => {
      const batch = aggregator.drain()
      try {
        await flush(pool, batch)
      } catch (err) {
        aggregator.restore(batch)
        console.error('access count: flush failed, kept for the next minute:', (err as Error).message)
        return
      }
      try {
        salts = await loadSalts(pool)
        if (++flushes % COMPACT_EVERY === 1) await compact(pool)
      } catch (err) {
        console.error('access count: salts or compaction failed:', (err as Error).message)
      }
    })().finally(() => {
      running = null
    })
    return running
  }

  const socket: Socket = createSocket('udp4')
  socket.on('message', (msg, rinfo) => receive(msg.toString('utf8'), rinfo.address))
  socket.on('error', (err) => console.error('access count: socket error:', err.message))
  await new Promise<void>((resolve) => socket.bind(port, host, resolve))
  const timer = setInterval(() => void flushNow(), FLUSH_MS)
  timer.unref()
  console.log(`access count listening on udp ${port} for ${siteHost}`)

  return {
    aggregator,
    receive,
    flushNow,
    stop: async () => {
      clearInterval(timer)
      socket.close()
      await flushNow()
    },
  }
}
