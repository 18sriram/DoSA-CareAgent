"""CareAgent application entry point:  uvicorn app.main:app"""
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import config, db, monitor
from app.routers import admin, auth, calls, doctor, frontdesk, schedule

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")

app = FastAPI(title="CareAgent", docs_url=None, redoc_url=None, openapi_url=None)   # no public API explorer
db.init_db()
os.makedirs(config.AUDIO_DIR, exist_ok=True)
monitor.sink = db.record_model_call
db.purge_sessions()

for r in (auth.router, admin.router, frontdesk.router, doctor.router, schedule.router, calls.router):
    app.include_router(r)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; media-src 'self' blob: data:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


MAX_BODY = 16 * 1024 * 1024


@app.middleware("http")
async def security_headers(request: Request, call_next):
    try:
        too_big = int(request.headers.get("content-length", 0)) > MAX_BODY
    except ValueError:
        too_big = True
    if too_big:
        return JSONResponse({"detail": "That upload is too large."}, status_code=413)
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "microphone=(self), camera=(), geolocation=()"
    if config.COOKIE_SECURE:                                  # served over HTTPS: tell browsers to insist on it
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.url.path.startswith("/api") or request.url.path.startswith("/audio"):
        response.headers["Cache-Control"] = "no-store"
    elif request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache"      # reuse only after the server confirms the file is unchanged
    return response


def _page(name):
    return FileResponse(os.path.join(STATIC, name), headers={"Cache-Control": "no-store"})


@app.get("/")
def root():
    return _page("login.html")


@app.get("/login")
def login_page():
    return _page("login.html")


@app.get("/doctor")
def doctor_page():
    return _page("doctor.html")


@app.get("/frontdesk")
def frontdesk_page():
    return _page("frontdesk.html")


@app.get("/admin")
def admin_page():
    return _page("admin.html")


@app.get("/call")
def call_page():
    return _page("call.html")


@app.get("/helpline")
def helpline_page():
    return _page("call.html")


@app.get("/healthz")
def healthz():
    try:
        with db.conn() as c:
            c.execute("SELECT 1")
        return {"status": "ok"}
    except Exception:
        return JSONResponse({"status": "database unavailable"}, status_code=503)
