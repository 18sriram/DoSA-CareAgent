"""Clinical API. Only the treating doctor can open a patient: another doctor, reception or an administrator gets
a plain 404, so the existence of a record is not revealed."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from app import config, db
from app import questionsets as QS
from app.audio_utils import transcribe_long
from app.checkin_logic import ALERT_RANK
from app.matching import case_record, similar_cases
from app.prescription_logic import clean_prescription, extract_prescription
from app.questions import TAG_VOCAB
from app.routers.frontdesk import _link
from app.security import RateLimiter, client_ip, hash_token, new_token, require_roles

router = APIRouter(prefix="/api")
doctor_only = Depends(require_roles("doctor"))
MAX_CONSULT_BYTES = 12 * 1024 * 1024          # four minutes of 16 kHz speech is about 8 MB
URGENCY_RANK = {"emergency": 0, "high": 1, "medium": 2, "routine": 3}


def _own_intake(intake_id: int, user: dict) -> dict:
    intake = db.get_intake(intake_id)
    if intake is None or intake["doctor_id"] != user["id"]:
        raise HTTPException(404, "Not found.")
    return intake


def _own_prescription(prescription_id: int, user: dict) -> dict:
    rx = db.get_prescription(prescription_id)
    if rx is None:
        raise HTTPException(404, "Not found.")
    _own_intake(rx["intake_id"], user)
    return rx


@router.get("/queue")
def queue(user: dict = Depends(require_roles("doctor"))):
    rows = db.list_queue_for_doctor(user["id"])
    alert_rank = {"emergency": 0, "doctor": 1, None: 2}

    def key(r):
        group = 1 if r["intake_id"] is None else (2 if r["doctor_confirmed"] else 0)
        return (alert_rank[r["open_alert"]], group, URGENCY_RANK.get(r["suggested_urgency"], 9), r["visit_created"])
    rows.sort(key=key)
    return {"patients": rows, "stats": db.agreement_stats(user["id"]), "messages_open": db.count_open_messages(user["id"])}


@router.get("/intakes/{intake_id}")
def intake_detail(intake_id: int, request: Request, user: dict = Depends(require_roles("doctor"))):
    p = _own_intake(intake_id, user)
    p["tag_labels"] = [TAG_VOCAB[t] for t in (p["tags"] or []) if t in TAG_VOCAB]
    p["similar"] = similar_cases(p["tags"] or [])
    p["prescription"] = db.latest_prescription(intake_id)
    p["checkins"] = db.list_checkins(intake_id)
    open_levels = [c["alert_level"] for c in p["checkins"] if not c["acknowledged"] and c["alert_level"] in ("emergency", "doctor")]
    p["open_alert"] = max(open_levels, key=ALERT_RANK.get) if open_levels else None
    db.audit(user, "intake_viewed", "intake", intake_id, {}, client_ip(request))
    return p


@router.post("/intakes/{intake_id}/review")
def review(intake_id: int, payload: dict, request: Request, user: dict = Depends(require_roles("doctor"))):
    _own_intake(intake_id, user)
    try:
        db.review_intake(intake_id, user, payload.get("urgency", ""), str(payload.get("notes", ""))[:2000],
                         payload.get("edits"), client_ip(request))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "stats": db.agreement_stats(user["id"])}


@router.post("/intakes/{intake_id}/close")
def close_visit(intake_id: int, request: Request, user: dict = Depends(require_roles("doctor"))):
    intake = _own_intake(intake_id, user)
    db.close_visit(intake["visit_id"])
    db.audit(user, "visit_closed", "visit", intake["visit_id"], {}, client_ip(request))
    return {"ok": True}


@router.get("/case/{case_id}")
def past_case(case_id: str, request: Request, intake_id: int = 0, user: dict = Depends(require_roles("doctor"))):
    """Full de-identified record of a past case, compared with this doctor's patient."""
    tags = None
    if intake_id:
        tags = _own_intake(intake_id, user)["tags"] or []
    rec = case_record(case_id, tags)
    if rec is None:
        raise HTTPException(404, "Case not found.")
    db.audit(user, "past_case_viewed", "intake", intake_id or None, {"case_id": case_id}, client_ip(request))
    return rec


@router.post("/intakes/{intake_id}/consultation")
def consultation(intake_id: int, request: Request, consent: str = Form(""), transcript: str = Form(None),
                 audio: UploadFile = File(None), user: dict = Depends(require_roles("doctor"))):
    """Add the consultation (recording or pasted transcript). Makes a DRAFT prescription for the doctor to confirm."""
    intake = _own_intake(intake_id, user)
    if consent.strip().lower() != "true":
        raise HTTPException(400, "The patient's consent to record the consultation is required.")
    text, failed = (transcript or "").strip(), []
    if not text and audio is not None:
        try:
            data = audio.file.read(MAX_CONSULT_BYTES + 1)
            if len(data) > MAX_CONSULT_BYTES:
                raise HTTPException(413, "The recording is too large. Record up to 4 minutes, or paste the transcript.")
            text, failed = transcribe_long(data, config.LANG)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if not text:
            raise HTTPException(502, "Speech recognition failed for every part of the recording. Please paste the transcript instead.")
    if not text:
        raise HTTPException(400, "Provide a recording or a transcript.")
    db.record_consent(intake["patient_id"], intake["visit_id"], "consultation_recording", True, "doctor_attested")
    db.audit(user, "consultation_added", "intake", intake_id, {"source": "transcript" if transcript else "audio"}, client_ip(request))
    res = extract_prescription(text)
    if res["error"]:
        print(f"\n[LLM ERROR] prescription draft unavailable: {res['error']}\n")
    pid = db.save_prescription_draft(intake_id, text, res["draft"], res["error"], user["id"])
    return {"prescription_id": pid, "failed_parts": failed, "llm_error": res["error"]}


@router.post("/prescriptions/{prescription_id}/confirm")
def confirm_rx(prescription_id: int, payload: dict, request: Request, user: dict = Depends(require_roles("doctor"))):
    _own_prescription(prescription_id, user)
    try:
        final = clean_prescription(payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.confirm_prescription(prescription_id, final, user, client_ip(request))
    return {"ok": True, "final": final}


@router.post("/prescriptions/{prescription_id}/checkin-link")
def checkin_link(prescription_id: int, request: Request, user: dict = Depends(require_roles("doctor"))):
    """A fresh, expiring link for the reminder call. Only the hash is stored, so a link cannot be looked up later."""
    rx = _own_prescription(prescription_id, user)
    if rx["status"] != "confirmed":
        raise HTTPException(400, "Confirm the prescription first.")
    token, expires = new_token(), db.later(days=config.CHECKIN_TOKEN_DAYS)
    db.set_checkin_token(prescription_id, hash_token(token), expires)
    db.audit(user, "checkin_link_created", "prescription", prescription_id, {}, client_ip(request))
    return {"url": _link(token) + "&k=checkin", "expires_at": expires}


@router.post("/checkins/{checkin_id}/acknowledge")
def ack_checkin(checkin_id: int, request: Request, user: dict = Depends(require_roles("doctor"))):
    c = db.get_checkin(checkin_id)
    if c is None or c["doctor_id"] != user["id"]:
        raise HTTPException(404, "Not found.")
    db.acknowledge_checkin(checkin_id, user, client_ip(request))
    return {"ok": True}


# ---------------------------------------------------------------- messages from the help line
def _message(e: dict) -> dict:
    e["tag_labels"] = [TAG_VOCAB[t] for t in e.pop("tags", []) if t in TAG_VOCAB]
    e["flags"] = [f["label"] for f in e.get("flags", [])]
    e["context"] = db.patient_context(e["patient_id"])
    if e["kind"] != "emergency":
        e.pop("phone", None)                 # only an emergency needs the number
    return e


@router.get("/messages")
def messages(request: Request, user: dict = Depends(require_roles("doctor"))):
    open_rows, answered = db.list_escalations_for_doctor(user["id"])
    db.audit(user, "messages_viewed", "doctor", user["id"], {"open": len(open_rows)}, client_ip(request))
    return {"open": [_message(e) for e in open_rows], "answered": [_message(e) for e in answered]}


def _own_message(message_id: int, user: dict) -> dict:
    e = db.get_escalation(message_id)
    if e is None or e["assigned_to"] != user["id"]:
        raise HTTPException(404, "Not found.")
    return e


@router.post("/messages/{message_id}/reply")
def reply_message(message_id: int, payload: dict, request: Request, user: dict = Depends(require_roles("doctor"))):
    e = _own_message(message_id, user)
    text = str(payload.get("text", "")).strip()
    if len(text) < 2 or len(text) > 600:
        raise HTTPException(400, "Write a reply of up to 600 characters.")
    if e["kind"] == "emergency":
        raise HTTPException(400, "Emergency calls are handled by phoning the patient, then closed.")
    try:
        db.answer_escalation(message_id, user, text, bool(payload.get("reusable")), client_ip(request))
    except LookupError as ex:
        raise HTTPException(404, str(ex))
    return {"ok": True}


@router.post("/messages/{message_id}/close")
def close_message(message_id: int, request: Request, user: dict = Depends(require_roles("doctor"))):
    _own_message(message_id, user)
    db.close_escalation(message_id, user, client_ip(request))
    return {"ok": True}


# ---------------------------------------------------------------- the doctor's own intake questions
preview_limiter = RateLimiter(30, 3600)          # each "hear it" costs a speech-synthesis call


@router.get("/question-set")
def get_question_set(user: dict = Depends(require_roles("doctor"))):
    custom = db.get_question_set(user["id"])
    return {"questions": custom["questions"] if custom else QS.default_editable(), "is_default": custom is None,
            "closing": {"te": QS.CLOSING["te"], "en": QS.CLOSING["en"]}, "min": QS.MIN_Q, "max": QS.MAX_Q}


@router.put("/question-set")
def save_question_set(payload: dict, request: Request, user: dict = Depends(require_roles("doctor"))):
    try:
        questions = QS.validate(payload.get("questions"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.save_question_set(user["id"], questions, user["id"])
    db.audit(user, "question_set_updated", "doctor", user["id"], {"questions": len(questions)}, client_ip(request))
    return {"ok": True, "questions": questions}


@router.delete("/question-set")
def reset_question_set(request: Request, user: dict = Depends(require_roles("doctor"))):
    db.delete_question_set(user["id"])
    db.audit(user, "question_set_reset", "doctor", user["id"], {}, client_ip(request))
    return {"ok": True, "questions": QS.default_editable()}


@router.post("/question-set/preview")
def preview_question(payload: dict, user: dict = Depends(require_roles("doctor"))):
    """Let the doctor hear a question exactly as the patient will."""
    from app.gnani_client import GnaniError
    from app.routers.calls import tts_name
    text = str(payload.get("text", "")).strip()
    if len(text) < 5 or len(text) > 300 or not QS.TELUGU.search(text):
        raise HTTPException(400, "Write the question in Telugu first (up to 300 characters).")
    preview_limiter.check(str(user["id"]))
    try:
        name = tts_name(text)
    except GnaniError as e:
        raise HTTPException(502, f"The voice service is not available right now: {e}")
    return FileResponse(f"{config.AUDIO_DIR}/{name}", media_type="audio/wav", headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- standard answers a doctor has approved
@router.get("/answer-bank")
def answer_bank(user: dict = Depends(require_roles("doctor"))):
    """Replies this doctor chose to reuse. The help line reads them to OTHER patients who ask the same thing."""
    return db.list_answer_bank(user["id"])


@router.delete("/answer-bank/{bank_id}")
def delete_answer(bank_id: int, request: Request, user: dict = Depends(require_roles("doctor"))):
    if not db.delete_bank_answer(bank_id, user["id"]):
        raise HTTPException(404, "Not found.")
    db.audit(user, "standard_answer_deleted", "answer_bank", bank_id, {}, client_ip(request))
    return {"ok": True}
