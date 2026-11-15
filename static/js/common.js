/* Shared by every staff page: DOM helper, API wrapper with CSRF, sign-in check and the top bar. */
const $ = (sel, root = document) => root.querySelector(sel);
const h = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e[k] = v;
    else if (v !== null && v !== undefined && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) e.append(k.nodeType ? k : document.createTextNode(k));
  return e;
};

let CSRF = "", ME = null, HOSPITAL = "";
const HOME = {doctor: "/doctor", front_desk: "/frontdesk", admin: "/admin"};
const ROLE_LABEL = {doctor: "Doctor", front_desk: "Reception", admin: "Administrator"};

function detailText(d) {
  if (Array.isArray(d)) return d.map(x => x.msg || JSON.stringify(x)).join("; ");
  return typeof d === "string" ? d : JSON.stringify(d);
}

async function api(path, opts = {}) {
  const o = {method: opts.method || "GET", headers: {}, credentials: "same-origin"};
  if (opts.json !== undefined) { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(opts.json); }
  if (opts.form) o.body = opts.form;
  if (o.method !== "GET") o.headers["X-CSRF-Token"] = CSRF;
  const r = await fetch(path, o);
  if (r.status === 401 && !opts.noRedirect) { location.href = "/login"; throw new Error("Signed out"); }
  if (!r.ok) {
    let t = await r.text();
    try { t = JSON.parse(t).detail ?? t; } catch (e) { /* plain text */ }
    const err = new Error(detailText(t)); err.status = r.status; throw err;
  }
  return r.json();
}

function toast(msg, isError) {
  const t = h("div", {class: "toast" + (isError ? " err" : ""), role: "status"}, msg);
  document.body.append(t); setTimeout(() => t.remove(), isError ? 6000 : 3000);
}

function fmtTime(iso) {
  return iso ? new Date(iso).toLocaleString("en-IN", {day: "numeric", month: "short", hour: "numeric", minute: "2-digit"}) : "";
}

async function boot(allowedRoles) {
  let me;
  try { me = await api("/api/auth/session", {noRedirect: true}); } catch (e) { location.href = "/login"; return new Promise(() => {}); }
  if (!me.signed_in) { location.href = "/login"; return new Promise(() => {}); }
  CSRF = me.csrf_token; ME = me.user; HOSPITAL = me.hospital_name;
  if (ME.must_change_password) { location.href = "/login"; return new Promise(() => {}); }
  if (!allowedRoles.includes(ME.role)) { location.href = HOME[ME.role]; return new Promise(() => {}); }
  const bar = h("header", {class: "topbar"},
    h("div", {class: "brand"}, "CareAgent", h("small", {}, HOSPITAL)),
    h("div", {class: "spacer"}),
    h("div", {class: "who"}, ME.full_name, h("small", {}, ROLE_LABEL[ME.role] + (ME.specialty ? " \u00b7 " + ME.specialty : ""))),
    h("button", {onclick: async () => { try { await api("/api/auth/logout", {method: "POST"}); } finally { location.href = "/login"; } }}, "Sign out"));
  document.body.prepend(bar);
  document.title = `${document.title} \u00b7 ${HOSPITAL}`;
  return me;
}

/* "2026-11-16T09:30" (hospital local time) -> "Mon, 16 Nov, 9:30 am" */
function fmtSlot(iso) {
  const [d, tm] = iso.split("T"), [y, m, dd] = d.split("-").map(Number), [hh, mm] = tm.split(":").map(Number);
  const day = new Date(y, m - 1, dd).toLocaleDateString("en-IN", {weekday: "short", day: "numeric", month: "short"});
  return `${day}, ${hh % 12 || 12}:${String(mm).padStart(2, "0")} ${hh < 12 ? "am" : "pm"}`;
}
function fmtDay(iso) { const [y, m, d] = iso.split("-").map(Number); return new Date(y, m - 1, d).toLocaleDateString("en-IN", {weekday: "long", day: "numeric", month: "long"}); }
