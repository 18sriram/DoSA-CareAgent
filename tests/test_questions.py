"""Doctor-editable intake questions. Run:  python -m tests.test_questions"""
from tests import helpers as H

from app import db, questionsets as QS
from app.routers import calls

H.reset()
ids = H.make_users()
n = 0


def check(name, cond, extra=""):
    global n
    n += 1
    print(("PASS" if cond else "FAIL"), "|", name, extra)
    assert cond, name


desk, rao, sharma, admin = H.signed_in("desk1"), H.signed_in("dr.rao"), H.signed_in("dr.sharma"), H.signed_in("admin1")
H.install_fakes()
MINE = [{"te": "మీకు నొప్పి ఎక్కడ ఉంది?", "en": "Where is the pain?"},
        {"te": "ఈ నొప్పి ఎంత కాలం నుండి ఉంది?", "en": "How long has it been there?"},
        {"te": "నొప్పి రాత్రిపూట ఎక్కువగా ఉంటుందా?", "en": "Is the pain worse at night?"}]

# ---------------------------------------------------------------- the standard list
r = rao.get("/api/question-set").json()
check("a doctor starts with the standard list, marked as default", r["is_default"] and len(r["questions"]) == 8 and r["questions"][0]["id"] == "name_age")
check("the closing question is shown separately and cannot be edited away", r["closing"]["en"] == QS.CLOSING["en"] and all(q["id"] != "corrections" for q in r["questions"]))
check("only doctors can read or change questions", desk.get("/api/question-set").status_code == 403 and admin.put("/api/question-set", json={"questions": MINE}).status_code == 403)

# ---------------------------------------------------------------- what is accepted
bad = [([{"te": "మీకు నొప్పి ఎక్కడ ఉంది?", "en": "Where?"}], "too few questions"),
       ([{"te": "మీకు నొప్పి ఎక్కడ ఉంది?", "en": "Where is the pain?"}] * 13, "too many questions"),
       ([MINE[0], {"te": "Where does it hurt?", "en": "Where does it hurt?"}], "Telugu field written in English"),
       ([MINE[0], {"te": "", "en": "How long?"}], "empty Telugu text"),
       ([MINE[0], {"te": "ఎంత కాలం నుండి?", "en": ""}], "empty English text"),
       ([MINE[0], {"te": "క" * 301, "en": "Long"}], "Telugu text too long"),
       ("not a list", "wrong type"), ([MINE[0], "text"], "an item that is not a question")]
for payload, why in bad:
    check(f"refused: {why}", rao.put("/api/question-set", json={"questions": payload}).status_code == 400)
check("the error message tells the doctor what to fix", "Telugu" in rao.put("/api/question-set", json={"questions": [MINE[0], {"te": "Where does it hurt", "en": "x"}]}).json()["detail"])

# ---------------------------------------------------------------- saving and using
sneaky = MINE + [{"id": "corrections", "te": "తొలగించు", "en": "remove me"}]
r = rao.put("/api/question-set", json={"questions": sneaky})
check("a doctor saves their own list; a fake 'closing question' in it is dropped", r.status_code == 200 and len(r.json()["questions"]) == 3
      and all(q["id"] != "corrections" for q in r.json()["questions"]))
check("question ids are generated and unique", len({q["id"] for q in r.json()["questions"]}) == 3 and all(q["id"] for q in r.json()["questions"]))
dup = rao.put("/api/question-set", json={"questions": [{"id": "pain", "te": "మీకు నొప్పి ఎక్కడ ఉంది?", "en": "Where is the pain?"}, {"id": "pain", "te": "ఎంత కాలం నుండి?", "en": "How long?"}]})
check("two questions asking for the same id are made distinct", dup.status_code == 200 and len({q["id"] for q in dup.json()["questions"]}) == 2)
rao.put("/api/question-set", json={"questions": MINE})
check("the saved list is returned as the doctor's own", rao.get("/api/question-set").json()["is_default"] is False and len(rao.get("/api/question-set").json()["questions"]) == 3)
check("another doctor still has the standard list", sharma.get("/api/question-set").json()["is_default"])
eff = QS.effective_questions(ids["rao"])
check("a patient of this doctor is asked their list plus the closing question", [q["en"] for q in eff] == [m["en"] for m in MINE] + [QS.CLOSING["en"]])
check("a patient of another doctor is asked the standard 9", len(QS.effective_questions(ids["sharma"])) == 9)

# ---------------------------------------------------------------- the call really uses it
prompts = []
import app.intake_logic as IL
orig = IL.ask_json
IL.ask_json = lambda s, u: (prompts.append(u), {"name": "", "age": None, "complaint_summary": "x", "medicines": "", "tags": ["location_knee"], "corrections": []})[1]
v = H.register(desk, ids["rao"], full_name="Custom Patient", phone="9000000010")
start = H.Client()
first = start.post("/api/call/start", json={"token": v["token"]}).json()
check("the call announces 4 questions (3 of the doctor's plus the closing one)", first["total"] == 4)
r = H.say(start, first["session_id"], "అవును").json()
check("the first question the patient hears is the doctor's own", r["agent_text"] == MINE[0]["te"] and r["total"] == 4)
# the doctor edits the list while the call is in progress: this call is unaffected
rao.put("/api/question-set", json={"questions": MINE[:2]})
r = H.say(start, first["session_id"], "మోకాలు").json()
check("an edit made during a call does not change that call", r["agent_text"] == MINE[1]["te"] and r["total"] == 4)
H.say(start, first["session_id"], "రెండు నెలలు")
last = H.say(start, first["session_id"], "అవును").json()
last = H.say(start, first["session_id"], "ఏమీ లేదు").json()
check("the call finishes after the doctor's questions and the closing question", last["done"])
check("the doctor's own question wording reaches the language model", any("Is the pain worse at night?" in p for p in prompts))
iid = rao.get("/api/queue").json()["patients"][0]["intake_id"]
tr = rao.get(f"/api/intakes/{iid}").json()["transcript"]
check("the doctor sees their own English questions next to the answers", [t["question_en"] for t in tr][:3] == [m["en"] for m in MINE] and tr[-1]["question_en"] == QS.CLOSING["en"])
v2 = H.register(desk, ids["rao"], full_name="Next Patient", phone="9000000011")
second = H.Client().post("/api/call/start", json={"token": v2["token"]}).json()
check("the next patient gets the doctor's newest list", second["total"] == 3)

# ---------------------------------------------------------------- reset
check("a doctor can go back to the standard list", rao.delete("/api/question-set").status_code == 200 and rao.get("/api/question-set").json()["is_default"])
v3 = H.register(desk, ids["rao"], full_name="Third Patient", phone="9000000012")
check("after a reset new calls use the standard 9 questions again", H.Client().post("/api/call/start", json={"token": v3["token"]}).json()["total"] == 9)
IL.ask_json = orig

# ---------------------------------------------------------------- hearing a question
check("a doctor can hear a question as the patient will", rao.post("/api/question-set/preview", json={"text": MINE[0]["te"]}).headers["content-type"] == "audio/wav")
check("a preview needs Telugu text", rao.post("/api/question-set/preview", json={"text": "Where is the pain?"}).status_code == 400)
check("only doctors can preview", desk.post("/api/question-set/preview", json={"text": MINE[0]["te"]}).status_code == 403)
from app.routers import doctor as D
D.preview_limiter.reset()
statuses = [rao.post("/api/question-set/preview", json={"text": MINE[0]["te"]}).status_code for _ in range(32)]
check("previews are rate limited (each one costs a speech call)", statuses[0] == 200 and statuses[-1] == 429)

actions = {a["action"] for a in db.list_audit(limit=2000)}
check("saving and resetting questions is audited", {"question_set_updated", "question_set_reset"} <= actions)
print(f"\nALL {n} QUESTION-SET CHECKS PASSED")
