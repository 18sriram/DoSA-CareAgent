"""The help line's brain. Two deliberately separate jobs:

1. classify(): the language model only decides WHAT the caller is asking about (a category), never the answer.
2. compose_facts(): the spoken answer is built from the doctor-CONFIRMED prescription with fixed Telugu templates,
   so a dose, timing or instruction is read out exactly as the doctor wrote it and can never be invented.

Anything that is not in the record is passed to the doctor.
"""
from app.llm import LLMError, ask_json
from app.questions import TAG_VOCAB

HELP_PROMPT = "నేను మీకు ఎలా సహాయం చేయగలను?"
ANYTHING_ELSE = "ఇంకేమైనా కావాలా?"
GOODBYE = "ధన్యవాదాలు. జాగ్రత్తగా ఉండండి."
SENT_TO_DOCTOR = "మీ ప్రశ్నను డాక్టర్ గారికి పంపాను. సమాధానం రాగానే తదుపరి కాల్‌లో తెలియజేస్తాము."
SENT_SYMPTOMS = "మీ లక్షణాలను డాక్టర్ గారికి పంపాను. పరిస్థితి తీవ్రమైతే వెంటనే ఆసుపత్రికి రండి."
NOT_IN_RECORD = "ఆ సమాచారం మీ రికార్డులో లేదు."
DOCTOR_REPLY = "మీ డాక్టర్ గారి సమాధానం: "
CALL_LIMIT = "ఈ కాల్‌లో ఇంకా సహాయం చేయలేను. దయచేసి మళ్ళీ కాల్ చేయండి."
OFFER_INTRO = "డాక్టర్ గారి ఖాళీ సమయాలు. "
ORDINALS = ["ఒకటి", "రెండు", "మూడు"]
ASK_CHOICE = "ఏ సమయం కావాలి? ఒకటి, రెండు లేదా మూడు అని చెప్పండి."
NO_SLOTS = "ప్రస్తుతం ఖాళీ సమయాలు లేవు. మా సిబ్బంది మిమ్మల్ని సంప్రదిస్తారు."
BOOKED = "మీ అపాయింట్‌మెంట్ ఖరారు అయ్యింది: "
TAKEN = "క్షమించండి, ఆ సమయం ఇప్పుడే వేరే వారికి ఇవ్వబడింది. "
INFO_NONE = "మీకు ప్రస్తుతం అపాయింట్‌మెంట్ లేదు."
INFO = "మీ అపాయింట్‌మెంట్: "
NO_DOCTOR = "క్షమించండి, ఇప్పుడు డాక్టర్ గారికి సందేశం పంపలేకపోతున్నాను. దయచేసి ఆసుపత్రి రిసెప్షన్‌ను సంప్రదించండి."

INTENTS = ("facts", "symptom_report", "appointment_slots", "appointment_book", "appointment_info", "end", "other")
TOPICS = ("medicines", "diet", "warning_signs", "follow_up")

SYSTEM = (
    "You are the first step of a hospital help line for orthopaedic patients who speak Telugu. "
    "Decide what the caller wants and answer with a JSON object only. "
    "You NEVER answer medical questions yourself and never give advice; you only choose a category."
)


def classify(heard: str, bank: list, offered_count: int = 0) -> dict:
    """What is the caller asking about? Returns a validated dict; on any problem the intent is 'other' (-> a doctor)."""
    bank_lines = "\n".join(f"- id {b['id']}: {b['question_en']}" for b in bank) or "(none)"
    vocab = "\n".join(f"- {k}: {v}" for k, v in TAG_VOCAB.items())
    user = (
        f"The caller said (Telugu): {heard}\n\n"
        "Categories (intent):\n"
        '- "facts": asking about something their doctor already told them or prescribed: medicines (names, doses, when to take), '
        "diet advice, warning signs to watch for, or when to come back. Put the matching topics in 'topics' "
        f"(allowed: {', '.join(TOPICS)}). If they name one medicine, put it in 'medicine_name'.\n"
        '- "symptom_report": describing a new or worsening symptom, pain or problem.\n'
        '- "appointment_slots": wants to know when the doctor is free, or wants to book or arrange a visit.\n'
        '- "appointment_info": asks when their own booked appointment is.\n'
        + (f'- "appointment_book": they are choosing one of the {offered_count} times just read out to them. Put the number they chose '
           f"(1 to {offered_count}) in 'choice'; use null if it is not clear which one.\n" if offered_count else "")
        + '- "end": they want to finish (thanks, goodbye, nothing else).\n'
        '- "other": anything else, including ANY medical question that is not about what the doctor already recorded '
        "(is this normal, other medicines, exercises, test results, side effects).\n\n"
        f"Standard answers already approved by the doctor:\n{bank_lines}\n"
        "If the caller's question means the same as one of them, give its id in 'bank_id'.\n\n"
        f"Symptom tags (for symptom_report only):\n{vocab}\n\n"
        'Return JSON: {"intent": "...", "topics": [], "medicine_name": "", "bank_id": null, '
        '"tags": [], "urgent": false, "choice": null, "question_en": "the caller\'s question or problem in one short English sentence"}'
    )
    try:
        out = ask_json(SYSTEM, user)
    except LLMError as e:
        return {"intent": "other", "topics": [], "medicine_name": "", "bank_id": None, "tags": [], "urgent": False, "choice": None,
                "question_en": "(Automatic understanding was unavailable. Please read the original words.)", "error": str(e)}
    intent = out.get("intent") if out.get("intent") in INTENTS else "other"
    topics = [t for t in dict.fromkeys(out.get("topics") or []) if t in TOPICS]
    ids = {b["id"] for b in bank}
    bank_id = out.get("bank_id") if isinstance(out.get("bank_id"), int) and out.get("bank_id") in ids else None
    return {"intent": intent, "topics": topics, "medicine_name": str(out.get("medicine_name") or "").strip(),
            "bank_id": bank_id, "choice": out.get("choice") if isinstance(out.get("choice"), int) and 1 <= out.get("choice") <= max(offered_count, 0) else None,
            "tags": [t for t in dict.fromkeys(out.get("tags") or []) if t in TAG_VOCAB],
            "urgent": bool(out.get("urgent")), "question_en": str(out.get("question_en") or "").strip()[:300] or "(not stated)"}


def compose_facts(topics: list, medicine_name: str, rx: dict) -> dict:
    """Build the spoken answer from the confirmed prescription. Returns {'text', 'answered', 'missing'}."""
    parts, answered, missing = [], [], []
    rx = rx or {}
    for topic in topics:
        if topic == "medicines":
            meds = rx.get("medicines") or []
            if medicine_name:
                meds = [m for m in meds if medicine_name.lower() in m["name"].lower()]
            if meds:
                lines = ["{}: {}".format(m["name"], ", ".join(x for x in (m.get("dose"), m.get("timing"), m.get("duration")) if x)) for m in meds]
                parts.append("డాక్టర్ గారు ఇచ్చిన మందులు. " + ". ".join(lines) + ".")
                answered.append(topic)
            else:
                missing.append(topic)
        elif topic == "diet" and rx.get("diet_advice"):
            parts.append("ఆహార సూచనలు: " + "; ".join(rx["diet_advice"]) + ".")
            answered.append(topic)
        elif topic == "warning_signs" and rx.get("warning_signs"):
            parts.append("ఈ లక్షణాలు పెరిగితే వెంటనే ఆసుపత్రిని సంప్రదించండి: " + "; ".join(rx["warning_signs"]) + ".")
            answered.append(topic)
        elif topic == "follow_up" and rx.get("follow_up"):
            parts.append("తదుపరి సందర్శన: " + rx["follow_up"] + ".")
            answered.append(topic)
        else:
            missing.append(topic)
    return {"text": " ".join(parts), "answered": answered, "missing": missing}
