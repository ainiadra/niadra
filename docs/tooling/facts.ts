/**
 * The product facts the pages repeat, kept in one place; `docs.test.ts` checks every page and `llms.txt`
 * against them.
 */

/**
 * The SDK versions published on PyPI and npm. A release bumps them here, in the same change that bumps the
 * SDK, and the test finds every page and `llms.txt` line that names the current version.
 */
export const SDK_VERSIONS = { python: '0.11.7', typescript: '0.11.7' } as const

/**
 * The tools of the space's MCP server, as the server lists them (adapters/inbound/http/mcp.py and
 * domain/serve/tools.py). A customer-facing source does not get `resolve_identity`; the agent memory tools
 * come with agent memory on, and `get_constraints` with the subject signals.
 */
export const MCP_TOOLS = {
  customer: [
    'get_customer_context',
    'search_customer_history',
    'get_customer_timeline',
    'open_history_item',
    'track_event',
    'record_action',
    'resolve_identity',
    'correct_customer_memory',
  ],
  notForCustomerFacing: ['resolve_identity'],
  agentMemory: ['search_agent_memory', 'remember'],
  signals: ['get_constraints'],
  /** The history tools the SDKs hand a framework as function definitions. */
  history: ['search_customer_history', 'get_customer_timeline', 'open_history_item'],
} as const
