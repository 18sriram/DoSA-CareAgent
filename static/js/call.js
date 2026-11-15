
const params = new URLSearchParams(location.search);
const token = params.get("t") || "";
fetch("/api/auth/info").then(r => r.json()).then(i => { document.getElementById("who").textContent = i.hospital_name; }).catch(() => {});
const inbound = location.pathname === "/helpline";
const kind = inbound ? "inbound" : (params.get("k") === "checkin" ? "checkin" : "intake");
if (inbound) {
  document.getElementById("title").textContent = "Hospital help line";
  document.getElementById("idform").hidden = false;
  document.getElementById("progress").hidden = true;
  document.getElementById("stepText").textContent = "Help line";
}
if (kind === "checkin") document.getElementById("title").textContent = "Follow-up check-in call";
async function errText(r) { const t = await r.text(); try { return JSON.parse(t).detail || t; } catch { return t; } }

const $ = id => document.getElementById(id);
const typebox = $("typebox"), typed = $("typed");
const log = $("log"), mic = $("mic"), startBtn = $("start"), statusEl = $("status");
let sessionId = null, recording = false, mr = null, chunks = [], stream = null, stopTimer = null;
let meterCtx = null, meterRaf = null;

function say(msg, isError) { statusEl.textContent = msg || ""; statusEl.className = isError ? "error" : ""; }
function bubble(cls, text, audioUrl) {
  const d = document.createElement("div");
  d.className = "bubble " + cls; d.textContent = text;
  if (audioUrl) {
    const b = document.createElement("button");
    b.className = "replay"; b.textContent = "Play again";
    b.onclick = () => playAgent(audioUrl);
    d.appendChild(b);
  }
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
}
function note(text) {
  const d = document.createElement("div"); d.className = "note"; d.textContent = text; log.appendChild(d);
}
function progress(step, total) {
  document.querySelector("#progress i").style.width = total ? (step / total * 100) + "%" : "0";
  $("stepText").textContent = step > 0 ? `Question ${step} of ${total}` : "Consent";
}
// One audio element for the whole call. It is "unlocked" by the Start button click,
// which is what lets the browser play the agent's voice automatically afterwards.
const player = new Audio();
const SILENCE = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=";
function unlockAudio() { player.src = SILENCE; player.play().catch(() => {}); }
function playAgent(url) {
  return new Promise(resolve => {
    if (!url) { say("The voice is unavailable right now. Please read the message.", true); return resolve(); }
    player.onended = resolve;
    player.onerror = () => { say("The voice file could not be played (check the server window for errors).", true); resolve(); };
    player.src = url;
    player.play().catch(err => {
      say("Your browser blocked the sound (" + err.name + "). Press 'Play again' under the message.", true);
      resolve();
    });
  });
}

async function handleAgent(data) {
  bubble("agent", data.agent_text, data.audio_url);
  if (!inbound) progress(data.step || 0, data.total || 0);
  if (inbound && data.done) document.getElementById("stepText").textContent = data.emergency ? "Emergency" : "Call ended";
  if (!inbound && data.done) { document.querySelector("#progress i").style.width = "100%"; document.getElementById("stepText").textContent = data.emergency ? "Emergency" : "Completed"; }
  if (data.emergency) {
    const b = $("banner");
    b.style.display = "block";
    b.textContent = "Emergency wording detected. Please go to the emergency department now. Staff have been alerted.";
  }
  say("Agent is speaking...");
  mic.disabled = true;
  const before = statusEl.className;
  await playAgent(data.audio_url);
  const playbackFailed = statusEl.className === "error";
  if (data.done) {
    mic.style.display = "none";
    note("Call ended.");
    say("");
    return;
  }
  mic.disabled = false;
  if (!playbackFailed) say("Tap the microphone, answer, then tap again.");
}

startBtn.onclick = async () => {
  unlockAudio();
  startBtn.disabled = true; say("Connecting...");
  try {
    const url = inbound ? "/api/inbound/start" : "/api/call/start";
    const body = inbound ? {phone: $("phone").value, pin: $("pin").value} : {token, kind};
    const r = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    if (!r.ok) throw new Error(await errText(r));
    const data = await r.json();
    sessionId = data.session_id;
    startBtn.style.display = "none"; mic.style.display = "block"; $("idform").hidden = true;
    await handleAgent(data);
  } catch (e) { say("Could not start the call: " + e.message, true); startBtn.disabled = false; }
};

mic.onclick = async () => {
  if (!recording) {
    try { stream = await navigator.mediaDevices.getUserMedia({audio: true}); }
    catch (e) { say("Microphone blocked. Allow microphone access and try again.", true); return; }
    chunks = []; mr = new MediaRecorder(stream);
    mr.ondataavailable = e => chunks.push(e.data);
    mr.onstop = onStopped;
    mr.start(); recording = true; mic.classList.add("recording");
    say("Listening... tap again when you finish.");
    startMeter(stream);
    stopTimer = setTimeout(() => mr.state === "recording" && mr.stop(), 25000);
  } else { mr.stop(); }
};

function startMeter(s) {
  meterCtx = new (window.AudioContext || window.webkitAudioContext)();
  const src = meterCtx.createMediaStreamSource(s), an = meterCtx.createAnalyser();
  an.fftSize = 512; src.connect(an);
  const buf = new Uint8Array(an.fftSize);
  const tick = () => {
    an.getByteTimeDomainData(buf);
    let sum = 0; for (const v of buf) sum += ((v - 128) / 128) ** 2;
    mic.style.setProperty("--level", Math.min(1, Math.sqrt(sum / buf.length) * 4));
    meterRaf = requestAnimationFrame(tick);
  };
  tick();
}
function stopMeter() {
  cancelAnimationFrame(meterRaf); mic.style.setProperty("--level", 0);
  if (meterCtx) meterCtx.close();
}

async function onStopped() {
  clearTimeout(stopTimer); stopMeter();
  stream.getTracks().forEach(t => t.stop());
  recording = false; mic.classList.remove("recording"); mic.disabled = true;
  say("Understanding your answer...");
  try {
    const wav = await toWav16k(new Blob(chunks));
    const fd = new FormData();
    fd.append("session_id", sessionId); fd.append("audio", wav, "answer.wav");
    await submitTurn(fd);
  } catch (e) { say("Something went wrong: " + e.message, true); mic.disabled = false; }
}

async function submitTurn(fd) {
  const r = await fetch("/api/call/turn", {method: "POST", body: fd});
  if (!r.ok) throw new Error(await errText(r));
  const data = await r.json();
  if (data.transcript) bubble("patient", data.transcript);
  typebox.style.display = (data.stt_failed && !data.done) ? "flex" : "none";
  await handleAgent(data);
}

$("sendTyped").onclick = async () => {
  const text = typed.value.trim();
  if (!text || !sessionId) return;
  typed.value = ""; mic.disabled = true; say("Sending...");
  const fd = new FormData(); fd.append("session_id", sessionId); fd.append("text", text);
  try { await submitTurn(fd); } catch (e) { say("Something went wrong: " + e.message, true); mic.disabled = false; }
};
typed.addEventListener("keydown", e => { if (e.key === "Enter") $("sendTyped").click(); });

// Browsers record WebM/MP4; Gnani wants WAV. Decode, resample to 16 kHz mono, write a WAV file.
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
  for (let i = 0; i < pcm.length; i++) {
    const s = Math.max(-1, Math.min(1, pcm[i]));
    out.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  ctx.close();
  return new Blob([out], {type: "audio/wav"});
}

if (!token && !inbound) { document.getElementById("start").disabled = true; say("This page needs the link or QR code from the reception desk.", true); }
