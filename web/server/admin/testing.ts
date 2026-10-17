/** Test helpers: a hash in the server's format for a password the tests choose. Not used by the API. */
import { randomBytes, scryptSync } from 'node:crypto'

export function hashPassword(password: string): string {
  const salt = randomBytes(16)
  const digest = scryptSync(password, salt, 32, { N: 2 ** 14, r: 8, p: 1 })
  return `scrypt$16384$8$1$${salt.toString('base64url')}$${digest.toString('base64url')}`
}
