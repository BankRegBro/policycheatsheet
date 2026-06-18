/*
 * FDIC BankFind API proxy — Cloudflare Worker
 * ------------------------------------------------------------------
 * Purpose: let the Call Report tools hosted on bankregwire.com call the
 * FDIC BankFind Suite API from the browser without hitting CORS, and add
 * a 1-hour edge cache so repeated lookups are fast and easy on the FDIC.
 *
 * It forwards   https://<this-worker>/api/<path>?<query>
 *        to     https://banks.data.fdic.gov/api/<path>?<query>
 * so in each tool you only change the API host. Set, in the tool's script:
 *     const API = "https://fdic-bankregwire.joeysamowitz.workers.dev/api";
 *
 * DEPLOY (pick one)
 *  A) Dashboard: Cloudflare > Workers & Pages > Create Worker > paste this >
 *     Deploy. Then add a custom domain/route, e.g. fdic.bankregwire.com,
 *     under the worker's Settings > Domains & Routes.
 *  B) Wrangler:  save as worker.js with the wrangler.toml below, then
 *     `npx wrangler deploy`.
 *
 * wrangler.toml:
 *   name = "fdic-proxy"
 *   main = "worker.js"
 *   compatibility_date = "2026-06-01"
 *   routes = [{ pattern = "fdic.bankregwire.com/*", custom_domain = true }]
 *
 * SECURITY: this is a narrow proxy. It only forwards GET requests whose
 * path starts with /api/ to the FDIC host, and only returns CORS headers
 * to the origins you allow below. It is not an open proxy.
 */

const ALLOWED_ORIGINS = [
  "https://bankregwire.com",
  "https://www.bankregwire.com",
  "http://localhost:8000",   // local testing; remove in production if you like
  "http://127.0.0.1:5500"
];
const UPSTREAM = "https://banks.data.fdic.gov";
const CACHE_SECONDS = 3600;

export default {
  async fetch(request, env, ctx) {
    const origin = request.headers.get("Origin") || "";
    const allow = ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];
    const cors = {
      "Access-Control-Allow-Origin": allow,
      "Access-Control-Allow-Methods": "GET, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Vary": "Origin"
    };

    if (request.method === "OPTIONS") return new Response(null, { headers: cors });
    if (request.method !== "GET")
      return new Response("Method not allowed", { status: 405, headers: cors });

    const url = new URL(request.url);
    if (!url.pathname.startsWith("/api/"))
      return new Response("Not found. Use /api/...", { status: 404, headers: cors });

    const target = UPSTREAM + url.pathname + url.search;
    const cache = caches.default;
    const cacheKey = new Request(target, { method: "GET" });

    let resp = await cache.match(cacheKey);
    if (!resp) {
      let upstream;
      try {
        upstream = await fetch(target, {
          headers: { "Accept": "application/json" },
          cf: { cacheTtl: CACHE_SECONDS, cacheEverything: true }
        });
      } catch (e) {
        return new Response(JSON.stringify({ error: "upstream fetch failed", detail: String(e) }),
          { status: 502, headers: { ...cors, "Content-Type": "application/json" } });
      }
      resp = new Response(upstream.body, upstream);
      resp.headers.set("Cache-Control", `public, max-age=${CACHE_SECONDS}`);
      ctx.waitUntil(cache.put(cacheKey, resp.clone()));
    } else {
      resp = new Response(resp.body, resp);
    }
    for (const [k, v] of Object.entries(cors)) resp.headers.set(k, v);
    return resp;
  }
};
