const SPEC_ROLE = () => $("#nr").value === "doctor";
let resetFor = null;

document.querySelectorAll("#tabs button").forEach(b => b.onclick = () => {
  document.querySelectorAll("#tabs button").forEach(x => x.setAttribute("aria-selected", String(x === b)));
  for (const t of ["staff", "audit", "impact", "system", "privacy"]) $("#tab-" + t).hidden = t !== b.dataset.tab;
  if (b.dataset.tab === "audit") loadAudit();
  if (b.dataset.tab === "system") loadSystem();
  if (b.dataset.tab === "impact") loadImpact();
  if (b.dataset.tab === "privacy") $("#pq").focus();
});

/* ---------- staff ---------- */
const ROLE_NAME = {admin: "Administrator", doctor: "Doctor", front_desk: "Reception"};
async function loadUsers() {
  const us = await api("/api/admin/users");
  $("#users").replaceChildren(...us.map(u => h("tr", {},
    h("td", {}, h("b", {}, u.full_name), u.specialty ? h("div", {class: "muted"}, u.specialty) : null),
    h("td", {}, u.username), h("td", {}, ROLE_NAME[u.role]),
    h("td", {}, u.active ? h("span", {class: "chip routine"}, u.must_change_password ? "Active, password not set" : "Active") : h("span", {class: "chip grey"}, "Deactivated")),
    h("td", {}, u.role === "doctor" ? supervisorSelect(u, us) : h("span", {class: "muted"}, "-")),
    h("td", {}, u.last_login_at ? fmtTime(u.last_login_at) : "Never"),
    h("td", {style: "white-space:nowrap"},
      h("button", {class: "btn secondary small", onclick: () => toggle(u)}, u.active ? "Deactivate" : "Reactivate"), " ",
      h("button", {class: "btn secondary small", onclick: () => openReset(u)}, "Reset password")))));
}
function supervisorSelect(u, all) {
  const sel = h("select", {"aria-label": "Supervisor for " + u.full_name, style: "min-width:150px", onchange: async () => {
    try { await api(`/api/admin/users/${u.id}`, {method: "PATCH", json: {supervisor_id: sel.value ? parseInt(sel.value, 10) : null}}); toast("Supervisor saved."); }
    catch (e) { toast(e.message, true); loadUsers(); } }},
    h("option", {value: ""}, "None"),
    ...all.filter(d => d.role === "doctor" && d.id !== u.id && d.active).map(d => h("option", {value: d.id, selected: d.id === u.supervisor_id}, d.full_name)));
  return sel;
}
async function toggle(u) {
  try { await api(`/api/admin/users/${u.id}`, {method: "PATCH", json: {active: !u.active}}); toast(u.active ? "Deactivated. They are signed out now." : "Reactivated."); loadUsers(); }
  catch (e) { toast(e.message, true); }
}
$("#nr").onchange = () => { $("#specwrap").hidden = !SPEC_ROLE(); };
$("#ncreate").onclick = async () => {
  $("#nerr").textContent = "";
  try {
    await api("/api/admin/users", {method: "POST", json: {username: $("#nu").value, full_name: $("#nn").value, role: $("#nr").value,
      specialty: $("#ns").value, password: $("#np").value}});
    for (const id of ["nu", "nn", "np"]) $("#" + id).value = "";
    toast("Account created."); loadUsers();
  } catch (e) { $("#nerr").textContent = e.message; }
};
function openReset(u) { resetFor = u; $("#mwho").textContent = `${u.full_name} (${u.username}) will be signed out everywhere and must choose a new password.`; $("#rp").value = ""; $("#rerr").textContent = ""; $("#modal").hidden = false; $("#rp").focus(); }
const closeReset = () => { $("#modal").hidden = true; };
$("#rcancel").onclick = closeReset; $("#mscrim").onclick = closeReset;
document.addEventListener("keydown", e => { if (e.key === "Escape") closeReset(); });
$("#rgo").onclick = async () => {
  try { await api(`/api/admin/users/${resetFor.id}/reset-password`, {method: "POST", json: {password: $("#rp").value}}); closeReset(); toast("Password reset."); loadUsers(); }
  catch (e) { $("#rerr").textContent = e.message; }
};

/* ---------- audit ---------- */
async function loadAudit() {
  const rows = await api("/api/admin/audit?limit=300&action=" + encodeURIComponent($("#af").value));
  $("#audit").replaceChildren(...rows.map(a => h("tr", {},
    h("td", {style: "white-space:nowrap"}, fmtTime(a.at)),
    h("td", {}, a.username || (a.role === "system" ? "system / patient" : "-"), h("div", {class: "muted"}, a.role)),
    h("td", {}, a.action.replaceAll("_", " ")),
    h("td", {}, a.object_type ? `${a.object_type} ${a.object_id ?? ""}` : "-"),
    h("td", {class: "detail"}, Object.keys(a.detail || {}).length ? JSON.stringify(a.detail) : ""),
    h("td", {class: "muted"}, a.ip || ""))));
}
$("#aref").onclick = loadAudit; $("#af").onchange = loadAudit;

/* ---------- system and models ---------- */
async function loadSystem() {
  const s = await api("/api/admin/system");
  const c = s.counts;
  $("#counts").replaceChildren(...[["Active staff", c.users], ["Patients registered", c.patients], ["Visits, last 24h", c.visits_24h],
    ["Intake calls, last 24h", c.intakes_24h], ["Check-in calls, last 24h", c.checkins_24h]].map(([k, v]) => h("div", {class: "stat"}, h("b", {}, String(v)), k)));
  $("#models").replaceChildren(...s.models.map(m => h("div", {class: "card model"},
    h("div", {class: "prov"}, m.provider), h("h3", {}, m.role), h("p", {class: "muted", style: "margin:6px 0 0"}, m.purpose),
    h("dl", {class: "kv"}, h("dt", {}, "Configured"), h("dd", {}, m.setting), h("dt", {}, "Language"), h("dd", {}, m.language)))));
  const byKind = Object.fromEntries(s.stats.map(x => [x.kind, x]));
  const names = {stt: "Speech to text", tts: "Text to speech", llm: "Language model"};
  $("#nohealth").hidden = s.stats.length > 0;
  $("#health").replaceChildren(...["stt", "tts", "llm"].filter(k => byKind[k]).map(k => {
    const x = byKind[k], pct = Math.round(x.success_rate * 100);
    return h("tr", {}, h("td", {}, names[k]), h("td", {}, String(x.calls)),
      h("td", {}, h("span", {class: "bar"}, h("i", {class: pct < 90 ? "bad" : "", style: `width:${pct}%`})), ` ${pct}%`),
      h("td", {}, x.avg_ms != null ? (x.avg_ms / 1000).toFixed(1) + " s" : "-"), h("td", {}, x.p95_ms != null ? (x.p95_ms / 1000).toFixed(1) + " s" : "-"),
      h("td", {class: "detail"}, x.last_error ? `${fmtTime(x.last_error.at)}: ${x.last_error.detail}` : "None"));
  }));
  const st = s.settings;
  $("#settings").replaceChildren(...[["Hospital", st.hospital], ["Staff sessions last", st.session_hours + " hours"], ["Patient links valid", st.visit_token_hours + " hours, single use"],
    ["Secure cookies", st.secure_cookies ? "On" : "Off (turn on when served over HTTPS)"], ["Database version", s.migrations.join(", ")]]
    .flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
}

(async () => { await boot(["admin"]); $("#specwrap").hidden = !SPEC_ROLE(); loadUsers(); })();


/* ---------- impact ---------- */
const pct = x => x == null ? "-" : Math.round(x * 100) + "%";
const mins = x => x == null ? "-" : (x < 1 ? "under a minute" : x < 90 ? Math.round(x) + " min" : (x / 60).toFixed(1) + " h");
const stat = (value, label, hint) => h("div", {class: "stat"}, h("b", {}, String(value)), label, hint ? h("div", {class: "muted", style: "margin-top:4px;font-size:.78rem"}, hint) : null);
const group = (title, intro, stats) => h("div", {class: "card", style: "margin-bottom:16px"}, h("h2", {}, title), intro ? h("p", {class: "muted", style: "margin:2px 0 0"}, intro) : null, h("div", {class: "stats"}, stats));

async function loadImpact() {
  const s = await api("/api/admin/analytics?days=" + $("#irange").value);
  const i = s.intake, d = s.decisions, f = s.followup, l = s.helpline, a = s.appointments;
  const total = d.reviewed || 0;
  $("#impact").replaceChildren(
    group("Before the consultation", "Patients answer the doctor's questions by voice while they wait.", [
      stat(i.visits_registered, "Visits registered"), stat(i.calls_completed, "Intake calls completed", `${pct(i.completion_rate)} of visits`),
      stat(i.avg_call_minutes == null ? "-" : i.avg_call_minutes + " min", "Average call length"),
      stat(i.by_urgency.emergency, "Emergencies caught", "emergency wording during the call"), stat(i.by_urgency.high, "Suggested high urgency"),
      stat(i.needed_manual_reading, "Needed the doctor to read the transcript", "language model unavailable"), stat(i.with_patient_corrections, "Patients who corrected themselves")]),
    group("Doctors' decisions", "The urgency level is only ever a suggestion. This shows how often doctors agreed.", [
      stat(d.reviewed, "Reviewed by a doctor"), stat(d.waiting_for_review, "Waiting for review"),
      stat(total ? pct(d.agreed_with_suggestion / total) : "-", "Agreed with the suggestion", `${d.changed_by_doctor} changed`),
      stat(mins(d.median_minutes_to_review), "Typical time to review", "from the end of the call")]),
    group("After the visit", "Confirmed prescriptions drive the reminder calls.", [
      stat(f.prescriptions_confirmed, "Prescriptions confirmed", `${f.prescriptions_drafted} drafted`), stat(f.checkin_calls, "Check-in calls"),
      stat(f.medicine_taken.yes, "Took their medicines"), stat(f.medicine_taken.partial + f.medicine_taken.no, "Missed some or all"),
      stat(f.pain_trend.better, "Pain better"), stat(f.pain_trend.worse, "Pain worse"),
      stat(f.alerts.doctor + f.alerts.emergency, "Alerts to doctors", `${f.alerts.emergency} emergency`), stat(mins(f.median_minutes_to_acknowledge), "Typical time to act on an alert")]),
    group("Help line", "Patients phone with questions. Answers come only from the doctor's confirmed record.", [
      stat(l.calls, "Calls"), stat(l.answered_from_record, "Answered from the record"), stat(l.passed_to_a_doctor, "Passed to a doctor", `${l.by_kind.symptom_report} symptom reports, ${l.by_kind.emergency} emergencies`),
      stat(mins(l.median_minutes_to_doctor_reply), "Typical doctor reply time"), stat(l.forwarded_to_supervisor, "Moved up to a supervisor", "no reply in time"),
      stat(l.standard_answers_in_use, "Standard answers in use"), stat(l.failed_verifications, "Failed identity checks")]),
    group("Appointments", null, [stat(a.booked_by_phone, "Booked by phone"), stat(a.booked_by_reception, "Booked by reception"), stat(a.cancelled, "Cancelled")]));
}
$("#irange").onchange = loadImpact;

/* ---------- privacy: erase a patient ---------- */
let eraseFor = null, ptimer;
$("#pq").oninput = () => {
  clearTimeout(ptimer); const q = $("#pq").value.trim();
  if (q.length < 2) { $("#prows").replaceChildren(); $("#pnone").hidden = true; return; }
  ptimer = setTimeout(async () => { try {
    const ps = await api("/api/admin/patients?q=" + encodeURIComponent(q));
    $("#pnone").hidden = ps.length > 0;
    $("#prows").replaceChildren(...ps.map(p => h("tr", {}, h("td", {}, h("b", {}, p.full_name), p.age ? h("span", {class: "muted"}, `, age ${p.age}`) : null), h("td", {}, p.mrn),
      h("td", {}, p.phone_last4 ? "ending " + p.phone_last4 : "-"), h("td", {}, fmtTime(p.created_at)),
      h("td", {}, p.full_name === "Erased patient" ? h("span", {class: "chip grey"}, "Already erased") : h("button", {class: "btn danger small", onclick: () => openErase(p)}, "Erase")))));
  } catch (e) { toast(e.message, true); } }, 250);
};
function openErase(p) { eraseFor = p; $("#ewho").textContent = `${p.full_name} (${p.mrn})`; $("#econfirm").value = ""; $("#eerr").textContent = ""; $("#emodal").hidden = false; $("#econfirm").focus(); }
const closeErase = () => { $("#emodal").hidden = true; };
$("#ecancel").onclick = closeErase; $("#escrim").onclick = closeErase;
document.addEventListener("keydown", e => { if (e.key === "Escape") closeErase(); });
$("#ego").onclick = async () => {
  $("#ego").disabled = true;
  try { await api(`/api/admin/patients/${eraseFor.id}/erase`, {method: "POST", json: {confirm_mrn: $("#econfirm").value}}); closeErase(); toast(`${eraseFor.mrn} erased.`); $("#pq").value = eraseFor.mrn; $("#pq").oninput(); }   // the name is gone, so look it up by record number
  catch (e) { $("#eerr").textContent = e.message; }
  $("#ego").disabled = false;
};
