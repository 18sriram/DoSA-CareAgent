"""Each doctor chooses what their patients are asked in the pre-visit call.

The doctor edits a list of questions (Telugu for the patient, English for the dashboard). The closing question
"anything to change or add?" is always added last: the correction feature depends on it, so it cannot be removed.
"""
import re

from app import db
from app.questions import QUESTIONS

MIN_Q, MAX_Q = 2, 12
TELUGU = re.compile(r"[\u0C00-\u0C7F]")
CLOSING = next(q for q in QUESTIONS if q["id"] == "corrections")


def default_editable() -> list:
    """The standard orthopaedic list, without the fixed closing question."""
    return [{"id": q["id"], "te": q["te"], "en": q["en"]} for q in QUESTIONS if q["id"] != "corrections"]


def validate(items) -> list:
    """Clean what a doctor submitted. Raises ValueError with a plain message a doctor can act on."""
    if not isinstance(items, list):
        raise ValueError("The question list is not valid.")
    clean, seen = [], set()
    for i, raw in enumerate(items, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"Question {i} is not valid.")
        te, en = str(raw.get("te", "")).strip(), str(raw.get("en", "")).strip()
        if raw.get("id") == "corrections":
            continue                                           # the closing question is added automatically
        if len(te) < 5 or len(en) < 3:
            raise ValueError(f"Question {i}: write both the Telugu and the English text.")
        if len(te) > 300 or len(en) > 200:
            raise ValueError(f"Question {i} is too long (Telugu up to 300 characters, English up to 200).")
        if not TELUGU.search(te):
            raise ValueError(f"Question {i}: the patient hears the Telugu text, so it must be written in Telugu.")
        qid = re.sub(r"[^a-z0-9_]", "", str(raw.get("id") or "").lower())[:40]
        if not qid or qid in seen:
            words = re.sub(r"[^a-z0-9 ]", "", en.lower()).split()[:3]
            base = "_".join(words) or "q"
            qid, n = base, 2
            while qid in seen or qid == "corrections":
                qid, n = f"{base}_{n}", n + 1
        seen.add(qid)
        clean.append({"id": qid, "te": te, "en": en})
    if not MIN_Q <= len(clean) <= MAX_Q:
        raise ValueError(f"Choose between {MIN_Q} and {MAX_Q} questions (the closing question is added for you).")
    return clean


def effective_questions(doctor_id) -> list:
    """What a patient of this doctor is asked: the doctor's own list if they made one, otherwise the standard list."""
    custom = db.get_question_set(doctor_id) if doctor_id else None
    base = custom["questions"] if custom else default_editable()
    return [{**q, "tags": []} for q in base] + [CLOSING]
