"""The reasoning pieces on their own: emergency rules, urgency suggestion, similar-case matching, prescription drafting,
check-in alerts, long-recording splitting and retry behaviour. Run:  python -m tests.test_logic"""
from tests import helpers as H   # sets up a throw-away environment
import array, io, json, math, wave

from app import audio_utils, checkin_logic, gnani_client, intake_logic, prescription_logic
from app.gnani_client import GnaniError
from app.llm import LLMError
from app.matching import MIN_CASES_FOR_PATTERN, case_record, load_cases, progress_view, similar_cases
from app.red_flags import check_red_flags, has_emergency
from app.questions import QUESTIONS, TAG_VOCAB

n = 0


def check(name, cond, extra=""):
    global n
    n += 1
    print(("PASS" if cond else "FAIL"), "|", name, extra)
    assert cond, name


# ---------------------------------------------------------------- emergency rules (no AI involved)
for text, emergency in [("నాకు ఛాతీ నొప్పి వస్తోంది", True), ("నా కాలు కదలడం లేదు", True), ("I have heavy bleeding", True),
                        ("నాకు ఊపిరి ఆడటం లేదు", True), ("నా మోకాలు నొప్పిగా ఉంది", False), ("జ్వరం లేదు", False)]:
    check(f"emergency rule: {text!r}", has_emergency(check_red_flags(text)) == emergency)

# ---------------------------------------------------------------- urgency suggestion is rule-based and explains itself
u = intake_logic.suggest_urgency
check("leg weakness is suggested as high with the reason", u(["location_back", "leg_weakness"], [])["level"] == "high" and "Leg weakness reported" in u(["leg_weakness"], [])["reasons"])
check("recent injury plus cannot bear weight is high (one merged reason)", u(["injury_recent", "cannot_bear_weight"], []) == {"level": "high", "reasons": ["Recent injury and cannot bear weight"]})
check("fever with swelling is high", u(["fever", "swelling"], [])["level"] == "high")
check("night pain alone is medium", u(["pain_night"], [])["level"] == "medium")
check("no concerning signs is routine", u(["location_knee", "stairs_difficulty"], []) == {"level": "routine", "reasons": []})
check("an emergency flag always wins", u([], [{"level": "emergency", "label": "Chest pain"}])["level"] == "emergency")
intake_logic.ask_json = lambda s, uu: {"name": "A", "age": "58", "complaint_summary": "x", "medicines": "", "tags": ["location_knee", "bogus", "location_knee"], "corrections": [{"item": "name", "earlier": "A", "final": "B"}, "junk"]}
info = intake_logic.extract_intake([{"question_id": "q", "question_en": "q", "answer": "a"}])
check("extracted tags are limited to the allowed vocabulary, de-duplicated; junk corrections dropped", info["tags"] == ["location_knee"] and len(info["corrections"]) == 1 and info["age"] is None)
check("the 'latest statement wins' rule is in the model's instructions", "LATEST statement always wins" in intake_logic.SYSTEM)
check("the intake script ends with a chance to correct or add", QUESTIONS[-1]["id"] == "corrections" and len(QUESTIONS) == 9)

# ---------------------------------------------------------------- similar cases
cases = load_cases()
check("42 synthetic past cases with full records", len(cases) == 42 and all(len(c["visits"]) == 3 and c["baseline"] and c["record_no"] for c in cases))
check("every symptom tag in the cases is in the shared vocabulary", all(t in TAG_VOCAB for c in cases for v in c["visits"] for t in v["tags"]))
knee = ["location_knee", "duration_chronic", "stairs_difficulty"]
r = similar_cases(knee)
check("a knee patient matches only knee cases, grouped into three paths", r["n_matched"] == 7 and {m["diagnosis"] for m in r["matches"]} == {"Knee osteoarthritis"}
      and sum(p["count"] for p in r["paths"]) == 7)
check("suggested questions are only signs not yet reported", r["suggested_questions"] and not ({q["tag"] for q in r["suggested_questions"]} & set(knee)))
check("different body parts never match; no location means no comparison", similar_cases(["pain_night"])["n_matched"] == 0 and similar_cases(["location_hip"])["n_matched"] == 0)
tiny = similar_cases(knee, cases=[c for c in cases if c["diagnosis"] == "Knee osteoarthritis"][:2])
check("two matches is flagged as anecdote, not a pattern", not tiny["enough_evidence"] and MIN_CASES_FOR_PATTERN == 3)
check("output uses counts only: no percentages, no 'predict', no risk score", not any(w in json.dumps(r).lower() for w in ("%", "predict", "risk score")))
rec = case_record(r["matches"][0]["id"], knee)
check("a past case opens with its record and a comparison, and no name", rec["comparison"]["both"] and "name" not in rec and rec["baseline"]["diet_notes"])
base = ["location_knee", "duration_chronic", "pain_walking", "morning_stiffness", "stairs_difficulty"]
better = progress_view(base, ["location_knee"], 3)
worse = progress_view(base, ["location_knee", "pain_night", "swelling", "limited_movement", "pain_walking"], 3)
check("a recovering patient is closest to the 'improved' path", better["closest"] == "improved" and "difficulty with stairs" in better["resolved"])
check("a worsening patient is not", worse["closest"] != "improved" and "night pain" in worse["new"])

# ---------------------------------------------------------------- check-in alerts
a = checkin_logic.build_alerts({"medicine_taken": "partial", "medicine_note": "x", "pain_trend": "worse", "tags": ["numbness", "pain_night", "swelling"]}, ["swelling"], [])
levels = [x["level"] for x in a]
check("missed medicine -> family; worse pain -> doctor; ONE merged line for new signs (swelling was already there)",
      levels == ["family", "doctor", "doctor"] and "numbness or tingling, night pain" in a[2]["text"] and "swelling" not in a[2]["text"])
check("an all-clear check-in raises nothing", checkin_logic.build_alerts({"medicine_taken": "yes", "pain_trend": "better", "tags": []}, [], []) == [])
check("emergency wording is the top alert level", checkin_logic.alert_level(checkin_logic.build_alerts({}, [], [{"level": "emergency", "label": "Chest pain"}])) == "emergency")
checkin_logic.ask_json = lambda s, uu: {"medicine_taken": "MAYBE", "pain_trend": "better", "followed_diet": "yes", "tags": ["numbness", "bogus"], "corrections": []}
ex = checkin_logic.extract_checkin([{"question_id": "meds", "question_en": "q", "answer": "a"}], ["Paracetamol"])
check("unexpected model values become 'unclear' instead of being trusted", ex["medicine_taken"] == "unclear" and ex["tags"] == ["numbness"])
q = checkin_logic.build_checkin_questions(["Paracetamol", "Calcium"])
check("check-in questions name the confirmed medicines", "Paracetamol, Calcium" in q[0]["te"] and q[-1]["id"] == "corrections")

# ---------------------------------------------------------------- prescriptions
prescription_logic.ask_json = lambda s, uu: {"medicines": [{"name": "Paracetamol", "dose": "500 mg", "timing": "after food", "duration": "5 days"}, {"name": "", "dose": "x"}, "junk"],
                                             "diet_advice": ["Eat calcium", ""], "warning_signs": [], "follow_up": "3 weeks", "unclear": ["second tablet unclear"]}
d = prescription_logic.extract_prescription("doctor: ...")
check("the draft keeps only what has a medicine name; unclear items are passed on", len(d["draft"]["medicines"]) == 1 and d["draft"]["diet_advice"] == ["Eat calcium"] and d["draft"]["unclear"])
def down(s, uu): raise LLMError("down")
prescription_logic.ask_json = down
check("an unavailable model gives an empty editable draft with the reason", prescription_logic.extract_prescription("x") == {"draft": {"medicines": [], "diet_advice": [], "warning_signs": [], "follow_up": "", "unclear": []}, "error": "down"})
try:
    prescription_logic.clean_prescription({"medicines": [], "diet_advice": []}); check("an empty prescription is refused", False)
except ValueError:
    check("an empty prescription is refused", True)

# ---------------------------------------------------------------- long recordings
def make_wav(seconds, quiet_at):
    rate, s = 16000, array.array("h")
    for i in range(int(seconds * rate)):
        t = i / rate
        s.append(0 if any(qq <= t < qq + 0.6 for qq in quiet_at) else int(8000 * math.sin(2 * math.pi * 220 * t)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(s.tobytes())
    return buf.getvalue(), len(s)
data, total = make_wav(70, [23.2, 47.9])
chunks = audio_utils.split_wav(data)
lens = [wave.open(io.BytesIO(c)).getnframes() for c in chunks]
tail = array.array("h"); tail.frombytes(chunks[0][-1600:])
check("a 70 s recording is cut into 3 parts of at most 25 s, at quiet moments, losing nothing",
      len(chunks) == 3 and max(lens) <= 25 * 16000 and sum(lens) == total and max(abs(x) for x in tail) < 200)
calls = []
def fake_stt(path, language_code=None):
    calls.append(1)
    if len(calls) == 2: raise GnaniError("504")
    return f"part{len(calls)}"
text, failed = audio_utils.transcribe_long(data, transcribe_fn=fake_stt)
check("one failed part is reported and the others still transcribed", text == "part1 part3" and failed == [2])
try:
    audio_utils.split_wav(b"not a wav"); check("garbage audio is refused", False)
except ValueError:
    check("garbage audio is refused", True)

# ---------------------------------------------------------------- retries against Gnani's occasional 504
gnani_client.time.sleep = lambda s: None
class R:
    def __init__(self, code, body=None): self.status_code, self._b, self.text = code, body or {}, str(body)
    def json(self): return self._b
seq = [R(504, "gateway timeout"), R(504, "gateway timeout"), R(200, {"success": True, "transcript": "మోకాలు"})]
gnani_client.requests.post = lambda *a, **k: seq.pop(0)
open("/tmp/_t.wav", "wb").write(b"RIFFxxxx")
check("speech-to-text recovers after two 504s", gnani_client.transcribe("/tmp/_t.wav") == "మోకాలు")
gnani_client.requests.post = lambda *a, **k: R(504, "gateway timeout")
try:
    gnani_client.transcribe("/tmp/_t.wav"); check("gives up cleanly after 3 attempts", False)
except GnaniError as e:
    check("gives up cleanly after 3 attempts", "3 attempts" in str(e))
cnt = []
gnani_client.requests.post = lambda *a, **k: (cnt.append(1), R(401, "bad key"))[1]
try: gnani_client.transcribe("/tmp/_t.wav")
except GnaniError: pass
check("a bad API key is not retried", len(cnt) == 1)
print(f"\nALL {n} LOGIC CHECKS PASSED")
