"""Doctor timetables, free slots, booking, and booking by phone. Run:  python -m tests.test_schedule"""
from tests import helpers as H
from datetime import datetime

from app import db, inbound_logic, scheduling as S

H.reset()
ids = H.make_users()
S.now_fn = lambda: datetime(2026, 11, 9, 8, 0)          # Monday 8:00, so 9:00 is the first possible slot
n = 0


def check(name, cond, extra=""):
    global n
    n += 1
    print(("PASS" if cond else "FAIL"), "|", name, extra)
    assert cond, name


desk, rao, sharma, admin = H.signed_in("desk1"), H.signed_in("dr.rao"), H.signed_in("dr.sharma"), H.signed_in("admin1")
H.install_fakes()
MON_WED = [{"weekday": 0, "start": "09:00", "end": "11:00"}, {"weekday": 2, "start": "14:00", "end": "15:00"}]

# ---------------------------------------------------------------- timetable rules
check("a doctor with no timetable has no free slots", S.free_slots(ids["rao"]) == [])
for bad, why in [([{"weekday": 0, "start": "11:00", "end": "09:00"}], "end before start"),
                 ([{"weekday": 0, "start": "09:00", "end": "11:00"}, {"weekday": 0, "start": "10:30", "end": "12:00"}], "overlap"),
                 ([{"weekday": 9, "start": "09:00", "end": "10:00"}], "bad day"),
                 ([{"weekday": 0, "start": "9am", "end": "10:00"}], "bad time"),
                 ([{"weekday": 0, "start": "25:00", "end": "26:00"}], "impossible time")]:
    check(f"an invalid timetable is refused ({why})", rao.put("/api/schedule", json={"windows": bad, "slot_minutes": 30}).status_code == 400)
check("an unsupported slot length is refused", rao.put("/api/schedule", json={"windows": MON_WED, "slot_minutes": 7}).status_code == 400)
check("only a doctor can set a timetable", desk.put("/api/schedule", json={"windows": MON_WED, "slot_minutes": 30}).status_code == 403
      and admin.put("/api/schedule", json={"windows": MON_WED, "slot_minutes": 30}).status_code == 403)
check("a doctor saves their timetable", rao.put("/api/schedule", json={"windows": MON_WED, "slot_minutes": 30}).status_code == 200)
check("the timetable is private to each doctor", sharma.get("/api/schedule").json()["windows"] == [] and len(rao.get("/api/schedule").json()["windows"]) == 2)

# ---------------------------------------------------------------- free slots
slots = S.free_slots(ids["rao"], days=7)
check("slots follow the timetable and slot length", slots == ["2026-11-09T09:00", "2026-11-09T09:30", "2026-11-09T10:00", "2026-11-09T10:30",
                                                              "2026-11-11T14:00", "2026-11-11T14:30"], str(slots))
S.now_fn = lambda: datetime(2026, 11, 9, 9, 40)
check("a slot starting within the hour cannot be booked", S.free_slots(ids["rao"], days=1) == [] and S.free_slots(ids["rao"], days=7)[0] == "2026-11-11T14:00")
S.now_fn = lambda: datetime(2026, 11, 9, 8, 0)
check("three slots are offered on different days where possible", S.pick_to_offer(S.free_slots(ids["rao"], days=14)) ==
      ["2026-11-09T09:00", "2026-11-11T14:00", "2026-11-16T09:00"])
check("a slot is spoken in Telugu with the weekday, date and time of day", S.speak_slot("2026-11-11T14:30") == "బుధవారం, నవంబర్ 11, మధ్యాహ్నం 2:30")

# ---------------------------------------------------------------- reception books
reg = H.register(desk, ids["rao"], full_name="Anitha Rao", phone="9000000055")
pid = db.get_visit(reg["visit_id"])["patient_id"]
sl = desk.get(f"/api/slots?doctor_id={ids['rao']}&days=7").json()
check("reception sees the doctor's free slots", sl["doctor"] == "Dr. Rao" and sl["slots"][0] == "2026-11-09T09:00")
check("doctors cannot use the reception slot list", rao.get(f"/api/slots?doctor_id={ids['rao']}").status_code == 403)
r = desk.post("/api/appointments", json={"patient_id": pid, "doctor_id": ids["rao"], "starts_at": "2026-11-09T09:30"})
check("reception books a slot", r.status_code == 200 and r.json()["starts_at"] == "2026-11-09T09:30" and r.json()["booked_via"] == "reception")
check("the same slot cannot be booked twice (the database refuses it)", desk.post("/api/appointments", json={"patient_id": pid, "doctor_id": ids["rao"], "starts_at": "2026-11-09T09:30"}).status_code == 409)
check("a time that is not in the timetable is refused", desk.post("/api/appointments", json={"patient_id": pid, "doctor_id": ids["rao"], "starts_at": "2026-11-10T09:00"}).status_code == 409)
check("a time in the past is refused", desk.post("/api/appointments", json={"patient_id": pid, "doctor_id": ids["rao"], "starts_at": "2026-11-02T09:00"}).status_code == 409)
check("a booked slot disappears from the free list", "2026-11-09T09:30" not in desk.get(f"/api/slots?doctor_id={ids['rao']}&days=7").json()["slots"])
check("booking needs a real doctor and patient", desk.post("/api/appointments", json={"patient_id": 9999, "doctor_id": ids["rao"], "starts_at": "2026-11-09T10:00"}).status_code == 400
      and desk.post("/api/appointments", json={"patient_id": pid, "doctor_id": ids["desk"], "starts_at": "2026-11-09T10:00"}).status_code == 400)
check("only reception can book", rao.post("/api/appointments", json={"patient_id": pid, "doctor_id": ids["rao"], "starts_at": "2026-11-09T10:00"}).status_code == 403)
appts = desk.get("/api/appointments").json()
check("reception sees upcoming appointments with the patient and doctor", len(appts) == 1 and appts[0]["patient_name"] == "Anitha Rao" and appts[0]["doctor_name"] == "Dr. Rao")
mine = rao.get("/api/schedule").json()["appointments"]
check("the doctor sees their own appointments", len(mine) == 1 and mine[0]["patient_name"] == "Anitha Rao")
check("another doctor sees none of them", sharma.get("/api/schedule").json()["appointments"] == [])
check("another doctor cannot cancel it", sharma.post(f"/api/appointments/{appts[0]['id']}/cancel").status_code == 404)

# ---------------------------------------------------------------- days off
r = rao.post("/api/schedule/time-off", json={"date": "2026-11-11", "reason": "Conference"})
check("a doctor can mark a day off", r.status_code == 200)
check("a day off removes that day's slots", not any(s.startswith("2026-11-11") for s in S.free_slots(ids["rao"], days=7)))
check("a past date cannot be marked", rao.post("/api/schedule/time-off", json={"date": "2026-11-01"}).status_code == 400)
check("a doctor cannot remove another doctor's day off", sharma.delete(f"/api/schedule/time-off/{r.json()['id']}").status_code == 404)
check("removing a day off brings the slots back", rao.delete(f"/api/schedule/time-off/{r.json()['id']}").status_code == 200
      and any(s.startswith("2026-11-11") for s in S.free_slots(ids["rao"], days=7)))
over = rao.post("/api/schedule/time-off", json={"date": "2026-11-09", "reason": "Unwell"}).json()
check("marking a day off warns about appointments already booked that day", len(over["appointments_that_day"]) == 1)
rao.delete(f"/api/schedule/time-off/{over['id']}")

# ---------------------------------------------------------------- cancelling
check("the doctor cancels an appointment", rao.post(f"/api/appointments/{appts[0]['id']}/cancel").status_code == 200)
check("...and the slot becomes free again", "2026-11-09T09:30" in S.free_slots(ids["rao"], days=7))
check("a cancelled appointment cannot be cancelled twice", desk.post(f"/api/appointments/{appts[0]['id']}/cancel").status_code == 400)
check("a cancelled slot can be booked again", desk.post("/api/appointments", json={"patient_id": pid, "doctor_id": ids["rao"], "starts_at": "2026-11-09T09:30"}).status_code == 200)

# ---------------------------------------------------------------- booking by phone
def classify_as(**kw):
    base = {"intent": "other", "topics": [], "medicine_name": "", "bank_id": None, "tags": [], "urgent": False, "choice": None, "question_en": "q"}
    base.update(kw)
    inbound_logic.ask_json = lambda s, u: base


def start_call(phone, pin):
    c = H.Client()
    sid = c.post("/api/inbound/start", json={"phone": phone, "pin": pin}).json()["session_id"]
    H.say(c, sid, "అవును")
    return c, sid


def say(c, sid, text):
    return H.say(c, sid, text).json()


newp = H.register(desk, ids["rao"], full_name="Prakash Caller", phone="9000000066")
c, sid = start_call("9000000066", newp["pin"])
classify_as(intent="appointment_slots", question_en="wants an appointment")
r = say(c, sid, "డాక్టర్ గారిని కలవాలి")
check("the agent offers three free times, spoken in Telugu, and asks which one",
      all(x in r["agent_text"] for x in (S.speak_slot("2026-11-09T09:00"), S.speak_slot("2026-11-11T14:00"), S.speak_slot("2026-11-16T09:00"), "ఒకటి", "మూడు"))
      and inbound_logic.ASK_CHOICE in r["agent_text"])
classify_as(intent="appointment_book", choice=None)
check("an unclear choice is asked again, nothing is booked", inbound_logic.ASK_CHOICE in say(c, sid, "ఏదైనా సరే")["agent_text"] and len(desk.get("/api/appointments").json()) == 1)
classify_as(intent="appointment_book", choice=3)
r = say(c, sid, "మూడవది")
booked = [a for a in desk.get("/api/appointments").json() if a["patient_name"] == "Prakash Caller"]
check("the chosen time is booked and read back", inbound_logic.BOOKED in r["agent_text"] and S.speak_slot("2026-11-16T09:00") in r["agent_text"]
      and len(booked) == 1 and booked[0]["starts_at"] == "2026-11-16T09:00" and booked[0]["booked_via"] == "call")
classify_as(intent="appointment_info")
check("the caller can ask when their appointment is", S.speak_slot("2026-11-16T09:00") in say(c, sid, "నా అపాయింట్‌మెంట్ ఎప్పుడు")["agent_text"])
fresh = H.register(desk, ids["rao"], full_name="Fresh Caller", phone="9000000044")
fc, fs = start_call("9000000044", fresh["pin"])
check("a caller with no appointment is told so", inbound_logic.INFO_NONE in say(fc, fs, "ఎప్పుడు")["agent_text"])

# two callers race for the same slot: the second is told it is gone and offered new times
other = H.register(desk, ids["rao"], full_name="Racer One", phone="9000000077")
racer = H.register(desk, ids["rao"], full_name="Racer Two", phone="9000000088")
classify_as(intent="appointment_slots")
c1, s1 = start_call("9000000077", other["pin"]); c2, s2 = start_call("9000000088", racer["pin"])
say(c1, s1, "slots"); say(c2, s2, "slots")
classify_as(intent="appointment_book", choice=1)
r1 = say(c1, s1, "ఒకటి"); r2 = say(c2, s2, "ఒకటి")
check("when two callers want the same time, only one gets it", inbound_logic.BOOKED in r1["agent_text"] and inbound_logic.TAKEN in r2["agent_text"])
after = r2["agent_text"].split(inbound_logic.TAKEN, 1)[1]
check("the second caller is immediately offered other times, not the one just lost", inbound_logic.OFFER_INTRO in after and S.speak_slot("2026-11-09T09:00") not in after)
check("exactly one booking exists for the contested slot", len([a for a in desk.get("/api/appointments").json() if a["starts_at"] == "2026-11-09T09:00"]) == 1)

# no timetable at all: the request goes to the doctor as a message
db.set_schedule(ids["sharma"], [], 15)
nt = H.register(desk, ids["sharma"], full_name="No Slots", phone="9000000099")
c3, s3 = start_call("9000000099", nt["pin"])
classify_as(intent="appointment_slots")
r = say(c3, s3, "అపాయింట్‌మెంట్ కావాలి")
msgs = sharma.get("/api/messages").json()["open"]
check("with no free times the caller is told, and the request reaches the doctor", inbound_logic.NO_SLOTS in r["agent_text"] and any(m["kind"] == "appointment_request" for m in msgs))

# a model hiccup must never invent a booking
classify_as(intent="appointment_book", choice=2)
c4, s4 = start_call("9000000066", newp["pin"])
r = say(c4, s4, "రెండు")
check("choosing before any times were offered offers times instead of booking blindly", inbound_logic.OFFER_INTRO in r["agent_text"] and inbound_logic.BOOKED not in r["agent_text"])
classify_as(intent="appointment_book", choice=99)
check("an impossible choice number from the model is ignored", inbound_logic.ASK_CHOICE in say(c4, s4, "తొంభై తొమ్మిది")["agent_text"])

actions = {a["action"] for a in db.list_audit(limit=2000)}
check("bookings, cancellations and timetable changes are audited", {"appointment_booked", "appointment_cancelled", "schedule_updated", "time_off_added"} <= actions)
print(f"\nALL {n} SCHEDULE CHECKS PASSED")
