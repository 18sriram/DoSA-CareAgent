let picked = null, lastSlip = null;

function status(v) {
  if (v.emergency || v.checkin_emergency) return h("span", {class: "chip emergency"}, "Emergency flagged");
  if (v.status === "closed") return h("span", {class: "chip grey"}, "Closed");
  if (v.status === "intake_done") return h("span", {class: "chip routine"}, "Intake call done");
  const expired = v.token_expires_at <= new Date().toISOString();
  return h("span", {class: "chip " + (expired ? "high" : "medium")}, expired ? "Link expired" : "Waiting for intake call");
}

async function loadVisits() {
  const [vs, calls] = await Promise.all([api("/api/visits"), api("/api/emergencies")]);
  const em = vs.filter(v => (v.emergency || v.checkin_emergency) && v.status !== "closed");
  $("#alerts").replaceChildren(
    ...calls.map(c => h("div", {class: "emergency-banner", role: "alert"},
      `Emergency call to the help line: ${c.name} (${c.mrn}) described an emergency at ${fmtTime(c.created_at)}. Phone ${c.phone || "(no number on file)"} now and send help. Doctor: ${c.doctor_name}.`,
      h("div", {style: "margin-top:8px"}, h("button", {class: "btn small", onclick: async () => {
        try { await api(`/api/emergencies/${c.id}/handled`, {method: "POST"}); toast("Marked as handled"); loadVisits(); } catch (e) { toast(e.message, true); } }}, "I have phoned the patient")))),
    ...em.map(v => h("div", {class: "emergency-banner", role: "alert"},
      `Emergency flagged: ${v.name} (${v.mrn}). Please go to the patient and inform ${v.doctor_name}.`)));
  $("#count").textContent = vs.length ? `${vs.length} visit${vs.length === 1 ? "" : "s"}` : "";
  $("#none").hidden = vs.length > 0;
  $("#rows").replaceChildren(...vs.map(v => h("tr", {},
    h("td", {}, h("b", {}, v.name), h("div", {class: "muted"}, `${v.mrn}${v.age ? ", age " + v.age : ""}`)),
    h("td", {}, v.doctor_name), h("td", {}, fmtTime(v.created_at)), h("td", {}, status(v)),
    h("td", {}, v.status === "registered" ? h("button", {class: "btn secondary small", onclick: () => newLink(v)}, "New link") : null))));
}

async function newLink(v) {
  try { showSlip(await api(`/api/visits/${v.id}/new-link`, {method: "POST"})); loadVisits(); }
  catch (e) { toast(e.message, true); }
}

function showSlip(s) {
  lastSlip = s;
  $("#s_hosp").textContent = HOSPITAL; $("#s_who").textContent = s.name; $("#s_meta").textContent = `${s.mrn}${s.doctor ? " \u00b7 " + s.doctor : ""}`;
  $("#mh").textContent = s.pinOnly ? "Help-line PIN" : "Pre-visit token";
  $("#s_qrbox").hidden = !!s.pinOnly;
  if (!s.pinOnly) { $("#s_qr").src = s.qr; $("#s_link").textContent = s.url; $("#s_exp").textContent = "Valid until " + fmtTime(s.expires_at) + ". It works once."; }
  $("#s_pinbox").hidden = !s.pin;
  if (s.pin) { $("#s_pin").textContent = s.pin; $("#s_help").textContent = s.helpline; }
  $("#copy").hidden = !!s.pinOnly;
  $("#modal").hidden = false; $("#close").focus();
}
const closeSlip = () => { $("#modal").hidden = true; };
$("#close").onclick = closeSlip; $("#mscrim").onclick = closeSlip;
document.addEventListener("keydown", e => { if (e.key === "Escape") closeSlip(); });
$("#print").onclick = () => window.print();
$("#copy").onclick = async () => { try { await navigator.clipboard.writeText(lastSlip.url); toast("Link copied"); } catch (e) { toast("Could not copy. Select the link and copy it.", true); } };

function pick(p) {
  picked = p; $("#found").hidden = true; $("#q").value = "";
  $("#newfields").hidden = true; $("#picked").hidden = false;
  $("#picked").replaceChildren(h("div", {}, h("b", {}, p.full_name), h("div", {class: "muted"}, `${p.mrn}${p.age ? ", age " + p.age : ""}${p.phone ? ", " + p.phone : ""}`)),
    h("div", {style: "display:flex;gap:6px;flex-wrap:wrap"},
      h("button", {class: "btn secondary small", title: "The old PIN stops working", onclick: async () => {
        if (!confirm("Issue a new help-line PIN for " + p.full_name + "? Their old PIN will stop working.")) return;
        try { const r = await api(`/api/patients/${p.id}/new-pin`, {method: "POST"}); showSlip({...r, pinOnly: true}); } catch (e) { toast(e.message, true); } }}, "New PIN"),
      h("button", {class: "btn secondary small", onclick: unpick}, "Change")));
}
function unpick() { picked = null; $("#picked").hidden = true; $("#newfields").hidden = false; }

let timer;
$("#q").oninput = () => {
  clearTimeout(timer);
  const q = $("#q").value.trim();
  if (q.length < 2) { $("#found").hidden = true; return; }
  timer = setTimeout(async () => {
    try {
      const ps = await api("/api/patients?q=" + encodeURIComponent(q));
      $("#found").hidden = !ps.length;
      $("#found").replaceChildren(...ps.map(p => h("button", {onclick: () => pick(p)},
        h("b", {}, p.full_name), ` \u00b7 ${p.mrn}${p.phone ? " \u00b7 " + p.phone : ""}`)));
    } catch (e) { /* ignore typing errors */ }
  }, 250);
};

$("#go").onclick = async () => {
  $("#err").textContent = "";
  const body = {doctor_id: parseInt($("#doctor").value, 10)};
  if (picked) body.patient_id = picked.id;
  else Object.assign(body, {full_name: $("#name").value, age: $("#age").value, sex: $("#sex").value, phone: $("#phone").value});
  $("#go").disabled = true;
  try {
    const s = await api("/api/visits", {method: "POST", json: body});
    showSlip(s); unpick();
    for (const id of ["name", "age", "phone"]) $("#" + id).value = ""; $("#sex").value = "";
    loadVisits();
  } catch (e) { $("#err").textContent = e.message; }
  $("#go").disabled = false;
};

(async () => {
  await boot(["front_desk", "admin"]);
  const docs = await api("/api/doctors");
  const opts = () => docs.map(d => h("option", {value: d.id}, `${d.full_name}${d.specialty ? " (" + d.specialty + ")" : ""}`));
  $("#doctor").replaceChildren(...opts()); $("#ap-doctor").replaceChildren(...opts());
  if (!docs.length) $("#err").textContent = "No doctors are set up yet. Ask an administrator.";
  loadVisits(); setInterval(loadVisits, 10000);
})();


/* ---------- Appointments tab ---------- */
let apPatient = null, apSlot = null;
document.querySelectorAll("#ftabs button").forEach(b => b.onclick = () => {
  document.querySelectorAll("#ftabs button").forEach(x => x.setAttribute("aria-selected", String(x === b)));
  $("#tab-visits").hidden = b.dataset.tab !== "visits"; $("#tab-appts").hidden = b.dataset.tab !== "appts";
  if (b.dataset.tab === "appts") { loadAppts(); loadSlots(); }
});
async function loadAppts() {
  try {
    const list = await api("/api/appointments?days=30");
    $("#ap-none").hidden = list.length > 0;
    $("#ap-rows").replaceChildren(...list.map(a => h("tr", {},
      h("td", {}, h("b", {}, fmtSlot(a.starts_at))), h("td", {}, a.patient_name, h("div", {class: "muted"}, a.mrn)), h("td", {}, a.doctor_name),
      h("td", {}, h("span", {class: "chip " + (a.booked_via === "call" ? "medium" : "grey")}, a.booked_via === "call" ? "By phone" : "Reception")),
      h("td", {}, h("button", {class: "btn secondary small", onclick: async () => {
        if (!confirm(`Cancel ${a.patient_name}'s appointment on ${fmtSlot(a.starts_at)}?`)) return;
        try { await api(`/api/appointments/${a.id}/cancel`, {method: "POST"}); toast("Appointment cancelled"); loadAppts(); loadSlots(); } catch (e) { toast(e.message, true); } }}, "Cancel")))));
  } catch (e) { toast(e.message, true); }
}
async function loadSlots() {
  apSlot = null; const box = $("#ap-slots");
  const id = $("#ap-doctor").value; if (!id) return;
  try {
    const d = await api(`/api/slots?doctor_id=${id}&days=14`);
    if (!d.slots.length) { box.replaceChildren(h("div", {class: "muted"}, `${d.doctor} has no free times in the next 2 weeks (or has not set their hours yet).`)); return; }
    const byDay = {}; d.slots.forEach(s => (byDay[s.slice(0, 10)] ||= []).push(s));
    box.replaceChildren(...Object.entries(byDay).slice(0, 6).flatMap(([day, list]) => [h("div", {class: "slotday"}, fmtDay(day)),
      h("div", {class: "slotbtns"}, list.map(s => { const b = h("button", {"aria-pressed": "false", onclick: () => {
        box.querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed", "false")); b.setAttribute("aria-pressed", "true"); apSlot = s; } }, fmtSlot(s).split(", ").slice(-1)[0]); return b; }))]));
  } catch (e) { box.replaceChildren(h("div", {class: "error"}, e.message)); }
}
function apPick(p) {
  apPatient = p; $("#ap-found").hidden = true; $("#ap-q").value = ""; $("#ap-picked").hidden = false;
  $("#ap-picked").replaceChildren(h("div", {}, h("b", {}, p.full_name), h("div", {class: "muted"}, `${p.mrn}${p.phone ? ", " + p.phone : ""}`)),
    h("button", {class: "btn secondary small", onclick: () => { apPatient = null; $("#ap-picked").hidden = true; }}, "Change"));
}
let apTimer;
$("#ap-q").oninput = () => {
  clearTimeout(apTimer); const q = $("#ap-q").value.trim();
  if (q.length < 2) { $("#ap-found").hidden = true; return; }
  apTimer = setTimeout(async () => { try {
    const ps = await api("/api/patients?q=" + encodeURIComponent(q));
    $("#ap-found").hidden = !ps.length;
    $("#ap-found").replaceChildren(...ps.map(p => h("button", {onclick: () => apPick(p)}, h("b", {}, p.full_name), ` \u00b7 ${p.mrn}${p.phone ? " \u00b7 " + p.phone : ""}`)));
  } catch (e) { /* ignore */ } }, 250);
};
$("#ap-doctor").onchange = loadSlots;
$("#ap-book").onclick = async () => {
  $("#ap-err").textContent = "";
  if (!apPatient) { $("#ap-err").textContent = "Search for and choose the patient first."; return; }
  if (!apSlot) { $("#ap-err").textContent = "Choose one of the free times."; return; }
  $("#ap-book").disabled = true;
  try { const a = await api("/api/appointments", {method: "POST", json: {patient_id: apPatient.id, doctor_id: parseInt($("#ap-doctor").value, 10), starts_at: apSlot}});
    toast(`Booked ${a.patient_name} with ${a.doctor_name}, ${fmtSlot(a.starts_at)}`); apPatient = null; $("#ap-picked").hidden = true; loadAppts(); loadSlots(); }
  catch (e) { $("#ap-err").textContent = e.message; loadSlots(); }
  $("#ap-book").disabled = false;
};
