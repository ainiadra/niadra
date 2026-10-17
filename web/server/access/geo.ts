/**
 * The country of an address, for the access report: which countries the site's people come from, per
 * day, so the founder sees whether a campaign aimed at one place brought anyone. Nothing per request is
 * kept, and no address is stored: Caddy masks the address before it reaches the counter (/24 for IPv4,
 * /32 for IPv6, deploy/Caddyfile), the counter turns the masked address into a two-letter country code
 * and adds one to that day's count (aggregate.ts). A /24 or a /32 is enough for a country.
 *
 * The table is the geo-whois-asn-country dataset (sapics/ip-location-db, public domain, CC0), packed by
 * scripts/geo-country.mjs into geo-country.bin next to api.mjs at build time. Without the file the
 * lookup answers null and the report has no country, nothing else changes.
 *
 * File format (little-endian): "NGEO", version 1, n4, n6, nCodes, then nCodes two-letter codes, then,
 * each 8-byte aligned: n4 starts (u32), n4 ends (u32), n4 codes (u16); n6 starts (u64, the upper 64
 * bits), n6 ends (u64), n6 codes (u16). Ranges are sorted by start and do not overlap.
 */
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

export interface Geo {
  countryOf: (ip: string) => string | null
  size: number
}

const MAGIC = 0x4f45474e // "NGEO" as a little-endian u32

const align = (n: number, to: number) => (n + to - 1) & ~(to - 1)

/** Builds the lookup from the packed table. Throws on a file of another format. */
export function geoFrom(data: Uint8Array): Geo {
  const buf = data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength) as ArrayBuffer
  const view = new DataView(buf)
  if (view.byteLength < 20 || view.getUint32(0, true) !== MAGIC || view.getUint32(4, true) !== 1) throw new Error('geo-country.bin: not a version 1 table')
  const n4 = view.getUint32(8, true)
  const n6 = view.getUint32(12, true)
  const nCodes = view.getUint32(16, true)
  let at = 20
  const codes: string[] = []
  for (let i = 0; i < nCodes; i++, at += 2) codes.push(String.fromCharCode(view.getUint8(at), view.getUint8(at + 1)))
  at = align(at, 8)
  const starts4 = new Uint32Array(buf, at, n4)
  at += n4 * 4
  const ends4 = new Uint32Array(buf, at, n4)
  at += n4 * 4
  const codes4 = new Uint16Array(buf, at, n4)
  at = align(at + n4 * 2, 8)
  const starts6 = new BigUint64Array(buf, at, n6)
  at += n6 * 8
  const ends6 = new BigUint64Array(buf, at, n6)
  at += n6 * 8
  const codes6 = new Uint16Array(buf, at, n6)

  const find4 = (n: number): string | null => {
    let lo = 0
    let hi = n4 - 1
    while (lo <= hi) {
      const mid = (lo + hi) >>> 1
      if (starts4[mid] > n) hi = mid - 1
      else if (ends4[mid] < n) lo = mid + 1
      else return codes[codes4[mid]] ?? null
    }
    return null
  }
  const find6 = (n: bigint): string | null => {
    let lo = 0
    let hi = n6 - 1
    while (lo <= hi) {
      const mid = (lo + hi) >>> 1
      if (starts6[mid] > n) hi = mid - 1
      else if (ends6[mid] < n) lo = mid + 1
      else return codes[codes6[mid]] ?? null
    }
    return null
  }
  return {
    size: n4 + n6,
    countryOf: (ip) => {
      const v4 = ipv4ToNumber(ip)
      if (v4 !== null) return find4(v4)
      const v6 = ipv6ToUpper64(ip)
      return v6 === null ? null : find6(v6)
    },
  }
}

/** "203.0.113.0" as a number, or null when it is not a dotted IPv4 address. */
export function ipv4ToNumber(ip: string): number | null {
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(ip)
  if (!m) return null
  let n = 0
  for (let i = 1; i <= 4; i++) {
    const part = Number(m[i])
    if (part > 255) return null
    n = n * 256 + part
  }
  return n
}

/** The upper 64 bits of an IPv6 address as a bigint, or null when it is not one. Handles "::" and a zone suffix. */
export function ipv6ToUpper64(ip: string): bigint | null {
  const addr = ip.split('%')[0]
  if (!addr.includes(':') || addr.length > 45) return null
  const halves = addr.split('::')
  if (halves.length > 2) return null
  const head = halves[0] ? halves[0].split(':') : []
  const tail = halves.length === 2 && halves[1] ? halves[1].split(':') : []
  // An embedded IPv4 tail ("::ffff:203.0.113.0") lives in the lower 64 bits: it never changes the upper half.
  const lastTail = tail[tail.length - 1]
  if (lastTail && lastTail.includes('.')) tail.splice(tail.length - 1, 1, '0', '0')
  const lastHead = head[head.length - 1]
  if (lastHead && lastHead.includes('.')) head.splice(head.length - 1, 1, '0', '0')
  if (head.length + tail.length > 8 || (halves.length === 1 && head.length !== 8)) return null
  const groups = [...head, ...Array(8 - head.length - tail.length).fill('0'), ...tail]
  let n = 0n
  for (let i = 0; i < 4; i++) {
    if (!/^[0-9a-fA-F]{1,4}$/.test(groups[i])) return null
    n = (n << 16n) | BigInt(parseInt(groups[i], 16))
  }
  return n
}

/**
 * Loads the packed table: GEO_COUNTRY_FILE, else geo-country.bin next to this module (api.mjs in the
 * image), else dist-server/geo-country.bin from the working directory (development). Null when none
 * exists, and the report has no country.
 */
export function loadGeo(): Geo | null {
  const here = dirname(fileURLToPath(import.meta.url))
  const candidates = [process.env.GEO_COUNTRY_FILE, join(here, 'geo-country.bin'), join(process.cwd(), 'dist-server', 'geo-country.bin')].filter((f): f is string => Boolean(f))
  for (const file of candidates) {
    if (!existsSync(file)) continue
    try {
      return geoFrom(readFileSync(file))
    } catch (err) {
      console.error(`access count: ${file} unusable:`, (err as Error).message)
      return null
    }
  }
  return null
}
