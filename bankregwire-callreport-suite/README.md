# BankRegWire — Call Report Suite

Five single-file tools (no build step) plus a Cloudflare Worker proxy. Drop the
HTML files into one folder in your `bankregwire` repo, deploy the Worker to
Cloudflare, and change one line in two of the tools.

## What's included

| File | What it is |
|---|---|
| `callreport-linemap.html` | Curated CAMELS line-map. The ~32 lines that matter, each grouped by component, with what it conveys and the signal to read. |
| `callreport-literal.html` | Literal line-by-line form viewer. Seven schedules (RC, RI, RC-R, RC-N, RC-B, RC-E, RI-B), verbatim item numbers and MDRM codes. Upload a CDR file to fill it with a bank's actual figures, including the RC-B HTM amortized-cost vs fair-value gap and the M.2 maturity ladder. |
| `bank-health-scanner.html` | Live FDIC scanner. Search a bank, pull eight quarters, render a CAMELS dashboard with deltas, sparklines, threshold flags, CET1, and a peer benchmark. |
| `ma-partner-finder.html` | M&A partner screen. Pick a bank, choose acquirer or target, screen the FDIC universe by geography, size, deposit fit, and health, then compare any candidate as a pro forma with regulatory threshold-crossing flags. |
| `worker.js` | Cloudflare Worker that proxies the FDIC API (CORS + 1-hour cache). Deployed to Cloudflare, not GitHub Pages. |

The scanner and M&A finder need the live FDIC API. The line-map and literal
form work entirely offline.

## 1. Deploy the static tools

Put all four `.html` files in the same folder in the repo (root, or something
like `/callreport/`). The shared top nav links between them with relative,
same-folder paths, so they must stay together. Renaming a file means updating
the matching `href` in the others. The nav's home link points to
`https://bankregwire.com`.

GitHub Pages serves them as-is. No build, no dependencies (fonts load from
Google Fonts).

## 2. Deploy the Worker

The Worker solves CORS for the two live tools and caches responses. It is live at `https://fdic-bankregwire.joeysamowitz.workers.dev/`.

1. Cloudflare dashboard, Workers & Pages, Create Worker, paste `worker.js`,
   Deploy. (Or `npx wrangler deploy` using the `wrangler.toml` block in the
   file's header comment.)
2. Give it a custom domain, e.g. `fdic.bankregwire.com`, under the Worker's
   Settings, Domains & Routes. `ALLOWED_ORIGINS` in the file already lists
   `bankregwire.com`.
3. In `bank-health-scanner.html` and `ma-partner-finder.html`, change the one
   `const API` line to:
   ```js
   const API = "https://fdic-bankregwire.joeysamowitz.workers.dev/api";
   ```
   Both tools call `${API}/institutions` and `${API}/financials`; the Worker
   maps `/api/*` straight through to `banks.data.fdic.gov/api/*`. This is already
   set in the two HTML files in this package.

   CORS note: the Worker only returns its allow-origin header to the origins in
   `ALLOWED_ORIGINS` (currently `bankregwire.com` and `www.bankregwire.com`). Serve
   the tools from `bankregwire.com` and they will work. If you test from another
   origin (for example a `*.github.io` preview), add that origin to `ALLOWED_ORIGINS`
   in `worker.js` and redeploy.

## 3. Filling the literal form with real data

In `callreport-literal.html`, use "Upload Call Report data" with either an
FFIEC CDR bulk schedule file (MDRM-coded column headers) or a two-column
`code,value` export. Parsing happens in the browser; nothing is uploaded.

## Caveats

- The live FDIC field codes use candidate-array fallbacks, so a wrong code
  degrades one metric to `n/a` rather than breaking the tool. CET1's exact code
  is a best guess with three alternates; on your first live run, watch for any
  metric showing `n/a` across the board and adjust the candidate list at the top
  of the script.
- The M&A pro forma sums reported balances and asset-weights capital ratios and
  ROA. It is not a purchase-accounting build (no goodwill, marks, or RWA
  recalculation) and excludes charter, ownership, and antitrust concentration.
- FDIC figures lag roughly 30 to 60 days after quarter-end.

These are screening and education tools, not investment, legal, or supervisory
advice.
