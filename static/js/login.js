const $ = id => document.getElementById(id);
const HOME = {doctor: "/doctor", front_desk: "/frontdesk", admin: "/admin"};
let csrf = "", lastPassword = "";

function detail(t) { try { const d = JSON.parse(t).detail; return Array.isArray(d) ? d.map(x => x.msg).join("; ") : d; } catch (e) { return t; } }

fetch("/api/auth/info").then(r => r.json()).then(i => { $("hospital").textContent = i.hospital_name; document.title = "Sign in \u00b7 " + i.hospital_name; }).catch(() => {});
fetch("/api/auth/session").then(r => r.json()).then(me => {
  if (!me.signed_in) return;
  if (me.user.must_change_password) { csrf = me.csrf_token; showChange(); } else location.href = HOME[me.user.role];
});

function showChange() { $("signin").hidden = true; $("change").hidden = false; $("n0wrap").hidden = !!lastPassword; ($("n0wrap").hidden ? $("n1") : $("n0")).focus(); }

$("signin").onsubmit = async e => {
  e.preventDefault(); $("err").textContent = ""; $("go").disabled = true;
  try {
    const r = await fetch("/api/auth/login", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({username: $("u").value.trim(), password: $("p").value})});
    if (!r.ok) throw new Error(detail(await r.text()));
    const me = await r.json();
    csrf = me.csrf_token; lastPassword = $("p").value; $("p").value = "";
    if (me.user.must_change_password) showChange(); else location.href = HOME[me.user.role];
  } catch (x) { $("err").textContent = x.message; $("p").value = ""; $("p").focus(); }
  $("go").disabled = false;
};

$("change").onsubmit = async e => {
  e.preventDefault(); $("err2").textContent = "";
  if ($("n1").value !== $("n2").value) { $("err2").textContent = "The two passwords do not match."; return; }
  try {
    const r = await fetch("/api/auth/change-password", {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf},
      body: JSON.stringify({current: lastPassword || $("n0").value, new: $("n1").value})});
    if (!r.ok) throw new Error(detail(await r.text()));
    const me = await r.json();
    location.href = HOME[me.user.role];
  } catch (x) { $("err2").textContent = x.message; }
};
