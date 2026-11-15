from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app import config, db
from app.security import (SESSION_COOKIE, _DUMMY_HASH, client_ip, current_user, hash_password, login_limiter,
                          password_problems, start_session, verify_password)

router = APIRouter(prefix="/api/auth")


def _public(user, csrf):
    return {"user": {k: user[k] for k in ("id", "username", "full_name", "role", "specialty", "must_change_password")},
            "csrf_token": csrf, "hospital_name": config.HOSPITAL_NAME}


def _set_cookie(response: Response, raw: str):
    response.set_cookie(SESSION_COOKIE, raw, httponly=True, samesite="lax", secure=config.COOKIE_SECURE,
                        max_age=config.SESSION_HOURS * 3600, path="/")


@router.get("/info")
def info():
    """Public: only the hospital's name, so the sign-in and patient pages can show it."""
    return {"hospital_name": config.HOSPITAL_NAME}


@router.get("/session")
def session(request: Request):
    """Used by the sign-in page. Answers normally (200) whether or not someone is signed in."""
    try:
        user = current_user(request)
    except HTTPException:
        return {"signed_in": False}
    return {"signed_in": True, **_public(user, user["csrf_token"])}


@router.post("/login")
def login(payload: dict, request: Request, response: Response):
    ip = client_ip(request)
    login_limiter.check(ip)
    username, password = str(payload.get("username", "")).strip(), str(payload.get("password", ""))
    user = db.get_user_by_username(username) if username else None
    wrong = HTTPException(401, "Incorrect username or password.")
    if user is None:
        verify_password(password, _DUMMY_HASH)           # same work either way, so timing reveals nothing
        db.audit(None, "login_failed", "user", None, {"username": username[:60]}, ip)
        raise wrong
    if user["locked_until"] and user["locked_until"] > db.utcnow():
        db.audit(None, "login_blocked", "user", user["id"], {"reason": "locked"}, ip)
        raise HTTPException(429, "Too many attempts. Please wait a few minutes and try again.")
    if not verify_password(password, user["password_hash"]) or not user["active"]:
        locked = db.register_failed_login(user["id"], config.LOCKOUT_ATTEMPTS, config.LOCKOUT_MINUTES)
        db.audit(None, "login_failed", "user", user["id"], {"locked": locked, "active": bool(user["active"])}, ip)
        raise wrong
    db.register_successful_login(user["id"])
    raw, csrf = start_session(user["id"], request)
    _set_cookie(response, raw)
    db.audit(user, "login", "user", user["id"], {}, ip)
    return _public(user, csrf)


@router.post("/logout")
def logout(request: Request, response: Response, user: dict = Depends(current_user)):
    db.delete_session(user["session_hash"])
    response.delete_cookie(SESSION_COOKIE, path="/")
    db.audit(user, "logout", "user", user["id"], {}, client_ip(request))
    return {"ok": True}


@router.get("/me")
def me(user: dict = Depends(current_user)):
    return _public(user, user["csrf_token"])


@router.post("/change-password")
def change_password(payload: dict, request: Request, response: Response, user: dict = Depends(current_user)):
    full = db.get_user(user["id"], with_secret=True)
    if not verify_password(str(payload.get("current", "")), full["password_hash"]):
        raise HTTPException(400, "The current password is not correct.")
    new = str(payload.get("new", ""))
    problems = password_problems(new, user["username"])
    if verify_password(new, full["password_hash"]):
        problems.append("Choose a password you have not used just now.")
    if problems:
        raise HTTPException(400, " ".join(problems))
    db.update_user(user["id"], password_hash=hash_password(new), must_change_password=0)
    db.delete_user_sessions(user["id"])                  # signs out every other device
    raw, csrf = start_session(user["id"], request)
    _set_cookie(response, raw)
    db.audit(user, "password_changed", "user", user["id"], {}, client_ip(request))
    fresh = db.get_user(user["id"])
    return _public(fresh, csrf)
