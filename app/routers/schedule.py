"""Doctor timetables and appointments. A doctor manages only their own timetable; reception books for anyone."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request

from app import db, scheduling as S
from app.security import client_ip, require_roles

router = APIRouter(prefix="/api")
desk_roles = ("front_desk", "admin")


def _today():
    return S.local_now().strftime("%Y-%m-%dT00:00")


# ------------------------------------------------------------------ a doctor's own timetable
@router.get("/schedule")
def my_schedule(user: dict = Depends(require_roles("doctor"))):
    sch = db.get_schedule(user["id"])
    return {**sch, "time_off": db.list_time_off(user["id"]), "slot_choices": list(S.SLOT_CHOICES),
            "appointments": db.list_appointments(user["id"], S.local_now().strftime("%Y-%m-%dT%H:%M"))}


@router.put("/schedule")
def save_schedule(payload: dict, request: Request, user: dict = Depends(require_roles("doctor"))):
    try:
        windows = S.validate_windows(payload.get("windows", []))
        minutes = int(payload.get("slot_minutes", 15))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    if minutes not in S.SLOT_CHOICES:
        raise HTTPException(400, "Choose a slot length from the list.")
    db.set_schedule(user["id"], windows, minutes)
    db.audit(user, "schedule_updated", "doctor", user["id"], {"sessions": len(windows), "slot_minutes": minutes}, client_ip(request))
    return {"ok": True, "windows": windows}


@router.post("/schedule/time-off")
def add_time_off(payload: dict, request: Request, user: dict = Depends(require_roles("doctor"))):
    try:
        day = datetime.strptime(str(payload.get("date", "")), "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "Choose a date.")
    if day < S.local_now().date():
        raise HTTPException(400, "That date has already passed.")
    tid = db.add_time_off(user["id"], day.isoformat(), str(payload.get("reason", ""))[:120])
    booked = db.list_appointments(user["id"], day.isoformat() + "T00:00", (day + timedelta(days=1)).isoformat() + "T00:00")
    db.audit(user, "time_off_added", "doctor", user["id"], {"date": day.isoformat(), "appointments_that_day": len(booked)}, client_ip(request))
    return {"id": tid, "appointments_that_day": booked}


@router.delete("/schedule/time-off/{time_off_id}")
def remove_time_off(time_off_id: int, user: dict = Depends(require_roles("doctor"))):
    if not db.delete_time_off(time_off_id, user["id"]):
        raise HTTPException(404, "Not found.")
    return {"ok": True}


# ------------------------------------------------------------------ reception: slots and bookings
@router.get("/slots")
def slots(doctor_id: int, days: int = 14, _=Depends(require_roles(*desk_roles))):
    doc = db.get_user(doctor_id)
    if not doc or doc["role"] != "doctor" or not doc["active"]:
        raise HTTPException(404, "Doctor not found.")
    return {"doctor": doc["full_name"], "slots": S.free_slots(doctor_id, min(max(days, 1), S.HORIZON_DAYS))}


@router.get("/appointments")
def appointments(days: int = 14, _=Depends(require_roles(*desk_roles))):
    start = S.local_now().strftime("%Y-%m-%dT%H:%M")
    end = (S.local_now() + timedelta(days=min(max(days, 1), 60))).strftime("%Y-%m-%dT%H:%M")
    return db.list_appointments(None, start, end)


@router.post("/appointments")
def book(payload: dict, request: Request, user: dict = Depends(require_roles(*desk_roles))):
    doctor = db.get_user(int(payload.get("doctor_id") or 0))
    patient = db.get_patient(int(payload.get("patient_id") or 0))
    if not doctor or doctor["role"] != "doctor" or not doctor["active"]:
        raise HTTPException(400, "Choose a doctor.")
    if not patient:
        raise HTTPException(400, "Choose a patient.")
    starts_at = str(payload.get("starts_at", ""))
    if not S.is_bookable(doctor["id"], starts_at):
        raise HTTPException(409, "That time is not available. Please pick another.")
    try:
        aid = db.book_appointment(patient["id"], doctor["id"], starts_at, "reception", user["id"])
    except ValueError as e:
        raise HTTPException(409, str(e))
    db.audit(user, "appointment_booked", "appointment", aid, {"via": "reception", "patient_id": patient["id"], "doctor_id": doctor["id"]}, client_ip(request))
    return db.get_appointment(aid)


@router.post("/appointments/{appointment_id}/cancel")
def cancel(appointment_id: int, request: Request, user: dict = Depends(require_roles("front_desk", "admin", "doctor"))):
    a = db.get_appointment(appointment_id)
    if a is None or (user["role"] == "doctor" and a["doctor_id"] != user["id"]):
        raise HTTPException(404, "Not found.")
    if not db.cancel_appointment(appointment_id, user, client_ip(request)):
        raise HTTPException(400, "This appointment is already cancelled.")
    return {"ok": True}
