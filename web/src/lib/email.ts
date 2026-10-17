/**
 * Work e-mail: the form is for companies. Personal webmail and disposable inboxes
 * stay out, in the browser (warning right away) and in the API (which also
 * checks that the domain exists and receives e-mail). Shared module: esbuild
 * takes the same file into server/api.ts.
 */

/** The most used personal e-mail providers, worldwide and in Brazil. */
const FREE = [
  'gmail.com', 'googlemail.com', 'hotmail.com', 'hotmail.com.br', 'hotmail.co.uk', 'hotmail.fr', 'hotmail.es', 'hotmail.it',
  'outlook.com', 'outlook.com.br', 'outlook.pt', 'live.com', 'live.com.br', 'live.com.pt', 'msn.com', 'passport.com',
  'yahoo.com', 'yahoo.com.br', 'yahoo.co.uk', 'yahoo.fr', 'yahoo.es', 'yahoo.com.ar', 'yahoo.com.mx', 'ymail.com', 'rocketmail.com',
  'icloud.com', 'me.com', 'mac.com', 'privaterelay.appleid.com', 'aol.com', 'aim.com',
  'protonmail.com', 'protonmail.ch', 'proton.me', 'pm.me', 'tutanota.com', 'tutanota.de', 'tuta.io', 'tuta.com',
  'gmx.com', 'gmx.net', 'gmx.de', 'gmx.at', 'web.de', 'mail.com', 'email.com', 'usa.com', 'zoho.com', 'zohomail.com',
  'yandex.com', 'yandex.ru', 'ya.ru', 'mail.ru', 'bk.ru', 'inbox.ru', 'list.ru', 'rambler.ru',
  'fastmail.com', 'fastmail.fm', 'hey.com', 'duck.com', 'hushmail.com', 'mailfence.com', 'posteo.de', 'runbox.com',
  'qq.com', '163.com', '126.com', 'sina.com', 'sohu.com', 'naver.com', 'daum.net', 'hanmail.net',
  'orange.fr', 'free.fr', 'laposte.net', 'sfr.fr', 'wanadoo.fr', 'libero.it', 'virgilio.it', 'tiscali.it', 'alice.it',
  'btinternet.com', 'sky.com', 'comcast.net', 'verizon.net', 'att.net', 'sbcglobal.net', 'cox.net', 'charter.net',
  'rediffmail.com', 'inbox.com', 'sapo.pt', 'clix.pt', 'bol.com.br', 'uol.com.br', 'terra.com.br', 'ig.com.br',
  'globo.com', 'globomail.com', 'r7.com', 'oi.com.br', 'zipmail.com.br', 'pop.com.br', 'superig.com.br', 'click21.com.br',
]

/** Disposable inboxes (they last minutes): never a buyer's. */
const DISPOSABLE = [
  'mailinator.com', 'guerrillamail.com', 'guerrillamail.info', 'guerrillamail.net', 'sharklasers.com', 'grr.la',
  '10minutemail.com', '10minutemail.net', 'temp-mail.org', 'tempmail.com', 'tempmail.net', 'tempmailo.com', 'tempail.com',
  'yopmail.com', 'yopmail.net', 'trashmail.com', 'trashmail.de', 'getnada.com', 'nada.email', 'dispostable.com',
  'maildrop.cc', 'mohmal.com', 'throwawaymail.com', 'fakeinbox.com', 'emailondeck.com', 'mintemail.com', 'mytemp.email',
  'burnermail.io', 'spamgourmet.com', 'moakt.com', 'mailnesia.com', 'mailcatch.com', 'tempinbox.com', 'discard.email',
  'emailfake.com', 'fakemail.net', 'minuteinbox.com', 'luxusmail.org', 'mailpoof.com', 'tmpmail.org', 'tmpmail.net',
]

const FREE_SET = new Set(FREE)
const DISPOSABLE_SET = new Set(DISPOSABLE)

export const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export function emailDomain(email: string): string {
  return email.trim().toLowerCase().split('@').pop() ?? ''
}

/** 'free' = personal webmail, 'disposable' = disposable inbox, null = may be a work address. */
export function personalKind(email: string): 'free' | 'disposable' | null {
  const d = emailDomain(email)
  if (FREE_SET.has(d)) return 'free'
  if (DISPOSABLE_SET.has(d)) return 'disposable'
  return null
}
