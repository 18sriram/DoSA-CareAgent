"""Shared test setup: a throw-away database, fake speech and a fake language model. Costs no API credits."""
import os
import shutil
import tempfile

_TMP = tempfile.mkdtemp(prefix="careagent-test-")
os.environ["HOSPITAL_DB"] = os.path.join(_TMP, "test.db")
os.environ["AUDIO_DIR"] = os.path.join(_TMP, "audio")
os.environ["GNANI_API_KEY"] = "test"
os.environ["GROQ_API_KEY"] = "test"
os.environ["PUBLIC_URL"] = "http://testserver"

from fastapi.testclient import TestClient  # noqa: E402

from app import config, db, intake_logic, checkin_logic, prescription_logic, security  # noqa: E402
from app.main import app  # noqa: E402
from app.routers import calls  # noqa: E402

PW = "Test-Passw0rd!"


def reset():
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(config.DB_PATH + suffix):
            os.remove(config.DB_PATH + suffix)
    shutil.rmtree(config.AUDIO_DIR, ignore_errors=True)
    db.init_db()
    security.login_limiter.reset()
    security.call_limiter.reset()
    calls.SESSIONS.clear()
    from app import scheduling
    scheduling.now_fn = None


def make_users():
    h = security.hash_password(PW)
    return {"admin": db.create_user("admin1", "Admin One", "admin", h),
            "rao": db.create_user("dr.rao", "Dr. Rao", "doctor", h, "orthopaedics"),
            "sharma": db.create_user("dr.sharma", "Dr. Sharma", "doctor", h, "orthopaedics"),
            "desk": db.create_user("desk1", "Desk One", "front_desk", h)}


class Client:
    """A browser: keeps its cookies and sends the CSRF token the way our pages do."""
    def __init__(self):
        self.c = TestClient(app)
        self.h = {}

    def login(self, username, password=PW):
        r = self.c.post("/api/auth/login", json={"username": username, "password": password})
        self.h = {"X-CSRF-Token": r.json()["csrf_token"]} if r.status_code == 200 else {}
        return r

    def get(self, url, **k):
        return self.c.get(url, **k)

    def post(self, url, json=None, **k):
        return self.c.post(url, json=json, headers=self.h, **k)

    def patch(self, url, json=None, **k):
        return self.c.patch(url, json=json, headers=self.h, **k)

    def put(self, url, json=None, **k):
        return self.c.put(url, json=json, headers=self.h, **k)

    def delete(self, url, **k):
        return self.c.delete(url, headers=self.h, **k)


def signed_in(username):
    c = Client()
    r = c.login(username)
    assert r.status_code == 200, r.text
    return c


# ---- fake speech + language model for patient calls ---------------------------------------------
SPEECH = []


def install_fakes(intake=None, checkin=None, rx=None):
    SPEECH.clear()
    calls.transcribe = lambda path, language_code=None: SPEECH.pop(0)
    calls.speak = lambda text, path, language=None: open(path, "wb").write(b"RIFF" + b"x" * 3000)
    intake_logic.ask_json = intake or (lambda s, u: {"name": "Ramu", "age": 58, "complaint_summary": "Right knee pain for 3 months.",
                                                     "medicines": "", "tags": ["location_knee", "duration_chronic", "stairs_difficulty"], "corrections": []})
    checkin_logic.ask_json = checkin or (lambda s, u: {"medicine_taken": "yes", "medicine_note": "", "diet_summary": "Ragi, dal",
                                                       "followed_diet": "yes", "pain_trend": "better", "tags": ["location_knee"], "corrections": []})
    prescription_logic.ask_json = rx or (lambda s, u: {"medicines": [{"name": "Paracetamol", "dose": "500 mg", "timing": "after food", "duration": "5 days"}],
                                                       "diet_advice": ["Eat calcium-rich foods"], "warning_signs": ["Night pain increasing"],
                                                       "follow_up": "3 weeks", "unclear": []})


def say(client, sid, text):
    import io
    SPEECH.append(text)
    return client.post("/api/call/turn", data={"session_id": sid}, files={"audio": ("a.wav", io.BytesIO(b"x"), "audio/wav")})


def run_call(client, token, answers, kind="intake"):
    """Drive a whole patient call. Returns (start_response_json, last_turn_json)."""
    r = client.post("/api/call/start", json={"token": token, "kind": kind})
    assert r.status_code == 200, r.text
    start = r.json()
    last = None
    for a in ["అవును"] + answers:
        last = say(client, start["session_id"], a)
        assert last.status_code == 200, last.text
        last = last.json()
        if last.get("done"):
            break
    return start, last


INTAKE_ANSWERS = ["నా పేరు రాము, వయసు 58", "మోకాలు", "మూడు నెలలు", "లేదు", "మెట్లు ఎక్కేటప్పుడు", "కొంచెం వాపు", "లేదు", "జ్వరం లేదు", "ఏమీ లేదు"]
CHECKIN_ANSWERS = ["అవును", "రాగి", "తగ్గింది", "లేదు", "లేదు"]


def register(desk_client, doctor_id, **over):
    body = {"full_name": "Ramu Patient", "age": 58, "sex": "M", "phone": "9000000011", "doctor_id": doctor_id}
    body.update(over)
    r = desk_client.post("/api/visits", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    j["token"] = j["url"].split("t=")[1]
    return j
