/** What every route of the site API shares: the JSON answer, the body reader and the client's address. */
import type { IncomingMessage, OutgoingHttpHeaders, ServerResponse } from 'node:http'

export function send(res: ServerResponse, status: number, body: object, headers: OutgoingHttpHeaders = {}) {
  const data = JSON.stringify(body)
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(data),
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    ...headers,
  })
  res.end(data)
}

/** The body as text, up to `max` bytes; past it, rejects with `too_large` and lets the rest drain. */
export function readBody(req: IncomingMessage, max: number): Promise<string> {
  return new Promise((resolve, reject) => {
    let size = 0
    const chunks: Buffer[] = []
    req.on('data', (c: Buffer) => {
      size += c.length
      // Too large: stops keeping it, but lets the rest drain so the 413 response gets through.
      if (size > max) reject(new Error('too_large'))
      else chunks.push(c)
    })
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')))
    req.on('error', reject)
  })
}

/**
 * The ingress puts the real IP at the end of X-Forwarded-For. We trust it only when
 * the connection comes from an internal address (the ingress, or the computer
 * itself in development); from outside, the connection's address counts.
 */
const INTERNAL = /^(::ffff:)?(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)|^::1$/
export function clientIp(req: IncomingMessage): string {
  const remote = req.socket.remoteAddress ?? ''
  const fwd = req.headers['x-forwarded-for']
  if (INTERNAL.test(remote) && typeof fwd === 'string') {
    return fwd.split(',').pop()!.trim()
  }
  return remote
}
