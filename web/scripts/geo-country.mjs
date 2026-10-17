/**
 * Packs the geo-whois-asn-country dataset (sapics/ip-location-db on npm, public domain, CC0) into
 * dist-server/geo-country.bin, the table server/access/geo.ts reads to turn a masked address into a
 * country for the access report. Runs before the API bundle (npm run build:api); the Dockerfile copies
 * the file next to api.mjs. The format is described in server/access/geo.ts.
 *
 * Usage: node scripts/geo-country.mjs [out]
 */
import { createRequire } from 'node:module'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)
const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const OUT = process.argv[2] ?? join(ROOT, 'dist-server', 'geo-country.bin')
const DIR = dirname(require.resolve('@ip-location-db/geo-whois-asn-country/package.json'))

const codes = []
const codeIndex = new Map()
const codeOf = (cc) => {
  let i = codeIndex.get(cc)
  if (i === undefined) {
    i = codes.length
    codes.push(cc)
    codeIndex.set(cc, i)
  }
  return i
}

/** Reads "start,end,CC" lines; v6 numbers are 128-bit and only their upper 64 bits are kept. */
function read(file, big) {
  const rows = []
  for (const line of readFileSync(join(DIR, file), 'utf8').split('\n')) {
    if (!line) continue
    const [start, end, cc] = line.split(',')
    if (!/^[A-Z]{2}$/.test(cc)) continue
    rows.push(big ? [BigInt(start) >> 64n, BigInt(end) >> 64n, codeOf(cc)] : [Number(start), Number(end), codeOf(cc)])
  }
  rows.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))
  return rows
}

const v4 = read('geo-whois-asn-country-ipv4-num.csv', false)
const v6 = read('geo-whois-asn-country-ipv6-num.csv', true)
// Two v6 ranges inside the same upper /64 would overlap after the cut: keep the first, which is the
// larger allocation in practice, so the search stays well defined.
const v6unique = v6.filter((r, i) => i === 0 || r[0] > v6[i - 1][1])
if (codes.length > 65535) throw new Error('too many country codes')

const align = (n, to) => (n + to - 1) & ~(to - 1)
const headerEnd = 20 + codes.length * 2
const at4 = align(headerEnd, 8)
const at6 = align(at4 + v4.length * 10, 8)
const total = at6 + v6unique.length * 18
const buf = new ArrayBuffer(align(total, 8))
const view = new DataView(buf)
view.setUint32(0, 0x4f45474e, true)
view.setUint32(4, 1, true)
view.setUint32(8, v4.length, true)
view.setUint32(12, v6unique.length, true)
view.setUint32(16, codes.length, true)
codes.forEach((cc, i) => {
  view.setUint8(20 + i * 2, cc.charCodeAt(0))
  view.setUint8(21 + i * 2, cc.charCodeAt(1))
})
const starts4 = new Uint32Array(buf, at4, v4.length)
const ends4 = new Uint32Array(buf, at4 + v4.length * 4, v4.length)
const codes4 = new Uint16Array(buf, at4 + v4.length * 8, v4.length)
v4.forEach(([s, e, c], i) => {
  starts4[i] = s
  ends4[i] = e
  codes4[i] = c
})
const starts6 = new BigUint64Array(buf, at6, v6unique.length)
const ends6 = new BigUint64Array(buf, at6 + v6unique.length * 8, v6unique.length)
const codes6 = new Uint16Array(buf, at6 + v6unique.length * 16, v6unique.length)
v6unique.forEach(([s, e, c], i) => {
  starts6[i] = s
  ends6[i] = e
  codes6[i] = c
})

mkdirSync(dirname(OUT), { recursive: true })
writeFileSync(OUT, Buffer.from(buf))
console.log(`geo-country.bin: ${v4.length} IPv4 and ${v6unique.length} IPv6 ranges, ${codes.length} countries, ${(buf.byteLength / 1e6).toFixed(1)} MB`)
