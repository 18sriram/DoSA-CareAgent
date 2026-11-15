"""A whole patient journey through the real API: register -> intake call -> doctor review -> consultation ->
prescription -> reminder call -> alerts -> follow-up. Run:  python -m tests.test_flow"""
from tests import helpers as H
from app import db, monitor
from app.llm import LLMError

H.reset()
ids = H.make_users()
H.install_fakes()
n = 0


def check(name, cond, extra=""):
    global n
    n += 1
    print(("PASS" if cond else "FAIL"), "|", name, extra)
    assert cond, name


desk, rao, admin = H.signed_in("desk1"), H.signed_in("dr.rao"), H.signed_in("admin1")

# ---------------------------------------------------------------- reception registers a visit
v = H.register(desk, ids["rao"], full_name="Ramu Patient", age=58)
check("reception registers a patient and gets an MRN and QR token", v["mrn"].startswith("MRN-") and v["qr"].startswith("data:image/svg"))
rows = desk.get("/api/visits").json()
check("the visit appears for reception as waiting, with no clinical data", rows[0]["status"] == "registered" and "tags" not in rows[0] and "transcript" not in rows[0])
q = rao.get("/api/queue").json()["patients"]
check("the doctor sees the patient as waiting for the intake call", len(q) == 1 and q[0]["intake_id"] is None and q[0]["name"] == "Ramu Patient")
check("bad registration input is refused", desk.post("/api/visits", json={"doctor_id": ids["rao"], "full_name": "X", "age": 5}).status_code == 400
      and desk.post("/api/visits", json={"doctor_id": ids["rao"], "full_name": "Valid Name", "age": 500}).status_code == 400
      and desk.post("/api/visits", json={"doctor_id": ids["rao"], "full_name": "Valid Name", "phone": "abc"}).status_code == 400
      and desk.post("/api/visits", json={"doctor_id": 9999, "full_name": "Valid Name"}).status_code == 400)
found = desk.get("/api/patients?q=Ramu").json()
check("a returning patient can be found and given a new visit", len(found) == 1
      and desk.post("/api/visits", json={"patient_id": found[0]["id"], "doctor_id": ids["rao"]}).status_code == 200)

# ---------------------------------------------------------------- the patient's intake call
pt = H.Client()
start, last = H.run_call(pt, v["token"], H.INTAKE_ANSWERS)
check("the intake call completes", last["done"] and not last.get("emergency"))
check("the start response contains no patient information", set(start) <= {"session_id", "total", "step", "kind", "agent_text", "audio_url"})
with db.conn() as c:
    consents = [dict(r) for r in c.execute("SELECT kind, granted, source FROM consents")]
check("the patient's spoken consent is recorded", {"kind": "call_recording", "granted": 1, "source": "voice"} in consents)
check("no audio recording of the patient is kept on the server", not any(f.endswith(".wav") and "answer" in f for f in __import__("os").listdir(H.config.AUDIO_DIR)))
q = rao.get("/api/queue").json()["patients"]
row = next(r for r in q if r["name"] == "Ramu Patient" and r["intake_id"])
check("the queue now shows the finished intake with a summary", row["complaint"] and row["suggested_urgency"] == "routine")
detail = rao.get(f"/api/intakes/{row['intake_id']}").json()
check("the detail has the registered name, what was said, tags and similar past cases",
      detail["form"]["name"] == "Ramu Patient" and detail["form"]["stated_name"] == "Ramu" and detail["tag_labels"] and detail["similar"]["n_matched"] >= 3)
check("reception sees the visit as intake-done", next(r for r in desk.get("/api/visits").json() if r["id"] == v["visit_id"])["status"] == "intake_done")
iid = detail["id"]

# ---------------------------------------------------------------- the doctor decides
check("urgency can only be set by the doctor, to a valid level", rao.post(f"/api/intakes/{iid}/review", json={"urgency": "banana"}).status_code == 400)
r = rao.post(f"/api/intakes/{iid}/review", json={"urgency": "medium", "notes": "check knee", "edits": {"age": 59}})
check("the doctor's review is stored; suggestion and decision stay separate", r.status_code == 200 and r.json()["stats"] == {"reviewed": 1, "agreed": 0, "changed": 1})
d2 = rao.get(f"/api/intakes/{iid}").json()
check("the decision, the reviewer and the age correction are recorded", d2["doctor_urgency"] == "medium" and d2["suggested_urgency"] == "routine"
      and d2["reviewed_by"] == ids["rao"] and d2["age"] == 59)
case = d2["similar"]["matches"][0]["id"]
rec = rao.get(f"/api/case/{case}?intake_id={iid}").json()
check("a past case opens as a de-identified record compared with the patient", rec["comparison"]["both"] and "name" not in rec and len(rec["visits"]) == 3)

# ---------------------------------------------------------------- consultation and prescription
check("a consultation cannot be added without consent", rao.post(f"/api/intakes/{iid}/consultation", data={"transcript": "x"}).status_code == 400)
check("...or without any content", rao.post(f"/api/intakes/{iid}/consultation", data={"consent": "true"}).status_code == 400)
r = rao.post(f"/api/intakes/{iid}/consultation", data={"consent": "true", "transcript": "doctor: take paracetamol five hundred after food"})
rx_id = r.json()["prescription_id"]
check("a draft prescription is made from the consultation", r.status_code == 200 and rao.get(f"/api/intakes/{iid}").json()["prescription"]["status"] == "draft")
check("a reminder link cannot be made from an unconfirmed draft", rao.post(f"/api/prescriptions/{rx_id}/checkin-link").status_code == 400)
edited = {"medicines": [{"name": "Paracetamol", "dose": "650 mg", "timing": "after food", "duration": "5 days"}], "diet_advice": ["Eat calcium-rich foods"],
          "warning_signs": ["Night pain increasing"], "follow_up": "3 weeks"}
check("the doctor confirms an edited prescription", rao.post(f"/api/prescriptions/{rx_id}/confirm", json=edited).status_code == 200
      and db.get_prescription(rx_id)["final"]["medicines"][0]["dose"] == "650 mg")
link1 = rao.post(f"/api/prescriptions/{rx_id}/checkin-link").json()
tok1 = link1["url"].split("t=")[1].split("&")[0]
with db.conn() as c:
    stored = [r[0] for r in c.execute("SELECT checkin_token_hash FROM prescriptions")]
check("a reminder link is created; only its hash is stored", link1["url"].endswith("&k=checkin") and tok1 not in stored and any(s and len(s) == 64 for s in stored))

# ---------------------------------------------------------------- the reminder call and alerts
worse = lambda s, u: {"medicine_taken": "partial", "medicine_note": "skipped calcium", "diet_summary": "Rice, fried snacks", "followed_diet": "no",
                      "pain_trend": "worse", "tags": ["location_knee", "pain_night", "swelling"], "corrections": []}
H.install_fakes(checkin=worse)
start, last = H.run_call(H.Client(), tok1, H.CHECKIN_ANSWERS, kind="checkin")
check("the check-in call completes and raises a doctor alert", last["done"] and last["alert_level"] == "doctor")
q = rao.get("/api/queue").json()["patients"]
check("the patient with an open alert is first in the doctor's queue", q[0]["intake_id"] == iid and q[0]["open_alert"] == "doctor")
d3 = rao.get(f"/api/intakes/{iid}").json()
ck = d3["checkins"][0]
check("the check-in records alerts, new signs and progress against similar cases",
      any("worse" in a["text"] for a in ck["alerts"]) and "night pain" in ck["progress"]["new"] and ck["progress"]["alignment"])
check("another doctor cannot acknowledge it", H.signed_in("dr.sharma").post(f"/api/checkins/{ck['id']}/acknowledge").status_code == 404)
check("the treating doctor acknowledges it", rao.post(f"/api/checkins/{ck['id']}/acknowledge").status_code == 200
      and rao.get("/api/queue").json()["patients"][0]["open_alert"] is None)
link2 = rao.post(f"/api/prescriptions/{rx_id}/checkin-link").json()
check("creating a new reminder link cancels the old one", H.Client().post("/api/call/start", json={"token": tok1, "kind": "checkin"}).status_code == 404)
tok2 = link2["url"].split("t=")[1].split("&")[0]
check("closing the visit removes the patient from the list", rao.post(f"/api/intakes/{iid}/close").status_code == 200
      and not any(r["intake_id"] == iid for r in rao.get("/api/queue").json()["patients"]))
H.run_call(H.Client(), tok2, H.CHECKIN_ANSWERS, kind="checkin")
check("a new alert on a closed visit brings the patient back", any(r["intake_id"] == iid and r["open_alert"] for r in rao.get("/api/queue").json()["patients"]))
with db.conn() as c:
    c.execute("UPDATE prescriptions SET checkin_token_expires=? WHERE id=?", (db.later(days=-1), rx_id))
check("an expired reminder link is refused", H.Client().post("/api/call/start", json={"token": tok2, "kind": "checkin"}).status_code == 404)

# ---------------------------------------------------------------- an emergency
v2 = H.register(desk, ids["rao"], full_name="Seeta Patient", phone="9000000022")
H.install_fakes()
start, last = H.run_call(H.Client(), v2["token"], ["నా పేరు సీత", "నాకు ఛాతీ నొప్పి వస్తోంది"])
check("emergency wording ends the call with the emergency message", last["done"] and last["emergency"])
vs = desk.get("/api/visits").json()
check("reception is told about the emergency (flag only, no clinical detail)", next(r for r in vs if r["id"] == v2["visit_id"])["emergency"] == 1)
check("the emergency patient tops the doctor's unreviewed list", next(r for r in rao.get("/api/queue").json()["patients"] if r["name"] == "Seeta Patient")["suggested_urgency"] == "emergency")

# ---------------------------------------------------------------- when the language model is down
def down(s, u): raise LLMError("groq unavailable")
H.install_fakes(intake=down)
v3 = H.register(desk, ids["rao"], full_name="Third Patient", phone="9000000033")
start, last = H.run_call(H.Client(), v3["token"], H.INTAKE_ANSWERS)
row3 = next(r for r in rao.get("/api/queue").json()["patients"] if r["name"] == "Third Patient")
d = rao.get(f"/api/intakes/{row3['intake_id']}").json()
check("the call still completes and the doctor is told to read the transcript", last["done"] and d["form"]["needs_manual_review"] and d["transcript"])
H.install_fakes()
v4 = H.register(desk, ids["rao"], full_name="Fourth Patient", phone="9000000044")
sid = H.Client()
start = sid.post("/api/call/start", json={"token": v4["token"]}).json()
H.say(sid, start["session_id"], "అవును")
H.say(sid, start["session_id"], "నా పేరు వెంకట్, వయసు 40")
H.install_fakes(intake=lambda s, u: {"name": "Venkat", "age": 40, "complaint_summary": "x", "medicines": "", "tags": [], "corrections": []})
for a in H.INTAKE_ANSWERS[1:]:
    last = H.say(sid, start["session_id"], a).json()
rowv = next(r for r in rao.get("/api/queue").json()["patients"] if r["name"] == "Fourth Patient")
dv = rao.get(f"/api/intakes/{rowv['intake_id']}").json()
check("the age said on the call is kept next to the registered age for the doctor to compare", dv["form"]["stated_age"] == 40 and dv["age"] == 58)

# ---------------------------------------------------------------- audit and model monitoring
actions = {a["action"] for a in db.list_audit(limit=1000)}
needed = {"visit_registered", "intake_completed", "intake_viewed", "urgency_reviewed", "past_case_viewed", "consultation_added", "prescription_confirmed",
          "checkin_link_created", "checkin_completed", "checkin_acknowledged", "visit_closed"}
check("every step of the journey is in the audit trail", needed <= actions, str(needed - actions))
views = [a for a in db.list_audit("intake_viewed") if a["user_id"] == ids["rao"]]
check("opening a record is logged with who opened it", views and views[0]["role"] == "doctor")

monitor.record("gnani", "stt", True, 900)
monitor.record("gnani", "stt", True, 1500)
monitor.record("gnani", "stt", False, 30000, "504 gateway timeout")
@monitor.monitored("groq", "llm")
def fake_model(x):
    if x == "bad": raise RuntimeError("boom")
    return x
fake_model("ok")
try: fake_model("bad")
except RuntimeError: pass
s = admin.get("/api/admin/system").json()
stt = next(x for x in s["stats"] if x["kind"] == "stt")
llm_stat = next(x for x in s["stats"] if x["kind"] == "llm")
check("model health is measured: calls, success rate, speed and the last error", stt["calls"] == 3 and stt["success_rate"] == 0.667
      and stt["avg_ms"] == 10800 and "504" in stt["last_error"]["detail"] and llm_stat["calls"] == 2 and llm_stat["ok"] == 1)
check("the system page lists the three models and what each is for", [m["kind"] for m in s["models"]] == ["stt", "tts", "llm"]
      and all(m["purpose"] for m in s["models"]) and s["counts"]["patients"] >= 4 and s["migrations"][:2] == ["0001_initial", "0002_helpline"])
check("model monitoring never breaks a call", (setattr(monitor, "sink", lambda *a: 1 / 0), monitor.record("x", "y", True, 1), setattr(monitor, "sink", db.record_model_call))[1] is None)
check("database health endpoint answers", H.Client().get("/healthz").json() == {"status": "ok"})
print(f"\nALL {n} FLOW CHECKS PASSED")
