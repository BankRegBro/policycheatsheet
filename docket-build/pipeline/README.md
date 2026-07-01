# The Docket - pipeline

Builds `data/docket.json` for the BankRegWire legislation tracker.

## Install
```
pip install -r pipeline/requirements.txt
```

## Run offline (no key)
Rebuilds `docket.json` from `curation.json` alone.
```
python pipeline/fetch.py
```

## Run online (live refresh from Congress.gov)
Put your key in a file the first time, then load it before each run.

1. Create `pipeline/.env` with one line (replace with your real key):
   ```
   CONGRESS_API_KEY=your_key_here
   ```
2. Load it and run:
   ```
   export $(grep -v '^#' pipeline/.env | xargs) && python pipeline/fetch.py
   ```

`.env` is gitignored - never commit your key. Get a free key at
https://api.data.gov/signup (same key works against the Congress.gov API).

## What it does
- Reads the curated watchlist in `curation.json`.
- Offline, emits a complete `docket.json` from curation alone.
- Online, refreshes each bill's status, actions, dates, subjects, committees, and
  cosponsor count from the Congress.gov API and merges curation on top. Human summary,
  provisions, theme, and status always win; stage never regresses.

## Files
- `curation.json`  your bills and prose (the only file you hand-edit routinely)
- `config.py`      committee codes, theme map, nets, salience thresholds
- `classify.py`    stage/jurisdiction/salience/theme logic (Phase 2 machinery)
- `fetch.py`       entry point

## Confirm before first online run
The two committee codes in `config.py` (`hsba00`, `ssbk00`). List `/committee/house`
and `/committee/senate` to verify; a wrong code returns nothing silently.

## Deployed refresh (GitHub Actions)
The scheduled run does NOT read a file. It reads the key from a repo secret named
`CONGRESS_API_KEY`. Set it once: repo Settings -> Secrets and variables -> Actions ->
New repository secret. The workflow in `.github/workflows/docket.yml` runs every 6
hours and on demand, and commits `data/docket.json` when it changes.
