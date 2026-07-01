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
DATA = ROOT / "data"
CURATION = ROOT / "curation.json"
OUT = DATA / "docket.json"
UNTRIAGED = DATA / "untriaged.json"
ARCHIVE = DATA / "archive.json"


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

    def committee_bills(self, chamber, code):
        """
        All bills referred to a committee, paginated. Returns light refs
        (type, number, latest action) - enough to dedupe and order before
        spending detail calls. The list container key has varied across API
        revisions, so we probe a couple of shapes defensively.
        """
        out, offset = [], 0
        while True:
            data = self.get(f"committee/{chamber}/{code}/bills",
                            offset=offset, limit=C.PAGE_LIMIT)
            container = data.get("committee-bills")
            if not isinstance(container, dict):
                container = data if isinstance(data, dict) else {}
            bills = container.get("bills") or data.get("bills") or []
            for b in bills:
                t = (b.get("type") or "").lower().strip()
                n = str(b.get("number") or "").strip()
                if not (t and n):
                    continue
                la = b.get("latestAction") or {}
                out.append({
                    "type": t,
                    "number": n,
                    "lastDate": la.get("actionDate", ""),
                    "latestActionText": la.get("text", ""),
                    "title": b.get("title", ""),
                })
            pag = container.get("pagination") or data.get("pagination") or {}
            count = pag.get("count")
            offset += C.PAGE_LIMIT
            if not bills:
                break
            if count is not None and offset >= count:
                break
            if offset > 10000:   # hard safety valve
                break
        return out


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
        "enacted": {
            "isLaw": stage >= 5,
            "publicLaw": cur.get("publicLaw"),
            "enactedDate": cur.get("enactedDate"),
            "textUrl": cur.get("textUrl"),
        },

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

    # Enactment metadata, from the API when the bill has become law.
    if b["stage"] >= 5:
        b["enacted"]["isLaw"] = True
        laws = detail.get("laws") or []
        if laws:
            pl = f"{laws[0].get('type', 'Public Law')} {laws[0].get('number', '')}".strip()
            b["enacted"]["publicLaw"] = pl or b["enacted"].get("publicLaw")
        b["enacted"]["enactedDate"] = b["enacted"].get("enactedDate") or b["lastDate"]
        # Curation may hand-supply a text URL; otherwise leave the congress.gov page.
        b["enacted"]["textUrl"] = b["enacted"].get("textUrl") or cur.get("textUrl") or b.get("src")

    # CRS summary only fills in if the curator left summary blank.
    if not cur.get("summary"):
        summaries = api.sub(t, n, "summaries", "summaries")
        if summaries:
            latest = summaries[-1]
            b["summary"] = latest.get("text", "").strip()
            b["summarySource"] = "crs" if b["summary"] else "none"

    b["lastRefreshed"] = now_iso()
    return b


# ---------------------------------------------------------------- Phase 3: archive
def load_archive():
    """Existing archive keyed by id. Prior entries are never lost on a refresh."""
    if ARCHIVE.exists():
        try:
            return {b["id"]: b for b in json.loads(ARCHIVE.read_text())}
        except Exception:
            return {}
    return {}


def to_archive_record(b, disposition):
    """Freeze a live docket record into the archive under a terminal disposition.
    'enacted' = became law; 'died' = end-of-Congress sweep with no enactment."""
    rec = dict(b)
    rec["disposition"] = disposition
    rec["tracked"] = False
    rec["archivedDate"] = now_iso()
    if disposition == "enacted":
        rec["enacted"] = dict(rec.get("enacted") or {})
        rec["enacted"]["isLaw"] = True
        if not rec["enacted"].get("enactedDate"):
            rec["enacted"]["enactedDate"] = rec.get("lastDate")
        rec["stage"] = max(rec.get("stage", 1), 5)
        rec["stageKey"] = X.stage_key(rec["stage"])
    else:  # died
        rec["diedDate"] = now_iso()
        rec["diedCongress"] = C.CONGRESS
    return rec


def partition_and_archive(docket, do_sweep):
    """Split enacted bills out of the live docket into the archive. On --sweep,
    also retire everything still in flight as 'died'. Returns the live docket.
    Enacted always beats died: a sweep never downgrades an enacted entry."""
    arch = load_archive()

    live = []
    for b in docket:
        already = arch.get(b["id"], {}).get("disposition") == "enacted"
        if b.get("stage", 1) >= 5 or already:
            rec = to_archive_record(b, "enacted")
            arch[b["id"]] = rec
            print(f"  archived (enacted) {b['no']}")
        else:
            live.append(b)

    if do_sweep:
        for b in live:
            if arch.get(b["id"], {}).get("disposition") == "enacted":
                continue
            arch[b["id"]] = to_archive_record(b, "died")
            print(f"  swept (died) {b['no']}")
        live = []   # the Congress is closed; nothing remains in flight

    records = list(arch.values())
    # Most recently closed first: enacted date, then died date, then archived stamp.
    def close_date(r):
        return (r.get("enacted", {}) or {}).get("enactedDate") or r.get("diedDate") or r.get("archivedDate") or ""
    records.sort(key=close_date, reverse=True)

    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVE.write_text(json.dumps(records, indent=2, ensure_ascii=False))
    print(f"Wrote {ARCHIVE.relative_to(REPO)}  ({len(records)} archived)")
    return live


# ---------------------------------------------------------------- main
def main():
    do_discover = "--discover" in sys.argv[1:]
    do_sweep = "--sweep" in sys.argv[1:]
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

    # Move enacted bills (and, on --sweep, dead ones) into the archive.
    docket = partition_and_archive(docket, do_sweep)

    # Furthest along first, then most recent action.
    docket.sort(key=lambda x: (x["stage"], x["lastDate"] or ""), reverse=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(docket, indent=2, ensure_ascii=False))
    print(f"Wrote {OUT.relative_to(REPO)}  ({len(docket)} bills)")

    if do_discover:
        if not api:
            print("discovery skipped: --discover needs CONGRESS_API_KEY", file=sys.stderr)
        else:
            print("Running committee auto-discovery...")
            untriaged = discover(api, curation)
            UNTRIAGED.parent.mkdir(parents=True, exist_ok=True)
            UNTRIAGED.write_text(json.dumps(untriaged, indent=2, ensure_ascii=False))
            print(f"Wrote {UNTRIAGED.relative_to(REPO)}  ({len(untriaged)} untriaged)")


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------- Phase 2: discovery
def base_from_discovery(ref, title):
    """A docket-shaped record for an auto-discovered bill. Mirrors base_from_curation
    but flagged untriaged: not tracked, nothing human-ratified, no provisions."""
    t, n = ref["type"], ref["number"]
    return {
        "id": f"{C.CONGRESS}-{t}-{n}",
        "slug": None,
        "congress": C.CONGRESS,
        "type": t,
        "number": n,
        "no": f"{t.upper()} {n}",
        "title": title or "",
        "officialTitle": title or "",

        "chamber": "House" if t.startswith("h") else "Senate",
        "sponsor": "",
        "party": "",
        "bipartisan": False,
        "cosponsorsCount": None,

        "committee": "",
        "committees": [],
        "policyArea": None,
        "subjects": [],
        "matchedOn": [],
        "theme": "Uncategorized",
        "core": True,

        "stage": 1,
        "stageKey": "introduced",
        "status": "",
        "latestActionText": ref.get("latestActionText", ""),
        "introducedDate": "",
        "lastDate": ref.get("lastDate", ""),
        "actions": [],

        "summary": "",
        "summarySource": "none",
        "provisions": [],

        "related": [],
        "src": f"https://www.congress.gov/bill/{C.CONGRESS}th-congress/"
               f"{'house-bill' if t == 'hr' else 'senate-bill' if t == 's' else t}/{n}",
        "crsSummaryUrl": "",

        "disposition": "in_flight",
        "enacted": {"isLaw": False, "publicLaw": None, "enactedDate": None, "textUrl": None},

        "tracked": False,
        "pinned": False,
        "review": {"theme": False, "content": False},

        "vehicle": False,
        "updateDate": None,
        "lastRefreshed": now_iso(),
    }


def hydrate_discovered(api, ref):
    """Fetch detail for one candidate, test jurisdiction + salience, and return a
    fully-populated untriaged record, or None if it does not qualify."""
    t, n = ref["type"], ref["number"]
    detail = api.bill(t, n)
    if not detail:
        return None

    title = detail.get("title") or ref.get("title", "")
    la = detail.get("latestAction", {}) or {}
    latest_text = la.get("text", ref.get("latestActionText", ""))
    policy_area = (detail.get("policyArea") or {}).get("name")

    actions_raw = api.sub(t, n, "actions", "actions")
    actions = [
        {"date": a.get("actionDate"), "type": (a.get("type") or ""), "text": a.get("text", "")}
        for a in actions_raw
    ]

    subjects_raw = api.sub(t, n, "subjects", "subjects")
    subjects = []
    if isinstance(subjects_raw, dict):
        subjects = [s.get("name") for s in subjects_raw.get("legislativeSubjects", []) if s.get("name")]

    committees_raw = api.sub(t, n, "committees", "committees")
    committees = [c.get("systemCode") for c in committees_raw if c.get("systemCode")]

    cosp = api.get(f"bill/{C.CONGRESS}/{t}/{n}/cosponsors").get("pagination", {}) or {}
    cosponsors = cosp.get("count")

    related_raw = api.sub(t, n, "relatedbills", "relatedBills")
    related = [
        {"no": f"{(r.get('type') or '').upper()} {r.get('number')}",
         "relationship": "Related", "url": r.get("url", "")}
        for r in related_raw if r.get("number")
    ]

    stage = X.derive_stage(actions, latest_text)

    # Jurisdiction gate (committee spine + policy/subject/keyword patches, minus excludes).
    in_scope, matched = X.jurisdiction_match(committees, policy_area, subjects, title)
    if not in_scope:
        return None
    # Salience gate (don't surface introduced-and-parked messaging bills).
    if not X.is_salient(cosponsors, actions, related, stage):
        return None

    b = base_from_discovery(ref, title)
    b["officialTitle"] = title
    b["latestActionText"] = latest_text
    b["lastDate"] = la.get("actionDate", b["lastDate"])
    b["introducedDate"] = detail.get("introducedDate", "")
    b["policyArea"] = policy_area
    b["updateDate"] = detail.get("updateDateIncludingText") or detail.get("updateDate")
    b["actions"] = actions
    b["subjects"] = subjects
    b["committees"] = committees
    b["cosponsorsCount"] = cosponsors
    b["related"] = related
    b["matchedOn"] = matched

    b["stage"] = stage
    b["stageKey"] = X.stage_key(stage)
    b["disposition"] = "enacted" if stage >= 5 else "in_flight"
    b["enacted"]["isLaw"] = stage >= 5

    theme = X.theme_for(subjects)
    b["theme"] = theme
    b["core"] = X.core_for(theme)

    # CRS summary, verbatim and public-domain. Provisions stay human-only (empty here).
    summaries = api.sub(t, n, "summaries", "summaries")
    if summaries:
        latest = summaries[-1]
        text = (latest.get("text") or "").strip()
        if text:
            b["summary"] = text
            b["summarySource"] = "crs"
            b["crsSummaryUrl"] = b["src"]

    b["lastRefreshed"] = now_iso()
    return b


def discover(api, curation, cap=None):
    """
    Committee auto-discovery. Pull the bill lists for the two committees, drop anything
    already curated, order by most-recent action, and hydrate up to `cap` candidates.
    Each survivor must clear jurisdiction_match + is_salient. Returns the untriaged list.
    """
    cap = C.DISCOVERY_CAP if cap is None else cap
    known = {(str(c["type"]).lower(), str(c["number"])) for c in curation}

    refs = {}
    for chamber, code in (("house", C.COMMITTEES["house"]), ("senate", C.COMMITTEES["senate"])):
        listed = api.committee_bills(chamber, code)
        print(f"  {chamber}/{code}: {len(listed)} bills referred")
        for r in listed:
            key = (r["type"], r["number"])
            if key in known:
                continue
            prev = refs.get(key)
            if prev is None or (r.get("lastDate", "") > prev.get("lastDate", "")):
                refs[key] = r

    ordered = sorted(refs.values(), key=lambda r: r.get("lastDate", "") or "", reverse=True)
    budget = ordered[:cap]
    print(f"  {len(refs)} new candidates; hydrating {len(budget)} (cap {cap})")

    untriaged = []
    for r in budget:
        try:
            b = hydrate_discovered(api, r)
            if b:
                untriaged.append(b)
                print(f"  + {b['no']:<12} stage {b['stage']}  [{','.join(b['matchedOn']) or 'none'}]  {b['summarySource']}")
        except Exception as e:
            print(f"  ! discover {r['type'].upper()} {r['number']}: {e}", file=sys.stderr)

    untriaged.sort(key=lambda x: (x["stage"], x["lastDate"] or ""), reverse=True)
    return untriaged
