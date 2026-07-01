# Ready-to-commit: BankRegWire docket pipeline + Untriaged view

Unzip at your repo ROOT (same folder as index.html and CNAME) and commit everything.

Contents:
  financial-services-bill-tracker.html   updated: adds the Untriaged view (3-way toggle)
  docket-build/                          pipeline source + published data/
  .github/workflows/                     docket.yml, discover.yml, sweep.yml
  .gitignore                             protects your API key

Then:
  1. Settings -> Secrets and variables -> Actions -> add CONGRESS_API_KEY
  2. Actions tab -> run "Discover committee bills" once by hand
  3. Open the tracker's Untriaged tab (or docket-build/data/untriaged.json) and
     promote bills into docket-build/curation.json to move them onto the live docket.

Data files are pre-generated so the page renders immediately after commit;
Untriaged and Closed views start empty until the first discovery run / first enactment.
