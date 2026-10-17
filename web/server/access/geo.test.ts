// The country lookup: a packed table in, a two-letter code per masked address out, nothing stored.
import { describe, expect, it } from 'vitest'
import { geoFrom, ipv4ToNumber, ipv6ToUpper64 } from './geo'

/** A tiny table in the format scripts/geo-country.mjs writes: three IPv4 ranges and two IPv6 ones. */
function table() {
  const codes = ['AE', 'BR', 'US']
  const v4: [number, number, number][] = [
    [ipv4ToNumber('5.30.0.0')!, ipv4ToNumber('5.30.255.255')!, 0],
    [ipv4ToNumber('177.0.0.0')!, ipv4ToNumber('177.255.255.255')!, 1],
    [ipv4ToNumber('203.0.113.0')!, ipv4ToNumber('203.0.113.255')!, 2],
  ]
  const v6: [bigint, bigint, number][] = [
    [ipv6ToUpper64('2001:8f8::')!, ipv6ToUpper64('2001:8f8:ffff:ffff::')!, 0],
    [ipv6ToUpper64('2804::')!, ipv6ToUpper64('2804:ffff:ffff:ffff::')!, 1],
  ]
  const align = (n: number, to: number) => (n + to - 1) & ~(to - 1)
  const at4 = align(20 + codes.length * 2, 8)
  const at6 = align(at4 + v4.length * 10, 8)
  const buf = new ArrayBuffer(align(at6 + v6.length * 18, 8))
  const view = new DataView(buf)
  view.setUint32(0, 0x4f45474e, true)
  view.setUint32(4, 1, true)
  view.setUint32(8, v4.length, true)
  view.setUint32(12, v6.length, true)
  view.setUint32(16, codes.length, true)
  codes.forEach((cc, i) => {
    view.setUint8(20 + i * 2, cc.charCodeAt(0))
    view.setUint8(21 + i * 2, cc.charCodeAt(1))
  })
  const s4 = new Uint32Array(buf, at4, v4.length)
  const e4 = new Uint32Array(buf, at4 + v4.length * 4, v4.length)
  const c4 = new Uint16Array(buf, at4 + v4.length * 8, v4.length)
  v4.forEach(([s, e, c], i) => ((s4[i] = s), (e4[i] = e), (c4[i] = c)))
  const s6 = new BigUint64Array(buf, at6, v6.length)
  const e6 = new BigUint64Array(buf, at6 + v6.length * 8, v6.length)
  const c6 = new Uint16Array(buf, at6 + v6.length * 16, v6.length)
  v6.forEach(([s, e, c], i) => ((s6[i] = s), (e6[i] = e), (c6[i] = c)))
  return new Uint8Array(buf)
}

describe('country of a masked address', () => {
  const geo = geoFrom(table())

  it('finds the country of a masked IPv4 address, and nothing outside the table', () => {
    expect(geo.countryOf('5.30.12.0')).toBe('AE')
    expect(geo.countryOf('177.45.0.0')).toBe('BR')
    expect(geo.countryOf('203.0.113.0')).toBe('US')
    expect(geo.countryOf('203.0.114.0')).toBeNull()
    expect(geo.countryOf('10.0.0.0')).toBeNull()
    expect(geo.countryOf('not an address')).toBeNull()
  })

  it('finds the country of a masked IPv6 address by its upper 64 bits', () => {
    expect(geo.countryOf('2001:8f8:1234::')).toBe('AE')
    expect(geo.countryOf('2804:14d::')).toBe('BR')
    expect(geo.countryOf('2a02::')).toBeNull()
  })

  it('parses addresses the way Caddy masks them', () => {
    expect(ipv4ToNumber('203.0.113.0')).toBe(3405803776)
    expect(ipv4ToNumber('256.0.0.0')).toBeNull()
    expect(ipv6ToUpper64('2001:db8::')).toBe((0x2001n << 48n) | (0xdb8n << 32n))
    expect(ipv6ToUpper64('::ffff:203.0.113.0')).toBe(0n)
    expect(ipv6ToUpper64('fe80::1%eth0')).toBe(0xfe80n << 48n)
    expect(ipv6ToUpper64('1:2:3:4:5:6:7:8:9')).toBeNull()
  })

  it('refuses a file of another format', () => {
    expect(() => geoFrom(new Uint8Array(8))).toThrow(/version 1/)
  })
})
