// The classifier table, row by row: what counts as a person, an AI, a search engine, a scanner or
// another robot, and what is not counted at all. User agents are the real ones seen in the site's log.
import { describe, expect, it } from 'vitest'
import { AI_CRAWLERS, classify, normalizePath } from './classify'

const CHROME = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36'
const IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 26_6_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/154.0.8037.55 Mobile/15E148 Safari/604.1'
const FIREFOX = 'Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0'
const at = (path: string, ua: string) => classify({ path, ua })

describe('access classifier', () => {
  it.each([
    ['/', CHROME],
    ['/blog/memoria-omnichannel', IPHONE],
    ['/en/preco', FIREFOX],
    ['/console/entrar', CHROME],
  ])('counts a browser on %s as a person', (path, ua) => {
    expect(at(path, ua)).toEqual({ counted: true, cls: 'human', agent: '' })
  })

  it.each([
    ['Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.4; +https://openai.com/gptbot)', 'GPTBot'],
    ['Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; +https://openai.com/bot', 'ChatGPT-User'],
    ['Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot', 'OAI-SearchBot'],
    ['Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)', 'ClaudeBot'],
    ['Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-User/1.0; +Claude-User@anthropic.com)', 'Claude-User'],
    ['anthropic-ai', 'anthropic-ai'],
    ['Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)', 'PerplexityBot'],
    ['Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Perplexity-User/1.0; +https://perplexity.ai/perplexity-user)', 'Perplexity-User'],
    ['Google-Extended', 'Google-Extended'],
    ['Mozilla/5.0 (Linux; Android 5.0) AppleWebKit/537.36 (KHTML, like Gecko) Mobile Safari/537.36 (compatible; Bytespider; spider-feedback@bytedance.com)', 'Bytespider'],
    ['CCBot/2.0 (https://commoncrawl.org/faq/)', 'CCBot'],
    ['Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15 (Applebot-Extended/0.1)', 'Applebot-Extended'],
    ['meta-externalagent/1.1 (+https://developers.facebook.com/docs/sharing/webmasters/crawler)', 'meta-externalagent'],
    ['Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Amazonbot/0.1; +https://developer.amazon.com/support/amazonbot) Chrome/119.0.6045.214 Safari/537.36', 'Amazonbot'],
    ['cohere-ai', 'cohere-ai'],
    ['DuckAssistBot/1.2; (+http://duckduckgo.com/duckassistbot.html)', 'DuckAssistBot'],
    ['Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; MistralAI-User/1.0; +https://docs.mistral.ai/robots)', 'MistralAI-User'],
  ])('names the AI crawler in %s', (ua, name) => {
    expect(at('/produtos/memoria', ua)).toEqual({ counted: true, cls: 'ai', agent: name })
  })

  it.each([
    ['Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.7390.122 Mobile Safari/537.36 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)', 'Googlebot'],
    ['Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm) Chrome/116.0.1938.76 Safari/537.36', 'bingbot'],
    ['Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15 (Applebot/0.1; +http://www.apple.com/go/applebot)', 'Applebot'],
    ['DuckDuckBot/1.1; (+http://duckduckgo.com/duckduckbot.html)', 'DuckDuckBot'],
    ['Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)', 'YandexBot'],
    ['Mozilla/5.0 (compatible; Baiduspider-render/2.0; +http://www.baidu.com/search/spider.html)', 'Baiduspider'],
  ])('names the search engine in %s', (ua, name) => {
    expect(at('/', ua)).toEqual({ counted: true, cls: 'search', agent: name })
  })

  it('keeps every AI crawler the founder named in the table', () => {
    const names = AI_CRAWLERS.map((c) => c.name)
    for (const n of ['GPTBot', 'ChatGPT-User', 'OAI-SearchBot', 'ClaudeBot', 'Claude-User', 'anthropic-ai', 'PerplexityBot', 'Perplexity-User',
      'Google-Extended', 'Bytespider', 'CCBot', 'Applebot-Extended', 'meta-externalagent', 'Amazonbot', 'cohere-ai', 'DuckAssistBot', 'MistralAI-User']) {
      expect(names).toContain(n)
    }
  })

  it.each([
    '/.env', '/.git/config', '/.aws/credentials', '/.DS_Store', '/wp-admin/install.php', '/wp-login.php', '/xmlrpc.php',
    '/blog/wp-includes/wlwmanifest.xml', '/phpmyadmin/', '/cgi-bin/luci', '/actuator/health', '/vendor/phpunit/phpunit/src/Util/PHP/eval-stdin.php',
    '/backup.sql', '/admin', '/admin/login', '/wp-content/plugins/x/readme.js',
  ])('counts %s as a scanner, whatever the user agent says', (path) => {
    expect(at(path, CHROME)).toMatchObject({ counted: true, cls: 'scanner' })
    expect(at(path, 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)')).toMatchObject({ counted: true, cls: 'scanner' })
    expect(at(path, 'curl/8.7.1')).toMatchObject({ counted: true, cls: 'scanner' })
  })

  it('names scanning tools by their user agent and counts an address as user agent as a scanner', () => {
    expect(at('/', 'Mozilla/5.0 zgrab/0.x')).toEqual({ counted: true, cls: 'scanner', agent: 'zgrab' })
    expect(at('/', 'Mozilla/5.0 (compatible; CensysInspect/1.1; +https://about.censys.io/)')).toEqual({ counted: true, cls: 'scanner', agent: 'CensysInspect' })
    expect(at('/', 'http://niadra.com/wp-admin/install.php?step=1')).toEqual({ counted: true, cls: 'scanner', agent: '' })
  })

  it.each([
    '/.well-known/security.txt', '/robots.txt', '/llms.txt', '/sitemap.xml', '/blog/feed.xml', '/en/benchmark.md',
  ])('keeps %s a page, not a scan', (path) => {
    expect(at(path, CHROME)).toMatchObject({ counted: true, cls: 'human' })
  })

  it.each([
    ['', 'empty user agent'],
    ['Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36', 'a Mozilla prefix with no browser'],
    ['Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/151.0.7922.34 Safari/537.36', 'headless Chrome'],
    ['python-httpx/0.28.1', 'an HTTP library'],
    ['Go-http-client/1.1', 'an HTTP library'],
    ['Mozilla/5.0 (compatible; SemrushBot/7~bl; +http://www.semrush.com/bot.html)', 'an SEO tool'],
    ['facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)', 'a link preview'],
    ['Mozilla/5.0 (compatible; ResearchScanBot/0.2; non-commercial web research)', 'a research crawler'],
  ])('counts %s as another robot (%s)', (ua) => {
    expect(at('/', ua)).toEqual({ counted: true, cls: 'bot', agent: '' })
  })

  it('does not count our probes, our health watch or static files', () => {
    expect(at('/healthz', 'kube-probe/1.33')).toEqual({ counted: false, reason: 'monitor' })
    expect(at('/healthz', CHROME)).toEqual({ counted: false, reason: 'monitor' })
    expect(at('/', 'curl/8.7.1')).toEqual({ counted: false, reason: 'monitor' })
    expect(at('/', 'kube-probe/1.33')).toEqual({ counted: false, reason: 'monitor' })
    for (const path of ['/assets/index-Dj3GcIQF.js', '/assets/index.css', '/favicon.svg', '/favicon-32.png', '/apple-touch-icon.png', '/og.png', '/config.json', '/benchmark/logos/x.svg']) {
      expect(at(path, CHROME)).toEqual({ counted: false, reason: 'asset' })
    }
  })

  it('stores the path without query string or fragment, and cuts a long one', () => {
    expect(normalizePath('/blog/x?utm_source=chatgpt.com#topo')).toBe('/blog/x')
    expect(normalizePath('?a=1')).toBe('/')
    expect(normalizePath(`/${'a'.repeat(400)}`)).toHaveLength(160)
  })
})
