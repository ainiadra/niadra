/**
 * Runtime config, written by the deploy next to index.html and served at
 * /config.json. Nothing about hosts is compiled into the bundle.
 *
 *   {
 *     "controlURL": "https://control.<ip>.sslip.io",
 *     "apiURL": "https://api.<ip>.sslip.io",
 *     "cells": { "<cell_id or region>": "https://..." }
 *   }
 *
 * `controlURL` is required. The data API of a space is `cells[space.cell_id]`,
 * then `cells[space.region]`, then `apiURL`; at least one of `apiURL` or
 * `cells` must exist. Customer data goes from that API to the browser and the
 * control plane never sees it.
 */
export interface ConsoleConfig {
  controlURL: string
  apiURL: string | null
  cells: Record<string, string>
}

export const CONFIG_PATH = '/config.json'

function isAllowedURL(v: unknown): v is string {
  if (typeof v !== 'string') return false
  try {
    const u = new URL(v)
    if (u.protocol === 'https:') return true
    // Plain HTTP only for a backend on the same machine during development.
    return u.protocol === 'http:' && (u.hostname === 'localhost' || u.hostname === '127.0.0.1')
  } catch {
    return false
  }
}

export class ConfigError extends Error {}

const trim = (u: string) => u.replace(/\/+$/, '')

export function parseConfig(raw: unknown): ConsoleConfig {
  if (!raw || typeof raw !== 'object') throw new ConfigError('o arquivo não é um objeto JSON')
  const r = raw as Record<string, unknown>
  if (!isAllowedURL(r.controlURL)) throw new ConfigError('controlURL ausente ou sem https')
  let apiURL: string | null = null
  if (r.apiURL !== undefined && r.apiURL !== null) {
    if (!isAllowedURL(r.apiURL)) throw new ConfigError('apiURL sem https')
    apiURL = trim(r.apiURL)
  }
  const cells: Record<string, string> = {}
  if (r.cells !== undefined && r.cells !== null) {
    if (typeof r.cells !== 'object') throw new ConfigError('cells precisa ser um objeto')
    for (const [key, url] of Object.entries(r.cells as Record<string, unknown>)) {
      if (!isAllowedURL(url)) throw new ConfigError(`a célula ${key} está sem https`)
      cells[key] = trim(url)
    }
  }
  if (!apiURL && Object.keys(cells).length === 0) throw new ConfigError('falta apiURL ou cells')
  return { controlURL: trim(r.controlURL), apiURL, cells }
}

/** The data API that serves a space, or null when the config has no address for it. */
export function dataURLFor(config: ConsoleConfig, space: { cell_id: string; region: string }): string | null {
  return config.cells[space.cell_id] ?? config.cells[space.region] ?? config.apiURL
}

export async function loadConfig(signal?: AbortSignal): Promise<ConsoleConfig> {
  let res: Response
  try {
    res = await fetch(CONFIG_PATH, { cache: 'no-store', credentials: 'same-origin', signal })
  } catch (e) {
    if (signal?.aborted) throw e
    throw new ConfigError('não foi possível baixar o arquivo')
  }
  if (!res.ok) throw new ConfigError(`o servidor respondeu ${res.status}`)
  let raw: unknown
  try {
    raw = await res.json()
  } catch {
    throw new ConfigError('o arquivo não é JSON válido')
  }
  return parseConfig(raw)
}
