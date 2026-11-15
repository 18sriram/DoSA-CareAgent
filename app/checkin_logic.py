"""The follow-up check-in call: questions built from the doctor-CONFIRMED prescription, extraction of what the
patient reported, and rule-based alerts. The AI reads the answers; the alert rules are plain and visible."""
from app.llm import LLMError, ask_json
from app.questions import QUESTIONS, TAG_VOCAB

CHECKIN_CLOSING = {
    "te": "ధన్యవాదాలు. మీ సమాచారం డాక్టర్‌కు పంపబడింది. త్వరగా కోలుకోండి.",
    "en": "Thank you. Your details have been sent to the doctor. Get well soon.",
}
_CORRECTIONS_Q = next(q for q in QUESTIONS if q["id"] == "corrections")


def build_checkin_questions(medicine_names: list) -> list:
    names = ", ".join(n for n in medicine_names if n)
    meds_te = (f"డాక్టర్ గారు ఇచ్చిన మందులు ({names}) ఈ రోజు వేసుకున్నారా?" if names
               else "డాక్టర్ గారు ఇచ్చిన మందులు ఈ రోజు వేసుకున్నారా?")
    return [
        {"id": "meds", "en": f"Did you take your medicines today ({names or 'as prescribed'})?", "te": meds_te},
        {"id": "diet", "en": "What did you eat today? Are you following the diet advice?",
         "te": "ఈ రోజు మీరు ఏమి తిన్నారు? డాక్టర్ గారు చెప్పిన ఆహార సూచనలు పాటిస్తున్నారా?"},
        {"id": "pain", "en": "How is your pain now compared with the first visit?",
         "te": "ఇప్పుడు మీ నొప్పి ఎలా ఉంది? మొదటిసారి కంటే తగ్గిందా, అలాగే ఉందా, లేక పెరిగిందా?"},
        {"id": "new_signs", "en": "Any new swelling, numbness, weakness, night pain or other new problem?",
         "te": "వాపు, తిమ్మిరి, బలహీనత లేదా రాత్రిపూట నొప్పి వంటి కొత్త సమస్యలు ఏమైనా ఉన్నాయా?"},
        {"id": "corrections", "en": _CORRECTIONS_Q["en"], "te": _CORRECTIONS_Q["te"]},
    ]


SYSTEM = (
    "You extract structured data from a Telugu follow-up phone call with an orthopaedic patient. "
    "Answer with a JSON object only. Use ONLY what the patient actually said. Never guess. "
    "The patient may correct earlier statements at any point; the answers are in time order and the LATEST "
    "statement always wins. Report FINAL values. A symptom the patient denied or took back must NOT be in tags."
)
_ENUMS = {"medicine_taken": {"yes", "partial", "no"}, "followed_diet": {"yes", "partly", "no"},
          "pain_trend": {"better", "same", "worse"}}


def extract_checkin(qa: list, medicines: list) -> dict:
    lines = "\n".join(f"Q ({x['question_id']}): {x['question_en']}\nA: {x['answer']}" for x in qa)
    vocab = "\n".join(f"- {k}: {v}" for k, v in TAG_VOCAB.items())
    user = (
        f"Prescribed medicines: {', '.join(medicines) or 'not listed'}\n\nConversation (answers in Telugu):\n{lines}\n\n"
        f"Allowed symptom tags (use only these ids):\n{vocab}\n\n"
        'Return JSON: {"medicine_taken": "yes|partial|no|unclear", "medicine_note": "short English note, e.g. which '
        'medicine was missed and why", "diet_summary": "one short English sentence on what they ate", '
        '"followed_diet": "yes|partly|no|unclear", "pain_trend": "better|same|worse|unclear", '
        '"tags": [tag ids for symptoms the patient has NOW], '
        '"corrections": [{"item": "", "earlier": "", "final": ""}]}'
    )
    try:
        out = ask_json(SYSTEM, user)
    except LLMError as e:
        return {"medicine_taken": "unclear", "medicine_note": "", "diet_summary": "", "followed_diet": "unclear",
                "pain_trend": "unclear", "tags": [], "corrections": [], "needs_manual_review": True, "error": str(e)}
    res = {k: (str(out.get(k, "")).strip().lower() if str(out.get(k, "")).strip().lower() in allowed else "unclear")
           for k, allowed in _ENUMS.items()}
    res["medicine_note"] = str(out.get("medicine_note") or "").strip()
    res["diet_summary"] = str(out.get("diet_summary") or "").strip()
    res["tags"] = [t for t in dict.fromkeys(out.get("tags") or []) if t in TAG_VOCAB]
    res["corrections"] = [{k: str(c.get(k, "")).strip() for k in ("item", "earlier", "final")}
                          for c in (out.get("corrections") or [])[:10] if isinstance(c, dict)]
    res["needs_manual_review"] = False
    return res


# Signs that deserve a doctor's attention if they were NOT present at the first visit.
WARNING_TAGS = ["numbness", "leg_weakness", "arm_weakness", "cannot_bear_weight", "joint_instability",
                "locking_catching", "fever", "pain_night", "swelling"]
ALERT_RANK = {"none": 0, "family": 1, "doctor": 2, "emergency": 3}


def build_alerts(info: dict, baseline_tags: list, red_flags: list) -> list:
    """Plain rules. Returns [{'level': 'emergency'|'doctor'|'family', 'text': ...}]."""
    alerts = []
    for f in red_flags:
        if f["level"] == "emergency":
            alerts.append({"level": "emergency", "text": f"Emergency wording during the call: {f['label']}"})
    if info.get("medicine_taken") in ("no", "partial"):
        note = f" ({info['medicine_note']})" if info.get("medicine_note") else ""
        word = "not taken" if info["medicine_taken"] == "no" else "only partly taken"
        alerts.append({"level": "family", "text": f"Medicine {word}{note}. A family member is reminded (simulated)."})
    if info.get("pain_trend") == "worse":
        alerts.append({"level": "doctor", "text": "Patient reports the pain is worse than at the first visit."})
    base = set(baseline_tags or [])
    new = [TAG_VOCAB[t] for t in WARNING_TAGS if t in (info.get("tags") or []) and t not in base]
    if new:
        alerts.append({"level": "doctor", "text": "New since the first visit: " + ", ".join(new)})
    return alerts


def alert_level(alerts: list) -> str:
    return max((a["level"] for a in alerts), key=lambda l: ALERT_RANK[l], default="none")
