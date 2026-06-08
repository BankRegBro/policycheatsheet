/**
 * The Regulatory Wire — aggregation Worker
 * --------------------------------------------------------------
 * scheduled()  : runs on the cron trigger, rebuilds the dispatch
 *                list, writes it to KV.
 * fetch()      : serves the cached JSON to the front end (CORS on).
 *                GET /          -> cached dispatches (or builds once if cold)
 *                GET /refresh   -> force a rebuild, return fresh payload
 *
 * Layer 1 (Federal Register): rules / proposed rules / notices, via the
 *   public JSON API, one request per agency, filtered with conditions[agencies][].
 * Layer 2 (agency RSS): speeches & guidance, via each agency's RSS feed.
 *
 * ~14 subrequests per run (7 Federal Register + 7 RSS) — still well under the
 * Workers Free limit of 50 external subrequests/invocation. The only thing that may push you to the
 * $5 Paid plan is CPU time: Free caps cron CPU at 10ms; parsing all feeds is
 * usually under that, but if you add many feeds and see "exceeded CPU limit",
 * upgrade (Paid raises cron CPU to 30s). CPU excludes time spent waiting on
 * fetch(), so the network calls themselves are effectively free.
 */

const UA = 'Mozilla/5.0 (compatible; RegulatoryWire/1.0; +https://bankregwire.com)';
const MAX_ITEMS = 300;          // cap stored in KV
const FR_PER_AGENCY = 20;       // newest N rules/notices per agency

/* Agency taxonomy. `slug` = Federal Register agency slug (all verified). */
const AGENCIES = {
  FRB:    { name: 'Federal Reserve', slug: 'federal-reserve-system' },
  OCC:    { name: 'OCC',             slug: 'comptroller-of-the-currency' },
  FDIC:   { name: 'FDIC',            slug: 'federal-deposit-insurance-corporation' },
  CFPB:   { name: 'CFPB',            slug: 'consumer-financial-protection-bureau' },
  TREAS:  { name: 'Treasury',        slug: 'treasury-department' },
  FinCEN: { name: 'FinCEN',          slug: 'financial-crimes-enforcement-network' },
  FHFA:   { name: 'FHFA',            slug: 'federal-housing-finance-agency' }
};
const AG_KEYS = Object.keys(AGENCIES);

/* Map Federal Register `type` -> our display type. */
const FR_TYPE_MAP = {
  'Rule': 'Final Rule',
  'Proposed Rule': 'Proposed Rule',
  'Notice': 'Notice',
  'Presidential Document': 'Notice'
};

/* Layer-2 RSS adapters. Add more here — that's the whole extension model. */
const RSS_FEEDS = [
  // Confirmed against each agency's official RSS index page:
  { ag: 'FRB', type: 'Speech',   src: 'federalreserve.gov · speeches', url: 'https://www.federalreserve.gov/feeds/speeches.xml' },
  { ag: 'OCC', type: 'Speech',   src: 'occ.gov · speeches',            url: 'https://www.occ.treas.gov/rss/occ-speeches.xml' },
  { ag: 'OCC', type: 'Guidance', src: 'occ.gov · bulletins',           url: 'https://www.occ.treas.gov/rss/occ_bulletins.xml' },

  // FDIC, Treasury, FinCEN, and FHFA syndicate through GovDelivery, not native
  // .xml files. URLs below use confirmed GovDelivery account/topic codes, but
  // GovDelivery blocks generic crawlers, so they were NOT end-to-end verified
  // from outside a real reader. After deploy, hit /refresh and read the `sources`
  // map: a number = working; "error: …" = swap that one URL.
  //   per-topic feed (cleaner):  https://public.govdelivery.com/topics/<CODE>/feed.rss
  //   account-wide bulletins:    https://content.govdelivery.com/accounts/<ACCT>/bulletins.rss
  // To get the exact code: open the agency's RSS/subscribe page and copy the feed link.
  { ag: 'FDIC',   type: 'Guidance', src: 'fdic.gov · FILs',      url: 'https://public.govdelivery.com/topics/USFDIC_19/feed.rss' },
  { ag: 'TREAS',  type: 'Notice',   src: 'treasury.gov · press', url: 'https://content.govdelivery.com/accounts/USTREAS/bulletins.rss' },
  { ag: 'FinCEN', type: 'Guidance', src: 'fincen.gov · updates', url: 'https://content.govdelivery.com/accounts/USFINCEN/bulletins.rss' },
  { ag: 'FHFA',   type: 'Notice',   src: 'fhfa.gov · news',      url: 'https://content.govdelivery.com/accounts/USFHFA/bulletins.rss' }

  // Optional extra FDIC feed — press releases (topic USFDIC_26):
  // { ag: 'FDIC', type: 'Notice', src: 'fdic.gov · press', url: 'https://public.govdelivery.com/topics/USFDIC_26/feed.rss' },
];

/* ------------------------------------------------------------------ */
/* Worker entrypoints                                                  */
/* ------------------------------------------------------------------ */
export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(refresh(env));
  },

  async fetch(request, env, ctx) {
    if (request.method === 'OPTIONS') return cors(new Response(null, { status: 204 }));
    const url = new URL(request.url);

    if (url.pathname === '/refresh') {
      const payload = await refresh(env);
      return cors(json(payload));
    }

    // default read path
    const cached = await env.WIRE_KV.get('dispatches');
    if (cached) return cors(json(cached, true));   // raw string, no re-stringify

    // cold cache (e.g. right after deploy) — build once, then serve
    const payload = await refresh(env);
    return cors(json(payload));
  }
};

/* ------------------------------------------------------------------ */
/* Aggregation                                                         */
/* ------------------------------------------------------------------ */
async function refresh(env) {
  const payload = await aggregate();
  await env.WIRE_KV.put('dispatches', JSON.stringify(payload));
  return payload;
}

async function aggregate() {
  const frTasks  = AG_KEYS.map(k => fetchFederalRegister(AGENCIES[k].slug));
  const rssTasks = RSS_FEEDS.map(f => fetchRss(f));
  const settled  = await Promise.allSettled([...frTasks, ...rssTasks]);

  const byKey = new Map();      // dedupe key -> dispatch
  const sources = {};           // per-source status for diagnostics
  let okCount = 0;

  settled.forEach((res, i) => {
    const label = i < AG_KEYS.length
      ? 'FR:' + AGENCIES[AG_KEYS[i]].slug
      : 'RSS:' + RSS_FEEDS[i - AG_KEYS.length].url;
    if (res.status === 'fulfilled') {
      okCount++;
      sources[label] = res.value.length;
      for (const d of res.value) {
        const key = d.url || (d.ag + '|' + d.title);
        if (!byKey.has(key)) byKey.set(key, d);
      }
    } else {
      sources[label] = 'error: ' + (res.reason && res.reason.message || res.reason);
    }
  });

  const dispatches = [...byKey.values()]
    .filter(d => d.date)
    .sort((a, b) => b.date.localeCompare(a.date))
    .slice(0, MAX_ITEMS);

  return {
    generatedAt: new Date().toISOString(),
    sourcesOk: okCount,
    sourcesTotal: settled.length,
    sources,
    count: dispatches.length,
    dispatches
  };
}

/* ------------------------------------------------------------------ */
/* Layer 1: Federal Register                                           */
/* ------------------------------------------------------------------ */
async function fetchFederalRegister(slug) {
  const fields = ['title', 'type', 'abstract', 'publication_date', 'html_url', 'document_number', 'agencies'];
  const fq = fields.map(f => 'fields[]=' + f).join('&');
  const url = 'https://www.federalregister.gov/api/v1/documents.json'
    + '?conditions[agencies][]=' + encodeURIComponent(slug)
    + '&order=newest&per_page=' + FR_PER_AGENCY + '&' + fq;

  const r = await fetch(url, { headers: { 'User-Agent': UA, 'Accept': 'application/json' } });
  if (!r.ok) throw new Error('FR HTTP ' + r.status);
  const data = await r.json();
  const out = [];
  for (const doc of (data.results || [])) {
    // Attribute to the first tracked agency actually listed on the doc.
    // (Collapses interagency rules and guards against filter drift.)
    const docSlugs = new Set((doc.agencies || []).map(a => a.slug));
    const agKey = AG_KEYS.find(k => docSlugs.has(AGENCIES[k].slug));
    if (!agKey) continue;
    out.push({
      ag: agKey,
      type: FR_TYPE_MAP[doc.type] || doc.type || 'Notice',
      date: doc.publication_date,
      title: doc.title || '(untitled document)',
      src: 'Federal Register',
      url: doc.html_url,
      excerpt: doc.abstract
        ? truncate(doc.abstract, 260)
        : 'No abstract was published with this document. Open the full text on the Federal Register.',
      live: true
    });
  }
  return out;
}

/* ------------------------------------------------------------------ */
/* Layer 2: RSS / Atom                                                 */
/* ------------------------------------------------------------------ */
async function fetchRss(feed) {
  const r = await fetch(feed.url, { headers: { 'User-Agent': UA, 'Accept': 'application/rss+xml, application/xml, text/xml' } });
  if (!r.ok) throw new Error('RSS HTTP ' + r.status);
  const xml = await r.text();
  return parseRss(xml, feed).slice(0, 20);
}

function parseRss(xml, feed) {
  const out = [];
  const isAtom = /<entry[\s>]/.test(xml) && !/<item[\s>]/.test(xml);
  const blocks = isAtom
    ? matchAll(xml, /<entry[\s\S]*?<\/entry>/gi)
    : matchAll(xml, /<item[\s\S]*?<\/item>/gi);

  for (const block of blocks) {
    const title = clean(tag(block, 'title'));
    let link, dateRaw, desc;
    if (isAtom) {
      const lm = block.match(/<link[^>]*href="([^"]+)"[^>]*>/i);
      link = lm ? lm[1] : '';
      dateRaw = clean(tag(block, 'updated') || tag(block, 'published'));
      desc = clean(tag(block, 'summary') || tag(block, 'content'));
    } else {
      link = clean(tag(block, 'link'));
      dateRaw = clean(tag(block, 'pubDate') || tag(block, 'dc:date'));
      desc = clean(tag(block, 'description'));
    }
    if (!title) continue;
    out.push({
      ag: feed.ag,
      type: feed.type,
      date: toISODate(dateRaw),
      title,
      src: feed.src,
      url: link || undefined,
      excerpt: desc ? truncate(stripTags(desc), 260) : '',
      live: true
    });
  }
  return out;
}

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */
function matchAll(str, re) {
  const res = [];
  let m;
  while ((m = re.exec(str)) !== null) res.push(m[0]);
  return res;
}
function tag(block, name) {
  const m = block.match(new RegExp('<' + name + '[^>]*>([\\s\\S]*?)<\\/' + name + '>', 'i'));
  return m ? m[1] : '';
}
function clean(s) {
  if (!s) return '';
  return decodeEntities(
    s.replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1').trim()
  ).trim();
}
function stripTags(s) { return s.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim(); }
const NAMED_ENTITIES = {
  amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ',
  ndash: '–', mdash: '—', hellip: '…', rsquo: '’', lsquo: '‘',
  ldquo: '“', rdquo: '”', amp_: '&'
};
function decodeEntities(s) {
  return s
    .replace(/&#x([0-9a-fA-F]+);/g, (_, h) => String.fromCharCode(parseInt(h, 16)))
    .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(+n))
    .replace(/&([a-zA-Z]+);/g, (m, name) => (name in NAMED_ENTITIES ? NAMED_ENTITIES[name] : m))
    // resolve &amp; last so we don't double-decode already-entity-escaped ampersands
    .replace(/&amp;/g, '&');
}
function truncate(s, n) {
  s = (s || '').replace(/\s+/g, ' ').trim();
  return s.length > n ? s.slice(0, n).replace(/\s+\S*$/, '') + '…' : s;
}
function toISODate(raw) {
  if (!raw) return '';
  const d = new Date(raw);
  return isNaN(d) ? '' : d.toISOString().slice(0, 10);
}

/* response helpers */
function json(body, isString) {
  const text = isString ? body : JSON.stringify(body);
  return new Response(text, {
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'public, max-age=300'
    }
  });
}
function cors(resp) {
  const h = new Headers(resp.headers);
  h.set('Access-Control-Allow-Origin', '*');
  h.set('Access-Control-Allow-Methods', 'GET, OPTIONS');
  return new Response(resp.body, { status: resp.status, headers: h });
}
