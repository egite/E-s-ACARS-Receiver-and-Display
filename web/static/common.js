"use strict";
// Shared helpers for the flights page (app.js) and the raw feed page (raw.js).

const LABELS = {
  "H1": "Message to/from onboard avionics (FMS, ACMS, CMU)",
  "H1/DF": "Aircraft condition monitoring data (engine/airframe health)",
  "H1/M1": "Flight management computer report",
  "_d": "General response / acknowledgement, no text",
  "Q0": "Link test",
  "SQ": "Ground station squitter",
  "SA": "Media advisory (link status)",
  "5V": "VDL switch advisory",
  "B9": "ATIS request",
  "5U": "Weather request",
  "5Z": "Airline-designated downlink",
  "QP": "OUT: left the gate",
  "QQ": "OFF: took off",
  "QR": "ON: landed",
  "QS": "IN: arrived at the gate",
};

const CATEGORY_NAMES = {
  atc: "ATC", position: "Position", flight: "Flight", request: "Request", crew: "Crew",
  maintenance: "Maint", data: "Data", encoded: "Encoded", link: "Link", other: "Other",
};

const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const pad = (n) => String(n).padStart(2, "0");
function clock(ts, seconds = true) {
  const d = new Date(ts * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}` + (seconds ? `:${pad(d.getSeconds())}` : "");
}
function ago(ts) {
  if (!ts) return "—";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m ago`;
}
const num = (n) => Number(n || 0).toLocaleString();

// Translations quote aircraft-reported times as "HH:MM[:SS] UTC"; show them in local time like the rest of the page.
function localizeTimes(text, ts) {
  return String(text ?? "").replace(/\b(\d{2}):(\d{2})(?::(\d{2}))? UTC\b/g, (_, h, mi, s) => {
    const ref = new Date(ts * 1000);
    let t = Date.UTC(ref.getUTCFullYear(), ref.getUTCMonth(), ref.getUTCDate(), +h, +mi, s ? +s : 0);
    const diff = t - ts * 1000;
    if (diff > 12 * 3600e3) t -= 86400e3;
    else if (diff < -12 * 3600e3) t += 86400e3;
    return clock(t / 1000, s !== undefined);
  });
}

function loadPref(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : JSON.parse(v);
  } catch {
    return fallback;
  }
}
function savePref(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage unavailable */ }
}
function labelTitle(label, sublabel) {
  return LABELS[`${label}/${sublabel}`] || LABELS[label] || "Airline-defined / other";
}

function renderStats(el, s) {
  if (!s) return;
  const h = s.last_hour || {}, c = s.last_hour_content || {};
  el.innerHTML =
    `<span>ACARS <b>${num(c.ACARS)}</b>/h</span>` +
    `<span>VDL2 <b>${num(c.VDL2)}</b>/h <span title="including link traffic">(${num(h.VDL2)} frames)</span></span>` +
    `<span>Tracked <b>${num(s.aircraft)}</b></span>` +
    (!s.vrs_configured ? "" : s.vrs_ok ? `<span>VRS <b>${num(s.vrs_aircraft)}</b> aircraft</span>`
              : `<span class="bad" title="${esc(s.vrs_error || "")}">VRS offline</span>`) +
    healthPill(s.health_alerts);
}

// Receiver problems (silent dongle, stopped decoder, tuning drift) show on every page and link to the stats page.
function healthPill(alerts) {
  if (!alerts || !alerts.length) return "";
  const worst = alerts.some((a) => a.level === "error") ? "error" : "warn";
  const text = alerts.length === 1 ? alerts[0].text : `${alerts.length} receiver alerts`;
  return `<a class="health-pill ${worst}" href="/stats" title="${esc(alerts.map((a) => a.text).join("\n"))}">
    <span aria-hidden="true">${worst === "error" ? "✕" : "⚠"}</span> ${esc(text)}</a>`;
}

// "Message types" dialog shared by both pages: a checkbox per ACARS / VDL2 message type (from the server's
// catalog), remembered per page. Changes apply immediately.
const LINK_TYPES_START = "linktest"; // catalog entries from here on are link-level housekeeping

class TypeFilter {
  constructor({ prefKey, button, onChange }) {
    this.prefKey = prefKey;
    this.button = button;
    this.onChange = onChange;
    this.types = [];
    this.byKey = new Map();
    this.saved = loadPref(prefKey, {});
    this.dialog = document.createElement("dialog");
    this.dialog.className = "types-dialog";
    document.body.appendChild(this.dialog);
    button.addEventListener("click", () => this.open());
    this.dialog.addEventListener("change", (e) => {
      const key = e.target.dataset.key;
      if (key) this.set({ [key]: e.target.checked });
    });
    this.dialog.addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (e.target === this.dialog || b?.dataset.action === "close") { this.dialog.close(); return; }
      if (!b) return;
      if (b.dataset.action === "defaults") {
        this.saved = {};
        this.set({});
      } else if (b.dataset.all || b.dataset.none) {
        const source = b.dataset.all || b.dataset.none;
        this.set(Object.fromEntries(this.types.filter((t) => t.source === source).map((t) => [t.key, !!b.dataset.all])));
      }
    });
  }

  setTypes(types) {
    this.types = types;
    this.byKey = new Map(types.map((t) => [t.key, t]));
    this.updateButton();
  }

  isOn(key) {
    if (key in this.saved) return this.saved[key];
    const t = this.byKey.get(key);
    return t ? t.default : true;
  }

  set(changes) {
    Object.assign(this.saved, changes);
    // Only remember choices that differ from the defaults, so new types added later get their default.
    for (const [key, on] of Object.entries(this.saved)) if (this.byKey.get(key)?.default === on) delete this.saved[key];
    savePref(this.prefKey, this.saved);
    this.updateButton();
    this.syncCheckboxes();
    this.onChange();
  }

  updateButton() {
    const on = this.types.filter((t) => this.isOn(t.key)).length;
    const isDefault = Object.keys(this.saved).length === 0;
    this.button.innerHTML = `Message types <span class="btn-count">${isDefault ? "default" : `${on} of ${this.types.length}`}</span>`;
  }

  syncCheckboxes() {
    for (const box of this.dialog.querySelectorAll("input[data-key]")) box.checked = this.isOn(box.dataset.key);
  }

  async open() {
    this.render({}, null);
    this.dialog.showModal();
    try {
      const resp = await fetch("/api/types");
      const data = await resp.json();
      this.render(data.counts, data.history_hours);
    } catch { /* counts are optional */ }
  }

  render(counts, hours) {
    const column = (source) => {
      const rows = this.types.filter((t) => t.source === source);
      const total = rows.reduce((n, t) => n + (counts[t.key] || 0), 0);
      return `<section class="types-col">
        <div class="types-col-head">
          <h3 class="${source.toLowerCase()}">${source} <span class="muted">${hours ? num(total) : ""}</span></h3>
          <span class="types-bulk"><button type="button" class="link-btn" data-all="${source}">All</button> ·
            <button type="button" class="link-btn" data-none="${source}">None</button></span>
        </div>
        ${rows.map((t) => `${t.type === LINK_TYPES_START ? `<div class="types-sub">Link &amp; housekeeping</div>` : ""}
          <label class="type-row">
            <input type="checkbox" data-key="${esc(t.key)}"${this.isOn(t.key) ? " checked" : ""}>
            <span class="type-text"><span class="type-label">${esc(t.label)}</span><span class="type-desc">${esc(t.description)}</span></span>
            <span class="type-count">${hours ? num(counts[t.key] || 0) : ""}</span>
          </label>`).join("")}
      </section>`;
    };
    this.dialog.innerHTML = `<div class="types-head">
        <div><h2>Message types</h2>
          <p class="muted">Choose which kinds of messages to show.${hours ? ` Counts cover the last ${hours} h in memory.` : ""}</p></div>
        <button type="button" class="close-btn" data-action="close" aria-label="Close">×</button>
      </div>
      <div class="types-cols">${column("ACARS")}${column("VDL2")}</div>
      <div class="types-foot">
        <button type="button" class="btn" data-action="defaults">Reset to defaults</button>
        <button type="button" class="btn primary" data-action="close">Done</button>
      </div>`;
  }
}

// WebSocket with automatic reconnect; onData receives parsed objects.
function connectSocket(path, connEl, onData) {
  let retry = 1000;
  const open = () => {
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`);
    ws.onopen = () => { retry = 1000; connEl.classList.add("up"); connEl.title = "Connected"; };
    ws.onclose = () => {
      connEl.classList.remove("up"); connEl.title = "Disconnected, retrying";
      setTimeout(open, retry);
      retry = Math.min(retry * 2, 30000);
    };
    ws.onmessage = (ev) => onData(JSON.parse(ev.data));
  };
  open();
}
