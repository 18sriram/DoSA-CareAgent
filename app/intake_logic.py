"""Turns the intake conversation into a filled form, symptom tags and a SUGGESTED urgency.
The LLM only extracts what the patient said. Urgency comes from visible rules, and the doctor confirms it."""
from app.llm import ask_json, LLMError
from app.questions import TAG_VOCAB

SYSTEM = (
    "You extract structured data from a Telugu phone intake at an orthopaedic clinic. "
    "Answer with a JSON object only. Use ONLY what the patient actually said. "
    "Never guess a diagnosis and never add symptoms the patient did not mention. "
    "IMPORTANT: patients forget things and change their minds. At ANY point in the conversation, whatever the "
    "question being asked, a patient may correct something said earlier (for example a different name or age, "
    "'sorry, I meant the other knee', 'no swelling, I said that by mistake') or add something they forgot. "
    "The answers are in time order and the LATEST statement always wins. Report the FINAL values. "
    "A symptom the patient took back must NOT appear in tags."
)


def extract_intake(qa: list) -> dict:
    """qa = [{'question_id','question_en','answer'}...]. Returns name, age, complaint_summary, medicines, tags."""
    lines = "\n".join(f"Q ({x['question_id']}): {x['question_en']}\nA: {x['answer']}" for x in qa)
    vocab = "\n".join(f"- {k}: {v}" for k, v in TAG_VOCAB.items())
    user = (
        f"Conversation (patient answers are in Telugu):\n{lines}\n\n"
        f"Allowed symptom tags (use only these ids):\n{vocab}\n\n"
        'Return JSON: {"name": string or "", "age": integer or null, '
        '"complaint_summary": "one factual English sentence", "medicines": string or "", '
        '"tags": [tag ids the patient\'s FINAL answers clearly support], '
        '"corrections": [{"item": "short English label, e.g. name", "earlier": "what they said first", '
        '"final": "what they finally said"}] (empty list if the patient never changed anything)}'
    )
    try:
        out = ask_json(SYSTEM, user)
        tags = []
        for t in out.get("tags", []):
            if t in TAG_VOCAB and t not in tags:
                tags.append(t)
        age = out.get("age")
        corrections = []
        for c in (out.get("corrections") or [])[:10]:
            if isinstance(c, dict):
                corrections.append({k: str(c.get(k, "")).strip() for k in ("item", "earlier", "final")})
        return {"name": (out.get("name") or "").strip(),
                "age": age if isinstance(age, int) else None,
                "complaint_summary": (out.get("complaint_summary") or "").strip(),
                "medicines": (out.get("medicines") or "").strip(),
                "tags": tags, "corrections": corrections, "needs_manual_review": False}
    except LLMError as e:
        return {"name": "", "age": None, "complaint_summary": "Automatic summary unavailable - see transcript.",
                "medicines": "", "tags": [], "corrections": [], "needs_manual_review": True, "error": str(e)}


def suggest_urgency(tags: list, red_flags: list) -> dict:
    """Visible rules, not a black box. Returns {'level', 'reasons'}. This is a SUGGESTION for the doctor."""
    reasons = []
    if any(f["level"] == "emergency" for f in red_flags):
        return {"level": "emergency", "reasons": [f["label"] for f in red_flags if f["level"] == "emergency"]}
    for f in red_flags:
        reasons.append(f["label"])
    level = "routine"
    if reasons:
        level = "high"
    t = set(tags)
    if "leg_weakness" in t:
        level, reasons = "high", reasons + ["Leg weakness reported"]
    if "fever" in t and "swelling" in t:
        level, reasons = "high", reasons + ["Fever with swelling (check for joint infection)"]
    covered = set()
    if "injury_recent" in t and "cannot_bear_weight" in t:
        level, reasons = "high", reasons + ["Recent injury and cannot bear weight"]
        covered = {"injury_recent", "cannot_bear_weight"}
    medium_signs = {"injury_recent": "Recent injury", "numbness": "Numbness or tingling",
                    "arm_weakness": "Arm weakness", "pain_night": "Night pain",
                    "cannot_bear_weight": "Cannot bear weight", "joint_instability": "Joint gives way",
                    "locking_catching": "Joint locking"}
    for tag, label in medium_signs.items():
        if tag in t and tag not in covered and label not in reasons:
            reasons.append(label)
            if level == "routine":
                level = "medium"
    return {"level": level, "reasons": reasons}
