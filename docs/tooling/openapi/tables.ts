/**
 * The tables the documentation adds to the contract, merged: English
 * descriptions and examples. The base (en.ts, examples.ts) came from the first draft of the
 * contract; each group of routes has its own files in extra/. The last one written wins.
 */
import { EN as BASE_EN } from './en'
import { EXAMPLES as BASE_EXAMPLES } from './examples'
import { EN as AGENT_CORE_EN } from './extra/agent-core.en'
import { EN as AGENT_MEMORY_EN } from './extra/agent-memory.en'
import { EN as CONTROL_EN } from './extra/control.en'
import { EXAMPLES as CONTROL_EX } from './extra/control.examples'
import { EN as DATA_EN } from './extra/data.en'
import { EXAMPLES as DATA_EX } from './extra/data.examples'
import { EN as GOVERNANCE_EN } from './extra/governance.en'
import { EXAMPLES as GOVERNANCE_EX } from './extra/governance.examples'
import type { OpExtra } from './model'

export const EN: Record<string, string> = Object.assign({}, BASE_EN, CONTROL_EN, DATA_EN, GOVERNANCE_EN, AGENT_MEMORY_EN, AGENT_CORE_EN)
export const EXAMPLES: Record<string, OpExtra> = Object.assign({}, BASE_EXAMPLES, CONTROL_EX, DATA_EX, GOVERNANCE_EX)
