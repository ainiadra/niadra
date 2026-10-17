import examples from '../content/pack-example.json'

/**
 * The context the voice agent receives in the demonstration: the text the server's compiler
 * produced for a synthetic customer (docs/tooling/pack in the monorepo), copied by
 * scripts/pack-example.mjs and shown as it is, line by line. Nothing here is written by hand.
 */
type PackLang = 'pt' | 'en'

/** The lines that came from the history, from other channels or from another agent, which the demonstration highlights. */
const HOT: Record<PackLang, string[]> = {
  pt: ['Histórico:', 'Conversa:', 'Outro agente:', 'Pendências:'],
  en: ['History:', 'Conversation:', 'Another agent:', 'Pending:'],
}

const packText = (lang: PackLang): string => examples.packs[lang].voice.text

export const packLines = (lang: PackLang): { t: string; hot: boolean }[] =>
  packText(lang)
    .split('\n')
    .map((t) => ({ t, hot: HOT[lang].some((prefix) => t.startsWith(prefix)) }))

export const packTokens = (lang: PackLang): number => examples.packs[lang].voice.tokens

/**
 * The lines about the customer: everything between the `<niadra>` tag and its closing tag, minus the
 * opening sentence that says the block is data and not instructions.
 */
const customerLines = (lang: PackLang): string[] => {
  const lines = packText(lang).split('\n')
  const open = lines.findIndex((l) => l.startsWith('<niadra'))
  const close = lines.findIndex((l) => l.startsWith('</niadra>'))
  return open >= 0 && close > open + 1 ? lines.slice(open + 2, close) : []
}

/** How many lines say something about the customer. */
export const packLineCount = (lang: PackLang): number => customerLines(lang).length

/** How many of those lines came from outside the call (the highlighted ones). */
export const packHotCount = (lang: PackLang): number =>
  customerLines(lang).filter((t) => HOT[lang].some((prefix) => t.startsWith(prefix))).length

const WORDS: Record<PackLang, string[]> = {
  pt: ['zero', 'uma', 'duas', 'três', 'quatro', 'cinco', 'seis', 'sete', 'oito', 'nove', 'dez', 'onze', 'doze'],
  en: ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve'],
}

/** A small count as a word, so the demo sentence reads as prose and stays true to the pack. */
export const countWord = (lang: PackLang, n: number): string => WORDS[lang][n] ?? String(n)
