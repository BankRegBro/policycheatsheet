"""
Configuration for The Docket pipeline.
All tunables live here so the fetch/classify logic stays generic.
No secrets in this file; the Congress key comes from the environment.
"""

CONGRESS = 119

# ---- API ----
API_BASE = "https://api.data.gov/congress/v3"
PAGE_LIMIT = 250          # max the API allows
REQUEST_TIMEOUT = 30      # seconds
RETRY = 3

# ---- Discovery spine: committee system codes ----
# CONFIRM these on first live run by listing /committee/house and /committee/senate.
# A wrong code returns an empty set silently.
COMMITTEES = {
    "house": "hsba00",    # House Financial Services (legacy "Banking" abbreviation)
    "senate": "ssbk00",   # Senate Banking, Housing, and Urban Affairs
}

# ---- Theme taxonomy ----
# Your existing nine, plus the extensions the full-committee net will need in Phase 2.
THEMES = [
    "Supervisory Relief",
    "Charters & Resolution",
    "Deposits",
    "Lending Data (1071)",
    "State Authority & Preemption",
    "Payments",
    "Data Privacy",
    "Digital Assets",
    "Housing",
    # Phase 2 extensions (full committee jurisdiction):
    "Securities & Capital Markets",
    "Insurance & Flood",
    "Sanctions & International",
]

# core=False themes are collapsed behind a "show all" toggle in the UI.
# These are the broader-appeal-but-outside-core buckets.
THEME_CORE = {t: True for t in THEMES}
for t in ["Housing", "Insurance & Flood", "Sanctions & International"]:
    THEME_CORE[t] = False

# Best-effort first-guess map from CRS legislative subject terms -> theme.
# Deterministic, human-ratified. First match wins; unmatched -> "Uncategorized".
SUBJECT_THEME_MAP = {
    "Digital currencies": "Digital Assets",
    "Securities": "Securities & Capital Markets",
    "Consumer credit": "State Authority & Preemption",
    "Bank accounts, deposits, capital": "Deposits",
    "Banking and financial institutions regulation": "Supervisory Relief",
    "Financial services and investments": "Supervisory Relief",
    "Housing finance and home ownership": "Housing",
    "Housing and community development funding": "Housing",
    "Flood insurance": "Insurance & Flood",
    "Insurance industry and regulation": "Insurance & Flood",
    "Sanctions": "Sanctions & International",
    "International monetary system and foreign exchange": "Sanctions & International",
}

# ---- Jurisdiction net (recall patches beyond the committee spine) ----
# CRS policy area that flags in-scope bills escaping the two committees.
POLICY_AREA_IN = "Finance and Financial Sector"

# Curated legislative-subject inclusion set (catches CFTC/digital-asset bills routed
# to Agriculture, tax-side financial provisions, etc.).
SUBJECTS_IN = set(SUBJECT_THEME_MAP.keys()) | {
    "Financial literacy",
    "Credit and credit markets",
    "Financial crises and stabilization",
    "Foreign loans and debt",
}

# Thin keyword net for coined terms the controlled vocabulary lags on.
KEYWORDS_IN = ["stablecoin", "debanking", "tokenization", "de novo", "fintech"]

# Exclusion terms to suppress obvious false positives.
KEYWORDS_OUT = ["world bank", "food bank", "blood bank", "data bank", "river bank", "bank holiday"]

# ---- Salience filter ----
# A bill auto-surfaces (tracked=True) if ANY of these hold. pinned bills always surface.
SALIENCE = {
    "min_cosponsors": 15,       # cosponsor floor
    "reported": True,           # any committee report / ordered reported
    "floor_action": True,       # any floor action
    "has_companion": True,      # an identified related/companion bill
}

# ---- Stage model (mirrors the existing tool) ----
# 1 introduced  2 committee  3 passed-origin  4 cross-chamber  5 enacted
STAGE_KEY = {1: "introduced", 2: "committee", 3: "passed_origin", 4: "cross_chamber", 5: "enacted"}

# Map Congress API action `type` values to a stage floor. Highest wins.
ACTION_TYPE_STAGE = {
    "IntroReferral": 1,
    "Committee": 2,
    "Calendars": 2,
    "Floor": 3,
    "ResolvingDifferences": 4,
    "President": 4,
    "BecameLaw": 5,
    "Veto": 4,
}

# ---- Render toggles ----
SHOW_SIGNIFICANCE = False   # flip True to surface the preserved significance text
