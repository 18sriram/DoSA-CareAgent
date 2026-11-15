"""Turns a consultation transcript into a DRAFT prescription card. The doctor edits and confirms it.
The model may only copy what the doctor said; it must not invent, correct or add anything."""
from app.llm import LLMError, ask_json

SYSTEM = (
    "You extract a prescription from the transcript of a doctor-patient consultation at an orthopaedic clinic "
    "(mostly Telugu with English medical words). Answer with a JSON object only. "
    "Use ONLY what the DOCTOR actually said. Copy medicine names and doses exactly as spoken; never invent, "
    "convert or 'fix' a dose; never add advice the doctor did not give. "
    "If something is unclear or missing, leave that field empty and describe the doubt in 'unclear'."
)


def _clean_list(value, limit=8):
    return [str(x).strip() for x in (value or []) if str(x).strip()][:limit] if isinstance(value, list) else []


def extract_prescription(transcript: str) -> dict:
    """Returns {'draft': {...}, 'error': ''}. On failure the draft is empty so the doctor can type it in."""
    empty = {"medicines": [], "diet_advice": [], "warning_signs": [], "follow_up": "", "unclear": []}
    user = (
        f"Consultation transcript:\n{transcript}\n\n"
        'Return JSON: {"medicines": [{"name": "", "dose": "", "timing": "", "duration": ""}], '
        '"diet_advice": ["each diet instruction the doctor gave"], '
        '"warning_signs": ["things the doctor told the patient to watch for or return for"], '
        '"follow_up": "when the doctor wants to see the patient again, or empty", '
        '"unclear": ["anything you were unsure about"]}'
    )
    try:
        out = ask_json(SYSTEM, user)
    except LLMError as e:
        return {"draft": empty, "error": str(e)}
    meds = []
    for m in (out.get("medicines") or [])[:10] if isinstance(out.get("medicines"), list) else []:
        if isinstance(m, dict) and str(m.get("name", "")).strip():
            meds.append({k: str(m.get(k, "")).strip() for k in ("name", "dose", "timing", "duration")})
    return {"draft": {"medicines": meds, "diet_advice": _clean_list(out.get("diet_advice")),
                      "warning_signs": _clean_list(out.get("warning_signs")),
                      "follow_up": str(out.get("follow_up") or "").strip(),
                      "unclear": _clean_list(out.get("unclear"))}, "error": ""}


def clean_prescription(obj: dict) -> dict:
    """Validate what the doctor submits when confirming. Raises ValueError if it is empty."""
    meds = []
    for m in (obj.get("medicines") or []) if isinstance(obj.get("medicines"), list) else []:
        if isinstance(m, dict) and str(m.get("name", "")).strip():
            meds.append({k: str(m.get(k, "")).strip() for k in ("name", "dose", "timing", "duration")})
    out = {"medicines": meds[:10], "diet_advice": _clean_list(obj.get("diet_advice")),
           "warning_signs": _clean_list(obj.get("warning_signs")), "follow_up": str(obj.get("follow_up") or "").strip()}
    if not out["medicines"] and not out["diet_advice"]:
        raise ValueError("A prescription needs at least one medicine or one diet instruction.")
    return out
