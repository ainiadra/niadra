/**
 * Who asked for a page of the site: a person, an AI crawler, a search engine, a scanner or some other
 * robot. The site API counts every request Caddy logs (server/access/intake.ts) under one of these
 * classes, so the admin panel can say how much of the traffic is people and which AIs read the site.
 *
 * The tables are the classifier: a user agent is matched by a case-insensitive substring, first match
 * wins, and the table order matters where one name contains another. Requests to paths only a scanner
 * asks for (/.env, /wp-admin, /.git) are scanners whatever the user agent claims, because scanners
 * send browser and crawler user agents. The tests (classify.test.ts) pin each row.
 */

export type AccessClass = 'human' | 'ai' | 'search' | 'scanner' | 'bot'
export const CLASSES: AccessClass[] = ['human', 'ai', 'search', 'scanner', 'bot']

/** Why an AI crawler fetched the page: to train models, to index for answers, or for a person in a chat. */
type Purpose = 'training' | 'search' | 'user'

export interface Crawler {
  /** The name as the panel shows it, and the substring the user agent carries. */
  name: string
  company: string
  purpose?: Purpose
}

/** AI crawlers and fetchers. `-User` agents fetch a page because a person asked the assistant. */
export const AI_CRAWLERS: Crawler[] = [
  { name: 'GPTBot', company: 'OpenAI', purpose: 'training' },
  { name: 'OAI-SearchBot', company: 'OpenAI', purpose: 'search' },
  { name: 'ChatGPT-User', company: 'OpenAI', purpose: 'user' },
  { name: 'ClaudeBot', company: 'Anthropic', purpose: 'training' },
  { name: 'Claude-SearchBot', company: 'Anthropic', purpose: 'search' },
  { name: 'Claude-User', company: 'Anthropic', purpose: 'user' },
  { name: 'anthropic-ai', company: 'Anthropic', purpose: 'training' },
  { name: 'PerplexityBot', company: 'Perplexity', purpose: 'search' },
  { name: 'Perplexity-User', company: 'Perplexity', purpose: 'user' },
  // Google-Extended and Applebot-Extended are robots.txt tokens; they are here in case a crawler sends them.
  { name: 'Google-Extended', company: 'Google', purpose: 'training' },
  { name: 'Google-CloudVertexBot', company: 'Google', purpose: 'user' },
  { name: 'GoogleOther', company: 'Google', purpose: 'training' },
  { name: 'Gemini-Deep-Research', company: 'Google', purpose: 'user' },
  { name: 'Applebot-Extended', company: 'Apple', purpose: 'training' },
  { name: 'Bytespider', company: 'ByteDance', purpose: 'training' },
  { name: 'CCBot', company: 'Common Crawl', purpose: 'training' },
  { name: 'meta-externalagent', company: 'Meta', purpose: 'training' },
  { name: 'meta-externalfetcher', company: 'Meta', purpose: 'user' },
  { name: 'Amazonbot', company: 'Amazon', purpose: 'search' },
  { name: 'cohere-training-data-crawler', company: 'Cohere', purpose: 'training' },
  { name: 'cohere-ai', company: 'Cohere', purpose: 'user' },
  { name: 'DuckAssistBot', company: 'DuckDuckGo', purpose: 'search' },
  { name: 'MistralAI-User', company: 'Mistral', purpose: 'user' },
  { name: 'YouBot', company: 'You.com', purpose: 'search' },
  { name: 'Diffbot', company: 'Diffbot', purpose: 'training' },
  { name: 'AI2Bot', company: 'Ai2', purpose: 'training' },
  { name: 'Timpibot', company: 'Timpi', purpose: 'training' },
  { name: 'ImagesiftBot', company: 'Hive', purpose: 'training' },
  { name: 'PanguBot', company: 'Huawei', purpose: 'training' },
  { name: 'Kangaroo Bot', company: 'Kangaroo LLM', purpose: 'training' },
  { name: 'omgili', company: 'Webz.io', purpose: 'training' },
]

/** Search engines. After the AI table, so Applebot-Extended and DuckAssistBot never land here. */
const SEARCH_ENGINES: Crawler[] = [
  { name: 'Googlebot', company: 'Google' },
  { name: 'Google-InspectionTool', company: 'Google' },
  { name: 'Storebot-Google', company: 'Google' },
  { name: 'AdsBot-Google', company: 'Google' },
  { name: 'bingbot', company: 'Microsoft' },
  { name: 'BingPreview', company: 'Microsoft' },
  { name: 'Applebot', company: 'Apple' },
  { name: 'DuckDuckBot', company: 'DuckDuckGo' },
  { name: 'YandexBot', company: 'Yandex' },
  { name: 'Baiduspider', company: 'Baidu' },
  { name: 'PetalBot', company: 'Huawei' },
  { name: 'Qwantbot', company: 'Qwant' },
  { name: 'SeznamBot', company: 'Seznam' },
  { name: 'Yeti', company: 'Naver' },
  { name: 'MojeekBot', company: 'Mojeek' },
  { name: 'Sogou', company: 'Sogou' },
]

/** Scanning tools that say who they are. */
const SCANNER_AGENTS = [
  'zgrab', 'masscan', 'Nmap', 'Nuclei', 'sqlmap', 'Nikto', 'WPScan', 'CensysInspect', 'Expanse',
  'l9explore', 'l9tcpid', 'ModatScanner', 'Palo Alto Networks', 'InternetMeasurement', 'Odin',
  'NetSystemsResearch', 'Keydrop', 'ips-agent',
]

/**
 * Paths nobody but a scanner asks for on this site: dotfiles (except /.well-known), PHP and the other
 * server-side extensions the site never serves, CMS and admin consoles, backups and dumps.
 */
const SCANNER_PATHS: RegExp[] = [
  /(^|\/)\.(?!well-known(\/|$))[^/]/,
  /\.(php\d?|phtml|asp|aspx|jsp|jspa|do|action|cgi|pl|env|ini|bak|old|orig|save|swp|sql|db|sqlite|log|conf|cfg|yml|yaml|toml|tar|gz|tgz|zip|rar|7z)$/i,
  /^\/(wp|wordpress|wp-admin|wp-login|wp-content|wp-includes|wp-json|xmlrpc)(\/|$|\.)/i,
  /\/wp-(admin|login|content|includes|json)(\/|\.|$)/i,
  /^\/(phpmyadmin|pma|myadmin|mysql|adminer|cgi-bin|actuator|server-status|server-info|vendor|boaform|hnap1|owa|ecp|autodiscover|solr|jenkins|manager|telescope|_ignition|_profiler|debug|admin|administrator|login|user\/login|remote|geoserver|cf_scripts|hudson|webui|global-protect|sslvpn|dana-na|evox|portal|mgmt|cpanel|shell|bin|etc|proc)(\/|$)/i,
]

/** Other robots: link previews, SEO tools, HTTP libraries, headless browsers, research crawlers. */
const OTHER_BOT = /(?<!cu)bot\b|bot\/|crawl|spider|slurp|fetch|preview|headless|python|curl|wget|go-http|okhttp|axios|node|java\/|libwww|httpx|aiohttp|scrapy|pip\/|facebookexternalhit|whatsapp|telegram|discord|slack|twitter|linkedin|embedly|research|scan|monitor|uptime|check|lighthouse|pagespeed|gtmetrix|harvest|feed|rss|http-client/i
/** A browser user agent names its engine and version. `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36` does not. */
const BROWSER = /^Mozilla\/5\.0 \(.+\) .*(Chrome|CriOS|Firefox|FxiOS|Safari|Edg|OPR|SamsungBrowser)\/\d/

/**
 * Requests that are not traffic: the kubelet's probe, our own health watch (curl from the operator's
 * laptop, on / and /healthz), static files and the Console's runtime config. They are not counted at
 * all. A curl that asks for /.env is still a scanner: the scanner paths are checked first.
 */
const ASSET = /^\/(assets\/|favicon|apple-touch-icon|config\.json$|benchmark\/logos\/)|\.(js|mjs|css|map|png|jpe?g|gif|webp|avif|svg|ico|woff2?|ttf|otf|webmanifest)$/i
const MONITOR_AGENTS = /^(kube-probe\/|curl\/)/

export interface Request {
  path: string
  ua: string
}

export type Verdict = { counted: false; reason: 'monitor' | 'asset' } | { counted: true; cls: AccessClass; agent: string }

const find = (table: Crawler[], ua: string) => {
  const lower = ua.toLowerCase()
  return table.find((c) => lower.includes(c.name.toLowerCase()))
}

export function classify({ path, ua }: Request): Verdict {
  if (path === '/healthz') return { counted: false, reason: 'monitor' }
  if (SCANNER_PATHS.some((re) => re.test(path))) return { counted: true, cls: 'scanner', agent: scannerName(ua) }
  if (MONITOR_AGENTS.test(ua)) return { counted: false, reason: 'monitor' }
  if (ASSET.test(path)) return { counted: false, reason: 'asset' }
  const scanner = scannerName(ua)
  if (scanner) return { counted: true, cls: 'scanner', agent: scanner }
  // A user agent that is an address (http://site/wp-admin/install.php) comes from a scanning script.
  if (/^https?:\/\//i.test(ua)) return { counted: true, cls: 'scanner', agent: '' }
  const ai = find(AI_CRAWLERS, ua)
  if (ai) return { counted: true, cls: 'ai', agent: ai.name }
  const search = find(SEARCH_ENGINES, ua)
  if (search) return { counted: true, cls: 'search', agent: search.name }
  if (!ua || OTHER_BOT.test(ua) || !BROWSER.test(ua)) return { counted: true, cls: 'bot', agent: '' }
  return { counted: true, cls: 'human', agent: '' }
}

function scannerName(ua: string): string {
  const lower = ua.toLowerCase()
  return SCANNER_AGENTS.find((s) => lower.includes(s.toLowerCase())) ?? ''
}

/** The company and purpose the panel shows beside an AI crawler's name. */
export function crawlerInfo(name: string): Crawler | undefined {
  return AI_CRAWLERS.find((c) => c.name === name) ?? SEARCH_ENGINES.find((c) => c.name === name)
}

const MAX_PATH = 160

/** The path as stored: no query string or fragment, at most 160 characters. */
export function normalizePath(uri: string): string {
  const path = uri.split(/[?#]/, 1)[0] || '/'
  return path.length > MAX_PATH ? path.slice(0, MAX_PATH) : path
}
