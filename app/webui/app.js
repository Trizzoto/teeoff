// TeeOff web UI — talks to the Python backend over /api/.
"use strict";

// Demo fallback: ONLY used when the page is opened with no Python backend (e.g. an
// editor preview). The real app always serves on 127.0.0.1, where this never triggers.
const ALLOW_DEMO = location.hostname !== "127.0.0.1";
const DEMO_STATUS = { registered: true, state: "Ready", paused: false, triggers: 2, next_run: "2026-06-30T18:55:00" };
const DEMO = {
  ping: { ok: true, app: "TeeOff", version: "1.2.3" },
  dashboard: { version: "1.2.3", status: DEMO_STATUS, last_run: null, email_enabled: false,
    upcoming: [
      { fire_pretty: "Tue 30 Jun, 6:55 PM", play_pretty: "Wed 15 Jul", target_time: "08:12", in_str: "in 1d 12h", one_off: false },
      { fire_pretty: "Sun 05 Jul, 6:55 PM", play_pretty: "Mon 20 Jul", target_time: "08:12", in_str: "in 6d 12h", one_off: false }],
    bookings_synced: { at: "2026-06-29T15:12:00", count: 3, user: "Member" } },
  calendar: { today: "2026-06-29", days: {
    "2026-06-15": { kind: "booked", time: "08:12 am" }, "2026-06-17": { kind: "booked", time: "08:12 am" },
    "2026-06-24": { kind: "booked", time: "07:36 am" }, "2026-07-06": { kind: "planned", time: "08:12" },
    "2026-07-08": { kind: "planned", time: "08:12" }, "2026-07-13": { kind: "planned", time: "08:12" },
    "2026-07-15": { kind: "planned", time: "08:12" }, "2026-07-20": { kind: "planned", time: "08:12" } } },
  settings: { days: { monday: { enabled: true, target_time: "08:12" }, tuesday: { enabled: false, target_time: "08:12" },
    wednesday: { enabled: true, target_time: "08:12" }, thursday: { enabled: false, target_time: "08:12" },
    friday: { enabled: false, target_time: "08:12" }, saturday: { enabled: false, target_time: "08:12" },
    sunday: { enabled: false, target_time: "08:12" } },
    credentials: { username: "", has_password: true },
    email: { enabled: false, smtp_user: "", notify_to: "", has_app_password: false },
    partners: [
      { id: "1", full_name: "Your Name", is_self: true, playing_days: ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"] },
      { id: "2", full_name: "Smith, John", is_self: false, playing_days: ["monday","wednesday"] },
      { id: "3", full_name: "Brown, Pat", is_self: false, playing_days: ["wednesday"] }] },
  activity: { last_run: null, runs: [
    { when: "2026-06-17T19:00:02", kind: "ok", title: "[Wednesday] OK — booked 08:12", detail: "Tee Off booking summary — fire target 2026-06-17T19:00:02\n\n[Wednesday] OK — booked 08:12\n  - 08:12 OK send=+651ms rtt=439ms" },
    { when: "2026-06-15T19:00:02", kind: "ok", title: "[Monday] OK — booked 08:12", detail: "[Monday] OK — booked 08:12" }] },
  booker_log: { text: "2026-06-17 18:55:00 [INFO] booker.main: config: play_days=['monday', 'wednesday']\n2026-06-17 18:55:01 [INFO] booker.main: logging in...\n2026-06-17 18:55:01 [INFO] booker.main: logged in\n2026-06-17 18:55:02 [INFO] booker.main: [Wednesday] prepared 11 candidate slot(s)\n2026-06-17 19:00:02 [INFO] booker.main: fire! drift=2ms\n2026-06-17 19:00:02 [INFO] booker.main: [Wednesday] OK — booked 08:12" },
  save_days: { ok: true, status: DEMO_STATUS, upcoming: [] },
  set_paused: { ok: true, status: DEMO_STATUS },
  check_update: { update: null, version: "1.2.3" },
  save_account: { ok: true }, save_email: { ok: true, email_enabled: false }, save_partners: { ok: true },
  send_test_email: { ok: false, error: "Email isn't set up in this preview." },
  refresh_partners: { ok: false, error: "Live sync needs the real app." },
};

async function api(route, payload) {
  const opts = payload
    ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }
    : {};
  try {
    const res = await fetch("/api/" + route, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data;
  } catch (e) {
    if (ALLOW_DEMO && DEMO[route]) return JSON.parse(JSON.stringify(DEMO[route]));
    throw e;
  }
}

const $ = (sel, root = document) => root.querySelector(sel);
const h = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const ICON = {
  pause: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1.3"/><rect x="14" y="5" width="4" height="14" rx="1.3"/></svg>',
  play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l11-6.5a1 1 0 0 0 0-1.72l-11-6.5A1 1 0 0 0 8 5.5Z"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.6-6.4M21 4v5h-5"/></svg>',
  update: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16V4M7 11l5 5 5-5M5 20h14"/></svg>',
  warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/></svg>',
  chevL: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 6-6 6 6 6"/></svg>',
  chevR: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 6 6 6-6 6"/></svg>',
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 11.5v5M12 7.5h.01"/></svg>',
};

const MONTHS = ["January","February","March","April","May","June","July","August","September","October","November","December"];
const DOW = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"];
const DAY_KEYS = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"];
const DAY_LABEL = { monday:"Monday", tuesday:"Tuesday", wednesday:"Wednesday", thursday:"Thursday", friday:"Friday", saturday:"Saturday", sunday:"Sunday" };
const TIMES = (() => { const a = []; for (let hh = 6; hh <= 10; hh++) for (const mm of [0,6,12,18,24,30,36,42,48,54]) a.push(`${String(hh).padStart(2,"0")}:${String(mm).padStart(2,"0")}`); return a; })();

const state = { dash: null, cal: null, view: { y: 0, m: 0 }, page: "dashboard", busy: {} };

function toast(msg, kind = "ok") {
  const t = h(`<div class="toast ${kind}">${esc(msg)}</div>`);
  $("#toast-wrap").appendChild(t);
  setTimeout(() => { t.style.transition = "opacity .3s"; t.style.opacity = "0"; setTimeout(() => t.remove(), 320); }, 3200);
}

const ymd = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const fmtClock = (iso) => { try { const d = new Date(iso); return d.toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }); } catch { return iso; } };

// ---- boot ----
(async () => {
  const conn = $("#conn"), connText = $("#conn-text");
  try {
    await api("ping");
    conn.classList.add("ok"); $(".spin", conn)?.remove();
    conn.prepend(h('<span class="dot"></span>')); connText.textContent = "Loading…";
    await loadAll();
    const now = new Date();
    state.view = { y: now.getFullYear(), m: now.getMonth() };
    $("#version").textContent = "v" + (state.dash?.version || "—");
    const initial = ["dashboard","schedule","account","activity"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "dashboard";
    navigate(initial);
    $("#splash").classList.add("fade");
    $("#app").classList.add("show");
    setTimeout(() => $("#splash").remove(), 500);
    // Pull fresh on-site bookings only when the cache is stale (>4h) or missing — keeps
    // the calendar current without re-scraping the site on every quick relaunch.
    const sync = state.dash?.bookings_synced;
    const fresh = sync && sync.at && (Date.now() - new Date(sync.at).getTime() < 4 * 3600 * 1000);
    if (!fresh) refreshBookings(true);
    setTimeout(() => checkUpdate(true), 1500);  // quiet auto update-check on launch
  } catch (e) {
    conn.classList.add("err"); $(".spin", conn)?.remove();
    conn.prepend(h('<span class="dot"></span>'));
    connText.textContent = "Backend not reachable";
  }
})();

async function loadAll() {
  const [dash, cal] = await Promise.all([api("dashboard"), api("calendar")]);
  state.dash = dash; state.cal = cal;
  updateMiniStatus();
}

function updateMiniStatus() {
  const s = state.dash?.status || {};
  const el = $("#mini-status"), txt = $("#mini-status-text");
  el.className = "mini-status " + (!s.registered ? "off" : s.paused ? "paused" : "ok");
  txt.textContent = !s.registered ? "Not installed" : s.paused ? "Paused" : "Ready";
}

// ---- navigation ----
function navigate(page) {
  state.page = page;
  if (location.hash.slice(1) !== page) history.replaceState(null, "", "#" + page);
  document.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
  const c = $("#content"); c.innerHTML = "";
  ({ dashboard: renderDashboard, schedule: renderSchedule, account: renderAccount, activity: renderActivity }[page] || renderDashboard)(c);
  c.scrollTop = 0;
}
$("#nav").addEventListener("click", (e) => { const b = e.target.closest(".nav-item"); if (b) navigate(b.dataset.page); });

function renderSoon(c) {
  c.appendChild(h(`<div class="page-head fade-in"><h1>${esc(state.page[0].toUpperCase() + state.page.slice(1))}</h1><p>This section is coming together next.</p></div>`));
}

// ---- schedule ----
async function renderSchedule(c) {
  c.appendChild(h(`<div class="page-head fade-in"><h1>Schedule</h1><p>Choose which days to auto-book, and at what time.</p></div>`));
  const card = h(`<div class="card card-pad sched-card fade-in">
    <div id="day-rows"></div>
    <div class="sched-note">${ICON.info}<span>Each enabled day is booked automatically <b>15 days ahead</b>, the moment tee times unlock at 7:00&nbsp;pm. If 8:12 is taken, the booker walks to the nearest earlier slot.</span></div>
    <div class="sched-foot"><button class="btn btn-primary" id="btn-save" style="flex:0 0 auto">Save changes</button></div>
  </div>`);
  c.appendChild(card);

  let s;
  try { s = await api("settings"); } catch (e) { card.innerHTML = `<div class="empty">Couldn't load settings: ${esc(e.message)}</div>`; return; }
  const days = JSON.parse(JSON.stringify(s.days || {}));
  const rows = $("#day-rows", card);
  DAY_KEYS.forEach((k) => {
    const cfg = days[k] || (days[k] = { enabled: false, target_time: "08:12" });
    const row = h(`<div class="day-row ${cfg.enabled ? "" : "off"}">
      <div class="toggle ${cfg.enabled ? "on" : ""}"><div class="knob"></div></div>
      <div class="day-name">${DAY_LABEL[k]}</div>
      <select class="tsel" ${cfg.enabled ? "" : "disabled"}>${TIMES.map((t) => `<option ${t === cfg.target_time ? "selected" : ""}>${t}</option>`).join("")}</select>
    </div>`);
    const toggle = $(".toggle", row), sel = $("select", row);
    toggle.onclick = () => { cfg.enabled = !cfg.enabled; toggle.classList.toggle("on", cfg.enabled); row.classList.toggle("off", !cfg.enabled); sel.disabled = !cfg.enabled; };
    sel.onchange = () => { cfg.target_time = sel.value; };
    rows.appendChild(row);
  });

  $("#btn-save", card).onclick = async () => {
    const btn = $("#btn-save", card);
    btn.disabled = true; btn.innerHTML = '<span class="spin-mini"></span>Saving…';
    try {
      const r = await api("save_days", { days });
      state.dash.status = r.status; state.dash.upcoming = r.upcoming; updateMiniStatus();
      try { state.cal = await api("calendar"); } catch {}
      toast(r.ok ? "Schedule saved" : "Saved, but schedule update failed", r.ok ? "ok" : "err");
    } catch (e) { toast(String(e.message || e), "err"); }
    finally { btn.disabled = false; btn.textContent = "Save changes"; }
  };
}

// ---- dashboard ----
function renderDashboard(c) {
  const wrap = h(`<div class="fade-in">
    <div class="page-head"><h1>Dashboard</h1><p>Your tee-time booker at a glance.</p></div>
    <div class="grid-dash">
      <div class="col">
        <div class="card card-pad" id="status-card"></div>
        <div class="card card-pad" id="next-card"></div>
      </div>
      <div class="col"><div class="card card-pad" id="cal-card"></div></div>
    </div>
  </div>`);
  c.appendChild(wrap);
  renderStatusCard($("#status-card", wrap));
  renderNextCard($("#next-card", wrap));
  renderCalendar($("#cal-card", wrap));
}

function renderStatusCard(card) {
  const s = state.dash.status, lr = state.dash.last_run;
  const led = !s.registered ? "led-off" : s.paused ? "led-paused" : "led-ok";
  const name = !s.registered ? "Not installed" : s.paused ? "Paused" : "Ready";
  const sub = !s.registered ? "No schedule installed yet." : `${s.triggers} weekly trigger${s.triggers === 1 ? "" : "s"} installed`;
  card.innerHTML = `
    <div class="status-row"><span class="status-led ${led}"></span><span class="status-name">${name}</span></div>
    <div class="status-sub">${esc(sub)}</div>
    ${lastRunHTML(lr)}
    <div class="btn-row">
      <button class="btn ${s.paused ? "btn-primary" : "btn-ghost"}" id="btn-pause" ${!s.registered ? "disabled" : ""}>
        ${s.paused ? ICON.play + "Resume" : ICON.pause + "Pause"}
      </button>
      <button class="btn btn-ghost" id="btn-update">${ICON.update}Updates</button>
    </div>`;
  $("#btn-pause", card).onclick = togglePause;
  $("#btn-update", card).onclick = () => checkUpdate(false);
}

function lastRunHTML(lr) {
  if (!lr) return `<div class="lastrun"><span class="dot"></span><div><div class="lr-main">No booking run yet</div><div class="lr-sub">Runs are recorded here automatically.</div></div></div>`;
  const k = (lr.status || "").toLowerCase();
  const label = k === "ok" ? "Last run successful" : k === "fail" ? "Last run failed" : k === "crash" ? "Last run crashed" : "Last run";
  const when = lr.time_adelaide || lr.time_local || "";
  return `<div class="lastrun ${k}"><span class="dot"></span><div><div class="lr-main">${esc(label)}</div><div class="lr-sub">${esc(lr.summary || "")}${when ? " · " + esc(fmtClock(when)) : ""}</div></div></div>`;
}

function renderNextCard(card) {
  const up = state.dash.upcoming || [];
  let body = `<div class="card-title">Next bookings</div>`;
  if (!up.length || !state.dash.status.registered) {
    body += `<div class="empty">No upcoming bookings — enable a day on the Schedule tab.</div>`;
  } else {
    body += `<div class="book-list">` + up.map((f) => `
      <div class="book-item">
        <div><div class="bk-when">${esc(f.fire_pretty)}${f.one_off ? '<span class="tag-oneoff">ONE-OFF</span>' : ""}</div>
        <div class="bk-sub">Books ${esc(f.play_pretty)} at ${esc(f.target_time)}</div></div>
        <div class="bk-in">${esc(f.in_str)}</div>
      </div>`).join("") + `</div>`;
  }
  card.innerHTML = body;
}

// ---- calendar ----
function renderCalendar(card) {
  const { y, m } = state.view;
  const todayIso = state.cal.today;
  const first = new Date(y, m, 1);
  const startOff = (first.getDay() + 6) % 7;
  const daysInMonth = new Date(y, m + 1, 0).getDate();
  const rows = Math.ceil((startOff + daysInMonth) / 7);

  let cells = "";
  for (let i = 0; i < rows * 7; i++) {
    const d = new Date(y, m, 1 - startOff + i);
    const iso = ymd(d);
    const inMonth = d.getMonth() === m;
    const isToday = iso === todayIso;
    const isPast = iso < todayIso && inMonth;
    const ev = state.cal.days[iso];
    let cls = "cal-cell";
    if (!inMonth) cls += " out";
    if (isToday) cls += " today";
    if (isPast) cls += " past";
    let dot = "";
    if (ev && inMonth) {
      cls += " has-dot";
      dot = `<div class="cal-dot"><span class="d d-${ev.kind}"></span></div>`;
    }
    const title = ev && inMonth ? dayTooltip(d, ev) : "";
    cells += `<div class="${cls}" ${title ? `title="${esc(title)}"` : ""} data-iso="${iso}"><span class="cal-num">${d.getDate()}</span>${dot}</div>`;
  }

  const sync = state.dash.bookings_synced;
  card.innerHTML = `
    <div class="cal-head">
      <div class="cal-month">${MONTHS[m]} ${y}</div>
      <div class="cal-nav">
        <button class="today-btn" id="cal-today">Today</button>
        <button id="cal-prev">${ICON.chevL}</button>
        <button id="cal-next">${ICON.chevR}</button>
      </div>
    </div>
    <div class="cal-legend">
      <span><i class="lg-booked"></i>Booked</span><span><i class="lg-planned"></i>Planned</span><span><i class="lg-failed"></i>Failed</span>
    </div>
    <div class="cal-grid">${DOW.map((d) => `<div class="cal-dow">${d}</div>`).join("")}${cells}</div>
    <div class="sync-row">
      <span class="sync-txt" id="sync-txt">${syncText(sync)}</span>
      <button class="btn btn-ghost btn-sm" id="btn-refresh" style="flex:0 0 auto">${ICON.refresh}Refresh</button>
    </div>`;

  $("#cal-prev", card).onclick = () => shiftMonth(-1);
  $("#cal-next", card).onclick = () => shiftMonth(1);
  $("#cal-today", card).onclick = () => { const n = new Date(); state.view = { y: n.getFullYear(), m: n.getMonth() }; renderCalendar(card); };
  $("#btn-refresh", card).onclick = refreshBookings;
  card.querySelectorAll(".cal-cell.has-dot").forEach((cell) => cell.onclick = () => showDay(cell.dataset.iso));
}

function shiftMonth(delta) {
  let m = state.view.m + delta, y = state.view.y;
  if (m < 0) { m = 11; y--; } if (m > 11) { m = 0; y++; }
  state.view = { y, m };
  renderCalendar($("#cal-card"));
}

function dayTooltip(d, ev) {
  const pretty = d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
  if (ev.kind === "booked") return `${pretty} — Booked ${ev.time || ""}${ev.partners?.length ? " with " + ev.partners.join(", ") : ""}`;
  if (ev.kind === "planned") return `${pretty} — Planned for ${ev.time || ""}${ev.one_off ? " (one-off)" : ""}`;
  if (ev.kind === "failed") return `${pretty} — Booking failed`;
  return pretty;
}
function showDay(iso) {
  const ev = state.cal.days[iso];
  if (ev) toast(dayTooltip(new Date(iso + "T00:00:00"), ev), ev.kind === "failed" ? "err" : "ok");
}

function syncText(sync) {
  if (!sync || !sync.at) return "On-site bookings not synced yet";
  let t = sync.at; try { t = new Date(sync.at).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }); } catch {}
  return `On-site bookings synced ${t} · ${sync.count} found`;
}

// ---- actions ----
async function togglePause() {
  const btn = $("#btn-pause"); if (!btn) return;
  const target = !state.dash.status.paused;
  btn.disabled = true; btn.innerHTML = '<span class="spin-mini"></span>';
  try {
    const r = await api("set_paused", { paused: target });
    state.dash.status = r.status; updateMiniStatus();
    renderStatusCard($("#status-card"));
    toast(target ? "Schedule paused" : "Schedule resumed");
  } catch (e) { toast(String(e.message || e), "err"); btn.disabled = false; }
}

async function checkUpdate(auto) {
  const btn = $("#btn-update");
  if (btn && !auto) { btn.disabled = true; var old = btn.innerHTML; btn.innerHTML = '<span class="spin-mini"></span>Checking…'; }
  try {
    const r = await api("check_update");
    if (r.update) {
      if (auto) toast(`Update available: v${r.update.version}. Click “Updates” to install.`);
      else if (confirm(`A new version of TeeOff is available.\n\nInstalled: v${r.version}\nNew: v${r.update.version}\n\nInstall it now? The app will briefly close and reopen.`)) applyUpdate();
    } else if (!auto) {
      toast(`You're up to date (v${r.version}).`);
    }
  } catch (e) { if (!auto) toast("Couldn't check for updates", "err"); }
  finally { if (btn && !auto) { btn.disabled = false; btn.innerHTML = old; } }
}

async function applyUpdate() {
  const ov = h(`<div class="splash" style="z-index:90"><div class="brand-mark"><svg viewBox="0 0 64 64" fill="none"><path d="M20 8v44" stroke="var(--text-dim)" stroke-width="3" stroke-linecap="round"/><path d="M20 9c8 4 14-4 23 1 0 0-3 5 0 12-9-5-15 3-23-1V9Z" fill="var(--accent)"/><circle cx="40" cy="50" r="5.5" fill="#fff"/></svg></div><div class="conn"><span class="spin"></span><span>Updating… the app will reopen in a moment.</span></div></div>`);
  document.body.appendChild(ov);
  try { await api("apply_update"); }
  catch (e) { ov.remove(); toast("Update failed: " + (e.message || e), "err"); }
}

async function refreshBookings(silent) {
  const btn = $("#btn-refresh");
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spin-mini"></span>Syncing…'; }
  const sync = $("#sync-txt"); if (sync) sync.textContent = "Syncing on-site bookings…";
  try {
    const r = await api("refresh_bookings");
    state.cal = r.calendar;
    state.dash.bookings_synced = r.bookings_synced;
    renderCalendar($("#cal-card"));
    if (!silent) toast(r.ok ? `Synced · ${r.bookings_synced.count} booking(s)` : "Sync failed", r.ok ? "ok" : "err");
  } catch (e) {
    if (sync) sync.textContent = "Sync failed";
    if (!silent) toast("Couldn't sync bookings", "err");
  }
}

// ---- account ----
const DAY_CHIP = ["Mo","Tu","We","Th","Fr","Sa","Su"];

async function renderAccount(c) {
  c.appendChild(h(`<div class="page-head fade-in"><h1>Account</h1><p>Your golf-site login and playing partners.</p></div>`));
  const host = h(`<div class="fade-in" style="display:flex;flex-direction:column;gap:18px;max-width:640px"></div>`);
  c.appendChild(host);
  let s;
  try { s = await api("settings"); } catch (e) { host.appendChild(h(`<div class="card card-pad"><div class="empty">Couldn't load: ${esc(e.message)}</div></div>`)); return; }
  host.appendChild(loginCard(s));
  host.appendChild(partnersCard(s));
}

function busyBtn(btn, label) { btn.disabled = true; btn._old = btn.innerHTML; btn.innerHTML = `<span class="spin-mini"></span>${label}`; }
function freeBtn(btn) { btn.disabled = false; if (btn._old != null) btn.innerHTML = btn._old; }

function loginCard(s) {
  const cred = s.credentials || {};
  const card = h(`<div class="card card-pad form-card">
    <div class="card-title">Golf site login</div>
    <div class="card-sub">Your West Beach Parks member number and password. Stored only on this computer.</div>
    <div class="field"><label>Member number</label><input id="ac-user" value="${esc(cred.username || "")}" placeholder="your member number"></div>
    <div class="field"><label>Password</label><input id="ac-pass" type="password" placeholder="${cred.has_password ? "•••••••• (unchanged)" : "enter password"}"></div>
    <div class="foot-row"><button class="btn btn-primary" id="ac-save">Save login</button></div>
  </div>`);
  $("#ac-save", card).onclick = async () => {
    const btn = $("#ac-save", card); busyBtn(btn, "Saving…");
    try { await api("save_account", { username: $("#ac-user", card).value, password: $("#ac-pass", card).value }); $("#ac-pass", card).value = ""; toast("Login saved"); }
    catch (e) { toast(String(e.message || e), "err"); } finally { freeBtn(btn); }
  };
  return card;
}

function partnersCard(s) {
  const partners = (s.partners || []).map((p) => ({ ...p, playing_days: [...(p.playing_days || [])] }));
  const card = h(`<div class="card card-pad form-card">
    <div class="card-title">Playing partners</div>
    <div class="card-sub">Tick which days each partner joins. They're added to the booking automatically.</div>
    <div id="pt-rows"></div>
    <div class="foot-row"><button class="btn btn-primary" id="pt-save">Save partners</button><button class="btn btn-ghost" id="pt-refresh">${ICON.refresh}Sync from site</button></div>
  </div>`);
  const rows = $("#pt-rows", card);
  function renderRows() {
    rows.innerHTML = "";
    if (!partners.length) { rows.appendChild(h(`<div class="empty">No partners yet — click “Sync from site”.</div>`)); return; }
    partners.forEach((p) => {
      const row = h(`<div class="partner-row"><div class="partner-name">${esc(p.full_name)}${p.is_self ? '<span class="self">YOU</span>' : ""}</div><div class="day-chips"></div></div>`);
      const chips = $(".day-chips", row);
      DAY_KEYS.forEach((k, i) => {
        const on = p.is_self || p.playing_days.includes(k);
        const chip = h(`<div class="chip ${on ? "on" : ""} ${p.is_self ? "locked" : ""}">${DAY_CHIP[i]}</div>`);
        if (!p.is_self) chip.onclick = () => { const idx = p.playing_days.indexOf(k); if (idx >= 0) p.playing_days.splice(idx, 1); else p.playing_days.push(k); chip.classList.toggle("on"); };
        chips.appendChild(chip);
      });
      rows.appendChild(row);
    });
  }
  renderRows();
  $("#pt-save", card).onclick = async () => {
    const btn = $("#pt-save", card); busyBtn(btn, "Saving…");
    try { await api("save_partners", { partners }); toast("Partners saved"); } catch (e) { toast(String(e.message || e), "err"); } finally { freeBtn(btn); }
  };
  $("#pt-refresh", card).onclick = async () => {
    const btn = $("#pt-refresh", card); busyBtn(btn, "Syncing…");
    try {
      const r = await api("refresh_partners");
      if (r.ok) { partners.length = 0; (r.partners || []).forEach((p) => partners.push({ ...p, playing_days: [...(p.playing_days || [])] })); renderRows(); toast("Partners synced"); }
      else toast(r.error || "Sync failed", "err");
    } catch (e) { toast(String(e.message || e), "err"); } finally { freeBtn(btn); }
  };
  return card;
}

// ---- activity ----
async function renderActivity(c) {
  c.appendChild(h(`<div class="page-head fade-in"><h1>Activity</h1><p>Every booking run, plus the full log — all here, no need to dig through files.</p></div>`));
  const wrap = h(`<div class="fade-in" style="max-width:800px"></div>`); c.appendChild(wrap);
  let data, logText = "";
  try { data = await api("activity"); } catch (e) { wrap.appendChild(h(`<div class="card card-pad"><div class="empty">Couldn't load activity: ${esc(e.message)}</div></div>`)); return; }
  try { logText = (await api("booker_log")).text || ""; } catch {}
  const runs = data.runs || [];

  const list = h(`<div class="act-list"></div>`);
  if (!runs.length) list.appendChild(h(`<div class="empty">No booking runs recorded yet — they appear here automatically after the booker fires.</div>`));
  runs.forEach((r) => {
    const item = h(`<div class="act-item"><div class="act-head"><span class="act-dot ${esc(r.kind)}"></span><div class="act-main"><div class="act-title">${esc(r.title)}</div><div class="act-when">${esc(fmtClock(r.when))}</div></div></div></div>`);
    let open = false;
    item.onclick = () => { open = !open; const d = $(".act-detail", item); if (open && !d) item.appendChild(h(`<div class="act-detail">${esc(r.detail || "")}</div>`)); else if (d) d.remove(); };
    list.appendChild(item);
  });
  wrap.appendChild(list);

  if (logText) {
    wrap.appendChild(h(`<div class="card-title" style="margin:26px 0 10px">Full log</div>`));
    const log = h(`<div class="act-detail" style="max-height:360px">${esc(logText)}</div>`);
    wrap.appendChild(log);
    log.scrollTop = log.scrollHeight;
  }
}
