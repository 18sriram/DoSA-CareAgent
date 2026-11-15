"""The patient-facing voice calls (pre-visit intake and follow-up check-in).

Patients do not have accounts. They arrive with a secret, expiring link from their QR code or reminder, and the
link is the only thing that identifies them. Nothing identifying is ever sent back before the call is complete.
In this build the 'phone call' runs in the browser; the same pipeline works behind a telephony provider.
"""
import hashlib
import os
import re
import tempfile
import time
import unicodedata
import uuid

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from app import config, db
from app import inbound_logic as IL
from app import questionsets as QS
from app import scheduling as S
from app.checkin_logic import (CHECKIN_CLOSING, alert_level, build_alerts, build_checkin_questions, extract_checkin)
from app.gnani_client import GnaniError, speak, transcribe
from app.intake_logic import extract_intake, suggest_urgency
from app.matching import progress_view
from app.questions import CLOSING, CONSENT_DECLINED, CONSENT_YES, GREETING, QUESTIONS
from app.red_flags import EMERGENCY_MESSAGE, check_red_flags, has_emergency
from app.security import _DUMMY_HASH, call_limiter, client_ip, hash_token, verify_password

router = APIRouter()
SESSIONS = {}
RETRY_TEXT = "క్షమించండి, నాకు వినిపించలేదు. దయచేసి మళ్ళీ చెప్పండి."
HELP_TEXT = "క్షమించండి, మీ మాట వినడంలో ఇబ్బంది ఉంది. దయచేసి మీ సమాధానం టైప్ చేయండి, లేదా రిసెప్షన్ సిబ్బందిని అడగండి."
INVALID = "This link is not valid or has expired. Please ask the reception desk for a new one."


def tts_name(text: str) -> str:
    """Speak text with Timbre, cached by text so repeated lines play instantly. Returns the cache file name."""
    h = hashlib.md5(text.encode("utf-8")).hexdigest()
    path = f"{config.AUDIO_DIR}/{h}.wav"
    if not os.path.exists(path) or os.path.getsize(path) < 1000:   # a missing or tiny file is not real speech
        os.makedirs(config.AUDIO_DIR, exist_ok=True)
        speak(text, path, language=config.LANG)
    return f"{h}.wav"


def reply(sess, text, **extra):
    """If the voice service is down, the call carries on in text rather than failing."""
    try:
        url = f"/audio/{tts_name(text)}?s={sess['id']}"
    except GnaniError as e:
        print(f"\n[VOICE UNAVAILABLE] {e}\n")
        url = None
    return {"agent_text": text, "audio_url": url, **extra}


def consent_given(heard: str) -> bool:
    norm = unicodedata.normalize("NFC", heard).lower()
    return any(w in norm for w in CONSENT_YES)


def _days_since(ts: str) -> int:
    return max(0, (db.parse(db.utcnow()) - db.parse(ts)).days)


# ------------------------------------------------------------------ intake
def finish_intake(sess, emergency=False):
    qa = sess["qa"]
    info = extract_intake(qa) if qa else {"name": "", "age": None, "complaint_summary": "", "medicines": "", "tags": [],
                                         "corrections": [], "needs_manual_review": True}
    if info.get("error"):
        print(f"\n[LLM ERROR] summary and tags unavailable (visit {sess['visit_id']}): {info['error']}\n")
    flags = check_red_flags(" ".join(x["answer"] for x in qa))
    urgency = suggest_urgency(info["tags"], flags)
    patient = db.get_patient(sess["patient_id"])
    form = {"name": patient["full_name"], "age": patient["age"],          # registration is authoritative
            "stated_name": info["name"], "stated_age": info["age"],        # what the patient said on the call
            "complaint_summary": info["complaint_summary"], "medicines": info["medicines"],
            "needs_manual_review": info["needs_manual_review"], "llm_error": info.get("error", ""),
            "corrections": info.get("corrections", []), "urgency_reasons": urgency["reasons"],
            "call_seconds": int(time.time() - sess["created"]),
            "answers": {x["question_id"]: x["answer"] for x in qa}}
    try:
        iid = db.save_intake(sess["visit_id"], sess["patient_id"], sess["doctor_id"], qa, form, info["tags"], flags, urgency["level"])
        db.audit(None, "intake_completed", "intake", iid, {"visit_id": sess["visit_id"], "urgency": urgency["level"]})
    except Exception as e:      # e.g. the same link used twice at the same moment
        print(f"[intake not saved] {e}")
    if urgency["level"] == "emergency":
        print(f"\n*** EMERGENCY ALERT: visit {sess['visit_id']} - {urgency['reasons']} ***\n")
    sess["stage"] = "done"
    text = EMERGENCY_MESSAGE["te"] if emergency else CLOSING["te"]
    return reply(sess, text, done=True, emergency=emergency, flags=[f["label"] for f in flags])


def handle_intake(sess, heard, total):
    questions = sess["questions"]
    if sess["stage"] == "consent":
        granted = consent_given(heard)
        db.record_consent(sess["patient_id"], sess["visit_id"], "call_recording", granted, "voice")
        if granted:
            sess["stage"] = "questions"
            return reply(sess, questions[0]["te"], step=1, total=total, done=False)
        sess["stage"] = "done"
        return reply(sess, CONSENT_DECLINED["te"], step=0, total=total, done=True)
    q = questions[sess["idx"]]
    sess["qa"].append({"question_id": q["id"], "question_en": q["en"], "answer": heard})
    if has_emergency(check_red_flags(heard)):          # emergency rules run on every utterance, before anything else
        return finish_intake(sess, emergency=True)
    sess["idx"] += 1
    if sess["idx"] < total:
        return reply(sess, questions[sess["idx"]]["te"], step=sess["idx"] + 1, total=total, done=False)
    return finish_intake(sess)


# ------------------------------------------------------------------ check-in
def finish_checkin(sess, emergency=False):
    qa = sess["qa"]
    info = extract_checkin(qa, sess["medicines"])
    if info.get("error"):
        print(f"\n[LLM ERROR] check-in summary unavailable (intake {sess['intake_id']}): {info['error']}\n")
    flags = check_red_flags(" ".join(x["answer"] for x in qa))
    alerts = build_alerts(info, sess["baseline_tags"], flags)
    complete = len(qa) >= sess["total"] and not info.get("needs_manual_review")
    if info.get("needs_manual_review"):
        alerts.append({"level": "doctor", "text": "Automatic summary unavailable. Please read the transcript."})
    if complete:
        progress = progress_view(sess["baseline_tags"], info["tags"], sess["snapshot"])
    else:   # an unfinished call must not look like "all symptoms resolved"
        progress = {"week": sess["snapshot"], "unavailable": True,
                    "reason": "The call ended before every question was answered." if not info.get("needs_manual_review")
                    else "Automatic summary unavailable."}
    progress["days_since_visit"] = sess["days"]
    level = alert_level(alerts)
    cid = db.save_checkin(sess["intake_id"], sess["prescription_id"], sess["snapshot"], qa, info, info["tags"], flags,
                          alerts, level, progress)
    db.audit(None, "checkin_completed", "checkin", cid, {"alert_level": level})
    for a in alerts:
        print(f"[ALERT:{a['level']}] intake {sess['intake_id']}: {a['text']}")
    sess["stage"] = "done"
    text = EMERGENCY_MESSAGE["te"] if emergency else CHECKIN_CLOSING["te"]
    return reply(sess, text, done=True, emergency=emergency, flags=[f["label"] for f in flags], alert_level=level)


def handle_checkin(sess, heard):
    qs, total = sess["questions"], sess["total"]
    if sess["stage"] == "consent":
        granted = consent_given(heard)
        db.record_consent(sess["patient_id"], None, "call_recording", granted, "voice")
        if granted:
            sess["stage"] = "questions"
            return reply(sess, qs[0]["te"], step=1, total=total, done=False)
        sess["stage"] = "done"
        return reply(sess, CONSENT_DECLINED["te"], step=0, total=total, done=True)
    q = qs[sess["idx"]]
    sess["qa"].append({"question_id": q["id"], "question_en": q["en"], "answer": heard})
    if has_emergency(check_red_flags(heard)):
        return finish_checkin(sess, emergency=True)
    sess["idx"] += 1
    if sess["idx"] < total:
        return reply(sess, qs[sess["idx"]]["te"], step=sess["idx"] + 1, total=total, done=False)
    return finish_checkin(sess)


# ------------------------------------------------------------------ inbound help line
MAX_HELPLINE_TURNS = 8
MAX_TURN_BYTES = 3 * 1024 * 1024      # a 30-second clip is under 1 MB
PRIORITY_FROM_URGENCY = {"emergency": "emergency", "high": "urgent", "medium": "routine", "routine": "routine"}


def _escalate(sess, kind, priority, question_en, original, tags=None, flags=None):
    """Pass something to the patient's doctor (their supervisor takes over if nobody answers in time)."""
    doctor_id = db.latest_doctor_for_patient(sess["patient_id"])
    if doctor_id is None:
        return None
    eid = db.create_escalation(sess["patient_id"], kind, priority, question_en, original, tags, flags, doctor_id)
    db.audit(None, "escalation_created", "escalation", eid, {"kind": kind, "priority": priority, "via": "help line"})
    print(f"[HELP LINE] {priority.upper()} message {eid} for doctor {doctor_id}")
    return eid


def _end_helpline(sess, text, emergency=False):
    sess["stage"] = "done"
    db.audit(None, "helpline_call_ended", "patient", sess["patient_id"], {"turns": sess["turns"], "emergency": emergency,
                                                                          "seconds": int(time.time() - sess["created"])})
    return reply(sess, text, done=True, emergency=emergency, kind="inbound")


def _offer_slots(sess, doctor_id, prefix=""):
    """Read out up to three free times. The caller then says which one they want."""
    slots = S.free_slots(doctor_id, 14) if doctor_id else []
    if not slots:
        _escalate(sess, "appointment_request", "routine", "Wants an appointment but no free times are available", "", [], [])
        return prefix + IL.NO_SLOTS
    sess["offered"], sess["offered_doctor"] = S.pick_to_offer(slots), doctor_id
    lines = " ".join(f"{IL.ORDINALS[i]}: {S.speak_slot(s)}." for i, s in enumerate(sess["offered"]))
    return f"{prefix}{IL.OFFER_INTRO}{lines} {IL.ASK_CHOICE}"


def _book_choice(sess, choice):
    slot, doctor_id = sess["offered"][choice - 1], sess["offered_doctor"]
    try:
        if not S.is_bookable(doctor_id, slot):
            raise ValueError("gone")
        aid = db.book_appointment(sess["patient_id"], doctor_id, slot, "call")
    except ValueError:
        return _offer_slots(sess, doctor_id, IL.TAKEN)
    db.audit(None, "appointment_booked", "appointment", aid, {"via": "call", "patient_id": sess["patient_id"], "doctor_id": doctor_id})
    sess.pop("offered", None)
    return f"{IL.BOOKED}{S.speak_slot(slot)}."


def handle_inbound(sess, heard):
    if sess["stage"] == "consent":
        granted = consent_given(heard)
        db.record_consent(sess["patient_id"], None, "call_recording", granted, "voice")
        if not granted:
            sess["stage"] = "done"
            return reply(sess, CONSENT_DECLINED["te"], done=True, kind="inbound")
        sess["stage"] = "open"
        pending = db.undelivered_answers(sess["patient_id"])
        db.mark_delivered([p["id"] for p in pending])        # the doctor's replies are read out once
        said = "".join(f"{IL.DOCTOR_REPLY}{p['answer_text'].strip().rstrip('.')}. " for p in pending)
        return reply(sess, said + IL.HELP_PROMPT, done=False, kind="inbound")

    sess["turns"] += 1
    flags = check_red_flags(heard)
    if has_emergency(flags):                                  # fixed rules, before any model
        _escalate(sess, "emergency", "emergency", "EMERGENCY wording on the help line", heard, [], flags)
        return _end_helpline(sess, EMERGENCY_MESSAGE["te"], emergency=True)

    doctor_id = db.latest_doctor_for_patient(sess["patient_id"])
    bank = db.bank_for_doctor(doctor_id) if doctor_id else []
    cls = IL.classify(heard, bank, len(sess.get("offered", [])))
    if cls.get("error"):
        print(f"\n[LLM ERROR] help line could not classify: {cls['error']}\n")
    sess["qa"].append({"heard": heard, "intent": cls["intent"]})
    text, final = "", False
    if cls["intent"] == "end":
        return _end_helpline(sess, IL.GOODBYE)
    if cls["intent"] == "facts":
        if cls["bank_id"] and doctor_id and db.get_bank_answer(cls["bank_id"], doctor_id):
            text = db.get_bank_answer(cls["bank_id"], doctor_id)["answer_text"]
            db.audit(None, "helpline_answered", "patient", sess["patient_id"], {"how": "standard_answer"})
        else:
            rx = (db.latest_confirmed_prescription_for_patient(sess["patient_id"]) or {}).get("final") or {}
            done = IL.compose_facts(cls["topics"], cls["medicine_name"], rx) if cls["topics"] else {"text": "", "answered": [], "missing": ["unknown"]}
            text = done["text"]
            if done["answered"]:
                db.audit(None, "helpline_answered", "patient", sess["patient_id"], {"how": "record", "topics": done["answered"]})
            if done["missing"]:                               # part of the question is not in the record
                text = (text + " " if text else "") + IL.NOT_IN_RECORD + " " + IL.SENT_TO_DOCTOR
                _escalate(sess, "question", "urgent" if cls["urgent"] else "routine", cls["question_en"], heard)
    elif cls["intent"] == "appointment_slots":
        text = _offer_slots(sess, doctor_id)
    elif cls["intent"] == "appointment_book":
        if not sess.get("offered"):
            text = _offer_slots(sess, doctor_id)
        elif not cls["choice"]:
            text = IL.ASK_CHOICE
        else:
            text = _book_choice(sess, cls["choice"])
    elif cls["intent"] == "appointment_info":
        mine = db.patient_appointments(sess["patient_id"], S.local_now().strftime("%Y-%m-%dT%H:%M"))
        text = IL.INFO + " ".join(S.speak_slot(a["starts_at"]) + "." for a in mine[:2]) if mine else IL.INFO_NONE
    elif cls["intent"] == "symptom_report":
        level = suggest_urgency(cls["tags"], flags)["level"]
        priority = PRIORITY_FROM_URGENCY[level] if not cls["urgent"] or level != "routine" else "urgent"
        _escalate(sess, "symptom_report", priority, cls["question_en"], heard, cls["tags"], flags)
        text = IL.SENT_SYMPTOMS
    else:
        if _escalate(sess, "question", "urgent" if cls["urgent"] else "routine", cls["question_en"], heard) is None:
            return _end_helpline(sess, IL.NO_DOCTOR)
        text = IL.SENT_TO_DOCTOR
    if sess["turns"] >= MAX_HELPLINE_TURNS:
        return _end_helpline(sess, text + " " + IL.CALL_LIMIT)
    return reply(sess, f"{text} {IL.ANYTHING_ELSE}", done=False, kind="inbound")


@router.post("/api/inbound/start")
def inbound_start(payload: dict, request: Request):
    """The caller proves who they are with their registered phone number and the PIN printed on their token slip."""
    call_limiter.check(client_ip(request))
    phone, pin = db.normalize_phone(str(payload.get("phone") or "")), str(payload.get("pin") or "").strip()
    refused = HTTPException(401, "We could not verify these details. Please check the phone number and the PIN on your token slip.")
    if db.pin_failures(phone) >= config.PIN_ATTEMPTS:
        raise HTTPException(429, "Too many wrong attempts for this number. Please try again later or ask the reception desk.")
    candidates = db.patients_by_phone(phone)
    match = next((p for p in candidates if p.get("pin_hash") and verify_password(pin, p["pin_hash"])), None)
    if not candidates:
        verify_password(pin, _DUMMY_HASH)                    # same work whether or not the number is known
    if match is None:
        db.record_pin_failure(phone or "unknown")
        db.audit(None, "helpline_verification_failed", "phone", None, {"phone_last4": phone[-4:]}, client_ip(request))
        raise refused
    db.clear_pin_failures(phone)
    sess = {"id": uuid.uuid4().hex, "kind": "inbound", "stage": "consent", "idx": 0, "qa": [], "created": time.time(),
            "patient_id": match["id"], "total": 0, "turns": 0}
    SESSIONS[sess["id"]] = sess
    db.audit(None, "helpline_call_started", "patient", match["id"], {}, client_ip(request))
    return {"session_id": sess["id"], "total": 0, "step": 0, "kind": "inbound", **reply(sess, GREETING["te"])}


# ------------------------------------------------------------------ routes
@router.post("/api/call/start")
def start(payload: dict, request: Request):
    call_limiter.check(client_ip(request))
    th, now = hash_token(str(payload.get("token") or "")), db.utcnow()
    for k in [k for k, v in SESSIONS.items() if time.time() - v["created"] > 7200]:
        del SESSIONS[k]                                 # forget abandoned calls
    sess = {"id": uuid.uuid4().hex, "stage": "consent", "idx": 0, "qa": [], "created": time.time()}
    if payload.get("kind") == "checkin":
        rx = db.get_prescription_by_checkin_token(th)
        if rx is None or (rx["token_expires"] or "") <= now:
            raise HTTPException(404, INVALID)
        meds = [m["name"] for m in (rx["final"] or {}).get("medicines", []) if m.get("name")]
        questions = build_checkin_questions(meds)
        days = _days_since(rx["intake_created"])
        sess.update(kind="checkin", questions=questions, total=len(questions), intake_id=rx["intake_id"],
                    prescription_id=rx["id"], patient_id=rx["patient_id"], baseline_tags=rx["baseline_tags"], medicines=meds,
                    days=days, snapshot=3 if days <= 28 else 6)
    else:
        v = db.get_visit_by_token(th)
        if v is None or v["token_expires_at"] <= now:
            raise HTTPException(404, INVALID)
        if v["intake_completed_at"]:
            raise HTTPException(410, "Your details have already been received. Please wait for your turn.")
        questions = QS.effective_questions(v["doctor_id"])        # a snapshot: edits made mid-call do not change this call
        sess.update(kind="intake", visit_id=v["id"], patient_id=v["patient_id"], doctor_id=v["doctor_id"], questions=questions, total=len(questions))
    SESSIONS[sess["id"]] = sess
    return {"session_id": sess["id"], "total": sess["total"], "step": 0, "kind": sess["kind"], **reply(sess, GREETING["te"])}


@router.post("/api/call/turn")
def turn(session_id: str = Form(...), audio: UploadFile = File(None), text: str = Form(None)):
    sess = SESSIONS.get(session_id)
    if not sess or sess["stage"] == "done":
        raise HTTPException(400, "This call has ended.")
    stt_failed = False
    if text is not None and text.strip():
        heard = text.strip()[:1000]                       # typed fallback when speech recognition is unavailable
    elif audio is not None:
        data = audio.file.read(MAX_TURN_BYTES + 1)
        if len(data) > MAX_TURN_BYTES:
            raise HTTPException(413, "That recording is too long. Please answer in a shorter sentence.")
        print(f"[audio] received {len(data) / 1024:.0f} KB, about {max(len(data) - 44, 0) / 32000:.1f} s of speech")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(data)
            path = tmp.name
        try:
            heard = transcribe(path, language_code=config.LANG)
        except GnaniError as e:
            print(f"\n[SPEECH RECOGNITION FAILED] {e}\n")
            heard, stt_failed = "", True
        finally:
            os.remove(path)                               # the patient's audio is never kept
    else:
        raise HTTPException(400, "Send audio or text.")

    total = sess["total"]
    if not heard:
        sess["stt_failures"] = sess.get("stt_failures", 0) + (1 if stt_failed else 0)
        msg = HELP_TEXT if sess["stt_failures"] >= 2 else RETRY_TEXT
        step = sess["idx"] + (1 if sess["stage"] == "questions" else 0)
        return {"transcript": "", **reply(sess, msg, step=step, total=total, done=False, stt_failed=stt_failed)}
    sess["stt_failures"] = 0
    handlers = {"checkin": lambda: handle_checkin(sess, heard), "inbound": lambda: handle_inbound(sess, heard)}
    out = handlers.get(sess["kind"], lambda: handle_intake(sess, heard, total))()
    return {"transcript": heard, **out}


@router.get("/audio/{name}")
def audio_file(name: str, s: str = ""):
    """Voice lines are only served to a live call, not to anyone who guesses a URL."""
    if s not in SESSIONS or not re.fullmatch(r"[0-9a-f]{32}\.wav", name):
        raise HTTPException(404)
    path = f"{config.AUDIO_DIR}/{name}"
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "no-store"})
