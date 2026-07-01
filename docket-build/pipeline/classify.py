"""
Classification helpers. Pure functions over already-fetched bill data.
Used by fetch.py. Phase 1 leans on curation for tracked bills; these
functions are the Phase 2 auto-discovery machinery and the fallbacks.
"""
import config as C


def derive_stage(actions, latest_action_text=""):
    """Highest stage floor implied by the action feed. Falls back to text sniffing."""
    stage = 1
    for a in actions or []:
        t = (a.get("type") or "").strip()
        stage = max(stage, C.ACTION_TYPE_STAGE.get(t, 1))
    if stage == 1 and latest_action_text:
        low = latest_action_text.lower()
        if "became public law" in low or "signed by president" in low:
            stage = 5
        elif "resolving differences" in low or "conference" in low:
            stage = 4
        elif "passed" in low or "agreed to in" in low:
            stage = 3
        elif "reported" in low or "ordered to be reported" in low or "calendar" in low:
            stage = 2
    return stage


def stage_key(stage):
    return C.STAGE_KEY.get(stage, "introduced")


def theme_for(subjects):
    """First-guess theme from CRS legislative subjects. Human ratifies in curation."""
    for s in subjects or []:
        if s in C.SUBJECT_THEME_MAP:
            return C.SUBJECT_THEME_MAP[s]
    return "Uncategorized"


def core_for(theme):
    return C.THEME_CORE.get(theme, True)


def jurisdiction_match(committees, policy_area, subjects, title):
    """
    Return (in_scope, matched_on[]). Committee referral is the spine;
    policy area and subject terms are the recall patches; keywords are last.
    """
    matched = []
    codes = {c.lower() for c in (committees or [])}
    if C.COMMITTEES["house"] in codes or C.COMMITTEES["senate"] in codes:
        matched.append("committee")
    if policy_area == C.POLICY_AREA_IN:
        matched.append("policyArea")
    if any(s in C.SUBJECTS_IN for s in (subjects or [])):
        matched.append("subject")

    low = (title or "").lower()
    if any(k in low for k in C.KEYWORDS_IN):
        matched.append("keyword")

    excluded = any(k in low for k in C.KEYWORDS_OUT)
    in_scope = bool(matched) and not excluded
    return in_scope, matched


def is_salient(cosponsors_count, actions, related, stage):
    """Does the bill clear the salience floor for auto-surfacing?"""
    s = C.SALIENCE
    if cosponsors_count is not None and cosponsors_count >= s["min_cosponsors"]:
        return True
    if s["reported"] and stage >= 2:
        return True
    if s["floor_action"] and stage >= 3:
        return True
    if s["has_companion"] and related:
        return True
    return False
