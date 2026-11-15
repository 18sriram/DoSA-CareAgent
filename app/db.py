"""SQLite storage with versioned migrations. Every table that holds patient data lives here, and every function
that touches it is in this file, so access can be reviewed in one place.

Times are stored in UTC as 'YYYY-MM-DDTHH:MM:SSZ'. Secrets (passwords, session and patient tokens) are never
stored in clear: only salted hashes or SHA-256 digests.
"""
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from app import config

FMT = "%Y-%m-%dT%H:%M:%SZ"


def utcnow():
    return datetime.now(timezone.utc).strftime(FMT)


def parse(ts):
    return datetime.strptime(ts, FMT).replace(tzinfo=timezone.utc)


def later(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).strftime(FMT)


@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


MIGRATIONS = [
    ("0001_initial", """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        full_name TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('admin','doctor','front_desk')),
        specialty TEXT,
        password_hash TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        must_change_password INTEGER NOT NULL DEFAULT 0,
        failed_logins INTEGER NOT NULL DEFAULT 0,
        locked_until TEXT,
        created_at TEXT NOT NULL,
        last_login_at TEXT
    );
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        csrf_token TEXT NOT NULL,
        created_at TEXT NOT NULL, expires_at TEXT NOT NULL, last_seen TEXT NOT NULL,
        ip TEXT, user_agent TEXT
    );
    CREATE TABLE patients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mrn TEXT NOT NULL UNIQUE,
        full_name TEXT NOT NULL, age INTEGER, sex TEXT, phone TEXT,
        language TEXT NOT NULL DEFAULT 'te-IN',
        created_at TEXT NOT NULL, created_by INTEGER REFERENCES users(id)
    );
    CREATE TABLE visits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL REFERENCES patients(id),
        doctor_id INTEGER NOT NULL REFERENCES users(id),
        status TEXT NOT NULL DEFAULT 'registered' CHECK (status IN ('registered','intake_done','closed')),
        token_hash TEXT NOT NULL UNIQUE, token_expires_at TEXT NOT NULL,
        intake_completed_at TEXT,
        created_at TEXT NOT NULL, created_by INTEGER REFERENCES users(id)
    );
    CREATE TABLE intakes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        visit_id INTEGER NOT NULL UNIQUE REFERENCES visits(id),
        patient_id INTEGER NOT NULL REFERENCES patients(id),
        doctor_id INTEGER NOT NULL REFERENCES users(id),
        created_at TEXT NOT NULL,
        transcript_json TEXT, form_json TEXT, tags_json TEXT, red_flags_json TEXT,
        suggested_urgency TEXT NOT NULL,            -- a SUGGESTION; never an official decision
        doctor_urgency TEXT, doctor_confirmed INTEGER NOT NULL DEFAULT 0, doctor_notes TEXT,
        reviewed_by INTEGER REFERENCES users(id), reviewed_at TEXT
    );
    CREATE TABLE prescriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        intake_id INTEGER NOT NULL REFERENCES intakes(id),
        created_at TEXT NOT NULL, created_by INTEGER REFERENCES users(id),
        transcript TEXT, draft_json TEXT, final_json TEXT,
        status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','confirmed')),
        confirmed_at TEXT, confirmed_by INTEGER REFERENCES users(id), llm_error TEXT,
        checkin_token_hash TEXT UNIQUE, checkin_token_expires TEXT
    );
    CREATE TABLE checkins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        intake_id INTEGER NOT NULL REFERENCES intakes(id),
        prescription_id INTEGER REFERENCES prescriptions(id),
        created_at TEXT NOT NULL, week INTEGER,
        transcript_json TEXT, result_json TEXT, tags_json TEXT, red_flags_json TEXT,
        alerts_json TEXT, alert_level TEXT, progress_json TEXT,
        acknowledged INTEGER NOT NULL DEFAULT 0, acknowledged_by INTEGER REFERENCES users(id), acknowledged_at TEXT
    );
    CREATE TABLE consents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL REFERENCES patients(id), visit_id INTEGER REFERENCES visits(id),
        kind TEXT NOT NULL, granted INTEGER NOT NULL, source TEXT NOT NULL, at TEXT NOT NULL
    );
    CREATE TABLE audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL, user_id INTEGER, role TEXT NOT NULL,
        action TEXT NOT NULL, object_type TEXT, object_id INTEGER, detail_json TEXT, ip TEXT
    );
    CREATE TABLE model_calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL, provider TEXT NOT NULL, kind TEXT NOT NULL, ok INTEGER NOT NULL, ms INTEGER, detail TEXT
    );
    CREATE INDEX idx_visits_doctor ON visits(doctor_id, status);
    CREATE INDEX idx_intakes_doctor ON intakes(doctor_id);
    CREATE INDEX idx_checkins_intake ON checkins(intake_id, acknowledged);
    CREATE INDEX idx_audit_at ON audit_log(at);
    CREATE INDEX idx_model_calls_at ON model_calls(at, kind);
    CREATE INDEX idx_patients_phone ON patients(phone);
    """),
    ("0002_helpline", """
    ALTER TABLE users ADD COLUMN supervisor_id INTEGER REFERENCES users(id);
    ALTER TABLE patients ADD COLUMN pin_hash TEXT;
    CREATE TABLE pin_attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT NOT NULL, at TEXT NOT NULL);
    CREATE INDEX idx_pin_attempts ON pin_attempts(phone, at);
    CREATE TABLE escalations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL REFERENCES patients(id),
        kind TEXT NOT NULL CHECK (kind IN ('question','symptom_report','emergency','appointment_request')),
        priority TEXT NOT NULL CHECK (priority IN ('emergency','urgent','routine')),
        question_en TEXT, question_original TEXT, tags_json TEXT, flags_json TEXT,
        assigned_to INTEGER NOT NULL REFERENCES users(id), level INTEGER NOT NULL DEFAULT 0,
        forwarded_from INTEGER REFERENCES users(id),
        created_at TEXT NOT NULL, due_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','answered','closed')),
        answer_text TEXT, answered_by INTEGER REFERENCES users(id), answered_at TEXT, reusable INTEGER NOT NULL DEFAULT 0,
        delivered_at TEXT, handled_by INTEGER REFERENCES users(id), handled_at TEXT
    );
    CREATE INDEX idx_esc_assigned ON escalations(assigned_to, status);
    CREATE TABLE answer_bank (
        id INTEGER PRIMARY KEY AUTOINCREMENT, doctor_id INTEGER NOT NULL REFERENCES users(id),
        question_en TEXT NOT NULL, answer_text TEXT NOT NULL, created_at TEXT NOT NULL,
        source_escalation INTEGER REFERENCES escalations(id)
    );
    """),
    ("0003_appointments", """
    -- Times below are the hospital's LOCAL time as 'YYYY-MM-DDTHH:MM' (patients think in local time).
    CREATE TABLE schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doctor_id INTEGER NOT NULL REFERENCES users(id),
        weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),     -- 0 = Monday
        start_time TEXT NOT NULL, end_time TEXT NOT NULL
    );
    CREATE INDEX idx_schedules_doctor ON schedules(doctor_id);
    CREATE TABLE doctor_settings (doctor_id INTEGER PRIMARY KEY REFERENCES users(id), slot_minutes INTEGER NOT NULL DEFAULT 15);
    CREATE TABLE time_off (id INTEGER PRIMARY KEY AUTOINCREMENT, doctor_id INTEGER NOT NULL REFERENCES users(id), date TEXT NOT NULL, reason TEXT);
    CREATE TABLE appointments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL REFERENCES patients(id), doctor_id INTEGER NOT NULL REFERENCES users(id),
        starts_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'booked' CHECK (status IN ('booked','cancelled','completed')),
        booked_via TEXT NOT NULL CHECK (booked_via IN ('call','reception','doctor')), booked_by INTEGER REFERENCES users(id),
        created_at TEXT NOT NULL, cancelled_at TEXT
    );
    CREATE UNIQUE INDEX uq_appointment_slot ON appointments(doctor_id, starts_at) WHERE status='booked';   -- no double booking, ever
    CREATE INDEX idx_appointments_patient ON appointments(patient_id, starts_at);
    """),
    ("0004_question_sets", """
    CREATE TABLE question_sets (
        doctor_id INTEGER PRIMARY KEY REFERENCES users(id),
        questions_json TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by INTEGER REFERENCES users(id)
    );
    """),
]


def init_db():
    """Create the database and apply any migrations not yet applied. Safe to run on every start."""
    folder = os.path.dirname(config.DB_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE IF NOT EXISTS schema_version (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        done = {r["name"] for r in c.execute("SELECT name FROM schema_version")}
        for name, sql in MIGRATIONS:
            if name not in done:
                c.executescript(sql)
                c.execute("INSERT INTO schema_version (name, applied_at) VALUES (?,?)", (name, utcnow()))


def _j(v):
    return json.dumps(v, ensure_ascii=False)


def _load(v, default=None):
    return json.loads(v) if v else default


# ------------------------------------------------------------------ audit
def audit(user, action, object_type="", object_id=None, detail=None, ip=""):
    """Who did what to which record. `user` is the signed-in staff member, or None for the system / a patient call."""
    with conn() as c:
        c.execute("INSERT INTO audit_log (at, user_id, role, action, object_type, object_id, detail_json, ip) VALUES (?,?,?,?,?,?,?,?)",
                  (utcnow(), user["id"] if user else None, user["role"] if user else "system", action, object_type,
                   object_id, _j(detail or {}), ip))


def list_audit(action=None, user_id=None, limit=200):
    q, args = ("SELECT a.*, u.username FROM audit_log a LEFT JOIN users u ON u.id=a.user_id WHERE 1=1"), []
    if action:
        q += " AND a.action LIKE ?"; args.append(action + "%")
    if user_id:
        q += " AND a.user_id=?"; args.append(user_id)
    q += " ORDER BY a.id DESC LIMIT ?"; args.append(min(int(limit), 1000))
    with conn() as c:
        rows = [dict(r) for r in c.execute(q, args)]
    for r in rows:
        r["detail"] = _load(r.pop("detail_json"), {})
    return rows


# ------------------------------------------------------------------ users and sessions
USER_COLS = "id, username, full_name, role, specialty, supervisor_id, active, must_change_password, created_at, last_login_at"


def create_user(username, full_name, role, password_hash, specialty=None, must_change=False):
    with conn() as c:
        cur = c.execute("INSERT INTO users (username, full_name, role, specialty, password_hash, must_change_password, created_at) VALUES (?,?,?,?,?,?,?)",
                        (username.strip(), full_name.strip(), role, specialty, password_hash, int(must_change), utcnow()))
        return cur.lastrowid


def get_user(user_id, with_secret=False):
    with conn() as c:
        row = c.execute(f"SELECT {USER_COLS}{', password_hash, failed_logins, locked_until' if with_secret else ''} FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_username(username):
    with conn() as c:
        row = c.execute(f"SELECT {USER_COLS}, password_hash, failed_logins, locked_until FROM users WHERE username=?", (username.strip(),)).fetchone()
        return dict(row) if row else None


def list_users(role=None, active_only=False):
    q, args = f"SELECT {USER_COLS} FROM users WHERE 1=1", []
    if role:
        q += " AND role=?"; args.append(role)
    if active_only:
        q += " AND active=1"
    with conn() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY role, full_name", args)]


def update_user(user_id, **fields):
    allowed = {"full_name", "role", "specialty", "supervisor_id", "active", "password_hash", "must_change_password", "failed_logins", "locked_until"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    with conn() as c:
        c.execute(f"UPDATE users SET {', '.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), user_id))


def register_failed_login(user_id, max_attempts, lock_minutes):
    """Count a wrong password. Returns True if this attempt locked the account."""
    with conn() as c:
        n = c.execute("SELECT failed_logins FROM users WHERE id=?", (user_id,)).fetchone()["failed_logins"] + 1
        if n >= max_attempts:
            c.execute("UPDATE users SET failed_logins=0, locked_until=? WHERE id=?", (later(minutes=lock_minutes), user_id))
            return True
        c.execute("UPDATE users SET failed_logins=? WHERE id=?", (n, user_id))
        return False


def register_successful_login(user_id):
    with conn() as c:
        c.execute("UPDATE users SET failed_logins=0, locked_until=NULL, last_login_at=? WHERE id=?", (utcnow(), user_id))


def create_session(user_id, token_hash, csrf, expires_at, ip="", user_agent=""):
    with conn() as c:
        c.execute("INSERT INTO sessions (token_hash, user_id, csrf_token, created_at, expires_at, last_seen, ip, user_agent) VALUES (?,?,?,?,?,?,?,?)",
                  (token_hash, user_id, csrf, utcnow(), expires_at, utcnow(), ip, (user_agent or "")[:200]))


def get_session(token_hash):
    with conn() as c:
        row = c.execute("SELECT * FROM sessions WHERE token_hash=?", (token_hash,)).fetchone()
        if row is None:
            return None
        s = dict(row)
    s["user"] = get_user(s["user_id"])
    return s


def touch_session(token_hash):
    with conn() as c:
        c.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (utcnow(), token_hash))


def delete_session(token_hash):
    with conn() as c:
        c.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))


def delete_user_sessions(user_id):
    with conn() as c:
        c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))


def purge_sessions():
    with conn() as c:
        c.execute("DELETE FROM sessions WHERE expires_at<=?", (utcnow(),))


# ------------------------------------------------------------------ patients and visits
def create_patient(full_name, age, sex, phone, language, created_by, pin_hash=None):
    with conn() as c:
        cur = c.execute("INSERT INTO patients (mrn, full_name, age, sex, phone, language, created_at, created_by, pin_hash) VALUES (?,?,?,?,?,?,?,?,?)",
                        ("TMP-" + uuid.uuid4().hex, full_name.strip(), age, sex, (phone or "").strip(), language, utcnow(), created_by, pin_hash))
        pid = cur.lastrowid
        c.execute("UPDATE patients SET mrn=? WHERE id=?", (f"MRN-{datetime.now().year}-{pid:05d}", pid))
        return pid


def get_patient(pid):
    with conn() as c:
        row = c.execute("SELECT * FROM patients WHERE id=?", (pid,)).fetchone()
        return dict(row) if row else None


def search_patients(q, limit=8):
    like = f"%{q.strip()}%"
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM patients WHERE full_name LIKE ? OR phone LIKE ? OR mrn LIKE ? ORDER BY id DESC LIMIT ?",
            (like, like, like, limit))]


def create_visit(patient_id, doctor_id, token_hash, expires_at, created_by):
    with conn() as c:
        cur = c.execute("INSERT INTO visits (patient_id, doctor_id, token_hash, token_expires_at, created_at, created_by) VALUES (?,?,?,?,?,?)",
                        (patient_id, doctor_id, token_hash, expires_at, utcnow(), created_by))
        return cur.lastrowid


def get_visit(visit_id):
    with conn() as c:
        row = c.execute("SELECT * FROM visits WHERE id=?", (visit_id,)).fetchone()
        return dict(row) if row else None


def get_visit_by_token(token_hash):
    with conn() as c:
        row = c.execute("""SELECT v.*, p.full_name AS patient_name, p.language, p.mrn FROM visits v
                           JOIN patients p ON p.id=v.patient_id WHERE v.token_hash=?""", (token_hash,)).fetchone()
        return dict(row) if row else None


def rotate_visit_token(visit_id, token_hash, expires_at):
    with conn() as c:
        c.execute("UPDATE visits SET token_hash=?, token_expires_at=? WHERE id=? AND intake_completed_at IS NULL",
                  (token_hash, expires_at, visit_id))
        return c.total_changes > 0


def close_visit(visit_id):
    with conn() as c:
        c.execute("UPDATE visits SET status='closed' WHERE id=?", (visit_id,))


def list_visits_for_desk(limit=200):
    """Reception view: who is registered, with whom, and whether the intake call is done. No clinical content,
    except a single 'emergency' flag so staff can react at once."""
    with conn() as c:
        rows = c.execute("""
            SELECT v.id, v.status, v.created_at, v.token_expires_at, v.intake_completed_at,
                   p.mrn, p.full_name AS name, p.age, u.full_name AS doctor_name,
                   COALESCE(i.suggested_urgency = 'emergency', 0) AS emergency,
                   EXISTS (SELECT 1 FROM checkins k WHERE k.intake_id = i.id AND k.acknowledged = 0
                           AND k.alert_level = 'emergency') AS checkin_emergency
            FROM visits v JOIN patients p ON p.id = v.patient_id JOIN users u ON u.id = v.doctor_id
            LEFT JOIN intakes i ON i.visit_id = v.id
            WHERE v.status != 'closed' OR v.created_at >= ?
            ORDER BY v.id DESC LIMIT ?""", (later(days=-1), limit)).fetchall()
        return [dict(r) for r in rows]


# ------------------------------------------------------------------ intakes (the pre-consultation call)
def record_consent(patient_id, visit_id, kind, granted, source):
    with conn() as c:
        c.execute("INSERT INTO consents (patient_id, visit_id, kind, granted, source, at) VALUES (?,?,?,?,?,?)",
                  (patient_id, visit_id, kind, int(granted), source, utcnow()))


def save_intake(visit_id, patient_id, doctor_id, transcript, form, tags, red_flags, suggested_urgency):
    with conn() as c:
        cur = c.execute("""INSERT INTO intakes (visit_id, patient_id, doctor_id, created_at, transcript_json, form_json, tags_json,
                           red_flags_json, suggested_urgency) VALUES (?,?,?,?,?,?,?,?,?)""",
                        (visit_id, patient_id, doctor_id, utcnow(), _j(transcript), _j(form), _j(tags), _j(red_flags), suggested_urgency))
        c.execute("UPDATE visits SET status='intake_done', intake_completed_at=? WHERE id=?", (utcnow(), visit_id))
        return cur.lastrowid


def _intake_row(r):
    d = dict(r)
    for k in ("transcript_json", "form_json", "tags_json", "red_flags_json"):
        d[k.replace("_json", "")] = _load(d.pop(k), [] if k != "form_json" else {})
    return d


def get_intake(intake_id):
    with conn() as c:
        row = c.execute("""SELECT i.*, p.mrn, p.full_name AS name, p.age, p.sex, v.status AS visit_status
                           FROM intakes i JOIN patients p ON p.id=i.patient_id JOIN visits v ON v.id=i.visit_id
                           WHERE i.id=?""", (intake_id,)).fetchone()
        return _intake_row(row) if row else None


def list_queue_for_doctor(doctor_id):
    """Open visits for one doctor, plus closed ones that still have an unacknowledged alert."""
    with conn() as c:
        rows = c.execute("""
            SELECT v.id AS visit_id, v.status AS visit_status, v.created_at AS visit_created,
                   p.mrn, p.full_name AS name, p.age,
                   i.id AS intake_id, i.created_at, i.suggested_urgency, i.doctor_urgency, i.doctor_confirmed,
                   i.form_json, i.red_flags_json
            FROM visits v JOIN patients p ON p.id=v.patient_id LEFT JOIN intakes i ON i.visit_id=v.id
            WHERE v.doctor_id=? AND (v.status != 'closed' OR EXISTS (
                  SELECT 1 FROM checkins k WHERE k.intake_id=i.id AND k.acknowledged=0 AND k.alert_level IN ('emergency','doctor')))
            ORDER BY v.created_at""", (doctor_id,)).fetchall()
        out = [dict(r) for r in rows]
        ids = [r["intake_id"] for r in out if r["intake_id"]]
        alerts = {}
        if ids:
            marks = ",".join("?" * len(ids))
            for k in c.execute(f"SELECT intake_id, alert_level FROM checkins WHERE acknowledged=0 AND alert_level IN ('emergency','doctor') AND intake_id IN ({marks})", ids):
                if alerts.get(k["intake_id"]) != "emergency":
                    alerts[k["intake_id"]] = k["alert_level"]
        rx = {}
        if ids:
            for k in c.execute(f"SELECT intake_id, status FROM prescriptions WHERE intake_id IN ({marks}) ORDER BY id", ids):
                rx[k["intake_id"]] = k["status"]
    for r in out:
        form = _load(r.pop("form_json"), {}) or {}
        flags = _load(r.pop("red_flags_json"), []) or []
        r["complaint"] = form.get("complaint_summary", "")
        r["red_flags"] = [f["label"] for f in flags]
        r["open_alert"] = alerts.get(r["intake_id"])
        r["prescription_status"] = rx.get(r["intake_id"])
    return out


def review_intake(intake_id, user, doctor_urgency, notes="", edits=None, ip=""):
    """The doctor confirms or changes the suggested urgency and may correct the form. Everything is audited."""
    levels = ["emergency", "high", "medium", "routine"]
    if doctor_urgency not in levels:
        raise ValueError("unknown urgency level")
    edits = edits or {}
    with conn() as c:
        row = c.execute("SELECT * FROM intakes WHERE id=?", (intake_id,)).fetchone()
        if row is None:
            raise LookupError("intake not found")
        form = _load(row["form_json"], {}) or {}
        changes = []
        for field in ("name", "age", "complaint_summary", "medicines"):
            new = edits.get(field)
            if new not in (None, "") and new != form.get(field):
                changes.append({"field": field, "from": form.get(field), "to": new})
                form[field] = new
        if edits.get("name") not in (None, "") or isinstance(edits.get("age"), int):
            c.execute("UPDATE patients SET full_name=COALESCE(NULLIF(?, ''), full_name), age=COALESCE(?, age) WHERE id=?",
                      (edits.get("name") or "", edits.get("age") if isinstance(edits.get("age"), int) else None, row["patient_id"]))
        c.execute("""UPDATE intakes SET doctor_urgency=?, doctor_confirmed=1, doctor_notes=?, form_json=?, reviewed_by=?, reviewed_at=?
                     WHERE id=?""", (doctor_urgency, notes, _j(form), user["id"], utcnow(), intake_id))
        suggested = row["suggested_urgency"]
    audit(user, "urgency_reviewed", "intake", intake_id,
          {"suggested": suggested, "doctor": doctor_urgency, "changed": suggested != doctor_urgency, "form_edits": changes}, ip)


def agreement_stats(doctor_id=None):
    q, args = "SELECT suggested_urgency s, doctor_urgency d FROM intakes WHERE doctor_confirmed=1", []
    if doctor_id:
        q += " AND doctor_id=?"; args.append(doctor_id)
    with conn() as c:
        rows = c.execute(q, args).fetchall()
    agreed = sum(1 for r in rows if r["s"] == r["d"])
    return {"reviewed": len(rows), "agreed": agreed, "changed": len(rows) - agreed}


# ------------------------------------------------------------------ prescriptions
def _rx_row(row):
    if row is None:
        return None
    r = dict(row)
    r["draft"] = _load(r.pop("draft_json"))
    r["final"] = _load(r.pop("final_json"))
    r.pop("checkin_token_hash", None)
    return r


def save_prescription_draft(intake_id, transcript, draft, llm_error="", created_by=None):
    with conn() as c:
        cur = c.execute("INSERT INTO prescriptions (intake_id, created_at, created_by, transcript, draft_json, llm_error) VALUES (?,?,?,?,?,?)",
                        (intake_id, utcnow(), created_by, transcript, _j(draft), llm_error))
        return cur.lastrowid


def get_prescription(prescription_id):
    with conn() as c:
        return _rx_row(c.execute("SELECT * FROM prescriptions WHERE id=?", (prescription_id,)).fetchone())


def latest_prescription(intake_id):
    with conn() as c:
        return _rx_row(c.execute("SELECT * FROM prescriptions WHERE intake_id=? ORDER BY id DESC LIMIT 1", (intake_id,)).fetchone())


def confirm_prescription(prescription_id, final, user, ip=""):
    """The doctor's digital confirmation. Only a confirmed prescription can drive reminder calls."""
    with conn() as c:
        row = c.execute("SELECT * FROM prescriptions WHERE id=?", (prescription_id,)).fetchone()
        if row is None:
            raise LookupError("prescription not found")
        draft = _load(row["draft_json"], {}) or {}
        c.execute("UPDATE prescriptions SET final_json=?, status='confirmed', confirmed_at=?, confirmed_by=? WHERE id=?",
                  (_j(final), utcnow(), user["id"], prescription_id))
        intake_id = row["intake_id"]
    audit(user, "prescription_confirmed", "prescription", prescription_id,
          {"edited_by_doctor": draft.get("medicines") != final.get("medicines") or draft.get("diet_advice") != final.get("diet_advice")}, ip)
    return intake_id


def set_checkin_token(prescription_id, token_hash, expires_at):
    with conn() as c:
        c.execute("UPDATE prescriptions SET checkin_token_hash=?, checkin_token_expires=? WHERE id=? AND status='confirmed'",
                  (token_hash, expires_at, prescription_id))
        return c.total_changes > 0


def get_prescription_by_checkin_token(token_hash):
    with conn() as c:
        row = c.execute("""SELECT rx.*, i.tags_json AS intake_tags, i.created_at AS intake_created, i.doctor_id, i.patient_id
                           FROM prescriptions rx JOIN intakes i ON i.id=rx.intake_id
                           WHERE rx.checkin_token_hash=? AND rx.status='confirmed'""", (token_hash,)).fetchone()
        if row is None:
            return None
        r = dict(row)
    expires, intake_created, tags = r.get("checkin_token_expires"), r.pop("intake_created"), _load(r.pop("intake_tags"), [])
    out = _rx_row(r)
    out.update({"baseline_tags": tags, "intake_created": intake_created, "token_expires": expires})
    return out


# ------------------------------------------------------------------ check-in calls
def save_checkin(intake_id, prescription_id, week, qa, result, tags, flags, alerts, alert_level, progress):
    with conn() as c:
        cur = c.execute("""INSERT INTO checkins (intake_id, prescription_id, created_at, week, transcript_json, result_json, tags_json,
                           red_flags_json, alerts_json, alert_level, progress_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (intake_id, prescription_id, utcnow(), week, _j(qa), _j(result), _j(tags), _j(flags), _j(alerts), alert_level, _j(progress)))
        return cur.lastrowid


def list_checkins(intake_id):
    with conn() as c:
        rows = c.execute("SELECT * FROM checkins WHERE intake_id=? ORDER BY id DESC", (intake_id,)).fetchall()
    out = []
    for row in rows:
        r = dict(row)
        for k in ("transcript_json", "result_json", "tags_json", "red_flags_json", "alerts_json", "progress_json"):
            r[k.replace("_json", "")] = _load(r.pop(k))
        out.append(r)
    return out


def get_checkin(checkin_id):
    with conn() as c:
        row = c.execute("""SELECT k.id, k.intake_id, i.doctor_id FROM checkins k JOIN intakes i ON i.id=k.intake_id WHERE k.id=?""",
                        (checkin_id,)).fetchone()
        return dict(row) if row else None


def acknowledge_checkin(checkin_id, user, ip=""):
    with conn() as c:
        row = c.execute("SELECT intake_id FROM checkins WHERE id=?", (checkin_id,)).fetchone()
        if row is None:
            raise LookupError("check-in not found")
        c.execute("UPDATE checkins SET acknowledged=1, acknowledged_by=?, acknowledged_at=? WHERE id=?", (user["id"], utcnow(), checkin_id))
    audit(user, "checkin_acknowledged", "checkin", checkin_id, {}, ip)


# ------------------------------------------------------------------ model monitoring
def record_model_call(provider, kind, ok, ms, detail=""):
    with conn() as c:
        c.execute("INSERT INTO model_calls (at, provider, kind, ok, ms, detail) VALUES (?,?,?,?,?,?)",
                  (utcnow(), provider, kind, int(ok), ms, detail))


def model_stats(hours=24):
    with conn() as c:
        rows = c.execute("SELECT provider, kind, ok, ms, detail, at FROM model_calls WHERE at>=? ORDER BY id DESC LIMIT 20000",
                         (later(hours=-hours),)).fetchall()
    stats = {}
    for r in rows:
        s = stats.setdefault(r["kind"], {"provider": r["provider"], "kind": r["kind"], "calls": 0, "ok": 0, "ms": [], "last_error": None})
        s["calls"] += 1
        s["ok"] += r["ok"]
        if r["ms"] is not None:
            s["ms"].append(r["ms"])
        if not r["ok"] and s["last_error"] is None:
            s["last_error"] = {"at": r["at"], "detail": r["detail"]}
    out = []
    for s in stats.values():
        ms = sorted(s.pop("ms"))
        s["avg_ms"] = int(sum(ms) / len(ms)) if ms else None
        s["p95_ms"] = ms[min(len(ms) - 1, int(len(ms) * 0.95))] if ms else None
        s["success_rate"] = round(s["ok"] / s["calls"], 3) if s["calls"] else None
        out.append(s)
    return sorted(out, key=lambda s: s["kind"])


def counts():
    with conn() as c:
        one = lambda q, *a: c.execute(q, a).fetchone()[0]
        today = later(hours=-24)
        return {"users": one("SELECT COUNT(*) FROM users WHERE active=1"), "patients": one("SELECT COUNT(*) FROM patients"),
                "visits_24h": one("SELECT COUNT(*) FROM visits WHERE created_at>=?", today),
                "intakes_24h": one("SELECT COUNT(*) FROM intakes WHERE created_at>=?", today),
                "checkins_24h": one("SELECT COUNT(*) FROM checkins WHERE created_at>=?", today)}


# ------------------------------------------------------------------ help line: identification
def set_pin(patient_id, pin_hash):
    with conn() as c:
        c.execute("UPDATE patients SET pin_hash=? WHERE id=?", (pin_hash, patient_id))


def normalize_phone(raw):
    """Digits only, last 10: '+91 98765-43210' and '9876543210' are the same number."""
    digits = re.sub(r"\D", "", raw or "")
    return digits[-10:] if len(digits) > 10 else digits


def patients_by_phone(phone):
    """Everyone registered with this phone number (a family may share one). Includes the PIN hash: internal use only."""
    p = normalize_phone(phone)
    if len(p) < 8:
        return []
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM patients WHERE phone=?", (p,))]


def patient_context(patient_id):
    """A few lines that let any doctor handling a message understand who it is about."""
    with conn() as c:
        row = c.execute("SELECT form_json FROM intakes WHERE patient_id=? ORDER BY id DESC LIMIT 1", (patient_id,)).fetchone()
    complaint = (_load(row["form_json"], {}) or {}).get("complaint_summary", "") if row else ""
    rx = latest_confirmed_prescription_for_patient(patient_id)
    final = (rx or {}).get("final") or {}
    return {"complaint": complaint, "follow_up": final.get("follow_up", ""),
            "medicines": [", ".join(x for x in (m["name"], m.get("dose"), m.get("timing")) if x) for m in final.get("medicines", [])]}


def record_pin_failure(phone):
    with conn() as c:
        c.execute("INSERT INTO pin_attempts (phone, at) VALUES (?,?)", (phone, utcnow()))


def pin_failures(phone, minutes=60):
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM pin_attempts WHERE phone=? AND at>=?", (phone, later(minutes=-minutes))).fetchone()[0]


def clear_pin_failures(phone):
    with conn() as c:
        c.execute("DELETE FROM pin_attempts WHERE phone=?", (phone,))


def latest_doctor_for_patient(patient_id):
    with conn() as c:
        row = c.execute("SELECT doctor_id FROM visits WHERE patient_id=? ORDER BY id DESC LIMIT 1", (patient_id,)).fetchone()
        if row:
            return row["doctor_id"]
        row = c.execute("SELECT id FROM users WHERE role='doctor' AND active=1 ORDER BY id LIMIT 1").fetchone()
        return row["id"] if row else None


def latest_confirmed_prescription_for_patient(patient_id):
    """The facts the help line is allowed to read out: the newest doctor-confirmed prescription."""
    with conn() as c:
        row = c.execute("""SELECT rx.* FROM prescriptions rx JOIN intakes i ON i.id=rx.intake_id
                           WHERE i.patient_id=? AND rx.status='confirmed' ORDER BY rx.id DESC LIMIT 1""", (patient_id,)).fetchone()
        return _rx_row(row)


# ------------------------------------------------------------------ help line: messages to doctors (escalations)
def _due(priority):
    from app import config as cfg
    return later(minutes=cfg.ESCALATION_MINUTES[priority])


def create_escalation(patient_id, kind, priority, question_en, question_original, tags, flags, assigned_to):
    with conn() as c:
        cur = c.execute("""INSERT INTO escalations (patient_id, kind, priority, question_en, question_original, tags_json, flags_json,
                           assigned_to, created_at, due_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (patient_id, kind, priority, question_en, question_original, _j(tags or []), _j(flags or []), assigned_to, utcnow(), _due(priority)))
        return cur.lastrowid


def _esc_row(r):
    d = dict(r)
    d["tags"] = _load(d.pop("tags_json"), [])
    d["flags"] = _load(d.pop("flags_json"), [])
    d["overdue"] = d["status"] == "open" and d["due_at"] < utcnow()
    return d


def get_escalation(escalation_id):
    with conn() as c:
        row = c.execute("""SELECT e.*, p.full_name AS name, p.mrn, p.age, p.phone FROM escalations e
                           JOIN patients p ON p.id=e.patient_id WHERE e.id=?""", (escalation_id,)).fetchone()
        return _esc_row(row) if row else None


def escalate_overdue():
    """Questions nobody answered in time move up to the doctor's supervisor (at most MAX_ESCALATION_LEVEL times).
    Called whenever a doctor's inbox is opened, so no background scheduler is needed."""
    from app import config as cfg
    moved = []
    with conn() as c:
        rows = c.execute("""SELECT e.id, e.assigned_to, e.priority, e.level, u.supervisor_id FROM escalations e
                            JOIN users u ON u.id=e.assigned_to
                            WHERE e.status='open' AND e.due_at<? AND e.level<? AND u.supervisor_id IS NOT NULL""",
                         (utcnow(), cfg.MAX_ESCALATION_LEVEL)).fetchall()
        for r in rows:
            sup = c.execute("SELECT id FROM users WHERE id=? AND active=1", (r["supervisor_id"],)).fetchone()
            if sup is None:
                continue
            c.execute("UPDATE escalations SET assigned_to=?, forwarded_from=?, level=level+1, due_at=? WHERE id=?",
                      (sup["id"], r["assigned_to"], _due(r["priority"]), r["id"]))
            moved.append((r["id"], r["assigned_to"], sup["id"]))
    for eid, old, new in moved:
        audit(None, "escalation_forwarded", "escalation", eid, {"from": old, "to": new, "reason": "no reply in time"})
    return len(moved)


PRIORITY_ORDER = "CASE e.priority WHEN 'emergency' THEN 0 WHEN 'urgent' THEN 1 ELSE 2 END"


def list_escalations_for_doctor(doctor_id):
    """Open messages for this doctor (most urgent first) plus the ten most recently answered."""
    escalate_overdue()
    base = """SELECT e.*, p.full_name AS name, p.mrn, p.age, f.full_name AS forwarded_from_name
              FROM escalations e JOIN patients p ON p.id=e.patient_id LEFT JOIN users f ON f.id=e.forwarded_from
              WHERE e.assigned_to=? AND e.status=?"""
    with conn() as c:
        open_rows = c.execute(base + f" ORDER BY {PRIORITY_ORDER}, e.created_at", (doctor_id, "open")).fetchall()
        done_rows = c.execute(base + " ORDER BY e.answered_at DESC LIMIT 10", (doctor_id, "answered")).fetchall()
    return [_esc_row(r) for r in open_rows], [_esc_row(r) for r in done_rows]


def count_open_messages(doctor_id):
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM escalations WHERE assigned_to=? AND status='open'", (doctor_id,)).fetchone()[0]


def answer_escalation(escalation_id, user, text, reusable=False, ip=""):
    with conn() as c:
        row = c.execute("SELECT * FROM escalations WHERE id=? AND status='open'", (escalation_id,)).fetchone()
        if row is None:
            raise LookupError("message not found or already answered")
        c.execute("UPDATE escalations SET status='answered', answer_text=?, answered_by=?, answered_at=?, reusable=? WHERE id=?",
                  (text, user["id"], utcnow(), int(bool(reusable)), escalation_id))
        if reusable and row["question_en"]:
            c.execute("INSERT INTO answer_bank (doctor_id, question_en, answer_text, created_at, source_escalation) VALUES (?,?,?,?,?)",
                      (user["id"], row["question_en"], text, utcnow(), escalation_id))
    audit(user, "message_answered", "escalation", escalation_id, {"reusable": bool(reusable)}, ip)


def close_escalation(escalation_id, user, ip=""):
    with conn() as c:
        c.execute("UPDATE escalations SET status='closed', handled_by=?, handled_at=? WHERE id=? AND status='open'", (user["id"], utcnow(), escalation_id))
    audit(user, "message_closed", "escalation", escalation_id, {}, ip)


def undelivered_answers(patient_id):
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT id, answer_text FROM escalations WHERE patient_id=? AND status='answered' AND delivered_at IS NULL ORDER BY answered_at", (patient_id,))]


def mark_delivered(ids):
    with conn() as c:
        for i in ids:
            c.execute("UPDATE escalations SET delivered_at=? WHERE id=?", (utcnow(), i))


def open_emergencies():
    """For reception: emergency calls to the help line nobody has acted on yet."""
    with conn() as c:
        return [dict(r) for r in c.execute("""SELECT e.id, e.created_at, p.full_name AS name, p.mrn, p.phone, u.full_name AS doctor_name
                                              FROM escalations e JOIN patients p ON p.id=e.patient_id JOIN users u ON u.id=e.assigned_to
                                              WHERE e.kind='emergency' AND e.handled_at IS NULL ORDER BY e.created_at""")]


def handle_emergency(escalation_id, user, ip=""):
    with conn() as c:
        c.execute("UPDATE escalations SET handled_by=?, handled_at=? WHERE id=? AND kind='emergency'", (user["id"], utcnow(), escalation_id))
    audit(user, "emergency_call_handled", "escalation", escalation_id, {}, ip)


def bank_for_doctor(doctor_id, limit=30):
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT id, question_en, answer_text FROM answer_bank WHERE doctor_id=? ORDER BY id DESC LIMIT ?", (doctor_id, limit))]


def get_bank_answer(bank_id, doctor_id):
    with conn() as c:
        row = c.execute("SELECT id, question_en, answer_text FROM answer_bank WHERE id=? AND doctor_id=?", (bank_id, doctor_id)).fetchone()
        return dict(row) if row else None


# ------------------------------------------------------------------ doctor schedules
def get_schedule(doctor_id):
    with conn() as c:
        wins = [dict(r) for r in c.execute("SELECT weekday, start_time AS start, end_time AS end FROM schedules WHERE doctor_id=? ORDER BY weekday, start_time", (doctor_id,))]
        row = c.execute("SELECT slot_minutes FROM doctor_settings WHERE doctor_id=?", (doctor_id,)).fetchone()
    return {"windows": wins, "slot_minutes": row["slot_minutes"] if row else 15}


def set_schedule(doctor_id, windows, slot_minutes):
    with conn() as c:
        c.execute("DELETE FROM schedules WHERE doctor_id=?", (doctor_id,))
        for w in windows:
            c.execute("INSERT INTO schedules (doctor_id, weekday, start_time, end_time) VALUES (?,?,?,?)", (doctor_id, w["weekday"], w["start"], w["end"]))
        c.execute("INSERT INTO doctor_settings (doctor_id, slot_minutes) VALUES (?,?) ON CONFLICT(doctor_id) DO UPDATE SET slot_minutes=excluded.slot_minutes",
                  (doctor_id, slot_minutes))


def list_time_off(doctor_id):
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT id, date, reason FROM time_off WHERE doctor_id=? ORDER BY date", (doctor_id,))]


def add_time_off(doctor_id, date_iso, reason):
    with conn() as c:
        return c.execute("INSERT INTO time_off (doctor_id, date, reason) VALUES (?,?,?)", (doctor_id, date_iso, reason)).lastrowid


def delete_time_off(time_off_id, doctor_id):
    with conn() as c:
        c.execute("DELETE FROM time_off WHERE id=? AND doctor_id=?", (time_off_id, doctor_id))
        return c.total_changes > 0


# ------------------------------------------------------------------ appointments
def booked_starts(doctor_id, start_iso, end_iso):
    with conn() as c:
        return {r[0] for r in c.execute("SELECT starts_at FROM appointments WHERE doctor_id=? AND status='booked' AND starts_at>=? AND starts_at<?",
                                        (doctor_id, start_iso, end_iso))}


def book_appointment(patient_id, doctor_id, starts_at, via, booked_by=None):
    """The database itself refuses a second booking of the same slot (unique index), so two callers can never both get it."""
    try:
        with conn() as c:
            return c.execute("INSERT INTO appointments (patient_id, doctor_id, starts_at, booked_via, booked_by, created_at) VALUES (?,?,?,?,?,?)",
                             (patient_id, doctor_id, starts_at, via, booked_by, utcnow())).lastrowid
    except sqlite3.IntegrityError:
        raise ValueError("That time has just been taken.")


APPT_SELECT = """SELECT a.id, a.patient_id, a.doctor_id, a.starts_at, a.status, a.booked_via, a.created_at,
                        p.full_name AS patient_name, p.mrn, d.full_name AS doctor_name
                 FROM appointments a JOIN patients p ON p.id=a.patient_id JOIN users d ON d.id=a.doctor_id"""


def get_appointment(appointment_id):
    with conn() as c:
        row = c.execute(APPT_SELECT + " WHERE a.id=?", (appointment_id,)).fetchone()
        return dict(row) if row else None


def list_appointments(doctor_id=None, from_iso="", to_iso="9999", status="booked"):
    q, args = APPT_SELECT + " WHERE a.status=? AND a.starts_at>=? AND a.starts_at<?", [status, from_iso, to_iso]
    if doctor_id:
        q += " AND a.doctor_id=?"; args.append(doctor_id)
    with conn() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY a.starts_at LIMIT 500", args)]


def patient_appointments(patient_id, from_iso):
    with conn() as c:
        return [dict(r) for r in c.execute(APPT_SELECT + " WHERE a.patient_id=? AND a.status='booked' AND a.starts_at>=? ORDER BY a.starts_at LIMIT 5",
                                           (patient_id, from_iso))]


def cancel_appointment(appointment_id, user, ip=""):
    with conn() as c:
        c.execute("UPDATE appointments SET status='cancelled', cancelled_at=? WHERE id=? AND status='booked'", (utcnow(), appointment_id))
        changed = c.total_changes > 0
    if changed:
        audit(user, "appointment_cancelled", "appointment", appointment_id, {}, ip)
    return changed


# ------------------------------------------------------------------ doctor-defined intake questions
def get_question_set(doctor_id):
    with conn() as c:
        row = c.execute("SELECT questions_json, updated_at FROM question_sets WHERE doctor_id=?", (doctor_id,)).fetchone()
    return {"questions": _load(row["questions_json"], []), "updated_at": row["updated_at"]} if row else None


def save_question_set(doctor_id, questions, user_id):
    with conn() as c:
        c.execute("""INSERT INTO question_sets (doctor_id, questions_json, updated_at, updated_by) VALUES (?,?,?,?)
                     ON CONFLICT(doctor_id) DO UPDATE SET questions_json=excluded.questions_json, updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
                  (doctor_id, _j(questions), utcnow(), user_id))


def delete_question_set(doctor_id):
    with conn() as c:
        c.execute("DELETE FROM question_sets WHERE doctor_id=?", (doctor_id,))


def list_answer_bank(doctor_id):
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT id, question_en, answer_text, created_at FROM answer_bank WHERE doctor_id=? ORDER BY id DESC", (doctor_id,))]


def delete_bank_answer(bank_id, doctor_id):
    with conn() as c:
        c.execute("DELETE FROM answer_bank WHERE id=? AND doctor_id=?", (bank_id, doctor_id))
        return c.total_changes > 0


# ------------------------------------------------------------------ aggregate numbers for the administrator
def _median(values):
    v = sorted(values)
    if not v:
        return None
    mid = len(v) // 2
    return v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2


def _minutes_between(a, b):
    return (parse(b) - parse(a)).total_seconds() / 60 if a and b else None


def analytics(days=30):
    """What the system did over the last `days`. Counts and medians only: no names, no clinical content."""
    since = later(days=-days)
    out = {"days": days, "since": since}
    with conn() as c:
        one = lambda sql, *a: c.execute(sql, a).fetchone()[0]
        visits = one("SELECT COUNT(*) FROM visits WHERE created_at>=?", since)
        intakes = [dict(r) for r in c.execute("SELECT created_at, reviewed_at, suggested_urgency, doctor_urgency, doctor_confirmed, form_json FROM intakes WHERE created_at>=?", (since,))]
        forms = [_load(i["form_json"], {}) or {} for i in intakes]
        secs = [f["call_seconds"] for f in forms if isinstance(f.get("call_seconds"), (int, float))]
        reviewed = [i for i in intakes if i["doctor_confirmed"]]
        out["intake"] = {
            "visits_registered": visits, "calls_completed": len(intakes),
            "completion_rate": round(len(intakes) / visits, 3) if visits else None,
            "avg_call_minutes": round(sum(secs) / len(secs) / 60, 1) if secs else None,
            "by_urgency": {lvl: sum(1 for i in intakes if i["suggested_urgency"] == lvl) for lvl in ("emergency", "high", "medium", "routine")},
            "needed_manual_reading": sum(1 for f in forms if f.get("needs_manual_review")),
            "with_patient_corrections": sum(1 for f in forms if f.get("corrections")),
        }
        mins = [m for m in (_minutes_between(i["created_at"], i["reviewed_at"]) for i in reviewed) if m is not None]
        out["decisions"] = {
            "reviewed": len(reviewed), "waiting_for_review": len(intakes) - len(reviewed),
            "agreed_with_suggestion": sum(1 for i in reviewed if i["suggested_urgency"] == i["doctor_urgency"]),
            "changed_by_doctor": sum(1 for i in reviewed if i["suggested_urgency"] != i["doctor_urgency"]),
            "median_minutes_to_review": round(_median(mins), 1) if mins else None,
        }
        rx = c.execute("SELECT status, COUNT(*) FROM prescriptions WHERE created_at>=? GROUP BY status", (since,)).fetchall()
        rx = dict((r[0], r[1]) for r in rx)
        cks = [dict(r) for r in c.execute("SELECT created_at, result_json, alert_level, acknowledged_at FROM checkins WHERE created_at>=?", (since,))]
        results = [_load(k["result_json"], {}) or {} for k in cks]
        ack = [m for m in (_minutes_between(k["created_at"], k["acknowledged_at"]) for k in cks if k["acknowledged_at"]) if m is not None]
        out["followup"] = {
            "prescriptions_drafted": sum(rx.values()), "prescriptions_confirmed": rx.get("confirmed", 0),
            "checkin_calls": len(cks),
            "medicine_taken": {k: sum(1 for r in results if r.get("medicine_taken") == k) for k in ("yes", "partial", "no", "unclear")},
            "pain_trend": {k: sum(1 for r in results if r.get("pain_trend") == k) for k in ("better", "same", "worse", "unclear")},
            "alerts": {lvl: sum(1 for k in cks if k["alert_level"] == lvl) for lvl in ("emergency", "doctor", "family")},
            "median_minutes_to_acknowledge": round(_median(ack), 1) if ack else None,
        }
        def audits(action):
            return one("SELECT COUNT(*) FROM audit_log WHERE action=? AND at>=?", action, since)
        esc = [dict(r) for r in c.execute("SELECT kind, priority, created_at, answered_at, level FROM escalations WHERE created_at>=?", (since,))]
        replies = [m for m in (_minutes_between(e["created_at"], e["answered_at"]) for e in esc if e["answered_at"]) if m is not None]
        calls_n, answered = audits("helpline_call_started"), audits("helpline_answered")
        out["helpline"] = {
            "calls": calls_n, "failed_verifications": audits("helpline_verification_failed"),
            "answered_from_record": answered, "passed_to_a_doctor": len(esc),
            "by_kind": {k: sum(1 for e in esc if e["kind"] == k) for k in ("question", "symptom_report", "emergency", "appointment_request")},
            "forwarded_to_supervisor": audits("escalation_forwarded"),
            "median_minutes_to_doctor_reply": round(_median(replies), 1) if replies else None,
            "standard_answers_in_use": one("SELECT COUNT(*) FROM answer_bank"),
        }
        appts = c.execute("SELECT booked_via, status, COUNT(*) FROM appointments WHERE created_at>=? GROUP BY booked_via, status", (since,)).fetchall()
        out["appointments"] = {
            "booked_by_phone": sum(r[2] for r in appts if r[0] == "call" and r[1] != "cancelled"),
            "booked_by_reception": sum(r[2] for r in appts if r[0] == "reception" and r[1] != "cancelled"),
            "cancelled": sum(r[2] for r in appts if r[1] == "cancelled"),
        }
    return out


# ------------------------------------------------------------------ privacy: erase one patient's data
ERASED_NAME = "Erased patient"


def search_patients_admin(q, limit=10):
    """Identification only (no clinical content), so an administrator can find the right person to erase."""
    like = f"%{q.strip()}%"
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, mrn, full_name, age, substr(phone, -4) AS phone_last4, created_at FROM patients WHERE full_name LIKE ? OR phone LIKE ? OR mrn LIKE ? ORDER BY id DESC LIMIT ?",
            (like, like, like, limit))]


def erase_patient(patient_id, user, ip=""):
    """Remove everything that identifies the patient or describes their care, keeping only anonymous counts.
    The record number and the consent log stay, so the hospital can still show what was done and when."""
    with conn() as c:
        p = c.execute("SELECT mrn, full_name FROM patients WHERE id=?", (patient_id,)).fetchone()
        if p is None:
            raise LookupError("patient not found")
        if p["full_name"] == ERASED_NAME:
            raise ValueError("This patient has already been erased.")
        intake_ids = [r[0] for r in c.execute("SELECT id FROM intakes WHERE patient_id=?", (patient_id,))]
        marks = ",".join("?" * len(intake_ids)) or "NULL"
        esc_ids = [r[0] for r in c.execute("SELECT id FROM escalations WHERE patient_id=?", (patient_id,))]
        emarks = ",".join("?" * len(esc_ids)) or "NULL"
        c.execute("UPDATE patients SET full_name=?, age=NULL, sex=NULL, phone=NULL, pin_hash=NULL WHERE id=?", (ERASED_NAME, patient_id))
        c.execute("UPDATE intakes SET transcript_json='[]', form_json='{\"erased\": true}', tags_json='[]', red_flags_json='[]', doctor_notes=NULL WHERE patient_id=?", (patient_id,))
        c.execute(f"UPDATE prescriptions SET transcript=NULL, draft_json='{{}}', final_json='{{}}', checkin_token_hash=NULL, checkin_token_expires=NULL WHERE intake_id IN ({marks})", intake_ids)
        c.execute(f"UPDATE checkins SET transcript_json='[]', result_json='{{}}', tags_json='[]', red_flags_json='[]', alerts_json='[]', progress_json='{{}}' WHERE intake_id IN ({marks})", intake_ids)
        c.execute("UPDATE escalations SET question_original=NULL, question_en='(erased)', answer_text=NULL, tags_json='[]', flags_json='[]' WHERE patient_id=?", (patient_id,))
        c.execute(f"DELETE FROM answer_bank WHERE source_escalation IN ({emarks})", esc_ids)
        c.execute("UPDATE appointments SET status='cancelled', cancelled_at=? WHERE patient_id=? AND status='booked'", (utcnow(), patient_id))
        for v in c.execute("SELECT id FROM visits WHERE patient_id=?", (patient_id,)).fetchall():
            c.execute("UPDATE visits SET token_hash=?, token_expires_at=? WHERE id=?", ("erased-" + uuid.uuid4().hex, later(days=-1), v["id"]))
        mrn = p["mrn"]
    audit(user, "patient_erased", "patient", patient_id, {"mrn": mrn}, ip)
    return mrn
