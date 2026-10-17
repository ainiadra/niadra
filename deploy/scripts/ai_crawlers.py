"""
AI access report of the site, from the site container's access log (Caddy, IP masked when written),
read from CloudWatch Logs by scripts/ai-crawlers.sh.

For the period asked, it shows:
  - which AI and search crawlers read the site, and how often
  - which pages each crawler family read most
  - visits of people who arrived by clicking an AI answer (referer or utm_source)
  - visits from search engines and the pages people viewed most

Reads a JSON list of log messages from standard input. Read only.
"""
import collections
import json
import sys
import time

DAYS = float(sys.argv[1]) if len(sys.argv) > 1 else 7
SINCE = time.time() - DAYS * 86400

# Name in the user agent -> (company, purpose). training: feeds models. search: indexes for
# answers with sources. user: a fetch a person triggered in a conversation (the strongest sign that
# an AI is using the site to answer).
BOTS = [
    ('OAI-SearchBot', 'OpenAI', 'search'), ('ChatGPT-User', 'OpenAI', 'user'), ('GPTBot', 'OpenAI', 'training'),
    ('Claude-SearchBot', 'Anthropic', 'search'), ('Claude-User', 'Anthropic', 'user'), ('ClaudeBot', 'Anthropic', 'training'),
    ('anthropic-ai', 'Anthropic', 'training'),
    ('PerplexityBot', 'Perplexity', 'search'), ('Perplexity-User', 'Perplexity', 'user'),
    ('Googlebot', 'Google', 'search'), ('Google-InspectionTool', 'Google', 'search'), ('GoogleOther', 'Google', 'training'),
    ('Storebot-Google', 'Google', 'search'), ('Google-CloudVertexBot', 'Google', 'agent (on the site owner request)'),
    ('bingbot', 'Microsoft', 'search'), ('BingPreview', 'Microsoft', 'search'),
    ('Applebot-Extended', 'Apple', 'training'), ('Applebot', 'Apple', 'search'),
    ('Amazonbot', 'Amazon', 'search'), ('meta-externalagent', 'Meta', 'training'), ('meta-externalfetcher', 'Meta', 'user'),
    ('FacebookBot', 'Meta', 'training'), ('DuckAssistBot', 'DuckDuckGo', 'search'), ('DuckDuckBot', 'DuckDuckGo', 'search'),
    ('YouBot', 'You.com', 'search'), ('MistralAI-User', 'Mistral', 'user'), ('cohere-ai', 'Cohere', 'training'),
    ('CCBot', 'Common Crawl', 'training'), ('Bytespider', 'ByteDance', 'training'), ('Diffbot', 'Diffbot', 'training'),
    ('YandexBot', 'Yandex', 'search'), ('Baiduspider', 'Baidu', 'search'), ('PetalBot', 'Huawei', 'search'),
    ('Brave', 'Brave', 'search'), ('xAI', 'xAI', 'search'), ('Grok', 'xAI', 'user'), ('DeepSeek', 'DeepSeek', 'search'),
]
AI_REFERRERS = {
    'chatgpt.com': 'ChatGPT', 'chat.openai.com': 'ChatGPT', 'perplexity.ai': 'Perplexity', 'www.perplexity.ai': 'Perplexity',
    'gemini.google.com': 'Gemini', 'copilot.microsoft.com': 'Copilot', 'claude.ai': 'Claude', 'you.com': 'You.com',
    'www.meta.ai': 'Meta AI', 'meta.ai': 'Meta AI', 'grok.com': 'Grok', 'chat.deepseek.com': 'DeepSeek',
    'chat.mistral.ai': 'Mistral', 'poe.com': 'Poe', 'search.brave.com': 'Brave Search',
}
SEARCH_REFERRERS = ('google.', 'bing.com', 'duckduckgo.com', 'yahoo.', 'ecosia.org', 'yandex.')
# ChatGPT and others add utm_source to the links they cite; the referer is sometimes missing.
AI_UTM = {'chatgpt.com': 'ChatGPT', 'chat.openai.com': 'ChatGPT', 'perplexity': 'Perplexity', 'perplexity.ai': 'Perplexity',
          'copilot.com': 'Copilot', 'copilot.microsoft.com': 'Copilot', 'gemini.google.com': 'Gemini', 'claude.ai': 'Claude'}


def lines():
    for message in json.load(sys.stdin):
        try:
            yield json.loads(message)
        except ValueError:
            continue


def first(h, key):
    v = (h or {}).get(key)
    return v[0] if isinstance(v, list) and v else ''


def bot_of(ua):
    for name, company, kind in BOTS:
        if name.lower() in ua.lower():
            return name, company, kind
    return None


def host_of(ref):
    if '://' not in ref:
        return ''
    return ref.split('://', 1)[1].split('/', 1)[0].lower()


bots = collections.Counter()
bot_pages = collections.defaultdict(collections.Counter)
ai_visits = collections.Counter()
ai_pages = collections.Counter()
search_visits = collections.Counter()
human_pages = collections.Counter()
total = 0

for e in lines():
    if e.get('ts', 0) < SINCE or not str(e.get('logger', '')).startswith('http.log.access'):
        continue
    req = e.get('request', {})
    uri = req.get('uri', '')
    if uri.startswith('/assets/') or uri.endswith(('.png', '.svg', '.ico', '.css', '.js', '.woff2')):
        continue
    total += 1
    ua = first(req.get('headers'), 'User-Agent')
    ref = first(req.get('headers'), 'Referer')
    b = bot_of(ua)
    if b:
        bots[b] += 1
        bot_pages[b[1]][uri] += 1
        continue
    host = host_of(ref)
    utm = ''
    if 'utm_source=' in uri:
        utm = uri.split('utm_source=', 1)[1].split('&', 1)[0].lower()
    if host in AI_REFERRERS or utm in AI_UTM:
        ai_visits[AI_REFERRERS.get(host) or AI_UTM[utm]] += 1
        ai_pages[uri.split('?', 1)[0]] += 1
    elif any(s in host for s in SEARCH_REFERRERS):
        search_visits[host] += 1
    human_pages[uri] += 1

print(f'Last {DAYS:g} days · {total} page requests (images and scripts left out)\n')

print('AI and search crawlers')
if not bots:
    print('  none yet')
for (name, company, kind), n in bots.most_common():
    print(f'  {n:6d}  {name:22s} {company:12s} {kind}')

print('\nPages read most, per company')
for company, pages in sorted(bot_pages.items(), key=lambda kv: -sum(kv[1].values())):
    top = ', '.join(f'{p} ({n})' for p, n in pages.most_common(5))
    print(f'  {company}: {top}')

print('\nPeople who arrived from an AI answer')
if not ai_visits:
    print('  none yet')
for name, n in ai_visits.most_common():
    print(f'  {n:6d}  {name}')
if ai_pages:
    print('  pages: ' + ', '.join(f'{p} ({n})' for p, n in ai_pages.most_common(8)))

print('\nPeople who arrived from a search engine')
if not search_visits:
    print('  none yet')
for host, n in search_visits.most_common(8):
    print(f'  {n:6d}  {host}')

print('\nPages people viewed most')
for p, n in human_pages.most_common(10):
    print(f'  {n:6d}  {p}')
