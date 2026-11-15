"""Generates synthetic_cases.json: 42 SYNTHETIC orthopaedic hospital records (no real patients).
Each case has a baseline (BMI, other conditions, diet) and three visits (weeks 0, 3, 6) with symptom tags,
examination findings, investigations and treatment, so the doctor can open a past case and read it like a record.
Run once:  python make_cases.py
"""
import json
import random
from datetime import date, timedelta
from questions import TAG_VOCAB

random.seed(7)

# diagnosis -> (base tags, BMI range, week-0 text, diet advice, {path: (outcome, w3 tags, w6 tags, w3 text, w6 text)})
# text = (examination findings, investigations, treatment)
ARCHETYPES = [
    {"diagnosis": "Knee osteoarthritis", "bmi": (26, 35),
     "base": ["location_knee", "duration_chronic", "pain_walking", "morning_stiffness", "stairs_difficulty"],
     "w0": ("Joint-line tenderness, crepitus on movement, morning stiffness under 30 minutes. No effusion.",
            "Standing knee X-ray: reduced joint space.",
            "Physiotherapy referral, quadriceps strengthening, weight management advice, simple pain relief as needed."),
     "diet": "Weight-reducing diet; calcium and vitamin D rich foods.",
     "paths": {
        "improved": ("Improved with physiotherapy and weight management",
                     ["location_knee", "stairs_difficulty"], ["location_knee"],
                     ("Pain easing, stairs still slow.", "None.", "Physiotherapy and exercises continued."),
                     ("Pain minimal, walking normal.", "None.", "Home exercise plan; review if pain returns.")),
        "imaging_injection": ("X-ray/MRI done; joint injection given",
                     ["location_knee", "pain_walking", "pain_night", "swelling"], ["location_knee", "pain_night", "swelling"],
                     ("Night pain and swelling despite physiotherapy.", "MRI knee ordered.", "Pain relief reviewed; walking stick advised."),
                     ("Swelling persists, partial relief only.", "MRI: cartilage thinning, small effusion.", "Intra-articular injection given; exercises continued.")),
        "surgical_referral": ("Referred for knee replacement evaluation",
                     ["location_knee", "pain_night", "swelling", "limited_movement"], ["location_knee", "pain_night", "limited_movement", "locking_catching"],
                     ("Night pain, swelling and reduced bending.", "MRI: advanced cartilage loss.", "Activity modification; analgesia adjusted."),
                     ("Locking episodes and limited movement.", "Repeat assessment.", "Referred to the joint replacement clinic."))}},
    {"diagnosis": "Lumbar disc prolapse", "bmi": (22, 32),
     "base": ["location_back", "duration_subacute", "radiating_leg_pain", "pain_sitting"],
     "w0": ("Pain on forward bending, positive straight-leg raise on one side, power normal.",
            "No imaging at first visit.",
            "Short rest, avoid heavy lifting, pain relief, physiotherapy referral."),
     "diet": "Hydration, fibre-rich diet; avoid long periods of sitting.",
     "paths": {
        "improved": ("Improved with rest, medication and physiotherapy",
                     ["location_back", "radiating_leg_pain"], ["location_back"],
                     ("Leg pain reducing, bending improved.", "None.", "Physiotherapy continued."),
                     ("Mild back pain, no leg pain.", "None.", "Gradual return to normal activity.")),
        "imaging_injection": ("MRI spine done; epidural injection given",
                     ["location_back", "radiating_leg_pain", "numbness"], ["location_back", "radiating_leg_pain", "numbness"],
                     ("Leg pain with new tingling.", "MRI lumbar spine ordered.", "Nerve-pain medication reviewed."),
                     ("Nerve pain persisting.", "MRI: disc bulge touching a nerve root.", "Epidural injection given.")),
        "surgical_referral": ("Referred for spine surgery evaluation",
                     ["location_back", "radiating_leg_pain", "numbness", "leg_weakness"], ["location_back", "numbness", "leg_weakness"],
                     ("Tingling and new leg weakness.", "MRI: large disc prolapse compressing a nerve root.", "Urgent spine review advised."),
                     ("Weakness not improving.", "Nerve conduction review.", "Referred for spine surgery evaluation."))}},
    {"diagnosis": "Frozen shoulder / rotator cuff problem", "bmi": (21, 30),
     "base": ["location_shoulder", "duration_subacute", "pain_night", "limited_movement"],
     "w0": ("Painful restricted movement, worse overhead and lying on the side.",
            "Shoulder X-ray: normal bone.",
            "Physiotherapy, heat, gentle stretching."),
     "diet": "Protein-rich diet; calcium and vitamin D.",
     "paths": {
        "improved": ("Improved with physiotherapy",
                     ["location_shoulder", "limited_movement"], ["location_shoulder"],
                     ("Movement slowly improving.", "None.", "Stretching programme continued."),
                     ("Near-full movement.", "None.", "Discharged to home exercises.")),
        "imaging_injection": ("Steroid injection given for persistent stiffness",
                     ["location_shoulder", "pain_night", "limited_movement"], ["location_shoulder", "pain_night", "limited_movement"],
                     ("Stiffness and night pain persist.", "Shoulder ultrasound ordered.", "Pain relief reviewed."),
                     ("Still stiff.", "Ultrasound: no full-thickness tear.", "Steroid injection given with physiotherapy.")),
        "surgical_referral": ("MRI showed cuff tear; referred for repair",
                     ["location_shoulder", "pain_night", "arm_weakness"], ["location_shoulder", "arm_weakness", "limited_movement"],
                     ("Weakness lifting the arm.", "MRI: rotator cuff tear.", "Sling for comfort; activity modification."),
                     ("Weakness persists.", "Repeat assessment.", "Referred for cuff repair assessment."))}},
    {"diagnosis": "Ankle sprain", "bmi": (20, 29),
     "base": ["location_ankle", "duration_acute", "injury_recent", "swelling", "pain_walking"],
     "w0": ("Swelling and bruising around the ankle, tender over the ligament, walks with pain.",
            "Ankle X-ray to rule out fracture.",
            "Rest, ice, compression brace, early gentle movement."),
     "diet": "Protein and vitamin C rich foods for healing.",
     "paths": {
        "improved": ("Healed with rest, brace and physiotherapy",
                     ["location_ankle", "pain_walking"], [],
                     ("Swelling resolved, mild pain on walking.", "None.", "Physiotherapy for balance."),
                     ("Full walking, no pain.", "None.", "Gradual return to sport.")),
        "imaging_injection": ("X-ray done to rule out fracture; boot given",
                     ["location_ankle", "swelling", "pain_walking", "cannot_bear_weight"], ["location_ankle", "swelling"],
                     ("Persistent swelling, difficulty bearing weight.", "Repeat X-ray to rule out fracture.", "Walking boot given."),
                     ("Swelling reducing.", "None.", "Boot weaned; physiotherapy.")),
        "surgical_referral": ("Ligament tear; referred for stabilisation",
                     ["location_ankle", "swelling", "joint_instability"], ["location_ankle", "joint_instability"],
                     ("Ankle gives way, swelling persists.", "MRI: complete ligament tear.", "Brace and crutches."),
                     ("Ongoing instability.", "Repeat assessment.", "Referred for ligament stabilisation."))}},
    {"diagnosis": "Wrist fracture (distal radius)", "bmi": (20, 28),
     "base": ["location_wrist", "duration_acute", "injury_recent", "swelling", "limited_movement"],
     "w0": ("Swelling and tenderness at the wrist after a fall, reduced movement.",
            "Wrist X-ray: distal radius fracture.",
            "Fracture reduced if needed, cast applied, elevation advised."),
     "diet": "Calcium, vitamin D and protein rich diet.",
     "paths": {
        "improved": ("Healed in cast; physiotherapy for movement",
                     ["location_wrist", "limited_movement"], ["location_wrist"],
                     ("Cast intact, fingers moving well.", "None.", "Cast continued."),
                     ("Cast removed, stiffness only.", "X-ray: healing well.", "Physiotherapy for wrist movement.")),
        "imaging_injection": ("Repeat X-ray; nerve irritation treated",
                     ["location_wrist", "limited_movement", "numbness"], ["location_wrist", "numbness"],
                     ("Tingling in the fingers.", "Repeat X-ray; nerve irritation assessed.", "Cast loosened, elevation."),
                     ("Tingling reduced.", "None.", "Nerve irritation treated; cast changed.")),
        "surgical_referral": ("Fracture shifted; plate fixation done",
                     ["location_wrist", "swelling", "limited_movement", "numbness"], ["location_wrist", "limited_movement"],
                     ("Swelling and tingling, fracture position shifted.", "Repeat X-ray: displacement.", "Referred to the surgeon."),
                     ("After fixation, movement limited.", "Post-operative X-ray.", "Plate fixation done; physiotherapy started."))}},
    {"diagnosis": "Plantar fasciitis / heel pain", "bmi": (24, 33),
     "base": ["location_foot", "duration_subacute", "pain_first_steps_morning", "pain_walking"],
     "w0": ("Tenderness at the heel, pain worst on the first steps in the morning.",
            "No imaging at first visit.",
            "Calf and plantar fascia stretching, heel cushions."),
     "diet": "Weight management; anti-inflammatory foods.",
     "paths": {
        "improved": ("Improved with stretching and heel cushions",
                     ["location_foot", "pain_first_steps_morning"], [],
                     ("Morning pain easing.", "None.", "Stretching continued."),
                     ("Pain-free walking.", "None.", "Continue heel cushions.")),
        "imaging_injection": ("Persistent pain; injection and orthotics",
                     ["location_foot", "pain_first_steps_morning", "pain_walking"], ["location_foot", "pain_walking"],
                     ("Pain on walking persists.", "Heel ultrasound.", "Orthotic insoles prescribed."),
                     ("Partial relief.", "None.", "Injection given; orthotics continued.")),
        "surgical_referral": ("X-ray/MRI found stress fracture; offloading boot",
                     ["location_foot", "pain_night", "swelling"], ["location_foot", "pain_night", "swelling"],
                     ("Night pain and swelling, unusual for fasciitis.", "X-ray/MRI: stress fracture.", "Offloading boot."),
                     ("Pain reducing in the boot.", "Follow-up imaging planned.", "Boot continued; weight-bearing restricted."))}},
]
WEIGHTS = [3, 2, 2]   # per archetype: 3 improved + 2 + 2 = 7 cases each, 42 in total
PATH_KEYS = ["improved", "imaging_injection", "surgical_referral"]
COMORBIDITIES = ["None", "None", "Hypertension", "Type 2 diabetes", "Hypothyroidism", "Type 2 diabetes, hypertension"]

cases, n = [], 1
for a in ARCHETYPES:
    for key, weight in zip(PATH_KEYS, WEIGHTS):
        outcome, w3, w6, t3, t6 = a["paths"][key]
        for _ in range(weight):
            # Real patients are not identical: some lose one warning sign, some improving patients also had one.
            w3v, w6v = list(w3), list(w6)
            signs = [t for t in w3v if not t.startswith("location_")]
            if key != "improved" and len(signs) > 2 and random.random() < 0.3:
                w3v.remove(random.choice(signs[1:]))
            if key == "improved" and random.random() < 0.25:
                extra = random.choice(["swelling", "pain_night"])
                if extra not in w3v:
                    w3v.append(extra)
            for t in a["base"] + w3v + w6v:
                assert t in TAG_VOCAB, f"unknown tag {t}"
            d0 = date(2025, 1, 6) + timedelta(days=random.randint(0, 270))
            visits = []
            for week, tags, (find, inv, treat) in ((0, a["base"], a["w0"]), (3, w3v, t3), (6, w6v, t6)):
                visits.append({"week": week, "date": (d0 + timedelta(weeks=week)).isoformat(), "tags": tags,
                               "findings": find, "investigations": inv, "treatment": treat})
            cases.append({
                "id": f"C{n:03d}", "record_no": f"HR-2025-{n:04d}",
                "age": random.randint(28, 78), "sex": random.choice(["F", "M"]),
                "diagnosis": a["diagnosis"], "path": key, "outcome": outcome,
                "baseline": {"bmi": random.randint(*a["bmi"]), "other_conditions": random.choice(COMORBIDITIES),
                             "diet_notes": a["diet"]},
                "visits": visits})
            n += 1

with open("data/synthetic_cases.json", "w", encoding="utf-8") as f:
    json.dump(cases, f, indent=2, ensure_ascii=False)
print(f"wrote {len(cases)} synthetic cases to synthetic_cases.json")
