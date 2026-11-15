"""What an administrator relies on: honest numbers, real erasure, and safe settings. Run:  python -m tests.test_ops"""
from tests import helpers as H
import io, json
from datetime import datetime

from app import config, db, healthcheck, inbound_logic, scheduling as S, security
from app.routers import calls

H.reset()
ids = H.make_users()
S.now_fn = lambda: datetime(2026, 11, 9, 8, 0)
n = 0


def check(name, cond, extra=""):
    global n
    n += 1
    print(("PASS" if cond else "FAIL"), "|", name, extra)
    assert cond, name


desk, rao, admin = H.signed_in("desk1"), H.signed_in("dr.rao"), H.signed_in("admin1")
H.install_fakes()
rao.put("/api/schedule", json={"windows": [{"weekday": 0, "start": "09:00", "end": "12:00"}, {"weekday": 2, "start": "14:00", "end": "16:00"}], "slot_minutes": 30})


def classify_as(**kw):
    base = {"intent": "other", "topics": [], "medicine_name": "", "bank_id": None, "tags": [], "urgent": False, "choice": None, "question_en": "q"}
    base.update(kw)
    inbound_logic.ask_json = lambda s, u: base


def helpline(phone, pin):
    c = H.Client()
    sid = c.post("/api/inbound/start", json={"phone": phone, "pin": pin}).json()["session_id"]
    H.say(c, sid, "అవును")
    return c, sid


# ---------------------------------------------------------------- build a day of real activity
a = H.register(desk, ids["rao"], full_name="Alpha Patient", phone="9000000101")
b = H.register(desk, ids["rao"], full_name="Bravo Patient", phone="9000000102")
H.register(desk, ids["rao"], full_name="Waiting Patient", phone="9000000103")            # registered, never called
H.run_call(H.Client(), a["token"], H.INTAKE_ANSWERS)
H.run_call(H.Client(), b["token"], ["నా పేరు బ్రావో", "నాకు ఛాతీ నొప్పి వస్తోంది"])
q = {p["name"]: p for p in rao.get("/api/queue").json()["patients"]}
rao.post(f"/api/intakes/{q['Alpha Patient']['intake_id']}/review", json={"urgency": "routine"})       # agrees with the suggestion
iid = q["Alpha Patient"]["intake_id"]
rx = rao.post(f"/api/intakes/{iid}/consultation", data={"consent": "true", "transcript": "x"}).json()["prescription_id"]
rao.post(f"/api/prescriptions/{rx}/confirm", json={"medicines": [{"name": "Paracetamol", "dose": "500 mg", "timing": "after food", "duration": "5 days"}],
                                                   "diet_advice": ["Eat well"], "warning_signs": ["Night pain"], "follow_up": "3 weeks"})
tok = rao.post(f"/api/prescriptions/{rx}/checkin-link").json()["url"].split("t=")[1].split("&")[0]
H.install_fakes(checkin=lambda s, u: {"medicine_taken": "partial", "medicine_note": "x", "diet_summary": "x", "followed_diet": "no",
                                      "pain_trend": "worse", "tags": ["location_knee", "pain_night"], "corrections": []})
H.run_call(H.Client(), tok, H.CHECKIN_ANSWERS, kind="checkin")
ck = rao.get(f"/api/intakes/{iid}").json()["checkins"][0]
rao.post(f"/api/checkins/{ck['id']}/acknowledge")
# the help line: one answer from the record, one question passed on and answered, one booking by phone
c, sid = helpline("9000000101", a["pin"])
classify_as(intent="facts", topics=["medicines"])
H.say(c, sid, "మందులు")
classify_as(intent="other", question_en="Can I swim?")
H.say(c, sid, "ఈత")
classify_as(intent="appointment_slots")
H.say(c, sid, "అపాయింట్‌మెంట్")
classify_as(intent="appointment_book", choice=1)
H.say(c, sid, "ఒకటి")
msg = rao.get("/api/messages").json()["open"]
swim = next(m for m in msg if m["question_en"] == "Can I swim?")
rao.post(f"/api/messages/{swim['id']}/reply", json={"text": "Yes, gently.", "reusable": True})
pid_b = db.get_visit(b["visit_id"])["patient_id"]
desk.post("/api/appointments", json={"patient_id": pid_b, "doctor_id": ids["rao"], "starts_at": "2026-11-11T14:00"})

# ---------------------------------------------------------------- the numbers
s = admin.get("/api/admin/analytics?days=30").json()
check("visits and completed intake calls are counted; the one who never called lowers the rate",
      s["intake"]["visits_registered"] == 3 and s["intake"]["calls_completed"] == 2 and s["intake"]["completion_rate"] == 0.667)
check("average call length is measured", s["intake"]["avg_call_minutes"] is not None and s["intake"]["avg_call_minutes"] >= 0)
check("urgency levels are counted (the emergency was caught)", s["intake"]["by_urgency"]["emergency"] == 1 and s["intake"]["by_urgency"]["routine"] == 1)
check("doctor decisions: one agreed, one still waiting", s["decisions"]["reviewed"] == 1 and s["decisions"]["agreed_with_suggestion"] == 1 and s["decisions"]["waiting_for_review"] == 1)
check("time to review is measured", s["decisions"]["median_minutes_to_review"] is not None)
check("prescriptions and check-in results are counted", s["followup"]["prescriptions_confirmed"] == 1 and s["followup"]["checkin_calls"] == 1
      and s["followup"]["medicine_taken"]["partial"] == 1 and s["followup"]["pain_trend"]["worse"] == 1 and s["followup"]["alerts"]["doctor"] == 1)
check("time to acknowledge an alert is measured", s["followup"]["median_minutes_to_acknowledge"] is not None)
h = s["helpline"]
check("help line: calls, answers from the record, and questions passed on", h["calls"] == 1 and h["answered_from_record"] == 1 and h["passed_to_a_doctor"] == 1)
check("help line: doctor reply time and standard answers", h["median_minutes_to_doctor_reply"] is not None and h["standard_answers_in_use"] == 1)
check("appointments: phone and reception bookings counted separately", s["appointments"]["booked_by_phone"] == 1 and s["appointments"]["booked_by_reception"] == 1)
check("an unsupported time range falls back to 30 days", admin.get("/api/admin/analytics?days=5").json()["days"] == 30 and admin.get("/api/admin/analytics?days=90").json()["days"] == 90)
blob = json.dumps(s)
check("the numbers contain no patient names, phones or record numbers", not any(x in blob for x in ("Alpha", "Bravo", "9000000101", "MRN-")))
check("only an administrator can see them", rao.get("/api/admin/analytics").status_code == 403 and desk.get("/api/admin/analytics").status_code == 403
      and H.Client().get("/api/admin/analytics").status_code == 401)

# ---------------------------------------------------------------- erasing a patient
pid_a = db.get_visit(a["visit_id"])["patient_id"]
mrn_a = db.get_patient(pid_a)["mrn"]
found = admin.get("/api/admin/patients?q=Alpha").json()
check("an administrator can find a patient to erase, seeing identification only", len(found) == 1 and set(found[0]) == {"id", "mrn", "full_name", "age", "phone_last4", "created_at"})
check("only an administrator can search or erase", rao.get("/api/admin/patients?q=Alpha").status_code == 403 and desk.post(f"/api/admin/patients/{pid_a}/erase", json={"confirm_mrn": mrn_a}).status_code == 403
      and rao.post(f"/api/admin/patients/{pid_a}/erase", json={"confirm_mrn": mrn_a}).status_code == 403)
check("erasing needs the record number typed exactly", admin.post(f"/api/admin/patients/{pid_a}/erase", json={}).status_code == 400
      and admin.post(f"/api/admin/patients/{pid_a}/erase", json={"confirm_mrn": "MRN-WRONG"}).status_code == 400
      and db.get_patient(pid_a)["full_name"] == "Alpha Patient")
live, live_sid = helpline("9000000101", a["pin"])
check("a help-line call is in progress for this patient", live_sid in calls.SESSIONS)
r = admin.post(f"/api/admin/patients/{pid_a}/erase", json={"confirm_mrn": mrn_a.lower()})
check("the administrator erases the patient (the record number is case-insensitive)", r.status_code == 200 and r.json()["mrn"] == mrn_a)
p = db.get_patient(pid_a)
check("the name, age, sex, phone and PIN are gone", p["full_name"] == "Erased patient" and p["phone"] is None and p["age"] is None and p["pin_hash"] is None and p["mrn"] == mrn_a)
with db.conn() as c:
    intake = dict(c.execute("SELECT * FROM intakes WHERE patient_id=?", (pid_a,)).fetchone())
    rxrow = dict(c.execute("SELECT * FROM prescriptions WHERE intake_id=?", (intake["id"],)).fetchone())
    ckrow = dict(c.execute("SELECT * FROM checkins WHERE intake_id=?", (intake["id"],)).fetchone())
    escs = [dict(r) for r in c.execute("SELECT * FROM escalations WHERE patient_id=?", (pid_a,))]
    bank = c.execute("SELECT COUNT(*) FROM answer_bank").fetchone()[0]
    appts = [dict(r) for r in c.execute("SELECT status FROM appointments WHERE patient_id=?", (pid_a,))]
    consents = c.execute("SELECT COUNT(*) FROM consents WHERE patient_id=?", (pid_a,)).fetchone()[0]
check("the intake call content and symptoms are wiped", json.loads(intake["form_json"]) == {"erased": True} and intake["transcript_json"] == "[]" and intake["tags_json"] == "[]")
check("the prescription, its transcript and the reminder link are wiped", rxrow["transcript"] is None and rxrow["final_json"] == "{}" and rxrow["checkin_token_hash"] is None)
check("the check-in call content is wiped", ckrow["transcript_json"] == "[]" and ckrow["result_json"] == "{}")
check("messages from the help line are wiped, and no standard answer made from them survives",
      all(e["question_en"] == "(erased)" and e["answer_text"] is None and e["question_original"] is None for e in escs) and bank == 0)
check("future appointments are cancelled", all(x["status"] == "cancelled" for x in appts) and appts)
check("the consent log is kept as legal evidence", consents >= 2)
check("a call in progress for this patient is ended", live_sid not in calls.SESSIONS and H.say(live, live_sid, "x").status_code == 400)
H.SPEECH.clear()
check("the old PIN and phone no longer open the help line", H.Client().post("/api/inbound/start", json={"phone": "9000000101", "pin": a["pin"]}).status_code == 401)
check("the old reminder link and visit link are dead", H.Client().post("/api/call/start", json={"token": tok, "kind": "checkin"}).status_code == 404
      and H.Client().post("/api/call/start", json={"token": a["token"]}).status_code == 404)
check("the doctor can still open the erased record without errors, and sees it is erased", rao.get(f"/api/intakes/{intake['id']}").status_code == 200
      and rao.get(f"/api/intakes/{intake['id']}").json()["name"] == "Erased patient")
check("erasing twice is refused", admin.post(f"/api/admin/patients/{pid_a}/erase", json={"confirm_mrn": mrn_a}).status_code == 400)
s2 = admin.get("/api/admin/analytics").json()
check("anonymous counts survive the erasure", s2["intake"]["calls_completed"] == 2 and s2["followup"]["checkin_calls"] == 1)
audit = [x for x in db.list_audit("patient_erased")]
check("the erasure is audited with the record number only", len(audit) == 1 and audit[0]["detail"] == {"mrn": mrn_a} and audit[0]["role"] == "admin")
other = db.get_patient(pid_b)
check("other patients are untouched", other["full_name"] == "Bravo Patient" and other["phone"])

# ---------------------------------------------------------------- hardening
r = H.Client().get("/login")
check("no HSTS header on plain HTTP", "strict-transport-security" not in {k.lower() for k in r.headers})
config.COOKIE_SECURE = True
r = H.Client().get("/login")
cl = H.Client()
login = cl.login("dr.rao")
check("over HTTPS the browser is told to insist on it", "max-age=31536000" in r.headers.get("strict-transport-security", ""))
check("over HTTPS the session cookie is marked Secure", "Secure" in login.headers.get("set-cookie", ""))
config.COOKIE_SECURE = False
start = H.Client()
sid = start.post("/api/call/start", json={"token": H.register(desk, ids["rao"], full_name="Big Upload", phone="9000000104")["token"]}).json()["session_id"]
big = start.post("/api/call/turn", data={"session_id": sid}, files={"audio": ("a.wav", io.BytesIO(b"x" * (4 * 1024 * 1024)), "audio/wav")})
check("an oversized answer recording is refused", big.status_code == 413)
check("a huge request is refused before it is read", rao.c.post("/api/messages/1/reply", content=b"x" * (17 * 1024 * 1024), headers={"Content-Type": "application/json", **rao.h}).status_code == 413)
check("a doctor's oversized consultation recording is refused", rao.post(f"/api/intakes/{iid}/consultation", data={"consent": "true"},
      files={"audio": ("c.wav", io.BytesIO(b"x" * (13 * 1024 * 1024)), "audio/wav")}).status_code == 413)

# ---------------------------------------------------------------- the pre-launch check
problems, warnings = healthcheck.run()
check("the check flags an http address that is not localhost", any("https" in p for p in problems))
check("the check does not flag demo accounts that do not exist", not any("DEMO password" in p for p in problems))
from app import demo_data
db.create_user("admin", "Demo Admin", "admin", security.hash_password(demo_data.DEMO_PASSWORD))
problems, _ = healthcheck.run()
check("the check flags an account that still has the published demo password", any("'admin'" in p and "DEMO password" in p for p in problems))
db.update_user(db.get_user_by_username("admin")["id"], password_hash=security.hash_password("A-Real-Secret-2026"))
check("...and stops flagging it once the password is changed", not any("DEMO password" in p for p in healthcheck.run()[0]))
import os
saved = os.environ.pop("GROQ_API_KEY")
check("the check flags a missing API key", any("GROQ_API_KEY" in p for p in healthcheck.run()[0]))
os.environ["GROQ_API_KEY"] = saved
print(f"\nALL {n} OPS CHECKS PASSED")
