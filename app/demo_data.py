"""SYNTHETIC demonstration data: invented patients, never real ones. Used by `python manage.py demo-data`."""
from app import config, db
from app import scheduling
from app.checkin_logic import alert_level, build_alerts, build_checkin_questions
from app.intake_logic import suggest_urgency
from app.matching import progress_view
from app.questions import QUESTIONS
from app.security import hash_password, hash_token, new_token

DEMO_PASSWORD = "Demo-Pass-2026!"
DEMO_PIN = "246810"          # every demo patient has this help-line PIN (demo only)
USERS = [("admin", "Hospital Administrator", "admin", None), ("dr.rao", "Dr. Ravi Rao", "doctor", "orthopaedics"),
         ("dr.sharma", "Dr. Meena Sharma", "doctor", "orthopaedics"), ("reception", "Front Desk", "front_desk", None)]

# name, age, sex, phone, complaint, tags, Telugu answers, (has intake?)
PATIENTS = [
    ("Ramu", 58, "M", "9000000001", "Right knee pain for 3 months, worse on stairs, morning stiffness, mild swelling.",
     ["location_knee", "duration_chronic", "stairs_difficulty", "morning_stiffness", "swelling"],
     ["నా పేరు రాము, వయసు 58", "మోకాలు", "మూడు నెలలు", "లేదు", "మెట్లు ఎక్కేటప్పుడు, ఉదయం లేచినప్పుడు బిగుసుకుపోతుంది",
      "కొంచెం వాపు ఉంది", "లేదు", "జ్వరం లేదు, మందులు వాడట్లేదు", "ఏమీ లేదు"]),
    ("Lakshmi", 46, "F", "9000000002", "Lower back pain for 3 weeks, shooting down the leg with numbness and leg weakness.",
     ["location_back", "duration_subacute", "pain_sitting", "radiating_leg_pain", "numbness", "leg_weakness", "limited_movement"],
     ["నా పేరు లక్ష్మి, వయసు 46", "నడుము", "మూడు వారాలు", "లేదు", "కూర్చున్నప్పుడు ఎక్కువగా ఉంటుంది", "కదలడం కష్టంగా ఉంది",
      "కాలు వరకు నొప్పి వస్తోంది, తిమ్మిరి, కాలు బలహీనంగా ఉంది", "జ్వరం లేదు", "ఏమీ లేదు"]),
    ("Venkat", 31, "M", "9000000003", "Ankle twisted while playing cricket 2 days ago; heavy swelling, cannot stand on the foot.",
     ["location_ankle", "duration_acute", "injury_recent", "pain_walking", "swelling", "cannot_bear_weight"],
     ["నా పేరు వెంకట్, వయసు 31", "చీలమండ", "రెండు రోజులు", "అవును, క్రికెట్ ఆడుతూ కాలు మెలిపడింది", "నడిచేటప్పుడు",
      "బాగా వాపు ఉంది, కాలు మీద నిలబడలేకపోతున్నాను", "లేదు", "జ్వరం లేదు", "ఏమీ లేదు"]),
    ("Suresh", 63, "M", "9000000004", "Left knee pain for 4 months, worse walking and on stairs, morning stiffness.",
     ["location_knee", "duration_chronic", "pain_walking", "morning_stiffness", "stairs_difficulty"],
     ["నా పేరు సురేష్, వయసు 63", "మోకాలు", "నాలుగు నెలలు", "లేదు", "నడిచేటప్పుడు, మెట్లు ఎక్కేటప్పుడు", "కొంచెం బిగుసుకుపోతుంది",
      "లేదు", "జ్వరం లేదు", "ఏమీ లేదు"]),
]
AWAITING = [("Anitha", 39, "F", "9000000005"), ("Prakash", 52, "M", "9000000006")]   # registered, intake call not made yet

RX = {"medicines": [{"name": "Paracetamol", "dose": "500 mg", "timing": "after food, three times a day", "duration": "5 days"},
                    {"name": "Calcium with vitamin D", "dose": "1 tablet", "timing": "after dinner", "duration": "30 days"}],
      "diet_advice": ["Eat calcium-rich foods such as milk, ragi and leafy greens", "Reduce rice portions and avoid fried food"],
      "warning_signs": ["Night pain, swelling or locking getting worse"], "follow_up": "After 3 weeks"}
RX_TRANSCRIPT = ("Doctor: Your knee has wear and tear. Take paracetamol 500 after food three times a day for five days, and calcium "
                 "with vitamin D after dinner for a month. Eat ragi and leafy greens, cut down rice and fried food. Come back in three "
                 "weeks, or earlier if the night pain or swelling increases.")
CHECKINS = {
    "Ramu": {"info": {"medicine_taken": "yes", "medicine_note": "", "diet_summary": "Ragi porridge, dal, vegetables", "followed_diet": "yes",
                      "pain_trend": "better", "tags": ["location_knee", "stairs_difficulty"], "corrections": [], "needs_manual_review": False},
             "answers": ["అవును అన్నీ వేసుకున్నాను", "రాగి జావ, పప్పు, కూరగాయలు", "నొప్పి తగ్గింది", "కొత్త సమస్యలు లేవు", "లేదు"]},
    "Suresh": {"info": {"medicine_taken": "partial", "medicine_note": "skipped the calcium tablet", "diet_summary": "Rice, fried snacks",
                        "followed_diet": "no", "pain_trend": "worse", "tags": ["location_knee", "pain_walking", "pain_night", "swelling", "limited_movement"],
                        "corrections": [], "needs_manual_review": False},
               "answers": ["కాల్షియం మాత్ర వేసుకోలేదు", "అన్నం, వేయించిన స్నాక్స్", "నొప్పి పెరిగింది", "రాత్రి నొప్పి, వాపు, కదలడం కష్టం", "లేదు"]},
}
DRAFT_ONLY = {"Lakshmi"}


def load(print_fn=print):
    """Create demo staff, patients, a prescription, check-ins and two patients still waiting for their intake call."""
    ids = {}
    for username, name, role, spec in USERS:
        if db.get_user_by_username(username) is None:
            ids[username] = db.create_user(username, name, role, hash_password(DEMO_PASSWORD), spec)
        else:
            ids[username] = db.get_user_by_username(username)["id"]
    doctor, desk = db.get_user(ids["dr.rao"]), db.get_user(ids["reception"])
    weekdays = [{"weekday": d, "start": "09:00", "end": "13:00"} for d in range(5)] + [{"weekday": d, "start": "16:00", "end": "18:00"} for d in (0, 2, 4)] \
        + [{"weekday": 5, "start": "09:00", "end": "12:00"}]
    for who in ("dr.rao", "dr.sharma"):
        db.set_schedule(ids[who], scheduling.validate_windows(weekdays), 15)
    for name, age, sex, phone, summary, tags, answers in PATIENTS:
        pid = db.create_patient(name, age, sex, phone, config.LANG, desk["id"], hash_password(DEMO_PIN))
        vid = db.create_visit(pid, doctor["id"], hash_token(new_token()), db.later(hours=24), desk["id"])
        qa = [{"question_id": q["id"], "question_en": q["en"], "answer": a} for q, a in zip(QUESTIONS, answers)]
        urg = suggest_urgency(tags, [])
        form = {"name": name, "age": age, "stated_name": name, "stated_age": age, "complaint_summary": summary, "medicines": "",
                "needs_manual_review": False, "urgency_reasons": urg["reasons"], "corrections": [],
                "answers": {x["question_id"]: x["answer"] for x in qa}}
        iid = db.save_intake(vid, pid, doctor["id"], qa, form, tags, [], urg["level"])
        if name in CHECKINS or name in DRAFT_ONLY:
            rx_id = db.save_prescription_draft(iid, RX_TRANSCRIPT, RX, created_by=doctor["id"])
            if name in CHECKINS:
                db.confirm_prescription(rx_id, RX, doctor)
        if name in CHECKINS:
            info = CHECKINS[name]["info"]
            questions = build_checkin_questions([m["name"] for m in RX["medicines"]])
            ck_qa = [{"question_id": q["id"], "question_en": q["en"], "answer": a} for q, a in zip(questions, CHECKINS[name]["answers"])]
            alerts = build_alerts(info, tags, [])
            prog = progress_view(tags, info["tags"], 3)
            prog["days_since_visit"] = 21
            db.save_checkin(iid, rx_id, 3, ck_qa, info, info["tags"], [], alerts, alert_level(alerts), prog)
        if name == "Ramu":
            db.create_escalation(pid, "question", "routine", "Can I take ibuprofen along with my other tablets?", "ఐబుప్రోఫెన్ కూడా వేసుకోవచ్చా?", [], [], doctor["id"])
        if name == "Suresh":
            db.create_escalation(pid, "symptom_report", "urgent", "Knee swelling has increased and pain wakes him at night", "మోకాలు వాపు పెరిగింది, రాత్రి నొప్పి వస్తోంది",
                                 ["swelling", "pain_night"], [], doctor["id"])
        print_fn(f"  patient {name}: suggested urgency {urg['level']}")
    first = scheduling.free_slots(doctor["id"], 14, limit=8)
    if len(first) >= 4:
        ramu = db.patients_by_phone("9000000001")[0]
        db.book_appointment(ramu["id"], doctor["id"], first[3], "call")
    links = []
    for name, age, sex, phone in AWAITING:
        pid = db.create_patient(name, age, sex, phone, config.LANG, desk["id"], hash_password(DEMO_PIN))
        token = new_token()
        db.create_visit(pid, doctor["id"], hash_token(token), db.later(hours=24), desk["id"])
        links.append((name, f"{config.PUBLIC_URL}/call?t={token}"))
    return links
