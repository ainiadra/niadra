/**
 * The one account of the admin panel, read from the environment: ADMIN_EMAIL and ADMIN_PASSWORD_HASH, a
 * scrypt hash in the server's format (scrypt$N$r$p$salt$digest, base64url without padding). Neither the
 * e-mail nor the hash is committed; without both, the panel refuses every sign-in.
 */
export interface AdminAccount {
  email: string
  passwordHash: string
}

export function adminAccount(env: Record<string, string | undefined> = process.env): AdminAccount | null {
  const email = env.ADMIN_EMAIL?.trim().toLowerCase()
  const passwordHash = env.ADMIN_PASSWORD_HASH?.trim()
  return email && passwordHash ? { email, passwordHash } : null
}
