/**
 * The contacts as a spreadsheet: semicolons (what Excel in Portuguese opens as columns), a byte order
 * mark so accents open right, every cell quoted. A cell that starts like a formula (=, +, -, @) gets a
 * leading apostrophe: a message typed into the site's form must never run as a formula on the laptop.
 */
import type { Contact } from './api'
import { FORMS, HEARD, INTERESTS, sourceName } from './labels'

const HEADER = ['Data', 'Nome', 'Empresa', 'E-mail', 'Interesse', 'Mensagem', 'Idioma', 'Domínio recebe e-mail', 'Origem', 'Página de entrada', 'Página do formulário', 'Como conheceu', 'Formulário']

export function csvCell(value: string | number | boolean | null | undefined): string {
  let s = value === null || value === undefined ? '' : String(value)
  if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`
  return `"${s.replace(/"/g, '""')}"`
}

export function contactsCsv(rows: Contact[], dateOf: (iso: string) => string): string {
  const lines = [HEADER, ...rows.map((c) => [
    dateOf(c.created_at),
    c.name,
    c.company,
    c.email,
    INTERESTS[c.interest] ?? c.interest,
    c.message,
    c.lang === 'en' ? 'Inglês' : 'Português',
    c.domain_has_mx === null ? 'Não verificado' : c.domain_has_mx ? 'Sim' : 'Não',
    sourceName(c.source),
    c.landing_page,
    c.page,
    c.heard_from ? (HEARD[c.heard_from] ?? c.heard_from) : '',
    FORMS[c.form] ?? c.form,
  ])]
  return `﻿${lines.map((l) => l.map(csvCell).join(';')).join('\r\n')}\r\n`
}

/** Saves the text as a file through the browser's download, never through a new window. */
export function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
