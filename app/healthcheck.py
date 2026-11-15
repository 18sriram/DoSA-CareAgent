"""Pre-launch safety check:  python manage.py check
Finds unsafe settings before real patients use the system. Returns (problems, warnings)."""
import os
from urllib.parse import urlparse

from app import config, db
from app.security import verify_password


def run():
    problems, warnings = [], []
    db.init_db()
    from app import demo_data
    for username, *_ in demo_data.USERS:
        u = db.get_user_by_username(username)
        if u and u["active"] and verify_password(demo_data.DEMO_PASSWORD, u["password_hash"]):
            problems.append(f"The account '{username}' still has the published DEMO password. Deactivate it or reset its password.")
    if not db.list_users("admin", active_only=True):
        problems.append("There is no active administrator. Run: python manage.py create-user")
    for key in ("GNANI_API_KEY", "GROQ_API_KEY"):
        if not os.getenv(key):
            problems.append(f"{key} is not set, so the voice and language services will not work.")
    host = urlparse(config.PUBLIC_URL)
    local = host.hostname in ("localhost", "127.0.0.1")
    if host.scheme != "https" and not local:
        problems.append(f"PUBLIC_URL is {config.PUBLIC_URL}. Patients' microphones only work over https:// away from localhost.")
    if not config.COOKIE_SECURE and not local:
        problems.append("COOKIE_SECURE is off. Turn it on (COOKIE_SECURE=1) once the site is served over HTTPS.")
    if config.TRUST_PROXY and not config.COOKIE_SECURE:
        warnings.append("TRUST_PROXY is on while COOKIE_SECURE is off. Check your proxy setup.")
    if config.HOSPITAL_NAME == "City Orthopaedic Hospital":
        warnings.append("HOSPITAL_NAME is still the default. Set your hospital's name.")
    if os.path.exists(config.DB_PATH) and os.stat(config.DB_PATH).st_mode & 0o077:
        warnings.append(f"The database file {config.DB_PATH} can be read by other users on this machine. Run: chmod 600 {config.DB_PATH}")
    if config.SESSION_HOURS > 12:
        warnings.append("Staff sessions last more than 12 hours. Shorter is safer on shared computers.")
    if not [d for d in db.list_users("doctor", active_only=True)]:
        warnings.append("There are no active doctors yet, so reception cannot register visits.")
    return problems, warnings
