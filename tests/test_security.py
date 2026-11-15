"""Who may do what. Run:  python -m tests.test_security"""
from tests import helpers as H
from app import db, security

H.reset()
ids = H.make_users()
checks = []


def check(name, cond, extra=""):
    checks.append(cond)
    print(("PASS" if cond else "FAIL"), "|", name, extra)
    assert cond, name


# ------------------------------------------------ 1) nothing is reachable without signing in
anon = H.Client()
protected = [("get", "/api/auth/me"), ("get", "/api/queue"), ("get", "/api/intakes/1"), ("get", "/api/visits"), ("get", "/api/patients?q=ab"),
             ("get", "/api/doctors"), ("get", "/api/admin/users"), ("get", "/api/admin/audit"), ("get", "/api/admin/system"), ("get", "/api/case/C001"),
             ("get", "/api/messages"), ("get", "/api/schedule"), ("get", "/api/slots?doctor_id=1"), ("get", "/api/appointments"),
             ("get", "/api/question-set"), ("get", "/api/answer-bank"), ("get", "/api/emergencies")]
bad = [u for m, u in protected if getattr(anon, m)(u).status_code != 401]
check("every protected endpoint refuses an anonymous visitor", not bad, str(bad))
check("posting without signing in is refused", anon.post("/api/visits", json={}).status_code == 401)
writes = [("post", "/api/appointments", {}), ("put", "/api/schedule", {}), ("put", "/api/question-set", {}), ("post", "/api/messages/1/reply", {}),
          ("post", "/api/patients/1/new-pin", {}), ("post", "/api/emergencies/1/handled", {}), ("post", "/api/question-set/preview", {}),
          ("post", "/api/appointments/1/cancel", {}), ("post", "/api/schedule/time-off", {})]
check("every new write endpoint also refuses an anonymous visitor", all(getattr(anon, m)(u, json=b).status_code == 401 for m, u, b in writes))
check("help-line identification does not need a staff account but still refuses wrong details",
      anon.post("/api/inbound/start", json={"phone": "9000000000", "pin": "123456"}).status_code == 401)
check("the API explorer and schema are not public", anon.get("/docs").status_code == 404 and anon.get("/openapi.json").status_code == 404)

check("the sign-in page can ask 'am I signed in?' without an error", anon.get("/api/auth/session").status_code == 200 and anon.get("/api/auth/session").json() == {"signed_in": False})

# ------------------------------------------------ 2) login behaviour
c = H.Client()
r1 = c.login("dr.rao", "wrong-password")
r2 = c.login("no.such.user", "whatever-123")
check("wrong password and unknown user give the same message", r1.status_code == r2.status_code == 401 and r1.json() == r2.json())
r = c.login("dr.rao")
cookie = r.headers.get("set-cookie", "")
check("sign-in sets an HttpOnly, SameSite cookie", r.status_code == 200 and "HttpOnly" in cookie and "SameSite=lax" in cookie, cookie[:60])
check("password hash is never sent to the browser", "password" not in r.text.lower().replace("must_change_password", ""))
check("a signed-in user can read /me", c.get("/api/auth/me").json()["user"]["username"] == "dr.rao")
check("the session check reports a signed-in user", c.get("/api/auth/session").json()["signed_in"] is True)

# ------------------------------------------------ 3) CSRF
no_header = c.c.post("/api/intakes/1/review", json={"urgency": "high"})
check("a state-changing request without the CSRF token is refused", no_header.status_code == 403)
wrong = c.c.post("/api/intakes/1/review", json={"urgency": "high"}, headers={"X-CSRF-Token": "forged"})
check("a forged CSRF token is refused", wrong.status_code == 403)

# ------------------------------------------------ 4) lock-out after repeated failures
victim = H.Client()
for _ in range(5):
    victim.login("desk1", "bad-password-x")
locked = victim.login("desk1", H.PW)
check("5 wrong passwords lock the account, even for the right password", locked.status_code == 429, locked.json()["detail"][:40])
db.update_user(ids["desk"], locked_until=None, failed_logins=0)
security.login_limiter.reset()
check("the lock lifts when it expires", victim.login("desk1", H.PW).status_code == 200)

# ------------------------------------------------ 5) roles
admin, rao, sharma, desk = (H.signed_in(n) for n in ("admin1", "dr.rao", "dr.sharma", "desk1"))
check("reception cannot open the clinical queue", desk.get("/api/queue").status_code == 403)
check("a doctor cannot manage users", rao.get("/api/admin/users").status_code == 403)
check("a doctor cannot register visits", rao.post("/api/visits", json={}).status_code == 403)
check("an administrator cannot read clinical records", admin.get("/api/queue").status_code == 403)
check("an administrator can list users", admin.get("/api/admin/users").status_code == 200)
check("reception cannot use doctor-only screens (messages, timetable, questions)",
      all(desk.get(u).status_code == 403 for u in ("/api/messages", "/api/schedule", "/api/question-set", "/api/answer-bank")))
check("a doctor cannot book appointments, issue PINs or see emergencies", rao.post("/api/appointments", json={}).status_code == 403
      and rao.post("/api/patients/1/new-pin").status_code == 403 and rao.get("/api/emergencies").status_code == 403)
check("an administrator cannot read messages or change a timetable", admin.get("/api/messages").status_code == 403 and admin.put("/api/schedule", json={}).status_code == 403)
check("reception can list doctors and search patients", desk.get("/api/doctors").status_code == 200 and desk.get("/api/patients?q=ab").status_code == 200)

# ------------------------------------------------ 6) one doctor cannot see another doctor's patient
H.install_fakes()
v = H.register(desk, ids["rao"])
H.run_call(H.Client(), v["token"], H.INTAKE_ANSWERS)
iid = rao.get("/api/queue").json()["patients"][0]["intake_id"]
check("the treating doctor can open the record", rao.get(f"/api/intakes/{iid}").status_code == 200)
check("another doctor gets 404 (not even 403) for the record", sharma.get(f"/api/intakes/{iid}").status_code == 404)
check("another doctor cannot review it", sharma.post(f"/api/intakes/{iid}/review", json={"urgency": "routine"}).status_code == 404)
check("another doctor cannot add a consultation", sharma.post(f"/api/intakes/{iid}/consultation", data={"consent": "true", "transcript": "x"}).status_code == 404)
check("another doctor's queue is empty", sharma.get("/api/queue").json()["patients"] == [])
check("another doctor cannot open past cases against it", sharma.get(f"/api/case/C001?intake_id={iid}").status_code == 404)
check("reception cannot open a clinical record", desk.get(f"/api/intakes/{iid}").status_code == 403)

# ------------------------------------------------ 7) administrator-created accounts and password rules
weak = admin.post("/api/admin/users", json={"username": "new.doc", "full_name": "New Doc", "role": "doctor", "password": "short"})
check("weak temporary passwords are refused", weak.status_code == 400, weak.json()["detail"][:50])
ok = admin.post("/api/admin/users", json={"username": "new.doc", "full_name": "New Doc", "role": "doctor", "password": "Temp-Pass-4821"})
check("an administrator can create a user", ok.status_code == 200 and ok.json()["must_change_password"] == 1)
check("duplicate usernames are refused", admin.post("/api/admin/users", json={"username": "NEW.DOC", "full_name": "X Y", "role": "doctor", "password": "Temp-Pass-4821"}).status_code == 409)
newbie = H.Client()
newbie.login("new.doc", "Temp-Pass-4821")
check("a new user is blocked from everything until the password is changed", newbie.get("/api/queue").status_code == 403)
check("...but can still see who they are", newbie.get("/api/auth/me").status_code == 200)
check("the change must meet the password rules", newbie.post("/api/auth/change-password", json={"current": "Temp-Pass-4821", "new": "short"}).status_code == 400)
check("the new password cannot contain the username", newbie.post("/api/auth/change-password", json={"current": "Temp-Pass-4821", "new": "new.doc-Secret-77"}).status_code == 400)
changed = newbie.post("/api/auth/change-password", json={"current": "Temp-Pass-4821", "new": "My-Own-Secret-77"})
newbie.h = {"X-CSRF-Token": changed.json()["csrf_token"]}
check("after changing it the user can work", changed.status_code == 200 and newbie.get("/api/queue").status_code == 200)

# ------------------------------------------------ 8) deactivation and last-admin protection
uid = db.get_user_by_username("new.doc")["id"]
admin.patch(f"/api/admin/users/{uid}", json={"active": False})
check("a deactivated user's open session stops working at once", newbie.get("/api/queue").status_code == 401)
check("a deactivated user cannot sign in", H.Client().login("new.doc", "My-Own-Secret-77").status_code == 401)
check("the last administrator cannot be removed", admin.patch(f"/api/admin/users/{ids['admin']}", json={"active": False}).status_code == 400)
check("an administrator cannot demote themselves", admin.patch(f"/api/admin/users/{ids['admin']}", json={"role": "doctor"}).status_code == 400)

# ------------------------------------------------ 9) session lifetime
s = H.signed_in("dr.rao")
th = security.hash_token(s.c.cookies.get("ca_session"))
with db.conn() as con:
    con.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (db.later(minutes=-120), th))
check("an idle session is ended", s.get("/api/queue").status_code == 401)
s = H.signed_in("dr.rao")
th = security.hash_token(s.c.cookies.get("ca_session"))
with db.conn() as con:
    con.execute("UPDATE sessions SET expires_at=? WHERE token_hash=?", (db.later(minutes=-1), th))
check("an expired session is ended", s.get("/api/queue").status_code == 401)
s = H.signed_in("dr.rao")
s.post("/api/auth/logout")
check("after signing out the old cookie is dead", s.get("/api/queue").status_code == 401)

# ------------------------------------------------ 10) patient links
early_actions = {a["action"] for a in db.list_audit(limit=1000)}
H.reset(); ids = H.make_users(); H.install_fakes()
desk = H.signed_in("desk1")
v1, v2 = H.register(desk, ids["rao"]), H.register(desk, ids["rao"], full_name="Second Patient")
check("patient tokens are long and different each time", len(v1["token"]) >= 30 and v1["token"] != v2["token"])
check("the QR code is returned as an image", v1["qr"].startswith("data:image/svg+xml"))
pt = H.Client()
check("a made-up token is refused", pt.post("/api/call/start", json={"token": "abc"}).status_code == 404)
check("an empty token is refused", pt.post("/api/call/start", json={}).status_code == 404)
with db.conn() as con:
    con.execute("UPDATE visits SET token_expires_at=? WHERE id=?", (db.later(hours=-1), v2["visit_id"]))
check("an expired token is refused", pt.post("/api/call/start", json={"token": v2["token"]}).status_code == 404)
fresh = desk.post(f"/api/visits/{v2['visit_id']}/new-link").json()
check("reception can issue a new link; the old one stays dead", pt.post("/api/call/start", json={"token": v2["token"]}).status_code == 404
      and pt.post("/api/call/start", json={"token": fresh["url"].split("t=")[1]}).status_code == 200)
start, last = H.run_call(H.Client(), v1["token"], H.INTAKE_ANSWERS)
check("a token works once: the finished intake call cannot be repeated", H.Client().post("/api/call/start", json={"token": v1["token"]}).status_code == 410)
check("a finished visit cannot get a new link", desk.post(f"/api/visits/{v1['visit_id']}/new-link").status_code == 400)
check("voice files need a live call", pt.get("/audio/" + "a" * 32 + ".wav?s=nope").status_code == 404)
spy = H.Client()
for i in range(45):
    last_status = spy.post("/api/call/start", json={"token": f"guess{i}"}).status_code
check("guessing tokens is rate limited", last_status == 429)

# ------------------------------------------------ 11) nothing sensitive is stored in the clear
with db.conn() as con:
    hashes = [r[0] for r in con.execute("SELECT password_hash FROM users")]
    sess = [r[0] for r in con.execute("SELECT token_hash FROM sessions")]
    visit_hashes = [r[0] for r in con.execute("SELECT token_hash FROM visits")]
check("passwords are stored only as salted scrypt hashes", all(h.startswith("scrypt$") and H.PW not in h for h in hashes))
check("session and visit tokens are stored only as SHA-256 digests", all(len(x) == 64 for x in sess + visit_hashes)
      and v1["token"] not in visit_hashes)

# ------------------------------------------------ 12) browser protections
r = H.Client().get("/login")
hdr = {k.lower(): v for k, v in r.headers.items()}
check("pages carry strict security headers", "script-src 'self'" in hdr.get("content-security-policy", "") and hdr.get("x-frame-options") == "DENY"
      and hdr.get("x-content-type-options") == "nosniff" and hdr.get("referrer-policy") == "no-referrer")
check("scripts and styles are always re-checked, so an update is never hidden by a cached copy",
      H.Client().get("/static/js/login.js").headers.get("cache-control") == "no-cache" and H.Client().get("/login").headers.get("cache-control") == "no-store")
check("API responses are never cached", H.signed_in("desk1").get("/api/visits").headers.get("cache-control") == "no-store")

# ------------------------------------------------ 13) the audit trail
actions = early_actions | {a["action"] for a in db.list_audit(limit=1000)}
needed = {"login", "login_failed", "login_blocked", "user_created", "user_updated", "password_changed", "visit_registered",
          "intake_completed", "visit_link_reissued", "logout"}
check("security-relevant actions are in the audit log", needed <= actions, str(needed - actions))
check("failed logins are recorded without the password", all("password" not in str(a["detail"]).lower() for a in db.list_audit("login_failed")))
print(f"\nALL {len(checks)} SECURITY CHECKS PASSED")
