"""Similar-case matching: compares a patient's symptom tags with anonymised past cases and shows how those
cases progressed. It does NOT predict anything. It shows paths seen in similar cases, with counts, for the doctor.
"""
import json
import os
from collections import Counter, defaultdict

from app.questions import TAG_VOCAB

CASES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "synthetic_cases.json")
LOCATION = {t for t in TAG_VOCAB if t.startswith("location_")}
DURATION = {t for t in TAG_VOCAB if t.startswith("duration_")}
PATH_ORDER = ["improved", "imaging_injection", "surgical_referral"]
PATH_LABELS = {
    "improved": "Improved with conservative treatment",
    "imaging_injection": "Needed imaging or an injection",
    "surgical_referral": "Referred for surgery or specialist procedure",
}
MIN_SIMILARITY = 0.35        # below this a case is not considered similar
MIN_CASES_FOR_PATTERN = 3    # fewer matches than this is anecdote, not a pattern
MAX_MATCHES = 12
_cache = {}


def load_cases():
    if "cases" not in _cache:
        try:
            with open(CASES_FILE, encoding="utf-8") as f:
                _cache["cases"] = json.load(f)
        except FileNotFoundError:
            _cache["cases"] = []
    return _cache["cases"]


def _w(tag):
    return 2.0 if tag in LOCATION else 0.5 if tag in DURATION else 1.0


def similarity(a: set, b: set) -> float:
    """Weighted overlap between two symptom sets (0 to 1). Location counts double, duration half."""
    union = sum(_w(t) for t in a | b)
    return sum(_w(t) for t in a & b) / union if union else 0.0


def _empty(note):
    return {"matches": [], "paths": [], "suggested_questions": [], "n_matched": 0,
            "enough_evidence": False, "note": note}


def similar_cases(tags, cases=None) -> dict:
    cases = load_cases() if cases is None else cases
    if not cases:
        return _empty("No past cases are loaded. Run make_cases.py.")
    p = set(tags or [])
    locs = p & LOCATION
    if not locs:
        return _empty("The location of the problem was not captured, so no comparison was made.")

    matches = []
    for c in cases:
        first = set(c["visits"][0]["tags"])
        if not (locs & first & LOCATION):     # a knee patient is never compared with a shoulder case
            continue
        score = similarity(p, first)
        if score >= MIN_SIMILARITY:
            matches.append((score, c))
    matches.sort(key=lambda x: -x[0])
    matches = matches[:MAX_MATCHES]
    if not matches:
        return _empty("No sufficiently similar past cases were found.")

    groups = defaultdict(list)
    for score, c in matches:
        groups[c["path"]].append((score, c))
    total = len(matches)

    def signs_at_week3(group):
        cnt = Counter(t for _, c in group for t in c["visits"][1]["tags"] if t not in LOCATION | DURATION)
        return cnt

    improved_group = groups.get("improved", [])
    improved_cnt = signs_at_week3(improved_group)

    paths, suggestions = [], {}
    for key in PATH_ORDER:
        grp = groups.get(key)
        if not grp:
            continue
        cnt = signs_at_week3(grp)
        n = len(grp)
        signs = [{"tag": t, "label": TAG_VOCAB[t], "count": k, "patient_has": t in p}
                 for t, k in cnt.most_common() if k / n >= 0.5]
        paths.append({"path": key, "label": PATH_LABELS[key], "count": n, "of": total, "signs": signs,
                      "outcomes": sorted({c["outcome"] for _, c in grp})[:2]})
        if key != "improved":
            for s in signs:
                rate_improved = (improved_cnt[s["tag"]] / len(improved_group)) if improved_group else 0
                if not s["patient_has"] and rate_improved < 0.5:   # a sign that tells the paths apart
                    best = suggestions.get(s["tag"])
                    if best is None or s["count"] > best["seen_in"]:
                        suggestions[s["tag"]] = {"tag": s["tag"], "label": s["label"], "seen_in": s["count"],
                                                 "of": n, "path_label": PATH_LABELS[key]}
    sug = sorted(suggestions.values(), key=lambda s: (-s["seen_in"] / s["of"], -s["seen_in"]))[:4]

    out_matches = []
    for score, c in matches:
        first = set(c["visits"][0]["tags"])
        out_matches.append({
            "id": c["id"], "age": c["age"], "sex": c["sex"], "diagnosis": c["diagnosis"],
            "similarity": round(score, 2), "path": c["path"], "outcome": c["outcome"],
            "shared": [TAG_VOCAB[t] for t in sorted(p & first)],
            "week3": [TAG_VOCAB[t] for t in c["visits"][1]["tags"] if t not in LOCATION | DURATION],
            "week6": [TAG_VOCAB[t] for t in c["visits"][2]["tags"] if t not in LOCATION | DURATION],
        })
    return {"matches": out_matches, "paths": paths, "suggested_questions": sug, "n_matched": total,
            "enough_evidence": total >= MIN_CASES_FOR_PATTERN, "note": ""}


def case_record(case_id, patient_tags=None, cases=None):
    """Full de-identified record of one past case, plus how it compares with the current patient."""
    cases = load_cases() if cases is None else cases
    c = next((x for x in cases if x["id"] == case_id), None)
    if c is None:
        return None
    first = set(c["visits"][0]["tags"])
    out = {
        "id": c["id"], "record_no": c.get("record_no", ""), "age": c["age"], "sex": c["sex"],
        "diagnosis": c["diagnosis"], "path": c["path"], "path_label": PATH_LABELS[c["path"]], "outcome": c["outcome"],
        "baseline": c.get("baseline", {}),
        "visits": [{"week": v["week"], "date": v.get("date", ""), "symptoms": [TAG_VOCAB[t] for t in v["tags"]],
                    "findings": v.get("findings", ""), "investigations": v.get("investigations", ""),
                    "treatment": v.get("treatment", "")} for v in c["visits"]],
        "comparison": None,
    }
    if patient_tags is not None:
        p = set(patient_tags)
        out["comparison"] = {"both": [TAG_VOCAB[t] for t in sorted(p & first)],
                             "only_case": [TAG_VOCAB[t] for t in sorted(first - p)],
                             "only_patient": [TAG_VOCAB[t] for t in sorted(p - first)],
                             "similarity": round(similarity(p, first), 2)}
    return out


def progress_view(baseline_tags, now_tags, week=3, cases=None):
    """How the patient is doing now: (1) against their OWN first visit, (2) against how similar past cases looked
    at the same point. Shown to the doctor as information only."""
    base, now = set(baseline_tags or []), set(now_tags or [])
    signs = lambda s: {t for t in s if t not in LOCATION | DURATION}
    res = {"week": week, "resolved": [TAG_VOCAB[t] for t in sorted(signs(base) - now)],
           "persisting": [TAG_VOCAB[t] for t in sorted(signs(base) & now)],
           "new": [TAG_VOCAB[t] for t in sorted(signs(now) - base)],
           "alignment": [], "closest": None, "closest_label": "", "n_matched": 0, "enough_evidence": False}
    sim = similar_cases(base, cases)
    if not sim["n_matched"]:
        return res
    pool = {c["id"]: c for c in (load_cases() if cases is None else cases)}
    idx = 1 if week <= 4 else 2
    loc = base & LOCATION
    now_cmp = signs(now) | loc
    scores = defaultdict(list)
    for mt in sim["matches"]:
        c = pool[mt["id"]]
        case_set = signs(set(c["visits"][idx]["tags"])) | loc
        scores[c["path"]].append(similarity(now_cmp, case_set))
    for key in PATH_ORDER:
        if key in scores:
            res["alignment"].append({"path": key, "label": PATH_LABELS[key], "count": len(scores[key]),
                                     "score": round(sum(scores[key]) / len(scores[key]), 2)})
    res["n_matched"] = sim["n_matched"]
    res["enough_evidence"] = sim["enough_evidence"]
    ranked = sorted(res["alignment"], key=lambda a: -a["score"])
    if ranked and (len(ranked) == 1 or ranked[0]["score"] - ranked[1]["score"] >= 0.05):
        res["closest"], res["closest_label"] = ranked[0]["path"], ranked[0]["label"]
    return res
