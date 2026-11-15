"""All settings in one place, read from environment variables (or a .env file)."""
import os

from dotenv import load_dotenv

load_dotenv()


def _flag(name, default=False):
    return os.getenv(name, "1" if default else "0").strip().lower() in ("1", "true", "yes")


HOSPITAL_NAME = os.getenv("HOSPITAL_NAME", "City Orthopaedic Hospital")
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8000").rstrip("/")   # used in patient QR links
DB_PATH = os.getenv("HOSPITAL_DB", "data/hospital.db")
AUDIO_DIR = os.getenv("AUDIO_DIR", "data/audio_cache")

COOKIE_SECURE = _flag("COOKIE_SECURE")          # set to 1 when served over HTTPS
TRUST_PROXY = _flag("TRUST_PROXY")              # set to 1 only behind your own reverse proxy
SESSION_HOURS = int(os.getenv("SESSION_HOURS", "8"))
SESSION_IDLE_MINUTES = int(os.getenv("SESSION_IDLE_MINUTES", "60"))
VISIT_TOKEN_HOURS = int(os.getenv("VISIT_TOKEN_HOURS", "24"))
CHECKIN_TOKEN_DAYS = int(os.getenv("CHECKIN_TOKEN_DAYS", "30"))
LOCKOUT_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
MIN_PASSWORD_LENGTH = 10
LANG = os.getenv("PATIENT_LANGUAGE", "te-IN")

# Inbound help line: how long a doctor has to answer before the question moves up to their supervisor
ESCALATION_MINUTES = {"emergency": 15, "urgent": 60, "routine": 24 * 60}
MAX_ESCALATION_LEVEL = 2
PIN_ATTEMPTS = 5                 # wrong PINs per phone number per hour before it is locked
TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")
