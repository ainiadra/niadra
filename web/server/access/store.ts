/**
 * The access counts in Postgres (database niadra_site): the tables, the flush of a batch and the
 * report the admin panel reads. A flush is one transaction on one pooled connection, with every row
 * of the minute in a single multi-row upsert, so the database sees one short write a minute however
 * busy the site is.
 *
 *   site_access_daily       day, path, class, agent name -> hits
 *   site_visitor_salts      the random salt of each day (today, tomorrow and yesterday only)
 *   site_visitor_hashes     the distinct people of today and yesterday, as salted hashes
 *   site_visitors_daily     the number of distinct people of every older day
 *   site_access_country     day, class, country -> hits (the country of the masked address, geo.ts)
 *   site_access_campaign    day, class, utm_source, utm_medium, utm_campaign -> hits
 */
import type pg from 'pg'
import { randomBytes } from 'node:crypto'
import { CLASSES, crawlerInfo, type AccessClass } from './classify'
import { dayOf, type Batch } from './aggregate'
import { LANGS, type Lang } from './lang'

export const ACCESS_SCHEMA = `
  create table if not exists site_access_daily (
    day    date   not null,
    path   text   not null,
    class  text   not null,
    agent  text   not null default '',
    hits   bigint not null,
    primary key (day, path, class, agent)
  );
  create table if not exists site_visitor_salts (
    day  date  primary key,
    salt bytea not null
  );
  create table if not exists site_visitor_hashes (
    day     date  not null,
    visitor bytea not null,
    primary key (day, visitor)
  );
  create table if not exists site_visitors_daily (
    day      date    primary key,
    visitors integer not null
  );
  create table if not exists site_access_country (
    day     date   not null,
    class   text   not null,
    country text   not null,
    hits    bigint not null,
    primary key (day, class, country)
  );
  create table if not exists site_access_campaign (
    day      date   not null,
    class    text   not null,
    source   text   not null,
    medium   text   not null default '',
    campaign text   not null default '',
    hits     bigint not null,
    primary key (day, class, source, medium, campaign)
  );
`

/** How long the counts stay: a bit over a year, for the same week of last year. */
const RETENTION_DAYS = 400

type Queryable = Pick<pg.Pool, 'query' | 'connect'>

export async function flush(pool: Queryable, batch: Batch): Promise<void> {
  const countries = batch.countries ?? []
  const campaigns = batch.campaigns ?? []
  if (!batch.rows.length && !batch.visitors.length && !countries.length && !campaigns.length) return
  const client = await pool.connect()
  try {
    await client.query('begin')
    if (batch.rows.length) {
      await client.query(
        `insert into site_access_daily (day, path, class, agent, hits)
         select * from unnest($1::date[], $2::text[], $3::text[], $4::text[], $5::bigint[])
         on conflict (day, path, class, agent) do update set hits = site_access_daily.hits + excluded.hits`,
        [batch.rows.map((r) => r.day), batch.rows.map((r) => r.path), batch.rows.map((r) => r.cls), batch.rows.map((r) => r.agent), batch.rows.map((r) => r.hits)],
      )
    }
    if (batch.visitors.length) {
      await client.query(
        `insert into site_visitor_hashes (day, visitor)
         select day, decode(v, 'hex') from unnest($1::date[], $2::text[]) as t(day, v)
         on conflict do nothing`,
        [batch.visitors.map((v) => v.day), batch.visitors.map((v) => v.visitor)],
      )
    }
    if (countries.length) {
      await client.query(
        `insert into site_access_country (day, class, country, hits)
         select * from unnest($1::date[], $2::text[], $3::text[], $4::bigint[])
         on conflict (day, class, country) do update set hits = site_access_country.hits + excluded.hits`,
        [countries.map((c) => c.day), countries.map((c) => c.cls), countries.map((c) => c.country), countries.map((c) => c.hits)],
      )
    }
    if (campaigns.length) {
      await client.query(
        `insert into site_access_campaign (day, class, source, medium, campaign, hits)
         select * from unnest($1::date[], $2::text[], $3::text[], $4::text[], $5::text[], $6::bigint[])
         on conflict (day, class, source, medium, campaign) do update set hits = site_access_campaign.hits + excluded.hits`,
        [campaigns.map((c) => c.day), campaigns.map((c) => c.cls), campaigns.map((c) => c.source), campaigns.map((c) => c.medium), campaigns.map((c) => c.campaign), campaigns.map((c) => c.hits)],
      )
    }
    await client.query('commit')
  } catch (err) {
    await client.query('rollback').catch(() => {})
    throw err
  } finally {
    client.release()
  }
}

const DAY_MS = 86_400_000
const shift = (day: string, days: number) => new Date(Date.parse(`${day}T12:00:00Z`) + days * DAY_MS).toISOString().slice(0, 10)

/**
 * Makes sure today and tomorrow have a salt and returns the salts of yesterday, today and tomorrow.
 * Tomorrow's is made ahead so the first requests after midnight already have one.
 */
export async function loadSalts(pool: Queryable, now = Date.now()): Promise<Map<string, Buffer>> {
  const today = dayOf(now)
  const days = [today, shift(today, 1)]
  await pool.query(
    `insert into site_visitor_salts (day, salt) select * from unnest($1::date[], $2::bytea[]) on conflict do nothing`,
    [days, days.map(() => randomBytes(32))],
  )
  const { rows } = await pool.query<{ day: string; salt: Buffer }>(
    `select to_char(day, 'YYYY-MM-DD') as day, salt from site_visitor_salts where day >= $1::date`,
    [shift(today, -1)],
  )
  return new Map(rows.map((r) => [r.day, r.salt]))
}

/**
 * Once a day's late requests have landed (two days later), its distinct people become a number and its
 * hashes and salt are deleted. Counts older than the retention go too.
 */
export async function compact(pool: Queryable, now = Date.now()): Promise<void> {
  const keepFrom = shift(dayOf(now), -1)
  await pool.query(
    `with done as (
       delete from site_visitor_hashes where day < $1::date returning day
     )
     insert into site_visitors_daily (day, visitors)
     select day, count(*) from done group by day
     on conflict (day) do update set visitors = site_visitors_daily.visitors + excluded.visitors`,
    [keepFrom],
  )
  await pool.query(`delete from site_visitor_salts where day < $1::date`, [keepFrom])
  const oldest = shift(dayOf(now), -RETENTION_DAYS)
  await pool.query(`delete from site_access_daily where day < $1::date`, [oldest])
  await pool.query(`delete from site_access_country where day < $1::date`, [oldest])
  await pool.query(`delete from site_access_campaign where day < $1::date`, [oldest])
}

export type Range = 'today' | '7d' | '30d'
const RANGE_DAYS: Record<Range, number> = { today: 1, '7d': 7, '30d': 30 }

export interface AccessReport {
  range: Range
  from: string
  to: string
  /** The first day with any count: before it, zero means "not counted yet", not "nobody came". */
  since: string | null
  days: { day: string; counts: Record<AccessClass, number>; visitors: number }[]
  totals: Record<AccessClass, number>
  visitors: number
  top: Record<AccessClass, { path: string; hits: number }[]>
  blog: { path: string; counts: Record<AccessClass, number>; total: number }[]
  crawlers: { name: string; company: string; purpose: string | null; cls: 'ai' | 'search'; hits: number; pages: number; topPath: string }[]
  /** Requests per site language, from the path: `/en` pages are English, files like llms.txt and the sitemap are neither. */
  languages: Record<Lang, Record<AccessClass, number>>
  /** Requests per country of the masked address, most first; `unknown` when the table has no answer. */
  countries: { country: string; counts: Record<AccessClass, number> }[]
  /** Requests whose link carried utm_source, by source, medium and campaign, most first. */
  campaigns: { source: string; medium: string; campaign: string; counts: Record<AccessClass, number> }[]
}


const zero = (): Record<AccessClass, number> => ({ human: 0, ai: 0, search: 0, scanner: 0, bot: 0 })
const isClass = (c: string): c is AccessClass => (CLASSES as string[]).includes(c)

const TOP_PAGES = 15
const BLOG_ROWS = 40
const COUNTRY_ROWS = 25
const CAMPAIGN_ROWS = 40

export async function report(pool: Queryable, range: Range, now = Date.now()): Promise<AccessReport> {
  const to = dayOf(now)
  const from = shift(to, 1 - RANGE_DAYS[range])
  const args = [from, to]
  // One connection, one query after another: the report runs when the founder opens the panel, and the
  // database is small.
  const client = await pool.connect()
  let perDay, top, blog, crawlers, visitors, since, langs, countries, campaigns
  try {
    perDay = await client.query<{ day: string; class: string; hits: string }>(
      `select to_char(day, 'YYYY-MM-DD') as day, class, sum(hits) as hits from site_access_daily
       where day between $1::date and $2::date group by day, class`,
      args,
    )
    top = await client.query<{ class: string; path: string; hits: string }>(
      `select class, path, hits from (
         select class, path, sum(hits) as hits, row_number() over (partition by class order by sum(hits) desc, path) as n
         from site_access_daily where day between $1::date and $2::date group by class, path
       ) t where n <= ${TOP_PAGES} order by class, hits desc, path`,
      args,
    )
    blog = await client.query<{ path: string; class: string; hits: string }>(
      `select path, class, sum(hits) as hits from site_access_daily
       where day between $1::date and $2::date and path ~ '^(/en)?/blog(/|$|\\.md$)' group by path, class`,
      args,
    )
    crawlers = await client.query<{ class: string; agent: string; hits: string; pages: string; top_path: string }>(
      `with per_path as (
         select class, agent, path, sum(hits) as hits from site_access_daily
         where day between $1::date and $2::date and class in ('ai', 'search') and agent <> ''
         group by class, agent, path
       )
       select class, agent, sum(hits) as hits, count(*) as pages, (array_agg(path order by hits desc, path))[1] as top_path
       from per_path group by class, agent order by sum(hits) desc, agent`,
      args,
    )
    visitors = await client.query<{ day: string; visitors: number }>(
      `select to_char(day, 'YYYY-MM-DD') as day, visitors from site_visitors_daily where day between $1::date and $2::date
       union all
       select to_char(day, 'YYYY-MM-DD'), count(*)::int from site_visitor_hashes where day between $1::date and $2::date group by day`,
      args,
    )
    langs = await client.query<{ lang: string; class: string; hits: string }>(
      `select case when path ~ '^/en(/|$|\\.md$)' then 'en'
                   when path ~ '\\.(txt|xml|json)$' then 'other'
                   else 'pt' end as lang,
              class, sum(hits) as hits
       from site_access_daily where day between $1::date and $2::date group by 1, class`,
      args,
    )
    countries = await client.query<{ country: string; class: string; hits: string }>(
      `select country, class, sum(hits) as hits from site_access_country
       where day between $1::date and $2::date group by country, class`,
      args,
    )
    campaigns = await client.query<{ source: string; medium: string; campaign: string; class: string; hits: string }>(
      `select source, medium, campaign, class, sum(hits) as hits from site_access_campaign
       where day between $1::date and $2::date group by source, medium, campaign, class`,
      args,
    )
    since = await client.query<{ since: string | null }>(`select to_char(min(day), 'YYYY-MM-DD') as since from site_access_daily`)
  } finally {
    client.release()
  }

  const days = new Map<string, { day: string; counts: Record<AccessClass, number>; visitors: number }>()
  for (let d = from; d <= to; d = shift(d, 1)) days.set(d, { day: d, counts: zero(), visitors: 0 })
  const totals = zero()
  for (const r of perDay.rows) {
    if (!isClass(r.class)) continue
    const n = Number(r.hits)
    days.get(r.day)!.counts[r.class] += n
    totals[r.class] += n
  }
  let visitorSum = 0
  for (const r of visitors.rows) {
    const d = days.get(r.day)
    if (!d) continue
    d.visitors += Number(r.visitors)
    visitorSum += Number(r.visitors)
  }

  const topByClass = Object.fromEntries(CLASSES.map((c) => [c, [] as { path: string; hits: number }[]])) as AccessReport['top']
  for (const r of top.rows) if (isClass(r.class)) topByClass[r.class].push({ path: r.path, hits: Number(r.hits) })

  const blogPaths = new Map<string, Record<AccessClass, number>>()
  for (const r of blog.rows) {
    if (!isClass(r.class)) continue
    const counts = blogPaths.get(r.path) ?? zero()
    counts[r.class] += Number(r.hits)
    blogPaths.set(r.path, counts)
  }
  // Scanners and anonymous robots are not readers: a blog row counts people, AIs and search engines.
  const readers = (c: Record<AccessClass, number>) => c.human + c.ai + c.search
  const blogRows = [...blogPaths]
    .map(([path, counts]) => ({ path, counts, total: readers(counts) }))
    .filter((r) => r.total > 0)
    .sort((a, b) => b.total - a.total || a.path.localeCompare(b.path))
    .slice(0, BLOG_ROWS)

  const languages = Object.fromEntries(LANGS.map((l) => [l, zero()])) as AccessReport['languages']
  for (const r of langs.rows) {
    if (!isClass(r.class) || !(LANGS as string[]).includes(r.lang)) continue
    languages[r.lang as Lang][r.class] += Number(r.hits)
  }

  // Requests with a known country, against every request of the period: the difference is `unknown`.
  const byCountry = new Map<string, Record<AccessClass, number>>()
  for (const r of countries.rows) {
    if (!isClass(r.class)) continue
    const counts = byCountry.get(r.country) ?? zero()
    counts[r.class] += Number(r.hits)
    byCountry.set(r.country, counts)
  }
  const known = zero()
  for (const counts of byCountry.values()) for (const c of CLASSES) known[c] += counts[c]
  const unknown = zero()
  for (const c of CLASSES) unknown[c] = Math.max(0, totals[c] - known[c])
  const total = (c: Record<AccessClass, number>) => CLASSES.reduce((n, k) => n + c[k], 0)
  const countryRows = [...byCountry]
    .map(([country, counts]) => ({ country, counts }))
    .sort((a, b) => total(b.counts) - total(a.counts) || a.country.localeCompare(b.country))
    .slice(0, COUNTRY_ROWS)
  if (total(unknown) > 0) countryRows.push({ country: 'unknown', counts: unknown })

  const byCampaign = new Map<string, { source: string; medium: string; campaign: string; counts: Record<AccessClass, number> }>()
  for (const r of campaigns.rows) {
    if (!isClass(r.class)) continue
    const key = [r.source, r.medium, r.campaign].join('\t')
    const row = byCampaign.get(key) ?? { source: r.source, medium: r.medium, campaign: r.campaign, counts: zero() }
    row.counts[r.class] += Number(r.hits)
    byCampaign.set(key, row)
  }
  const campaignRows = [...byCampaign.values()]
    .sort((a, b) => total(b.counts) - total(a.counts) || a.source.localeCompare(b.source) || a.campaign.localeCompare(b.campaign))
    .slice(0, CAMPAIGN_ROWS)

  return {
    range,
    from,
    to,
    since: since.rows[0]?.since ?? null,
    days: [...days.values()],
    totals,
    visitors: visitorSum,
    top: topByClass,
    blog: blogRows,
    crawlers: crawlers.rows.map((r) => {
      const info = crawlerInfo(r.agent)
      return {
        name: r.agent,
        company: info?.company ?? '',
        purpose: info?.purpose ?? null,
        cls: r.class === 'search' ? 'search' : 'ai',
        hits: Number(r.hits),
        pages: Number(r.pages),
        topPath: r.top_path,
      }
    }),
    languages,
    countries: countryRows,
    campaigns: campaignRows,
  }
}
