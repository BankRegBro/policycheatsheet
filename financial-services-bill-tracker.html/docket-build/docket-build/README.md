# The Docket — pipeline

Builds the JSON behind the BankRegWire legislation tracker
(`financial-services-bill-tracker.html`), which has three views:
**Live docket**, **Untriaged** (auto-discovered committee bills awaiting review),
and **Closed record** (enacted or expired).

## Layout
- `docket-build/` — the build tooling (this folder). Not read by the site directly.
- `docket-build/data/` — published JSON the page fetches: `docket.json`,
  `untriaged.json`, `archive.json`.
- `financial-services-bill-tracker.html` — the single-page tracker at the repo root.

## Files
- `curation.json`  your watchlist and prose (the only file you hand-edit routinely)
- `config.py`      committee codes, theme map, jurisdiction nets, salience + discovery caps
- `classify.py`    stage / jurisdiction / salience / theme logic
- `fetch.py`       entry point

## Run
```
pip install -r docket-build/requirements.txt

# Offline: rebuild docket.json + archive.json from curation alone (no key needed)
python docket-build/fetch.py

# Online refresh: put your key in docket-build/.env, then
export $(grep -v '^#' docket-build/.env | xargs) && python docket-build/fetch.py

# Discovery: scan HFSC (hsba00) + Senate Banking (ssbk00) into untriaged.json
python docket-build/fetch.py --discover

# End-of-Congress sweep: retire in-flight bills as "died", empty the live docket
python docket-build/fetch.py --sweep
```
Put your key in `docket-build/.env` as `CONGRESS_API_KEY=...` (gitignored). Free key: https://api.data.gov/signup

## How discovery feeds the Untriaged view
`--discover` keeps only bills that clear jurisdiction + salience, drops anything already
curated, and writes them to `untriaged.json` with the CRS summary attached verbatim.
They surface in the tracker's **Untriaged** tab. To promote one, copy it into
`curation.json`; on the next refresh it moves onto the live docket and leaves the queue.

## Automation (.github/workflows)
- `docket.yml`   every 6h + manual — curated refresh; commits docket.json + archive.json
- `discover.yml` daily + manual — discovery; commits docket.json + untriaged.json + archive.json
- `sweep.yml`    manual only — end-of-Congress sweep

All read the `CONGRESS_API_KEY` repo secret. Set it under
Settings → Secrets and variables → Actions.

## Confirm on first discovery run
Watch the log for `house/hsba00: N bills referred`. If N is 0, the committee-bills
container key needs a one-line tweak in `Congress.committee_bills`.
