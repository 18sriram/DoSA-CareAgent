import re
import secrets

import segno
from fastapi import APIRouter, Depends, HTTPException, Request

from app import config, db
from app.security import client_ip, hash_password, hash_token, new_token, require_roles

router = APIRouter(prefix="/api")
desk = Depends(require_roles("front_desk", "admin"))
staff = Depends(require_roles("front_desk", "admin", "doctor"))


def _qr(url):
    return segno.make(url, error="m").svg_data_uri(scale=6, border=2, dark="#12303b")


def _new_pin():
    return f"{secrets.randbelow(10 ** 6):06d}"


def _link(token):
    return f"{config.PUBLIC_URL}/call?t={token}"


@router.get("/doctors")
def doctors(_=staff):
    return [{"id": u["id"], "full_name": u["full_name"], "specialty": u["specialty"]} for u in db.list_users("doctor", active_only=True)]


@router.get("/patients")
def patients(q: str = "", _=desk):
    if len(q.strip()) < 2:
        return []
    return [{k: p[k] for k in ("id", "mrn", "full_name", "age", "sex", "phone", "language")} for p in db.search_patients(q)]


@router.post("/visits")
def register_visit(payload: dict, request: Request, user: dict = Depends(require_roles("front_desk", "admin"))):
    doctor = db.get_user(int(payload.get("doctor_id") or 0))
    if not doctor or doctor["role"] != "doctor" or not doctor["active"]:
        raise HTTPException(400, "Choose a doctor.")
    if payload.get("patient_id"):
        patient = db.get_patient(int(payload["patient_id"]))
        if patient is None:
            raise HTTPException(404, "Patient not found.")
        pid, pin = patient["id"], None
    else:
        name = str(payload.get("full_name", "")).strip()
        phone = re.sub(r"[\s-]", "", str(payload.get("phone", "")))
        try:
            age = int(payload.get("age")) if payload.get("age") not in (None, "") else None
        except (TypeError, ValueError):
            raise HTTPException(400, "Age must be a number.")
        if len(name) < 2 or len(name) > 80:
            raise HTTPException(400, "Enter the patient's full name.")
        if age is not None and not 0 <= age <= 120:
            raise HTTPException(400, "Age must be between 0 and 120.")
        if phone and not re.fullmatch(r"\+?\d{8,15}", phone):
            raise HTTPException(400, "Phone number must be 8 to 15 digits.")
        sex = payload.get("sex") if payload.get("sex") in ("F", "M", "O") else None
        pin = _new_pin()                                        # shown once, on the slip; only its hash is kept
        pid = db.create_patient(name, age, sex, db.normalize_phone(phone), config.LANG, user["id"], hash_password(pin))
    token = new_token()
    expires = db.later(hours=config.VISIT_TOKEN_HOURS)
    vid = db.create_visit(pid, doctor["id"], hash_token(token), expires, user["id"])
    db.audit(user, "visit_registered", "visit", vid, {"patient_id": pid, "doctor_id": doctor["id"]}, client_ip(request))
    patient = db.get_patient(pid)
    url = _link(token)
    return {"visit_id": vid, "mrn": patient["mrn"], "name": patient["full_name"], "doctor": doctor["full_name"],
            "url": url, "qr": _qr(url), "expires_at": expires, "pin": pin, "helpline": f"{config.PUBLIC_URL}/helpline",
            "has_phone": bool(patient["phone"])}


@router.post("/patients/{patient_id}/new-pin")
def new_pin(patient_id: int, request: Request, user: dict = Depends(require_roles("front_desk", "admin"))):
    """Issue a fresh help-line PIN (the old one stops working). Shown once."""
    p = db.get_patient(patient_id)
    if p is None:
        raise HTTPException(404, "Patient not found.")
    pin = _new_pin()
    db.set_pin(patient_id, hash_password(pin))
    db.audit(user, "pin_reissued", "patient", patient_id, {}, client_ip(request))
    return {"name": p["full_name"], "mrn": p["mrn"], "pin": pin, "helpline": f"{config.PUBLIC_URL}/helpline"}


@router.get("/emergencies")
def emergencies(_=desk):
    """Emergency calls to the help line that nobody has acted on yet: staff should phone the patient."""
    return db.open_emergencies()


@router.post("/emergencies/{escalation_id}/handled")
def emergency_handled(escalation_id: int, request: Request, user: dict = Depends(require_roles("front_desk", "admin"))):
    db.handle_emergency(escalation_id, user, client_ip(request))
    return {"ok": True}


@router.get("/visits")
def visits(_=desk):
    return db.list_visits_for_desk()


@router.post("/visits/{visit_id}/new-link")
def new_link(visit_id: int, request: Request, user: dict = Depends(require_roles("front_desk", "admin"))):
    v = db.get_visit(visit_id)
    if v is None:
        raise HTTPException(404, "Visit not found.")
    if v["intake_completed_at"]:
        raise HTTPException(400, "This patient has already completed the intake call.")
    token, expires = new_token(), db.later(hours=config.VISIT_TOKEN_HOURS)
    db.rotate_visit_token(visit_id, hash_token(token), expires)
    db.audit(user, "visit_link_reissued", "visit", visit_id, {}, client_ip(request))
    patient = db.get_patient(v["patient_id"])
    url = _link(token)
    return {"visit_id": visit_id, "mrn": patient["mrn"], "name": patient["full_name"], "url": url, "qr": _qr(url), "expires_at": expires}
