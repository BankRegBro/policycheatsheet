#!/usr/bin/env python3
"""
The Docket - build pipeline (Phase 1: curated watchlist).

Modes:
  - No CONGRESS_API_KEY set  -> offline: build data/docket.json from curation.json alone.
  - CONGRESS_API_KEY set      -> online: refresh each curated bill's live status/actions
                                 from the Congress.gov API and merge curation over it.

Human fields (summary, provisions, theme, status, stage) always win over API data.
A refresh never regresses a bill's stage and never overwrites your prose.

Phase 2 (committee auto-discovery) is stubbed at the bottom; not run yet.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import config as C
import classify as X

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
CURATION = ROOT / "curation.json"
OUT = REPO / "data" / "docket.json"


# ---------------------------------------------------------------- API client
class Congress:
    def __init__(self, key):
        import requests  # lazy: offline mode needs no dependency
        self.s = requests.Session()
        self.key = key

    def get(self, path, **params):
        import requests
        params.setdefault("format", "json")
        params["api_key"] = self.key
        url = f"{C.API_BASE}/{path.lstrip('/')}"
        for attempt in range(C.RETRY):
            try:
                r = self.s.get(url, params=params, timeout=C.REQUEST_TIMEOUT)
                if r.status_code == 429:
                    time.sleep(2 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()
            except requests.RequestException as e:
                if attempt == C.RETRY - 1:
                    print(f"  ! {path}: {e}", file=sys.stderr)
                    return {}
                time.sleep(1 + attempt)
        return {}

    def bill(self, t, n):
        return self.get(f"bill/{C.CONGRESS}/{t}/{n}").get("bill", {})

    def sub(self, t, n, resource, container):
        return self.get(f"bill/{C.CONGRESS}/{t}/{n}/{resource}").get(container, [])


# ---------------------------------------------------------------- helpers
def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def related_objs(strings):
    return [{"no": s, "relationship": "Related", "url": ""} for s in (strings or [])]


def base_from_curation(cur):
    """The Bill object as far as curation alone can populate it (offline-complete)."""
    t, n = cur["type"], cur["number"]
    stage = cur.get("stage", 1)
    theme = cur.get("theme", "Uncategorized")
    summary = cur.get("summary", "")
    b = {
        "id": f"{C.CONGRESS}-{t}-{n}",
        "slug": cur.get("slug"),
        "congress": C.CONGRESS,
        "type": t,
        "number": n,
        "no": cur.get("no", f"{t.upper()} {n}"),
        "title": cur.get("title", ""),
        "officialTitle": None,

        "chamber": cur.get("chamber", ""),
        "sponsor": cur.get("sponsor", ""),
        "party": cur.get("party", ""),
        "bipartisan": cur.get("bipartisan", False),
        "cosponsorsCount": None,

        "committee": cur.get("committee", ""),
        "committees": [],
        "policyArea": None,
        "subjects": [],
        "matchedOn": ["curation"],
        "theme": theme,
        "core": X.core_for(theme),

        "stage": stage,
        "stageKey": X.stage_key(stage),
        "status": cur.get("status", ""),
        "latestActionText": cur.get("status", ""),
        "introducedDate": cur.get("introduced", ""),
        "lastDate": cur.get("lastDate", ""),
        "actions": [],

        "summary": summary,
        "summarySource": "human" if summary else "none",
        "provisions": cur.get("provisions", []),

        "related": related_objs(cur.get("related")),
        "src": cur.get("src", ""),
        "crsSummaryUrl": cur.get("src", ""),

        "disposition": "enacted" if stage >= 5 else "in_flight",
        "enacted": {"isLaw": stage >= 5, "publicLaw": None, "enactedDate": None, "textUrl": None},

        "tracked": True,
        "pinned": cur.get("pinned", False),
        "review": {"theme": True, "content": True},

        "vehicle": cur.get("vehicle", False),
        "updateDate": None,
        "lastRefreshed": now_iso(),
    }
    if C.SHOW_SIGNIFICANCE and cur.get("significance"):
        b["significance"] = cur["significance"]
    return b


def enrich(b, api, cur):
    """Refresh live fields from the API. Human fields are preserved."""
    t, n = cur["type"], cur["number"]
    detail = api.bill(t, n)
    if not detail:
        return b

    b["officialTitle"] = (detail.get("title") or b["title"])
    la = detail.get("latestAction", {}) or {}
    b["latestActionText"] = la.get("text", b["latestActionText"])
    b["lastDate"] = la.get("actionDate", b["lastDate"])
    b["introducedDate"] = detail.get("introducedDate", b["introducedDate"])
    b["policyArea"] = (detail.get("policyArea") or {}).get("name")
    b["updateDate"] = detail.get("updateDateIncludingText") or detail.get("updateDate")

    actions = api.sub(t, n, "actions", "actions")
    b["actions"] = [
        {"date": a.get("actionDate"), "type": (a.get("type") or ""), "text": a.get("text", "")}
        for a in actions
    ]
    subjects = api.sub(t, n, "subjects", "subjects")
    if isinstance(subjects, dict):  # API nests: subjects.legislativeSubjects[]
        leg = subjects.get("legislativeSubjects", [])
        b["subjects"] = [s.get("name") for s in leg if s.get("name")]
    committees = api.sub(t, n, "committees", "committees")
    b["committees"] = [c.get("systemCode") for c in committees if c.get("systemCode")]
    try:
        cosp = api.get(f"bill/{C.CONGRESS}/{t}/{n}/cosponsors").get("pagination", {})
        b["cosponsorsCount"] = cosp.get("count")
    except Exception:
        pass

    # Stage never regresses below the human-curated value.
    derived = X.derive_stage(b["actions"], b["latestActionText"])
    b["stage"] = max(cur.get("stage", 1), derived)
    b["stageKey"] = X.stage_key(b["stage"])
    b["disposition"] = "enacted" if b["stage"] >= 5 else "in_flight"

    # CRS summary only fills in if the curator left summary blank.
    if not cur.get("summary"):
        summaries = api.sub(t, n, "summaries", "summaries")
        if summaries:
            latest = summaries[-1]
            b["summary"] = latest.get("text", "").strip()
            b["summarySource"] = "crs" if b["summary"] else "none"

    b["lastRefreshed"] = now_iso()
    return b


# ---------------------------------------------------------------- main
def main():
    key = os.environ.get("CONGRESS_API_KEY", "").strip()
    curation = json.loads(CURATION.read_text())["bills"]
    api = Congress(key) if key else None
    mode = "online (API refresh)" if api else "offline (curation only)"
    print(f"Building docket from {len(curation)} curated bills -- {mode}")

    docket = []
    for cur in curation:
        b = base_from_curation(cur)
        if api:
            try:
                b = enrich(b, api, cur)
                print(f"  refreshed {b['no']}  stage {b['stage']}")
            except Exception as e:
                print(f"  ! {cur.get('no')}: {e}", file=sys.stderr)
        docket.append(b)

    # Furthest along first, then most recent action.
    docket.sort(key=lambda x: (x["stage"], x["lastDate"] or ""), reverse=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(docket, indent=2, ensure_ascii=False))
    print(f"Wrote {OUT.relative_to(REPO)}  ({len(docket)} bills)")


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------- Phase 2 stub
def discover(api):
    """
    Committee auto-discovery. Not run in Phase 1.
    Pull /committee/{chamber}/{code}/bills for the two committees, classify each with
    classify.jurisdiction_match + is_salient, tag summarySource='crs', and add anything
    not already in curation to an 'untriaged' bucket for review.
    """
    raise NotImplementedError("Phase 2")
