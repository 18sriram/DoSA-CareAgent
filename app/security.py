"""Authentication and authorisation.

- Passwords: salted scrypt (standard library). Never stored or logged in clear.
- Sessions: random 256-bit cookie (HttpOnly, SameSite=Lax); only its SHA-256 is stored, so a leaked database
  cannot be used to impersonate anyone. Expires after SESSION_HOURS or after an idle period.
- CSRF: every state-changing request must carry the session's CSRF token in the X-CSRF-Token header.
- Roles: admin, doctor, front_desk. Each API route names the roles allowed to call it.
- Login: per-account lockout after repeated failures, plus a per-IP rate limit. Error messages never reveal
  whether a username exists.
"""
import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request

from app import config, db

SESSION_COOKIE = "ca_session"
COMMON_PASSWORDS = {"password", "password1", "password123", "1234567890", "qwertyuiop", "letmein123", "welcome123",
                    "admin12345", "iloveyou12", "abc1234567", "hospital123", "doctor12345"}


# ------------------------------------------------------------------ passwords
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$14$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n_exp, r, p, salt_b64, dk_b64 = stored.split("$")
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=2 ** int(n_exp), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:
        return False


_DUMMY_HASH = hash_password("timing-equaliser-not-a-real-password")


def password_problems(password: str, username: str = "") -> list:
    """Return a list of reasons a password is not acceptable (empty list = fine)."""
    problems = []
    if len(password) < config.MIN_PASSWORD_LENGTH:
        problems.append(f"Use at least {config.MIN_PASSWORD_LENGTH} characters.")
    if password.lower() in COMMON_PASSWORDS:
        problems.append("That password is too common.")
    if username and username.lower() in password.lower():
        problems.append("The password must not contain the username.")
    classes = sum(bool(re.search(p, password)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    if classes < 2:
        problems.append("Mix at least two kinds of characters (letters, digits, symbols).")
    return problems


# ------------------------------------------------------------------ tokens
def new_token() -> str:
    return secrets.token_urlsafe(24)       # 192 random bits


def hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


# ------------------------------------------------------------------ rate limiting
class RateLimiter:
    def __init__(self, limit: int, window_seconds: int):
        self.limit, self.window, self.hits = limit, window_seconds, defaultdict(deque)

    def check(self, key: str):
        now = time.time()
        q = self.hits[key]
        while q and q[0] < now - self.window:
            q.popleft()
        if len(q) >= self.limit:
            raise HTTPException(429, "Too many attempts. Please wait a few minutes and try again.")
        q.append(now)

    def reset(self):
        self.hits.clear()


login_limiter = RateLimiter(30, 600)      # 30 login attempts per IP per 10 minutes
call_limiter = RateLimiter(40, 600)       # patient-link lookups per IP


def client_ip(request: Request) -> str:
    if config.TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


# ------------------------------------------------------------------ sessions
def start_session(user_id: int, request: Request) -> tuple:
    """Create a session. Returns (raw cookie value, csrf token)."""
    raw, csrf = new_token(), new_token()
    db.create_session(user_id, hash_token(raw), csrf, db.later(hours=config.SESSION_HOURS),
                      client_ip(request), request.headers.get("user-agent", ""))
    return raw, csrf


_PASSWORD_CHANGE_OK = ("/api/auth/me", "/api/auth/session", "/api/auth/change-password", "/api/auth/logout")


def current_user(request: Request) -> dict:
    """FastAPI dependency: the signed-in staff member, or 401. Also enforces CSRF on unsafe methods."""
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        raise HTTPException(401, "Please sign in.")
    th = hash_token(raw)
    s = db.get_session(th)
    now = db.utcnow()
    if s is None or s["expires_at"] <= now or not s["user"] or not s["user"]["active"]:
        if s:
            db.delete_session(th)
        raise HTTPException(401, "Your session has ended. Please sign in again.")
    idle_limit = db.later(minutes=-config.SESSION_IDLE_MINUTES)
    if s["last_seen"] < idle_limit:
        db.delete_session(th)
        raise HTTPException(401, "You were signed out after a period of inactivity.")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), s["csrf_token"]):
            raise HTTPException(403, "Security check failed. Please reload the page.")
    if s["last_seen"] < db.later(minutes=-1):
        db.touch_session(th)
    user = s["user"]
    user["csrf_token"], user["session_hash"] = s["csrf_token"], th
    if user["must_change_password"] and request.url.path not in _PASSWORD_CHANGE_OK:
        raise HTTPException(403, "You must change your password before continuing.")
    return user


def require_roles(*roles):
    def dependency(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(403, "Your role does not allow this.")
        return user
    return dependency
