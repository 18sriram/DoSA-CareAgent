"""The inbound help line: who may call, what it will and will not answer, and what happens to everything else.
Run:  python -m tests.test_helpline"""
from tests import helpers as H
import io

from app import db, inbound_logic
from app.llm import LLMError
from app.routers import calls

H.reset()
ids = H.make_users()
db.update_user(ids["rao"], supervisor_id=ids["sharma"])
n = 0


def check(name, cond, extra=""):
    global n
    n += 1
    print(("PASS" if cond else "FAIL"), "|", name, extra)
    assert cond, name


desk, rao, sharma, admin = H.signed_in("desk1"), H.signed_in("dr.rao"), H.signed_in("dr.sharma"), H.signed_in("admin1")
H.install_fakes()

# ---------------------------------------------------------------- a patient with a confirmed prescription
v = H.register(desk, ids["rao"], full_name="Ramu Patient", phone="+91 98765-43210")
PIN = v["pin"]
check("registration issues a 6-digit PIN once, and stores only its hash", len(PIN) == 6 and PIN.isdigit()
      and PIN not in str(db.get_patient(db.get_visit(v["visit_id"])["patient_id"])))
check("the phone number is stored in a standard form", db.get_patient(db.get_visit(v["visit_id"])["patient_id"])["phone"] == "9876543210")
H.run_call(H.Client(), v["token"], H.INTAKE_ANSWERS)
iid = rao.get("/api/queue").json()["patients"][0]["intake_id"]
rx = rao.post(f"/api/intakes/{iid}/consultation", data={"consent": "true", "transcript": "x"}).json()["prescription_id"]
rao.post(f"/api/prescriptions/{rx}/confirm", json={"medicines": [
    {"name": "Paracetamol", "dose": "500 mg", "timing": "after food, three times a day", "duration": "5 days"},
    {"name": "Calcium with vitamin D", "dose": "1 tablet", "timing": "after dinner", "duration": "30 days"}],
    "diet_advice": ["Eat calcium-rich foods", "Avoid fried food"], "warning_signs": ["Night pain increasing"], "follow_up": "After 3 weeks"})
patient_id = db.get_visit(v["visit_id"])["patient_id"]


def call_in(phone, pin, client=None):
    return (client or H.Client()).post("/api/inbound/start", json={"phone": phone, "pin": pin})


def talk(client, sid, text):
    r = H.say(client, sid, text)
    assert r.status_code == 200, r.text
    return r.json()


def classify_as(**kw):
    base = {"intent": "other", "topics": [], "medicine_name": "", "bank_id": None, "tags": [], "urgent": False, "question_en": "q"}
    base.update(kw)
    inbound_logic.ask_json = lambda s, u: base


# ---------------------------------------------------------------- identification
check("a caller with the wrong PIN is refused", call_in("9876543210", "000000").status_code == 401)
check("an unknown number is refused with the very same message", call_in("9000099999", "123456").json() == call_in("9876543210", "000000").json())
check("a missing PIN is refused", call_in("9876543210", "").status_code == 401)
r = call_in("+91 98765 43210", PIN)
check("the right phone number (in any format) and PIN start the call", r.status_code == 200 and r.json()["kind"] == "inbound")
check("the start response reveals nothing about the patient", set(r.json()) <= {"session_id", "total", "step", "kind", "agent_text", "audio_url"})
spam = H.Client()
for _ in range(5):
    call_in("9876543210", "111111", spam)
check("five wrong PINs lock the number, even for the right PIN", call_in("9876543210", PIN, spam).status_code == 429)
with db.conn() as c:
    c.execute("DELETE FROM pin_attempts")
from app import security
security.call_limiter.reset()

# ---------------------------------------------------------------- reading from the record
caller = H.Client()
start = call_in("9876543210", PIN, caller).json()
sid = start["session_id"]
r = talk(caller, sid, "అవును")
check("after consent the agent asks how it can help; consent is recorded", inbound_logic.HELP_PROMPT in r["agent_text"] and not r["done"])
classify_as(intent="facts", topics=["medicines"], medicine_name="paracetamol", question_en="What is my paracetamol dose?")
r = talk(caller, sid, "పారాసెటమాల్ ఎలా వేసుకోవాలి")
check("a medicine question is answered word for word from the confirmed prescription",
      "Paracetamol: 500 mg, after food, three times a day, 5 days" in r["agent_text"] and "Calcium" not in r["agent_text"])
classify_as(intent="facts", topics=["diet", "warning_signs", "follow_up"])
r = talk(caller, sid, "ఆహారం ఏమి తినాలి")
check("diet, warning signs and the next visit are read from the record",
      all(x in r["agent_text"] for x in ("Eat calcium-rich foods", "Avoid fried food", "Night pain increasing", "After 3 weeks")))
check("answers from the record are not sent to the doctor", db.count_open_messages(ids["rao"]) == 0)

# ---------------------------------------------------------------- what is not in the record goes to the doctor
classify_as(intent="facts", topics=["medicines"], medicine_name="ibuprofen", question_en="Can I take ibuprofen?")
r = talk(caller, sid, "ఐబుప్రోఫెన్ వేసుకోవచ్చా")
check("a medicine that was never prescribed is NOT answered; the caller is told it is not in the record",
      "Ibuprofen" not in r["agent_text"] and inbound_logic.NOT_IN_RECORD in r["agent_text"] and inbound_logic.SENT_TO_DOCTOR in r["agent_text"])
classify_as(intent="other", question_en="Is it normal that my knee clicks?")
r = talk(caller, sid, "మోకాలు శబ్దం చేస్తోంది, ఇది సాధారణమేనా")
check("a general medical question is never answered by the agent; it is passed on", inbound_logic.SENT_TO_DOCTOR in r["agent_text"])
classify_as(intent="symptom_report", tags=["numbness", "leg_weakness"], question_en="New numbness and weakness in the leg")
r = talk(caller, sid, "కాలు తిమ్మిరి, బలహీనంగా ఉంది")
check("a new symptom is recorded for the doctor with a safety message", inbound_logic.SENT_SYMPTOMS in r["agent_text"])
open_msgs = rao.get("/api/messages").json()["open"]
by_kind = {m["kind"]: m for m in open_msgs}
check("the doctor receives all three, most urgent first", [m["kind"] for m in open_msgs][0] == "symptom_report" and len(open_msgs) == 3)
check("the symptom report is marked urgent by the fixed rules, with tags", by_kind["symptom_report"]["priority"] == "urgent"
      and "leg weakness" in by_kind["symptom_report"]["tag_labels"])
check("each message shows who it is about and the context", by_kind["question"]["name"] == "Ramu Patient"
      and by_kind["question"]["context"]["medicines"] and "phone" not in by_kind["question"])
check("the caller's own words are kept next to the English summary", by_kind["symptom_report"]["question_original"] and by_kind["symptom_report"]["question_en"])
classify_as(intent="end")
r = talk(caller, sid, "అంతే ధన్యవాదాలు")
check("the caller can end the call", r["done"] and inbound_logic.GOODBYE in r["agent_text"])
check("the finished call cannot be continued", H.say(caller, sid, "hello").status_code == 400)
H.SPEECH.clear()      # the refused request never consumed its scripted words

# ---------------------------------------------------------------- the doctor answers; the patient hears it next time
q = by_kind["question"]
check("only the assigned doctor can reply", sharma.post(f"/api/messages/{q['id']}/reply", json={"text": "No"}).status_code == 404)
check("an empty reply is refused", rao.post(f"/api/messages/{q['id']}/reply", json={"text": " "}).status_code == 400)
check("the doctor replies", rao.post(f"/api/messages/{q['id']}/reply", json={"text": "Clicking is common; avoid deep squats.", "reusable": True}).status_code == 200)
caller2 = H.Client()
start2 = call_in("9876543210", PIN, caller2).json()
r = talk(caller2, start2["session_id"], "అవును")
check("on the next call the agent first reads out the doctor's reply", "Clicking is common; avoid deep squats." in r["agent_text"] and inbound_logic.HELP_PROMPT in r["agent_text"]
      and ".." not in r["agent_text"], r["agent_text"][:300])
caller3 = H.Client()
r = talk(caller3, call_in("9876543210", PIN, caller3).json()["session_id"], "అవును")
check("...exactly once", "Clicking is common" not in r["agent_text"])

# ---------------------------------------------------------------- doctor-approved standard answers
v2 = H.register(desk, ids["rao"], full_name="Other Patient", phone="9000000077")
c4 = H.Client()
sid4 = call_in("9000000077", v2["pin"], c4).json()["session_id"]
talk(c4, sid4, "అవును")
bank = db.bank_for_doctor(ids["rao"])
check("a reply saved as 'standard answer' is stored for this doctor", len(bank) == 1 and bank[0]["answer_text"].startswith("Clicking"))
classify_as(intent="facts", topics=[], bank_id=bank[0]["id"], question_en="my knee clicks")
r = talk(c4, sid4, "నా మోకాలు శబ్దం")
check("another of the doctor's patients gets the doctor-approved answer", "Clicking is common; avoid deep squats." in r["agent_text"] and db.count_open_messages(ids["rao"]) == 2)
classify_as(intent="facts", topics=[], bank_id=99999, question_en="x")
r = talk(c4, sid4, "ఏదో")
check("an answer id the model invented is ignored (the question goes to the doctor)", inbound_logic.SENT_TO_DOCTOR in r["agent_text"] or inbound_logic.NOT_IN_RECORD in r["agent_text"])
check("a patient with no confirmed prescription gets nothing from the record",
      (classify_as(intent="facts", topics=["medicines"]), "Paracetamol" not in talk(c4, sid4, "మందులు ఏవి")["agent_text"])[1])

# ---------------------------------------------------------------- emergencies
c5 = H.Client()
sid5 = call_in("9876543210", PIN, c5).json()["session_id"]
talk(c5, sid5, "అవును")
r = talk(c5, sid5, "నాకు ఛాతీ నొప్పి వస్తోంది")
check("emergency wording ends the call with the emergency message", r["done"] and r["emergency"])
ems = desk.get("/api/emergencies").json()
check("reception sees the emergency with the phone number to ring, nothing clinical", len(ems) == 1 and ems[0]["phone"] == "9876543210"
      and ems[0]["name"] == "Ramu Patient" and "tags" not in ems[0])
check("the doctor also gets it, first in the inbox", rao.get("/api/messages").json()["open"][0]["kind"] == "emergency")
check("an emergency is closed by phoning the patient, not answered by text", rao.post(f"/api/messages/{rao.get('/api/messages').json()['open'][0]['id']}/reply", json={"text": "ok"}).status_code == 400)
check("only reception or admin can mark it handled", rao.post(f"/api/emergencies/{ems[0]['id']}/handled").status_code == 403
      and desk.post(f"/api/emergencies/{ems[0]['id']}/handled").status_code == 200 and desk.get("/api/emergencies").json() == [])

# ---------------------------------------------------------------- nobody answers: it moves up the hierarchy
before = db.count_open_messages(ids["rao"])
with db.conn() as c:
    c.execute("UPDATE escalations SET due_at=? WHERE status='open' AND assigned_to=?", (db.later(minutes=-5), ids["rao"]))
inbox = sharma.get("/api/messages").json()["open"]
check("unanswered messages move to the doctor's supervisor", db.count_open_messages(ids["rao"]) == 0 and len(inbox) == before)
check("the supervisor sees who it came from and how late it is", all(m["forwarded_from_name"] == "Dr. Rao" for m in inbox))
check("the supervisor sees enough context to act", inbox[0]["context"]["complaint"] is not None and inbox[0]["name"])
with db.conn() as c:
    c.execute("UPDATE escalations SET due_at=? WHERE status='open'", (db.later(minutes=-5),))
sharma.get("/api/messages")
check("with no one above them, a message stays put instead of looping", db.count_open_messages(ids["sharma"]) == before)
check("the original doctor can no longer answer a forwarded message", rao.post(f"/api/messages/{inbox[0]['id']}/reply", json={"text": "late"}).status_code == 404)
ordinary = next(m for m in inbox if m["kind"] != "emergency")
check("the supervisor answers an ordinary message", sharma.post(f"/api/messages/{ordinary['id']}/reply", json={"text": "Please come in on Monday."}).status_code == 200)
emerg = next(m for m in inbox if m["kind"] == "emergency")
check("an emergency forwarded up the chain is still closed by phoning, not by text", sharma.post(f"/api/messages/{emerg['id']}/reply", json={"text": "ok"}).status_code == 400)

# ---------------------------------------------------------------- supervisors are set by an administrator
check("a doctor cannot be their own supervisor", admin.patch(f"/api/admin/users/{ids['rao']}", json={"supervisor_id": ids["rao"]}).status_code == 400)
check("two doctors cannot supervise each other", admin.patch(f"/api/admin/users/{ids['sharma']}", json={"supervisor_id": ids["rao"]}).status_code == 400)
check("only a doctor can be a supervisor", admin.patch(f"/api/admin/users/{ids['rao']}", json={"supervisor_id": ids["desk"]}).status_code == 400)
check("an administrator can clear it", admin.patch(f"/api/admin/users/{ids['rao']}", json={"supervisor_id": None}).json()["supervisor_id"] is None)

# ---------------------------------------------------------------- when the model is down, the call still helps safely
def down(s, u): raise LLMError("groq unavailable")
inbound_logic.ask_json = down
c6 = H.Client()
sid6 = call_in("9876543210", PIN, c6).json()["session_id"]
talk(c6, sid6, "అవును")
r = talk(c6, sid6, "నా మందులు ఏవి")
check("with the language model down the question goes to the doctor; nothing is guessed", inbound_logic.SENT_TO_DOCTOR in r["agent_text"] and "Paracetamol" not in r["agent_text"])

# ---------------------------------------------------------------- PIN management and audit
patient = db.get_visit(v["visit_id"])["patient_id"]
new = desk.post(f"/api/patients/{patient}/new-pin").json()
check("reception can issue a new PIN; the old one stops working", new["pin"] != PIN and call_in("9876543210", PIN).status_code == 401
      and call_in("9876543210", new["pin"]).status_code == 200)
check("doctors cannot issue PINs", rao.post(f"/api/patients/{patient}/new-pin").status_code == 403)
returning = desk.post("/api/visits", json={"patient_id": patient, "doctor_id": ids["rao"]}).json()
check("a returning patient's slip does not reveal or change the PIN", returning["pin"] is None)
# ---------------------------------------------------------------- the doctor can see and remove standard answers
bank_list = rao.get("/api/answer-bank").json()
check("a doctor sees the standard answers they approved", len(bank_list) == 1 and bank_list[0]["answer_text"].startswith("Clicking is common"))
check("another doctor sees none of them", sharma.get("/api/answer-bank").json() == [])
check("another doctor cannot delete them", sharma.delete(f"/api/answer-bank/{bank_list[0]['id']}").status_code == 404)
check("reception cannot see or delete them", desk.get("/api/answer-bank").status_code == 403 and desk.delete(f"/api/answer-bank/{bank_list[0]['id']}").status_code == 403)
inbound_logic.ask_json = lambda s, u: {"intent": "facts", "topics": [], "medicine_name": "", "bank_id": bank_list[0]["id"], "tags": [], "urgent": False, "choice": None, "question_en": "my knee clicks"}
check("the doctor deletes a standard answer", rao.delete(f"/api/answer-bank/{bank_list[0]['id']}").status_code == 200 and rao.get("/api/answer-bank").json() == [])
c9 = H.Client()
sid9 = call_in("9000000077", v2["pin"], c9).json()["session_id"]
talk(c9, sid9, "అవును")
r = talk(c9, sid9, "నా మోకాలు శబ్దం")
check("a deleted standard answer is never read out again; the question goes to the doctor instead",
      "Clicking is common" not in r["agent_text"] and inbound_logic.SENT_TO_DOCTOR in r["agent_text"])

actions = {a["action"] for a in db.list_audit(limit=2000)}
needed = {"standard_answer_deleted", "helpline_call_started", "helpline_verification_failed", "escalation_created", "message_answered", "escalation_forwarded", "helpline_call_ended",
          "emergency_call_handled", "pin_reissued", "messages_viewed"}
check("the help line leaves a full audit trail", needed <= actions, str(needed - actions))
print(f"\nALL {n} HELPLINE CHECKS PASSED")
