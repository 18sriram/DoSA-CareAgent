"""The doctor-defined orthopaedic intake script (Telugu for the patient, English for the dashboard),
plus the shared symptom-tag vocabulary used by intake, matching and the synthetic past cases.

NOTE: have a Telugu speaker read the Telugu lines once before you record the demo.
"""

GREETING = {
    "te": "నమస్కారం. ఈ కాల్ మీ డాక్టర్‌కు సహాయపడటానికి రికార్డ్ చేయబడుతుంది. కొనసాగించడానికి మీరు అంగీకరిస్తే 'అవును' అని చెప్పండి.",
    "en": "Hello. This call is recorded to help your doctor. Say 'yes' to continue.",
}
CONSENT_YES = ["అవును", "సరే", "ఒప్పుకుంటున్నాను", "yes", "ok", "okay"]
CONSENT_DECLINED = {
    "te": "సరే, మీ అనుమతి లేకుండా కొనసాగించము. దయచేసి రిసెప్షన్ సిబ్బందిని సంప్రదించండి.",
    "en": "Understood. We will not continue without consent. Please see the reception staff.",
}
CLOSING = {
    "te": "ధన్యవాదాలు. మీ సమాచారం డాక్టర్‌కు పంపబడింది. దయచేసి మీ వంతు కోసం వేచి ఉండండి.",
    "en": "Thank you. Your details have been sent to the doctor. Please wait for your turn.",
}

# The doctor can edit this list; each question lists the tags its answer can produce.
QUESTIONS = [
    {"id": "name_age", "en": "Your name and age?",
     "te": "మీ పేరు మరియు వయసు చెప్పండి.",
     "tags": []},
    {"id": "location", "en": "Where is the pain or problem?",
     "te": "మీకు ఎక్కడ నొప్పి ఉంది? ఉదాహరణకు మోకాలు, నడుము, భుజం, చీలమండ.",
     "tags": ["location_knee", "location_back", "location_shoulder", "location_ankle",
              "location_hip", "location_wrist", "location_foot"]},
    {"id": "duration", "en": "How long has it been going on?",
     "te": "ఈ సమస్య ఎన్ని రోజులు లేదా వారాల నుండి ఉంది?",
     "tags": ["duration_acute", "duration_subacute", "duration_chronic"]},
    {"id": "injury", "en": "Any recent fall, injury or accident?",
     "te": "ఇటీవల మీకు ఏదైనా దెబ్బ, పడిపోవడం లేదా ప్రమాదం జరిగిందా?",
     "tags": ["injury_recent"]},
    {"id": "pain_pattern", "en": "When is the pain worst?",
     "te": "నొప్పి ఎప్పుడు ఎక్కువగా ఉంటుంది? నడిచేటప్పుడా, రాత్రిపూటా, కూర్చున్నప్పుడా, లేక ఉదయం లేచినప్పుడా?",
     "tags": ["pain_walking", "pain_night", "pain_sitting", "pain_first_steps_morning",
              "morning_stiffness", "stairs_difficulty"]},
    {"id": "swelling_movement", "en": "Any swelling, stiffness, locking or trouble moving?",
     "te": "వాపు ఉందా? కదలడంలో ఇబ్బంది, బిగుసుకుపోవడం లేదా కీలు ఇరుక్కోవడం ఉందా?",
     "tags": ["swelling", "limited_movement", "locking_catching", "joint_instability",
              "cannot_bear_weight"]},
    {"id": "numbness_weakness", "en": "Any numbness, tingling, weakness or pain shooting down the leg?",
     "te": "కాళ్ళు లేదా చేతుల్లో తిమ్మిరి, బలహీనత ఉందా? నొప్పి కాలు వరకు వ్యాపిస్తోందా?",
     "tags": ["numbness", "leg_weakness", "arm_weakness", "radiating_leg_pain"]},
    {"id": "fever_meds", "en": "Any fever? Any medicines you take now?",
     "te": "జ్వరం ఉందా? ప్రస్తుతం ఏవైనా మందులు వాడుతున్నారా?",
     "tags": ["fever"]},
    {"id": "corrections", "en": "Is there anything you want to change, correct or add?",
     "te": "చివరగా, ఇప్పటివరకు మీరు చెప్పిన దానిలో ఏదైనా మార్చాలనుకుంటున్నారా, లేదా ఇంకేమైనా చెప్పాలనుకుంటున్నారా?",
     "tags": []},
]

TAG_VOCAB = {
    "location_knee": "knee", "location_back": "lower back", "location_shoulder": "shoulder",
    "location_ankle": "ankle", "location_hip": "hip", "location_wrist": "wrist", "location_foot": "foot/heel",
    "duration_acute": "under 2 weeks", "duration_subacute": "2 to 6 weeks", "duration_chronic": "over 6 weeks",
    "injury_recent": "recent injury", "pain_walking": "pain on walking", "pain_night": "night pain",
    "pain_sitting": "pain on sitting", "pain_first_steps_morning": "pain on first steps in the morning",
    "morning_stiffness": "morning stiffness", "stairs_difficulty": "difficulty with stairs",
    "swelling": "swelling", "limited_movement": "limited movement", "locking_catching": "locking or catching",
    "joint_instability": "joint gives way", "cannot_bear_weight": "cannot bear weight",
    "numbness": "numbness or tingling", "leg_weakness": "leg weakness", "arm_weakness": "arm weakness",
    "radiating_leg_pain": "pain radiating down the leg", "fever": "fever",
}
