import re

from fastapi import APIRouter, Depends, HTTPException, Request

from app import config, db
from app.security import client_ip, hash_password, password_problems, require_roles

router = APIRouter(prefix="/api/admin")
admin_only = Depends(require_roles("admin"))
ROLES = ("admin", "doctor", "front_desk")


def _active_admins():
    return [u for u in db.list_users("admin", active_only=True)]


@router.get("/users")
def users(_=admin_only):
    return db.list_users()


@router.post("/users")
def create_user(payload: dict, request: Request, admin: dict = Depends(require_roles("admin"))):
    username, name = str(payload.get("username", "")).strip(), str(payload.get("full_name", "")).strip()
    role, password = payload.get("role"), str(payload.get("password", ""))
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,40}", username):
        raise HTTPException(400, "Username: 3 to 40 letters, digits, dot, dash or underscore.")
    if len(name) < 2 or role not in ROLES:
        raise HTTPException(400, "A full name and a valid role are required.")
    problems = password_problems(password, username)
    if problems:
        raise HTTPException(400, "Temporary password: " + " ".join(problems))
    if db.get_user_by_username(username):
        raise HTTPException(409, "That username is already taken.")
    specialty = (str(payload.get("specialty") or "orthopaedics").strip() if role == "doctor" else None)
    uid = db.create_user(username, name, role, hash_password(password), specialty, must_change=True)
    db.audit(admin, "user_created", "user", uid, {"username": username, "role": role}, client_ip(request))
    return db.get_user(uid)


@router.patch("/users/{user_id}")
def update_user(user_id: int, payload: dict, request: Request, admin: dict = Depends(require_roles("admin"))):
    target = db.get_user(user_id)
    if target is None:
        raise HTTPException(404, "User not found.")
    changes = {}
    if "full_name" in payload and str(payload["full_name"]).strip():
        changes["full_name"] = str(payload["full_name"]).strip()
    if "specialty" in payload:
        changes["specialty"] = str(payload["specialty"] or "").strip() or None
    if "role" in payload:
        if payload["role"] not in ROLES:
            raise HTTPException(400, "Unknown role.")
        changes["role"] = payload["role"]
    if "supervisor_id" in payload:
        sid = payload["supervisor_id"]
        if sid in (None, "", 0):
            changes["supervisor_id"] = None
        else:
            sup = db.get_user(int(sid))
            if sup is None or sup["role"] != "doctor" or not sup["active"] or int(sid) == user_id:
                raise HTTPException(400, "A supervisor must be another active doctor.")
            if sup.get("supervisor_id") == user_id:
                raise HTTPException(400, "That would make two doctors supervise each other.")
            changes["supervisor_id"] = int(sid)
    if "active" in payload:
        changes["active"] = int(bool(payload["active"]))
    losing_admin = target["role"] == "admin" and (changes.get("role", "admin") != "admin" or changes.get("active", 1) == 0)
    if losing_admin and (user_id == admin["id"] or len(_active_admins()) <= 1):
        raise HTTPException(400, "You cannot remove the last active administrator, or yourself.")
    if user_id == admin["id"] and changes.get("active") == 0:
        raise HTTPException(400, "You cannot deactivate your own account.")
    db.update_user(user_id, **changes)
    if changes.get("active") == 0 or "role" in changes:
        db.delete_user_sessions(user_id)                 # takes effect immediately
    db.audit(admin, "user_updated", "user", user_id, changes, client_ip(request))
    return db.get_user(user_id)


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, payload: dict, request: Request, admin: dict = Depends(require_roles("admin"))):
    target = db.get_user(user_id)
    if target is None:
        raise HTTPException(404, "User not found.")
    password = str(payload.get("password", ""))
    problems = password_problems(password, target["username"])
    if problems:
        raise HTTPException(400, "Temporary password: " + " ".join(problems))
    db.update_user(user_id, password_hash=hash_password(password), must_change_password=1, failed_logins=0, locked_until=None)
    db.delete_user_sessions(user_id)
    db.audit(admin, "password_reset", "user", user_id, {}, client_ip(request))
    return {"ok": True}


@router.get("/audit")
def audit_log(action: str = "", user_id: int = 0, limit: int = 200, _=admin_only):
    return db.list_audit(action or None, user_id or None, limit)


@router.get("/analytics")
def analytics(days: int = 30, _=admin_only):
    """Aggregate numbers only (counts and medians). An administrator never sees a patient record."""
    return db.analytics(days if days in (7, 30, 90) else 30)


@router.get("/system")
def system(_=admin_only):
    from app import gnani_client, llm
    with db.conn() as c:
        version = [r["name"] for r in c.execute("SELECT name FROM schema_version ORDER BY name")]
    return {
        "models": [
            {"kind": "stt", "role": "Speech to text", "provider": "Gnani Prisma",
             "setting": gnani_client.STT_MODEL or "Prisma v2.5 (default)", "language": config.LANG,
             "purpose": "Turns the patient's and doctor's speech into text."},
            {"kind": "tts", "role": "Text to speech", "provider": "Gnani Timbre",
             "setting": f"timbre-v2.5, voice {gnani_client.VOICE}", "language": config.LANG,
             "purpose": "Speaks the agent's questions to the patient."},
            {"kind": "llm", "role": "Language model", "provider": "Groq",
             "setting": llm.MODEL, "language": "English and Telugu",
             "purpose": "Reads answers into symptom tags and drafts prescriptions. Never decides urgency or dosing."},
        ],
        "stats": db.model_stats(24), "counts": db.counts(), "migrations": version,
        "settings": {"hospital": config.HOSPITAL_NAME, "session_hours": config.SESSION_HOURS,
                     "visit_token_hours": config.VISIT_TOKEN_HOURS, "secure_cookies": config.COOKIE_SECURE},
    }


# ------------------------------------------------------------------ privacy
@router.get("/patients")
def find_patients(q: str = "", _=admin_only):
    return db.search_patients_admin(q) if len(q.strip()) >= 2 else []


@router.post("/patients/{patient_id}/erase")
def erase_patient(patient_id: int, payload: dict, request: Request, admin: dict = Depends(require_roles("admin"))):
    """Permanent. The administrator must type the patient's record number to confirm."""
    from app.routers import calls
    p = db.get_patient(patient_id)
    if p is None:
        raise HTTPException(404, "Patient not found.")
    if str(payload.get("confirm_mrn", "")).strip().upper() != p["mrn"].upper():
        raise HTTPException(400, "Type the patient's record number exactly to confirm.")
    try:
        mrn = db.erase_patient(patient_id, admin, client_ip(request))
    except ValueError as e:
        raise HTTPException(400, str(e))
    for sid in [k for k, v in calls.SESSIONS.items() if v.get("patient_id") == patient_id]:
        del calls.SESSIONS[sid]                          # end any call in progress for this person
    return {"ok": True, "mrn": mrn}
