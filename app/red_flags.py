"""Rule-based emergency check. No AI involved: fixed phrases in Telugu and English.
Runs on EVERY transcript (intake, reminder and inbound calls). Demo-grade phrase lists:
a hospital would have its doctors review and extend them.
"""
import unicodedata

EMERGENCY_MESSAGE = {
    "te": "ఇది అత్యవసర పరిస్థితి కావచ్చు. దయచేసి వెంటనే సమీప అత్యవసర విభాగానికి వెళ్ళండి. మా సిబ్బందికి తెలియజేస్తున్నాము.",
    "en": "This may be an emergency. Please go to the nearest emergency department now. We are alerting our staff.",
}

RULES = [
    {"id": "chest_pain", "level": "emergency", "label": "Chest pain",
     "any": ["ఛాతీ నొప్పి", "ఛాతి నొప్పి", "గుండె నొప్పి", "chest pain"]},
    {"id": "breathlessness", "level": "emergency", "label": "Difficulty breathing",
     "any": ["ఊపిరి ఆడ", "ఊపిరాడ", "శ్వాస తీసుకోలే", "breathless", "can't breathe", "cannot breathe"]},
    {"id": "heavy_bleeding", "level": "emergency", "label": "Heavy bleeding",
     "any": ["రక్తస్రావం", "రక్తం ఆగ", "heavy bleeding", "bleeding"]},
    {"id": "fainting", "level": "emergency", "label": "Fainting or loss of consciousness",
     "any": ["స్పృహ తప్ప", "మూర్ఛ", "faint", "unconscious"]},
    {"id": "open_fracture", "level": "emergency", "label": "Bone visible or open wound over a fracture",
     "any": ["ఎముక బయట", "ఎముక కనిపిస్తోంది", "open fracture", "bone sticking"]},
    {"id": "bladder_bowel", "level": "emergency", "label": "Loss of bladder or bowel control with back or leg symptoms",
     "any": ["మూత్రం ఆపుకోలే", "మలమూత్ర", "మూత్రం కంట్రోల్", "lost control of bladder", "bladder control"]},
    {"id": "sudden_paralysis", "level": "emergency", "label": "Sudden inability to move a limb",
     "any": ["పక్షవాతం", "కాలు కదలడం లేదు", "చేయి కదలడం లేదు", "paralysis", "cannot move my leg", "cannot move my arm"]},
]


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text or "").lower()


def check_red_flags(text: str) -> list:
    """Return the list of rules (id, level, label) that match the transcript."""
    t = _norm(text)
    hits = []
    for rule in RULES:
        matched = False
        if "any" in rule:
            matched = any(_norm(p) in t for p in rule["any"])
        elif "all_of" in rule:
            matched = all(any(_norm(p) in t for p in group) for group in rule["all_of"])
        if matched:
            hits.append({"id": rule["id"], "level": rule["level"], "label": rule["label"]})
    return hits


def has_emergency(hits: list) -> bool:
    return any(h["level"] == "emergency" for h in hits)
