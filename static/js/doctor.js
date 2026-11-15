
const LEVELS = ["emergency", "high", "medium", "routine"];
const LABEL = {emergency: "Emergency", high: "High", medium: "Medium", routine: "Routine"};
let patients = [], selectedId = null, detail = null;

const chip = lvl => h("span", {class: "chip " + lvl}, LABEL[lvl] || lvl);

async function load() {
  try {
    const data = await api("/api/queue");
    patients = data.patients; renderStats(data.stats); renderCounts(); renderList();
    const b = document.getElementById("msgbadge"); b.hidden = !data.messages_open; b.textContent = data.messages_open;
  } catch (e) { document.getElementById("stats").textContent = "Cannot reach the server."; }
}
async function loadDetail() {
  if (!selectedId) { detail = null; renderDetail(); return; }
  try { detail = await api(`/api/intakes/${selectedId}`); } catch (e) { detail = null; toast(e.message, true); }
  renderDetail();
}
async function reload() { await load(); await loadDetail(); }

/* ---------- sections: Patients, Messages ---------- */
const VIEWS = ["patients", "messages", "schedule", "questions"];
let currentView = "patients", msgTimer = null;
function showView(v) {
  currentView = v;
  for (const id of VIEWS) { const el = document.getElementById("view-" + id); if (el) el.hidden = id !== v; }
  document.querySelectorAll("#subnav button").forEach(b => b.setAttribute("aria-selected", String(b.dataset.view === v)));
  clearInterval(msgTimer);
  if (v === "messages") { loadMessages(); msgTimer = setInterval(loadMessages, 15000); }
  if (v === "schedule") loadSchedule();
  if (v === "questions") loadQuestions();
}
document.querySelectorAll("#subnav button").forEach(b => b.onclick = () => showView(b.dataset.view));

const KIND = {question: "Question", symptom_report: "New symptoms reported", emergency: "Emergency call", appointment_request: "Appointment request"};
const PRI_CHIP = {emergency: "emergency", urgent: "high", routine: "routine"};
const PRI_WORD = {emergency: "Emergency", urgent: "Urgent", routine: "Routine"};
function ago(iso) {
  const m = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  return m < 1 ? "just now" : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`;
}
function messageCard(m, answered) {
  const ctx = m.context || {};
  const lines = [ctx.complaint ? "Complaint: " + ctx.complaint : "", (ctx.medicines || []).length ? "Medicines: " + ctx.medicines.join("; ") : "", ctx.follow_up ? "Next visit: " + ctx.follow_up : ""].filter(Boolean);
  const card = h("div", {class: `msg ${PRI_CHIP[m.priority] || ""}${answered ? " done" : ""}`},
    h("div", {class: "mh"}, h("div", {}, h("span", {class: "who"}, m.name), h("span", {class: "meta"}, ` \u00b7 ${m.mrn}${m.age ? ", age " + m.age : ""}`)),
      h("div", {style: "display:flex;gap:8px;align-items:center"}, h("span", {class: "chip " + (PRI_CHIP[m.priority] || "")}, PRI_WORD[m.priority] || m.priority),
        h("span", {class: "meta"}, `${KIND[m.kind] || m.kind} \u00b7 ${ago(m.created_at)}`))),
    m.forwarded_from_name ? h("div", {class: "fwd"}, `Forwarded to you from ${m.forwarded_from_name}: no reply in time.`) : null,
    m.overdue && !answered ? h("div", {class: "fwd"}, "Overdue") : null,
    h("div", {class: "q"}, m.question_en || ""),
    m.question_original ? h("div", {class: "orig"}, "In the patient's words: " + m.question_original) : null,
    (m.tag_labels || []).length ? h("div", {class: "tags", style: "margin-top:8px"}, m.tag_labels.map(t => h("span", {class: "tag"}, t))) : null,
    (m.flags || []).length ? h("div", {class: "alert", style: "margin-top:8px"}, "Red flag: " + m.flags.join(", ")) : null,
    lines.length ? h("div", {class: "ctx"}, lines.map(l => h("div", {}, l))) : null);
  if (answered) {
    card.append(h("div", {class: "ctx"}, h("b", {}, "You replied: "), m.answer_text || "",
      h("div", {class: "meta"}, m.delivered_at ? "Read out to the patient " + ago(m.delivered_at) : "Will be read out on the patient's next call")));
    return card;
  }
  if (m.kind === "emergency") {
    card.append(h("div", {class: "alert", style: "margin-top:8px"}, `Phone the patient now${m.phone ? ": " + m.phone : ""}. Reception has been alerted. Emergencies are closed by phoning, not by a text reply.`),
      h("div", {class: "acts"}, h("button", {class: "secondary", onclick: () => doClose(m)}, "Close, patient has been phoned")));
    return card;
  }
  const text = h("textarea", {rows: "3", maxlength: "600", placeholder: "Your reply. It will be read out to the patient on their next call.", "aria-label": "Your reply"});
  const reusable = h("input", {type: "checkbox"});
  const send = h("button", {class: "primary", onclick: async () => {
    if (text.value.trim().length < 2) { toast("Please write a reply first.", true); return; }
    send.disabled = true;
    try { await api(`/api/messages/${m.id}/reply`, {method: "POST", json: {text: text.value, reusable: reusable.checked}}); toast("Reply saved. The patient will hear it on their next call."); loadMessages(); load(); }
    catch (e) { toast(e.message, true); send.disabled = false; }
  }}, "Send reply");
  card.append(text, h("div", {class: "acts"}, send, h("button", {class: "secondary", onclick: () => doClose(m)}, "Close without reply"),
    h("label", {class: "chk"}, reusable, "Save as a standard answer for similar questions from my patients")));
  return card;
}
async function doClose(m) {
  try { await api(`/api/messages/${m.id}/close`, {method: "POST"}); loadMessages(); load(); } catch (e) { toast(e.message, true); }
}
async function loadMessages() {
  try {
    const data = await api("/api/messages");
    document.getElementById("msg-open").replaceChildren(...(data.open.length ? data.open.map(m => messageCard(m, false)) : [h("div", {class: "empty-note"}, "No messages waiting for you.")]));
    renderBank(await api("/api/answer-bank"));
    document.getElementById("msg-done").replaceChildren(...(data.answered.length ? data.answered.map(m => messageCard(m, true)) : [h("div", {class: "empty-note"}, "Nothing answered yet.")]));
  } catch (e) { toast(e.message, true); }
}
function renderCounts() {
  const review = patients.filter(p => p.intake_id && !p.doctor_confirmed).length, wait = patients.filter(p => !p.intake_id).length, alerts = patients.filter(p => p.open_alert).length;
  const el = document.getElementById("counts"); el.replaceChildren(
    h("span", {class: "pill" + (review ? " on" : "")}, `${review} to review`), h("span", {class: "pill"}, `${wait} waiting`),
    alerts ? h("span", {class: "pill bad"}, `${alerts} alert${alerts === 1 ? "" : "s"}`) : null);
}
function renderStats(s) {
  document.getElementById("stats").textContent = s.reviewed
    ? `Your urgency matched the suggestion for ${s.agreed} of ${s.reviewed} reviewed patients.`
    : "Urgency levels shown here are suggestions. You decide.";
}
function renderList() {
  const list = document.getElementById("list"); list.replaceChildren();
  if (!patients.length) list.append(h("div", {class: "empty", style: "margin-top:40px"}, "No patients are waiting for you."));
  for (const p of patients) {
    const who = `${p.mrn}${p.age ? ", age " + p.age : ""}`;
    if (!p.intake_id) {
      list.append(h("div", {class: "row awaiting", role: "listitem"},
        h("div", {class: "top"}, h("span", {class: "name"}, p.name), h("span", {class: "chip grey"}, "Waiting")),
        h("div", {class: "sub"}, who), h("div", {class: "sub"}, "Has not done the intake call yet")));
      continue;
    }
    const lvl = p.doctor_confirmed ? p.doctor_urgency : p.suggested_urgency;
    const flagNote = (p.red_flags || []).length ? " \u00b7 red flag" : "";
    list.append(h("button", {class: `row ${lvl}${p.doctor_confirmed ? " done" : ""}`, role: "listitem",
        "aria-current": p.intake_id === selectedId ? "true" : "false", onclick: () => { selectedId = p.intake_id; activeTab = "summary"; renderList(); loadDetail(); }},
      h("div", {class: "top"}, h("span", {class: "name"}, p.name || "Unnamed patient"),
        h("span", {style: "display:flex;gap:6px;align-items:center"},
          p.open_alert ? h("span", {class: "chip emergency"}, "Check-in alert") : null, chip(lvl))),
      h("div", {class: "sub"}, who + flagNote),
      h("div", {class: "sub"}, p.doctor_confirmed ? "Reviewed" : (p.complaint || ""))));
  }
}

const PATH_CLASS = {improved: "improved", imaging_injection: "imaging", surgical_referral: "surgical"};
const fmtDate = iso => iso ? new Date(iso).toLocaleDateString("en-IN", {day: "numeric", month: "short", year: "numeric"}) : "";
const lower1 = s => s.charAt(0).toLowerCase() + s.slice(1);

function laneEl(p, path, matches) {
  const list = matches.filter(m => m.path === path.path);
  const caseList = h("div", {class: "caselist", hidden: ""}, list.map(m =>
    h("button", {class: "casebtn", onclick: () => openCase(m.id, p.id)},
      h("span", {class: "cid"}, m.id), h("span", {}, `${m.sex}, ${m.age} \u00b7 ${m.diagnosis}`),
      h("span", {class: "m"}, `similarity ${m.similarity}`), h("span", {class: "go"}, "Open record \u203a"))));
  const label = n => `View ${n} case${n === 1 ? "" : "s"}`;
  const toggle = h("button", {class: "lane-btn", "aria-expanded": "false", onclick: () => {
    caseList.hidden = !caseList.hidden;
    toggle.setAttribute("aria-expanded", String(!caseList.hidden));
    toggle.textContent = caseList.hidden ? label(list.length) : "Hide cases";
  }}, label(list.length));
  return h("div", {class: "lane " + (PATH_CLASS[path.path] || "")},
    h("div", {class: "lane-title"}, path.label),
    h("div", {class: "lane-count"}, h("span", {class: "big"}, String(path.count)), `of ${path.of} similar cases`,
      h("span", {class: "dots", "aria-hidden": "true"}, Array.from({length: path.of}, (_, i) => h("i", {class: i < path.count ? "on" : ""})))),
    path.signs.length
      ? h("div", {class: "signs"}, h("span", {class: "lab"}, "At week 3:"), path.signs.map(s => h("span", {class: "sign"}, `${s.label} \u00d7${s.count}`)))
      : h("div", {class: "signs lab"}, "No sign common to these cases at week 3"),
    toggle, caseList);
}

function similarSection(p) {
  const s = p.similar;
  const sec = h("section", {}, h("h2", {}, "Similar past cases"));
  sec.append(h("div", {class: "meta"}, "Paths seen in similar past cases, for your review. This demo uses synthetic records and a small sample. It is not a diagnosis and not a prediction."));
  if (!s || !s.n_matched) { sec.append(h("div", {class: "meta"}, (s && s.note) || "No similar past cases found.")); return sec; }
  if (!s.enough_evidence) sec.append(h("div", {class: "warn"}, `Only ${s.n_matched} similar case${s.n_matched === 1 ? "" : "s"} found. Treat this as an anecdote, not a pattern.`));
  sec.append(h("div", {class: "paths"},
    h("div", {class: "root"}, h("b", {}, "This patient"), h("span", {}, `${s.n_matched} similar cases`)),
    h("div", {class: "stem"}),
    h("div", {class: "lanes"}, s.paths.map(path => laneEl(p, path, s.matches)))));
  if ((p.tag_labels || []).length) sec.append(h("div", {class: "compared"}, "Compared using: " + p.tag_labels.join(", ")));
  if (s.suggested_questions.length) {
    sec.append(h("h2", {style: "margin-top:16px"}, "You may want to ask about"),
      h("ul", {class: "sug"}, s.suggested_questions.map(q => h("li", {}, h("b", {}, q.label),
        ` \u2013 reported in ${q.seen_in} of ${q.of} similar cases that ${lower1(q.path_label)}`))));
  }
  return sec;
}

/* ---- past case viewer ---- */
const drawer = document.getElementById("drawer"), dBody = document.getElementById("dBody"), dClose = document.getElementById("dClose");
let lastFocus = null;
async function openCase(caseId, intakeId) {
  lastFocus = document.activeElement;
  drawer.hidden = false;
  document.getElementById("dTitle").textContent = "Past case " + caseId;
  document.getElementById("dSub").textContent = "";
  dBody.replaceChildren(h("div", {class: "meta", style: "margin-top:16px"}, "Loading record..."));
  dClose.focus();
  try {
    renderCase(await api(`/api/case/${encodeURIComponent(caseId)}?intake_id=${intakeId}`));
  } catch (e) { dBody.replaceChildren(h("div", {class: "alert", style: "margin-top:16px"}, "Could not open the record: " + e.message)); }
}
function closeCase() { drawer.hidden = true; if (lastFocus) lastFocus.focus(); }
dClose.onclick = closeCase;
document.getElementById("scrim").onclick = closeCase;
document.addEventListener("keydown", e => { if (e.key === "Escape" && !drawer.hidden) closeCase(); });

function chipRow(items, cls) {
  return items.length ? h("div", {class: "chips"}, items.map(t => h("span", {class: "chip2 " + cls}, t))) : h("span", {class: "meta"}, "None");
}
function renderCase(c) {
  document.getElementById("dTitle").textContent = `Past case ${c.id}`;
  document.getElementById("dSub").textContent = `${c.sex}, ${c.age} \u00b7 ${c.diagnosis} \u00b7 Record ${c.record_no} \u00b7 name withheld`;
  const body = [];
  body.push(h("div", {class: "outcome " + (PATH_CLASS[c.path] || "")}, h("b", {}, c.path_label), h("div", {}, c.outcome)));
  if (c.comparison) {
    body.push(h("section", {}, h("h2", {}, `Compared with this patient (similarity ${c.comparison.similarity})`),
      h("div", {class: "cmp"},
        h("div", {class: "k"}, "Both have"), chipRow(c.comparison.both, "both"),
        h("div", {class: "k"}, "Only in this past case"), chipRow(c.comparison.only_case, "case"),
        h("div", {class: "k"}, "Only in your patient"), chipRow(c.comparison.only_patient, "pat"))));
  }
  const b = c.baseline || {};
  body.push(h("section", {}, h("h2", {}, "Baseline"),
    h("div", {class: "visit", style: "margin-top:0"}, h("dl", {style: "margin:0"},
      h("dt", {}, "BMI"), h("dd", {}, String(b.bmi ?? "-")),
      h("dt", {}, "Other conditions"), h("dd", {}, b.other_conditions || "-"),
      h("dt", {}, "Diet advice"), h("dd", {}, b.diet_notes || "-")))));
  body.push(h("section", {}, h("h2", {}, "Visit history"), c.visits.map(v => h("div", {class: "visit"},
    h("h3", {}, `Week ${v.week}${v.date ? " \u00b7 " + fmtDate(v.date) : ""}`),
    chipRow(v.symptoms, "case"),
    h("dl", {}, h("dt", {}, "Examination"), h("dd", {}, v.findings || "-"),
      h("dt", {}, "Investigations"), h("dd", {}, v.investigations || "-"),
      h("dt", {}, "Treatment"), h("dd", {}, v.treatment || "-"))))));
  body.push(h("div", {class: "foot"}, "Synthetic demonstration record, shown without the patient's name. Opening a past case is recorded in the audit log."));
  dBody.replaceChildren(...body);
}

/* ---- consultation, prescription, check-ins ---- */
const WORD = {yes: "Yes", partly: "Partly", no: "No", unclear: "Not clear", better: "Better", same: "About the same", worse: "Worse"};
const MEDWORD = {yes: "Taken", partial: "Partly taken", no: "Not taken", unclear: "Not clear"};
let recState = {rec: null, stream: null, chunks: [], blob: null, timer: null, secs: 0};


async function toWav16k(blob) {
  const ctx = new (window.AudioContext || window.webkitAudioContext)({sampleRate: 16000});
  const audio = await ctx.decodeAudioData(await blob.arrayBuffer());
  const pcm = audio.getChannelData(0);
  const out = new DataView(new ArrayBuffer(44 + pcm.length * 2));
  const w = (o, s) => { for (let i = 0; i < s.length; i++) out.setUint8(o + i, s.charCodeAt(i)); };
  w(0, "RIFF"); out.setUint32(4, 36 + pcm.length * 2, true); w(8, "WAVE"); w(12, "fmt ");
  out.setUint32(16, 16, true); out.setUint16(20, 1, true); out.setUint16(22, 1, true);
  out.setUint32(24, 16000, true); out.setUint32(28, 32000, true); out.setUint16(32, 2, true);
  out.setUint16(34, 16, true); w(36, "data"); out.setUint32(40, pcm.length * 2, true);
  for (let i = 0; i < pcm.length; i++) { const s = Math.max(-1, Math.min(1, pcm[i])); out.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true); }
  ctx.close();
  return new Blob([out], {type: "audio/wav"});
}

function consultationBox(p) {
  recState.blob = null;
  const consent = h("input", {type: "checkbox", id: "c_consent"});
  const status = h("span", {class: "meta", id: "c_status"});
  const recBtn = h("button", {class: "secondary", id: "c_rec"}, "Record consultation");
  const paste = h("textarea", {id: "c_text", rows: "4", placeholder: "Or paste the consultation transcript here"});
  const go = h("button", {class: "primary"}, "Create prescription draft");
  const say = (t, err) => { status.textContent = t; status.style.color = err ? "var(--emergency)" : ""; };

  recBtn.onclick = async () => {
    if (recState.rec && recState.rec.state === "recording") { recState.rec.stop(); return; }
    try { recState.stream = await navigator.mediaDevices.getUserMedia({audio: true}); }
    catch (e) { say("Microphone blocked. Allow access, or paste the transcript instead.", true); return; }
    recState.chunks = []; recState.secs = 0; recState.blob = null;
    recState.rec = new MediaRecorder(recState.stream);
    recState.rec.ondataavailable = e => recState.chunks.push(e.data);
    recState.rec.onstop = () => {
      clearInterval(recState.timer); recState.stream.getTracks().forEach(t => t.stop());
      recState.blob = new Blob(recState.chunks); recBtn.classList.remove("recording"); recBtn.textContent = "Record again";
      say(`Recording ready (${Math.floor(recState.secs / 60)}:${String(recState.secs % 60).padStart(2, "0")}). Press "Create prescription draft".`);
    };
    recState.rec.start(); recBtn.classList.add("recording"); recBtn.textContent = "Stop recording";
    recState.timer = setInterval(() => {
      recState.secs++; say(`Recording... ${Math.floor(recState.secs / 60)}:${String(recState.secs % 60).padStart(2, "0")}`);
      if (recState.secs >= 240) recState.rec.stop();
    }, 1000);
  };

  go.onclick = async () => {
    if (!consent.checked) { say("Please confirm the patient has agreed to the recording.", true); return; }
    const text = paste.value.trim();
    if (!text && !recState.blob) { say("Record the consultation or paste a transcript first.", true); return; }
    go.disabled = true; say(text ? "Creating the draft..." : "Transcribing the recording. This can take up to a minute...");
    const fd = new FormData(); fd.append("consent", "true");
    try {
      if (text) fd.append("transcript", text); else fd.append("audio", await toWav16k(recState.blob), "consultation.wav");
      const out = await api(`/api/intakes/${p.id}/consultation`, {method: "POST", form: fd});
      await reload();
      if (out.failed_parts && out.failed_parts.length) alert(`Part(s) ${out.failed_parts.join(", ")} of the recording could not be transcribed. Please check the draft carefully.`);
    } catch (e) { say("Could not create the draft: " + e.message, true); go.disabled = false; }
  };
  return h("div", {class: "rx-box"},
    h("div", {class: "meta", style: "margin-bottom:8px"}, "Record the doctor-patient conversation, or paste its transcript. The assistant drafts a prescription card, and nothing is used until you confirm it."),
    h("label", {class: "consent"}, consent, h("span", {}, "The patient has agreed to this consultation being recorded.")),
    h("div", {class: "rec-row"}, recBtn, status), paste, h("div", {style: "margin-top:10px"}, go));
}

function rxEditor(p, rx) {
  const d = rx.draft || {};
  const medsBox = h("div");
  const addMed = (m = {}) => {
    const row = h("div", {class: "medrow"},
      h("input", {type: "text", class: "m-name", placeholder: "Medicine", value: m.name || "", "aria-label": "Medicine"}),
      h("input", {type: "text", class: "m-dose", placeholder: "Dose", value: m.dose || "", "aria-label": "Dose"}),
      h("input", {type: "text", class: "m-timing", placeholder: "When to take", value: m.timing || "", "aria-label": "When to take"}),
      h("input", {type: "text", class: "m-duration", placeholder: "For how long", value: m.duration || "", "aria-label": "Duration"}),
      h("button", {title: "Remove", "aria-label": "Remove this medicine", onclick: () => row.remove()}, "\u00d7"));
    medsBox.append(row);
  };
  (d.medicines && d.medicines.length ? d.medicines : [{}]).forEach(addMed);
  const diet = h("textarea", {rows: "3", id: "rx_diet", placeholder: "One instruction per line"}, (d.diet_advice || []).join("\n"));
  const warn = h("textarea", {rows: "2", id: "rx_warn", placeholder: "One per line"}, (d.warning_signs || []).join("\n"));
  const follow = h("input", {type: "text", id: "rx_follow", value: d.follow_up || ""});
  const status = h("span", {class: "meta", style: "margin-left:12px"});
  const confirm = h("button", {class: "primary", onclick: async () => {
    confirm.disabled = true; status.textContent = "";
    const body = {medicines: [...medsBox.querySelectorAll(".medrow")].map(r => ({name: r.querySelector(".m-name").value, dose: r.querySelector(".m-dose").value,
        timing: r.querySelector(".m-timing").value, duration: r.querySelector(".m-duration").value})),
      diet_advice: diet.value.split("\n"), warning_signs: warn.value.split("\n"), follow_up: follow.value};
    try {
      await api(`/api/prescriptions/${rx.id}/confirm`, {method: "POST", json: body});
      await reload();
    } catch (e) { status.textContent = "Could not confirm: " + e.message; status.style.color = "var(--emergency)"; confirm.disabled = false; }
  }}, "Confirm prescription");
  const unsure = [...(d.unclear || [])];
  const box = h("div", {class: "rx-box"});
  if (rx.llm_error) box.append(h("div", {class: "warn"}, "The assistant could not draft this automatically. Please fill it in from the transcript. (" + rx.llm_error.slice(0, 100) + ")"));
  if (unsure.length) box.append(h("div", {class: "warn"}, "The assistant was unsure about: " + unsure.join("; ") + ". Please check against the transcript."));
  box.append(h("div", {class: "meta"}, "Draft made by the assistant from the consultation. Check every line, edit what is wrong, then confirm."),
    h("span", {class: "lbl"}, "Medicines"), medsBox,
    h("button", {class: "link", onclick: () => addMed()}, "+ Add a medicine"),
    h("label", {class: "lbl", for: "rx_diet"}, "Diet advice"), diet,
    h("label", {class: "lbl", for: "rx_warn"}, "Warning signs to watch for"), warn,
    h("label", {class: "lbl", for: "rx_follow"}, "Next visit"), follow,
    h("div", {style: "margin-top:14px"}, confirm, status),
    h("details", {class: "tx"}, h("summary", {}, "Consultation transcript"), h("div", {class: "meta", style: "white-space:pre-wrap;margin-top:6px"}, rx.transcript || "")));
  return box;
}

function rxConfirmed(p, rx) {
  const f = rx.final || {};
  const linkArea = h("div");
  const make = h("button", {class: "secondary", onclick: async () => {
    make.disabled = true;
    try {
      const out = await api(`/api/prescriptions/${rx.id}/checkin-link`, {method: "POST"});
      const box = h("input", {type: "text", readonly: true, value: out.url, "aria-label": "Reminder call link"});
      linkArea.replaceChildren(h("div", {class: "linkbox"}, box,
        h("button", {class: "secondary", onclick: async () => { try { await navigator.clipboard.writeText(out.url); toast("Link copied"); } catch (e) { box.select(); } }}, "Copy"),
        h("a", {href: out.url, target: "_blank", rel: "noopener", class: "secondary", style: "padding:9px 16px;border-radius:8px;text-decoration:none;font-weight:600;background:var(--teal-soft)"}, "Open call")),
        h("div", {class: "meta", style: "margin-top:6px"}, `Single patient link, valid until ${fmtTime(out.expires_at)}. Creating a new one cancels the old one.`));
    } catch (e) { toast(e.message, true); }
    make.disabled = false;
  }}, "Create reminder call link");
  return h("div", {class: "rx-box"},
    h("div", {}, h("span", {class: "ok-pill"}, "Confirmed by doctor"), " ", h("span", {class: "meta"}, fmtTime(rx.confirmed_at))),
    h("dl", {class: "kv"},
      h("dt", {}, "Medicines"), h("dd", {}, h("ul", {class: "medlist"}, (f.medicines || []).map(m =>
        h("li", {}, [m.name, m.dose, m.timing, m.duration].filter(Boolean).join(" \u00b7 "))))),
      h("dt", {}, "Diet advice"), h("dd", {}, (f.diet_advice || []).join("; ") || "-"),
      h("dt", {}, "Watch for"), h("dd", {}, (f.warning_signs || []).join("; ") || "-"),
      h("dt", {}, "Next visit"), h("dd", {}, f.follow_up || "-")),
    h("div", {class: "meta", style: "margin:12px 0 8px"}, "Reminder calls use only this confirmed prescription. In a hospital deployment the call is placed automatically at the right times; here you can create the patient's link yourself."),
    make, linkArea);
}

function rxSection(p) {
  const sec = h("section", {}, h("h2", {}, "Consultation and prescription"));
  const rx = p.prescription;
  if (!rx) sec.append(consultationBox(p));
  else if (rx.status === "confirmed") sec.append(rxConfirmed(p, rx));
  else sec.append(rxEditor(p, rx));
  return sec;
}

function checkinSection(p) {
  const list = p.checkins || [];
  if (!list.length) return null;
  const sec = h("section", {}, h("h2", {}, "Follow-up check-in calls"));
  for (const c of list) {
    const r = c.result || {}, pr = c.progress || {};
    const card = h("div", {class: "ck a-" + (c.alert_level || "none")});
    card.append(h("h3", {}, h("span", {}, `Check-in \u00b7 ${fmtTime(c.created_at)} \u00b7 treated as week ${c.week}`),
      c.acknowledged ? h("span", {class: "meta"}, "Seen") : null));
    for (const a of c.alerts || []) card.append(h("div", {class: "alert-line " + a.level}, a.text));
    if (!c.acknowledged && ["emergency", "doctor"].includes(c.alert_level)) {
      card.append(h("button", {class: "secondary", style: "margin:4px 0 8px", onclick: async () => {
        try { await api(`/api/checkins/${c.id}/acknowledge`, {method: "POST"}); await reload(); } catch (e) { toast(e.message, true); } }}, "Mark alerts as seen"));
    }
    card.append(h("dl", {class: "kv"},
      h("dt", {}, "Medicine"), h("dd", {}, (MEDWORD[r.medicine_taken] || "-") + (r.medicine_note ? ` (${r.medicine_note})` : "")),
      h("dt", {}, "Diet"), h("dd", {}, (r.diet_summary || "-") + (r.followed_diet && r.followed_diet !== "unclear" ? ` \u00b7 following advice: ${(WORD[r.followed_diet] || "").toLowerCase()}` : "")),
      h("dt", {}, "Pain"), h("dd", {}, WORD[r.pain_trend] || "-")));
    if ((r.corrections || []).length) card.append(h("div", {class: "meta", style: "margin-top:6px"},
      "Changed by the patient: " + r.corrections.map(x => `${x.item}: ${x.earlier} \u2192 ${x.final}`).join("; ")));
    const prog = h("div", {class: "prog"}, h("h2", {style: "margin:0 0 4px"}, "Progress"),
      h("div", {class: "meta"}, "Based only on what the patient mentioned in this call. A symptom that was not mentioned may still be present; the pain answer above is the direct signal."));
    if (pr.unavailable) prog.append(h("div", {class: "meta"}, "Not shown. " + (pr.reason || "")));
    else {
      prog.append(h("div", {class: "cmp"},
        h("div", {class: "k"}, "No longer reported"), chipRow(pr.resolved || [], "both"),
        h("div", {class: "k"}, "Still reported"), chipRow(pr.persisting || [], "case"),
        h("div", {class: "k"}, "New since first visit"), chipRow(pr.new || [], "pat")));
      if ((pr.alignment || []).length) {
        prog.append(h("div", {class: "meta", style: "margin-top:10px"}, `Compared with how ${pr.n_matched} similar past cases looked around week ${pr.week}:`));
        for (const a of pr.alignment) prog.append(h("div", {class: "align" + (a.path === pr.closest ? " best" : "")},
          h("span", {}, a.label), h("div", {class: "track"}, h("div", {class: "fill", style: `width:${Math.round(a.score * 100)}%`})), h("span", {class: "meta"}, `similarity ${a.score}`)));
        prog.append(h("div", {class: "meta", style: "margin-top:8px"}, pr.closest ? `Closest to: ${pr.closest_label}.` : "No clearly closest path.",
          " For your review only. This is not a prediction."));
        if (!pr.enough_evidence) prog.append(h("div", {class: "warn"}, "Only a few similar cases exist. Treat this as an anecdote, not a pattern."));
      }
    }
    card.append(prog);
    card.append(h("details", {class: "tx"}, h("summary", {}, "What the patient said"),
      (c.transcript || []).map(x => h("div", {class: "qa"}, h("div", {class: "q"}, x.question_en), h("div", {class: "a"}, x.answer)))));
    sec.append(card);
  }
  return sec;
}

let activeTab = "summary";
const TABS = [["summary", "Summary"], ["similar", "Similar cases"], ["rx", "Prescription"], ["followup", "Follow-up"], ["transcript", "Call transcript"]];
const SHORT = {improved: "improved", imaging_injection: "needed imaging or an injection", surgical_referral: "were referred for surgery"};
const setTab = key => { activeTab = key; renderDetail(); };

function tabBadge(p, key) {
  if (key === "rx" && p.prescription) return [p.prescription.status === "draft" ? "Needs you" : "\u2713", p.prescription.status === "draft" ? "warn" : "ok"];
  if (key === "followup" && (p.checkins || []).length) return p.open_alert ? ["Alert", "bad"] : [String((p.checkins || []).length), "plain"];
  return null;
}

function alertBanners(p, form) {
  const out = [];
  if (form.stated_age && p.age && Math.abs(form.stated_age - p.age) > 2)
    out.push(h("div", {class: "ident", role: "alert"}, `The age given on the call (${form.stated_age}) differs from the registered age (${p.age}). Please check this is the right patient.`));
  if ((p.red_flags || []).length) out.push(h("div", {class: "alert", role: "alert"}, "Red flag in the call: " + p.red_flags.map(f => f.label).join(", ")));
  if (form.needs_manual_review) out.push(h("div", {class: "alert"}, "The automatic summary was unavailable. Please read the call transcript." + (form.llm_error ? " (" + form.llm_error.slice(0, 120) + ")" : "")));
  if (p.open_alert) {
    const open = (p.checkins || []).filter(c => !c.acknowledged && ["emergency", "doctor"].includes(c.alert_level));
    out.push(h("button", {class: "banner-alert linkish " + (p.open_alert === "emergency" ? "emergency" : ""), role: "alert", onclick: () => setTab("followup")},
      "Check-in alert: " + open.flatMap(c => c.alerts.filter(a => a.level !== "family").map(a => a.text)).slice(0, 3).join(" \u00b7 "), h("span", {class: "go"}, "\u2003See details \u203a")));
  }
  return out;
}

function statusRow(label, text, tab, tone) {
  return h("button", {class: "statusrow" + (tone ? " " + tone : ""), onclick: () => setTab(tab)},
    h("span", {class: "k"}, label), h("span", {class: "v"}, text), h("span", {class: "go"}, "\u203a"));
}

function summaryTab(p, form) {
  if (form.erased) return [h("section", {class: "sumcard"}, h("h2", {}, "Record erased"),
    h("p", {class: "complaint"}, "This patient's data was permanently erased by the hospital administrator. Their identity, call transcripts, symptoms and prescriptions are no longer stored."),
    h("div", {class: "meta"}, "Only anonymous counts and the record number " + p.mrn + " remain."))];
  const out = [];
  const said = [form.stated_name, form.stated_age ? "age " + form.stated_age : ""].filter(Boolean).join(", ");
  const corr = form.corrections || [];
  out.push(h("section", {class: "sumcard"}, h("h2", {}, "What the patient reported"),
    h("p", {class: "complaint"}, form.complaint_summary || "No summary was produced. Please read the call transcript."),
    (p.tag_labels || []).length ? h("div", {class: "tags"}, p.tag_labels.map(t => h("span", {class: "tag"}, t))) : null,
    said ? h("div", {class: "said2"}, "Said on the call: " + said) : null,
    corr.length ? h("div", {class: "said2"}, "Changed during the call: " + corr.map(c => `${c.item}: ${c.earlier} \u2192 ${c.final}`).join("; ")) : null));

  // ---- the decision
  const current = p.doctor_confirmed ? p.doctor_urgency : p.suggested_urgency;
  const reasons = form.urgency_reasons || [];
  const levels = h("div", {class: "levels"}, LEVELS.map(l => h("label", {class: l},
    h("input", {type: "radio", name: "lvl", value: l, checked: l === current ? "" : null}), h("span", {}, LABEL[l]))));
  const notes = h("textarea", {id: "f_notes", rows: "2", placeholder: "Note for the record (optional)", hidden: p.doctor_notes ? null : ""}, p.doctor_notes || "");
  const addNote = p.doctor_notes ? null : h("button", {class: "link", onclick: () => { notes.hidden = false; addNote.remove(); notes.focus(); }}, "+ Add a note");
  const saved = h("span", {id: "saved"});
  const btn = h("button", {class: "primary", onclick: () => save(p, btn, saved)}, p.doctor_confirmed ? "Update decision" : "Confirm urgency");
  const edit = h("details", {class: "edit"}, h("summary", {}, "Correct name, age or summary"),
    h("label", {class: "lbl", for: "f_name"}, "Name"), h("input", {type: "text", id: "f_name", value: form.name || p.name || ""}),
    h("label", {class: "lbl", for: "f_age"}, "Age"), h("input", {type: "number", id: "f_age", value: form.age || p.age || ""}),
    h("label", {class: "lbl", for: "f_summary"}, "Summary of the complaint"), h("textarea", {id: "f_summary", rows: "2"}, form.complaint_summary || ""));
  out.push(h("section", {class: "sumcard"}, h("h2", {}, "Urgency"),
    h("div", {class: "suggest"}, "Suggested by the assistant: ", chip(p.suggested_urgency),
      reasons.length ? " because " + reasons.join("; ").toLowerCase() + "." : " No concerning signs were reported."),
    h("div", {class: "meta", style: "margin-top:2px"}, "Fixed rules, not AI. This is a suggestion only. You decide."),
    levels, addNote, notes, edit,
    h("div", {style: "margin-top:14px;display:flex;align-items:center;gap:12px;flex-wrap:wrap"}, btn, saved,
      p.doctor_confirmed ? h("span", {class: "meta"}, `Confirmed by you as ${LABEL[p.doctor_urgency]}, ${fmtTime(p.reviewed_at)}`) : null)));

  // ---- where things stand, one line each
  const s = p.similar, rx = p.prescription, cks = p.checkins || [];
  const sim = s && s.n_matched
    ? s.paths.map(x => `${x.count} ${SHORT[x.path] || x.label.toLowerCase()}`).join(" \u00b7 ") + ` (of ${s.n_matched} similar cases)` + (s.enough_evidence ? "" : ". Very few cases, treat as an anecdote")
    : (s && s.note) || "No similar past cases found";
  const rxText = !rx ? "No consultation added yet" : rx.status === "draft" ? "A draft is waiting for your confirmation" : `Confirmed ${fmtTime(rx.confirmed_at)}`;
  let fu = "No check-in calls yet";
  if (cks.length) { const c = cks[0], first = (c.alerts || []).find(a => a.level !== "family"); fu = first ? first.text : `Last check-in ${fmtTime(c.created_at)}: nothing concerning`; }
  else if (rx && rx.status === "confirmed") fu = "No check-in calls yet. Create the reminder link in the Prescription tab";
  out.push(h("section", {class: "sumcard rows"}, h("h2", {}, "Where things stand"),
    statusRow("Similar past cases", sim, "similar"),
    statusRow("Prescription", rxText, "rx", rx && rx.status === "draft" ? "warn" : ""),
    statusRow("Follow-up calls", fu, "followup", p.open_alert ? "bad" : "")));
  return out;
}

function transcriptTab(p) {
  const qa = p.transcript || [];
  return h("section", {class: "sumcard"}, h("h2", {}, "What the patient said"),
    qa.length ? qa.map(x => h("div", {class: "qa"}, h("div", {class: "q"}, x.question_en), h("div", {class: "a"}, x.answer)))
              : h("div", {class: "meta"}, "The call ended before any question was answered."));
}

function renderDetail() {
  const p = detail; const box = document.getElementById("detail"); box.replaceChildren();
  if (!p) { box.append(h("div", {class: "empty"}, "Choose a patient from the list.")); return; }
  const form = p.form || {};
  const wrap = h("div", {class: "wrap"});
  const lvl = p.doctor_confirmed ? p.doctor_urgency : p.suggested_urgency;
  wrap.append(h("div", {class: "phead"},
    h("div", {}, h("h1", {class: "pname"}, p.name),
      h("div", {class: "meta"}, `${p.age ? p.age + " years" : "Age not given"}${p.sex ? " \u00b7 " + ({F: "Female", M: "Male", O: "Other"}[p.sex] || "") : ""} \u00b7 ${p.mrn} \u00b7 intake call ${fmtTime(p.created_at)}`)),
    h("div", {class: "pright"}, chip(lvl), p.doctor_confirmed ? h("span", {class: "meta"}, "confirmed") : h("span", {class: "meta"}, "suggested"),
      h("button", {class: "secondary small", title: "Removes the patient from your list. A new check-in alert brings them back.", onclick: async () => {
        try { await api(`/api/intakes/${p.id}/close`, {method: "POST"}); selectedId = null; detail = null; toast("Visit marked complete"); await load(); renderDetail(); }
        catch (e) { toast(e.message, true); } }}, "Mark visit complete"))));
  alertBanners(p, form).forEach(el => wrap.append(el));
  wrap.append(h("div", {class: "tabs", role: "tablist"}, TABS.map(([key, label]) => {
    const bdg = tabBadge(p, key);
    return h("button", {role: "tab", "aria-selected": String(key === activeTab), onclick: () => setTab(key)}, label,
      bdg ? h("span", {class: "tbadge " + bdg[1]}, bdg[0]) : null);
  })));
  const content = {summary: () => summaryTab(p, form), similar: () => similarSection(p), rx: () => rxSection(p),
                   followup: () => checkinSection(p) || h("section", {class: "sumcard"}, h("h2", {}, "Follow-up calls"),
                     h("div", {class: "meta"}, "No check-in calls yet. Once you confirm a prescription, create the patient's reminder link in the Prescription tab.")),
                   transcript: () => transcriptTab(p)}[activeTab]();
  [].concat(content).forEach(el => wrap.append(el));
  box.append(wrap);
}

async function save(p, btn, saved) {
  btn.disabled = true; saved.textContent = "";
  const ageVal = parseInt(document.getElementById("f_age").value, 10);
  const body = {urgency: document.querySelector("input[name=lvl]:checked").value,
                notes: document.getElementById("f_notes").value,
                edits: {name: document.getElementById("f_name").value.trim(),
                        age: Number.isNaN(ageVal) ? null : ageVal,
                        complaint_summary: document.getElementById("f_summary").value.trim()}};
  try {
    await api(`/api/intakes/${p.id}/review`, {method: "POST", json: body});
    saved.textContent = "Saved";
    await reload();
  } catch (e) { saved.textContent = "Could not save: " + e.message; saved.style.color = "var(--emergency)"; }
  btn.disabled = false;
}

(async () => { await boot(["doctor"]); await load(); setInterval(load, 5000); })();


/* ---------- Schedule ---------- */
const DAYNAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
let sched = {windows: [], slot_minutes: 15};
function renderDays() {
  const box = document.getElementById("sch-days"); box.replaceChildren();
  DAYNAMES.forEach((name, wd) => {
    const wins = sched.windows.filter(w => w.weekday === wd);
    const cell = h("div", {}, wins.length ? null : h("div", {class: "closed"}, "Not seeing patients"));
    wins.forEach(w => cell.append(h("div", {class: "sch-win"},
      h("input", {type: "time", value: w.start, "aria-label": `${name} start`, oninput: e => { w.start = e.target.value; }}), "to",
      h("input", {type: "time", value: w.end, "aria-label": `${name} end`, oninput: e => { w.end = e.target.value; }}),
      h("button", {class: "link", onclick: () => { sched.windows.splice(sched.windows.indexOf(w), 1); renderDays(); }}, "Remove"))));
    cell.append(h("button", {class: "link", onclick: () => { sched.windows.push({weekday: wd, start: wins.length ? "16:00" : "09:00", end: wins.length ? "19:00" : "13:00"}); renderDays(); }},
      wins.length ? "+ Add another session" : "+ Add hours"));
    box.append(h("div", {class: "sch-day"}, h("div", {class: "dn"}, name), cell));
  });
}
function renderOff(list) {
  document.getElementById("off-list").replaceChildren(...(list.length ? list.map(t => h("div", {class: "offrow"},
    h("span", {}, `${fmtDay(t.date)}${t.reason ? " \u00b7 " + t.reason : ""}`),
    h("button", {class: "link", onclick: async () => { try { await api(`/api/schedule/time-off/${t.id}`, {method: "DELETE"}); loadSchedule(); } catch (e) { toast(e.message, true); } }}, "Remove")))
    : [h("div", {class: "meta"}, "No days off marked.")]));
}
function renderAppts(list) {
  document.getElementById("appt-list").replaceChildren(...(list.length ? list.map(a => h("div", {class: "offrow"},
    h("span", {}, h("b", {}, fmtSlot(a.starts_at)), `  ${a.patient_name} (${a.mrn}) \u00b7 booked ${a.booked_via === "call" ? "by phone" : a.booked_via === "reception" ? "by reception" : "by you"}`),
    h("button", {class: "link", onclick: async () => { if (!confirm(`Cancel ${a.patient_name}'s appointment on ${fmtSlot(a.starts_at)}?`)) return;
      try { await api(`/api/appointments/${a.id}/cancel`, {method: "POST"}); toast("Appointment cancelled"); loadSchedule(); } catch (e) { toast(e.message, true); } }}, "Cancel")))
    : [h("div", {class: "meta"}, "No upcoming appointments.")]));
}
async function loadSchedule() {
  try {
    const d = await api("/api/schedule");
    sched = {windows: d.windows.map(w => ({...w})), slot_minutes: d.slot_minutes};
    const sel = document.getElementById("sch-len");
    sel.replaceChildren(...d.slot_choices.map(m => h("option", {value: m, selected: m === d.slot_minutes}, `${m} minutes`)));
    sel.onchange = () => { sched.slot_minutes = parseInt(sel.value, 10); };
    renderDays(); renderOff(d.time_off); renderAppts(d.appointments);
  } catch (e) { toast(e.message, true); }
}
document.getElementById("sch-save").onclick = async () => {
  const note = document.getElementById("sch-saved"); note.textContent = "";
  try { await api("/api/schedule", {method: "PUT", json: {windows: sched.windows, slot_minutes: sched.slot_minutes}}); note.textContent = "Saved"; note.style.color = "var(--routine)"; loadSchedule(); }
  catch (e) { note.textContent = e.message; note.style.color = "var(--emergency)"; }
};
document.getElementById("off-add").onclick = async () => {
  const warn = document.getElementById("off-warn"); warn.hidden = true;
  try {
    const r = await api("/api/schedule/time-off", {method: "POST", json: {date: document.getElementById("off-date").value, reason: document.getElementById("off-reason").value}});
    if (r.appointments_that_day.length) { warn.hidden = false; warn.textContent = `${r.appointments_that_day.length} appointment(s) are already booked that day. Cancel or move them in the list below.`; }
    document.getElementById("off-reason").value = ""; loadSchedule();
  } catch (e) { toast(e.message, true); }
};


/* ---------- Standard answers (inside Messages) ---------- */
function renderBank(list) {
  document.getElementById("bank-list").replaceChildren(...(list.length ? list.map(b => h("div", {class: "msg routine"},
    h("div", {class: "q"}, b.question_en), h("div", {class: "ctx"}, h("b", {}, "Read to patients: "), b.answer_text),
    h("div", {class: "acts"}, h("button", {class: "secondary", onclick: async () => {
      if (!confirm("Delete this standard answer? It will no longer be read to patients.")) return;
      try { await api(`/api/answer-bank/${b.id}`, {method: "DELETE"}); toast("Standard answer deleted"); loadMessages(); } catch (e) { toast(e.message, true); } }}, "Delete"))))
    : [h("div", {class: "empty-note"}, "None yet. When you reply to a message, tick \"Save as a standard answer\" to add one.")]));
}

/* ---------- Intake questions ---------- */
let qset = [], qinfo = {min: 2, max: 12};
function renderQuestions() {
  const box = document.getElementById("q-list"); box.replaceChildren();
  qset.forEach((q, i) => {
    const te = h("textarea", {lang: "te", rows: "2", maxlength: "300", "aria-label": `Question ${i + 1} in Telugu`, oninput: e => { q.te = e.target.value; }}, q.te);
    const en = h("textarea", {rows: "2", maxlength: "200", "aria-label": `Question ${i + 1} in English`, oninput: e => { q.en = e.target.value; }}, q.en);
    const hear = h("button", {class: "secondary", onclick: async () => {
      hear.disabled = true; hear.textContent = "Loading...";
      try {
        const r = await fetch("/api/question-set/preview", {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json", "X-CSRF-Token": CSRF}, body: JSON.stringify({text: q.te})});
        if (!r.ok) { let t = await r.text(); try { t = JSON.parse(t).detail; } catch (e) { /* plain */ } throw new Error(detailText(t)); }
        const url = URL.createObjectURL(await r.blob()); const a = new Audio(url); a.onended = () => URL.revokeObjectURL(url); await a.play();
      } catch (e) { toast(e.message, true); }
      hear.disabled = false; hear.textContent = "Hear it";
    }}, "Hear it");
    box.append(h("div", {class: "qcard"}, h("div", {class: "num"}, String(i + 1)),
      h("div", {class: "fields"}, h("label", {}, "Telugu (what the patient hears)"), te, h("label", {}, "English (what you read)"), en),
      h("div", {class: "tools"}, hear,
        h("button", {class: "secondary", disabled: i === 0, onclick: () => { [qset[i - 1], qset[i]] = [qset[i], qset[i - 1]]; renderQuestions(); }}, "Move up"),
        h("button", {class: "secondary", disabled: i === qset.length - 1, onclick: () => { [qset[i + 1], qset[i]] = [qset[i], qset[i + 1]]; renderQuestions(); }}, "Move down"),
        h("button", {class: "secondary", onclick: () => { qset.splice(i, 1); renderQuestions(); }}, "Remove"))));
  });
  document.getElementById("q-count").textContent = `${qset.length} of up to ${qinfo.max} questions, plus the closing question`;
  document.getElementById("q-add").disabled = qset.length >= qinfo.max;
}
async function loadQuestions() {
  try {
    const d = await api("/api/question-set");
    qset = d.questions.map(q => ({...q})); qinfo = d;
    document.getElementById("q-closing").replaceChildren(h("div", {class: "te", lang: "te"}, d.closing.te), h("div", {class: "meta"}, d.closing.en));
    document.getElementById("q-note").textContent = d.is_default ? "Using the standard list" : "Using your own list";
    renderQuestions();
  } catch (e) { toast(e.message, true); }
}
document.getElementById("q-add").onclick = () => { qset.push({te: "", en: ""}); renderQuestions(); const t = document.querySelectorAll("#q-list .qcard textarea"); if (t.length) t[t.length - 2].focus(); };
document.getElementById("q-save").onclick = async () => {
  const note = document.getElementById("q-note");
  try { const r = await api("/api/question-set", {method: "PUT", json: {questions: qset}}); qset = r.questions; renderQuestions(); note.textContent = "Saved. New calls will use these questions."; note.style.color = "var(--routine)"; }
  catch (e) { note.textContent = e.message; note.style.color = "var(--emergency)"; }
};
document.getElementById("q-reset").onclick = async () => {
  if (!confirm("Go back to the standard question list? Your own list will be deleted.")) return;
  try { const r = await api("/api/question-set", {method: "DELETE"}); qset = r.questions; await loadQuestions(); toast("Back to the standard list"); } catch (e) { toast(e.message, true); }
};
