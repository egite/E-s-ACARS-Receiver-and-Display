"use strict";
// Watch list, keyword and emergency-squawk alerts for the flights page. Settings live in this browser.

const EMERGENCY_SQUAWKS = { "7500": "hijack", "7600": "radio failure", "7700": "emergency" };
const ALERT_LOG_SIZE = 100;
const EMERGENCY_REPEAT_SECS = 30 * 60;

function emergencyOf(ac) {
  const v = ac?.vrs || {};
  if (EMERGENCY_SQUAWKS[v.Sqk]) return { squawk: v.Sqk, meaning: EMERGENCY_SQUAWKS[v.Sqk] };
  if (v.Help) return { squawk: v.Sqk || "", meaning: "emergency" };
  return null;
}

class AlertManager {
  constructor({ bell, onSelect }) {
    this.bell = bell;
    this.onSelect = onSelect;
    this.prefs = { watch: [], keywords: [], atcRequests: false, maintenance: false, emergency: true, sound: false,
      ...loadPref("alerts.prefs", {}) };
    this.log = [];
    this.unread = 0;
    this.seenMessages = new Set();
    this.seenEmergency = new Map(); // aircraft key|squawk -> time alerted

    this.panel = document.createElement("div");
    this.panel.className = "alert-panel";
    this.panel.hidden = true;
    document.body.appendChild(this.panel);
    this.toast = document.createElement("div");
    this.toast.className = "toast-stack";
    document.body.appendChild(this.toast);
    this.dialog = document.createElement("dialog");
    this.dialog.className = "types-dialog alerts-dialog";
    document.body.appendChild(this.dialog);

    bell.addEventListener("click", (e) => { e.stopPropagation(); this.togglePanel(); });
    document.addEventListener("click", (e) => {
      if (!this.panel.hidden && !this.panel.contains(e.target)) this.panel.hidden = true;
    });
    this.panel.addEventListener("click", (e) => {
      const item = e.target.closest("[data-key]");
      if (e.target.closest("[data-action=settings]")) { this.panel.hidden = true; this.openSettings(); return; }
      if (e.target.closest("[data-action=clear]")) { this.log = []; this.renderPanel(); return; }
      if (item) { this.panel.hidden = true; this.onSelect(item.dataset.key); }
    });
    this.toast.addEventListener("click", (e) => {
      const item = e.target.closest("[data-key]");
      if (item) { item.remove(); this.onSelect(item.dataset.key); }
    });
    this.dialog.addEventListener("click", (e) => {
      if (e.target === this.dialog || e.target.closest("[data-action=close]")) this.dialog.close();
      if (e.target.closest("[data-action=save]")) this.saveSettings();
    });
    this.updateBell();
  }

  // ------------------------------------------------------------ matching

  watchMatch(ac, msg) {
    const names = [ac?.callsign, ac?.flight, ac?.reg, ac?.icao, msg?.flight, msg?.reg, msg?.icao]
      .filter(Boolean).map((s) => s.toUpperCase().replace(/[^A-Z0-9]/g, ""));
    for (const raw of this.prefs.watch) {
      const w = raw.toUpperCase().replace(/[^A-Z0-9*]/g, "");
      if (!w) continue;
      const hit = w.endsWith("*") ? names.some((n) => n.startsWith(w.slice(0, -1))) : names.includes(w);
      if (hit) return raw;
    }
    return null;
  }

  keywordMatch(msg) {
    const hay = [msg.english?.summary, ...(msg.english?.details || []), msg.text].join(" ").toUpperCase();
    return this.prefs.keywords.find((k) => k.trim() && hay.includes(k.trim().toUpperCase())) || null;
  }

  isWatched(ac) {
    return !!this.watchMatch(ac, null);
  }

  // Called for every live message the page receives.
  checkMessage(msg, ac) {
    if (!msg || this.seenMessages.has(msg.id) || msg.english?.category === "link") return;
    this.seenMessages.add(msg.id);
    if (this.seenMessages.size > 5000) this.seenMessages = new Set([...this.seenMessages].slice(-2000));
    const reasons = [];
    const watched = this.watchMatch(ac, msg);
    if (watched) reasons.push(`watch list: ${watched}`);
    const keyword = this.keywordMatch(msg);
    if (keyword) reasons.push(`keyword “${keyword.trim()}”`);
    if (this.prefs.atcRequests && /^Crew request\/report to ATC/.test(msg.english?.summary || "")) reasons.push("crew request to ATC");
    if (this.prefs.maintenance && msg.english?.category === "maintenance") reasons.push("maintenance alert");
    if (reasons.length) {
      this.raise({ key: msg.key || ac?.key, level: "info", who: displayNameOf(ac, msg), reason: reasons.join(" · "),
        text: localizeTimes(msg.english?.summary || msg.text || "", msg.ts), ts: msg.ts });
    }
  }

  // Called whenever aircraft data refreshes (squawks come from VRS).
  checkAircraft(list) {
    if (!this.prefs.emergency) return;
    const now = Date.now() / 1000;
    for (const ac of list) {
      const em = emergencyOf(ac);
      if (!em) continue;
      const id = `${ac.key}|${em.squawk}`;
      if (now - (this.seenEmergency.get(id) || 0) < EMERGENCY_REPEAT_SECS) continue;
      this.seenEmergency.set(id, now);
      this.raise({ key: ac.key, level: "emergency", who: displayNameOf(ac), ts: now,
        reason: `squawk ${em.squawk}`, text: `${displayNameOf(ac)} is squawking ${em.squawk} (${em.meaning})` });
    }
  }

  raise(alert) {
    this.log.unshift(alert);
    this.log.length = Math.min(this.log.length, ALERT_LOG_SIZE);
    this.unread++;
    this.updateBell();
    if (!this.panel.hidden) this.renderPanel();
    const el = document.createElement("div");
    el.className = `toast ${alert.level}`;
    el.dataset.key = alert.key || "";
    el.innerHTML = `<b>${alert.level === "emergency" ? "🚨 " : ""}${esc(alert.who)}</b> <span class="muted">${esc(alert.reason)}</span><div>${esc(alert.text)}</div>`;
    this.toast.prepend(el);
    setTimeout(() => el.remove(), alert.level === "emergency" ? 20000 : 8000);
    while (this.toast.children.length > 3) this.toast.lastElementChild.remove();
    if (this.prefs.sound) this.beep(alert.level === "emergency");
  }

  beep(urgent) {
    try {
      this.audio ??= new AudioContext();
      const tones = urgent ? [880, 660, 880] : [740];
      tones.forEach((f, i) => {
        const osc = this.audio.createOscillator(), gain = this.audio.createGain();
        osc.frequency.value = f;
        gain.gain.value = 0.08;
        osc.connect(gain).connect(this.audio.destination);
        const t = this.audio.currentTime + i * 0.18;
        osc.start(t);
        osc.stop(t + 0.14);
      });
    } catch { /* audio not available */ }
  }

  // --------------------------------------------------------------- UI

  updateBell() {
    const active = this.prefs.watch.length || this.prefs.keywords.length || this.prefs.atcRequests || this.prefs.maintenance || this.prefs.emergency;
    this.bell.innerHTML = `<span aria-hidden="true">🔔</span><span class="bell-text">Alerts</span>` +
      (this.unread ? `<span class="bell-count">${num(this.unread)}</span>` : "");
    this.bell.classList.toggle("muted-bell", !active);
    this.bell.title = active ? "Alerts" : "No alerts set up";
  }

  togglePanel() {
    this.panel.hidden = !this.panel.hidden;
    if (!this.panel.hidden) {
      this.unread = 0;
      this.updateBell();
      this.renderPanel();
    }
  }

  renderPanel() {
    const p = this.prefs;
    const summary = [p.watch.length ? `${p.watch.length} watched` : null, p.keywords.length ? `${p.keywords.length} keywords` : null,
      p.emergency ? "emergency squawks" : null, p.atcRequests ? "ATC requests" : null, p.maintenance ? "maintenance" : null]
      .filter(Boolean).join(" · ") || "nothing set up";
    this.panel.innerHTML = `<div class="alert-panel-head"><b>Alerts</b><span class="muted small">${esc(summary)}</span>
        <button type="button" class="btn" data-action="settings">Settings</button></div>
      <ol class="alert-items">${this.log.length ? this.log.map((a) => `
        <li class="alert-item ${a.level}" data-key="${esc(a.key || "")}">
          <time>${clock(a.ts, false)}</time>
          <div><b>${a.level === "emergency" ? "🚨 " : ""}${esc(a.who)}</b> <span class="muted">${esc(a.reason)}</span><div>${esc(a.text)}</div></div>
        </li>`).join("") : `<li class="empty">No alerts yet.</li>`}</ol>
      ${this.log.length ? `<button type="button" class="link-btn clear-btn" data-action="clear">Clear list</button>` : ""}`;
  }

  openSettings() {
    const p = this.prefs;
    const check = (id, on, label, desc) => `<label class="type-row"><input type="checkbox" id="${id}"${on ? " checked" : ""}>
      <span class="type-text"><span class="type-label">${label}</span><span class="type-desc">${desc}</span></span><span></span></label>`;
    this.dialog.innerHTML = `<div class="types-head"><div><h2>Alert settings</h2>
        <p class="muted">Alerts appear on this page while it's open, and are saved in this browser.</p></div>
        <button type="button" class="close-btn" data-action="close" aria-label="Close">×</button></div>
      <div class="alert-settings">
        <label class="field"><span class="type-label">Watch list</span>
          <span class="type-desc">One per line: callsign (UAL1234), flight (UA1234), registration (N12345), ICAO address (A1B2C3), or a prefix ending in * (UAL*, N8*).</span>
          <textarea id="al-watch" rows="5" spellcheck="false">${esc(p.watch.join("\n"))}</textarea></label>
        <label class="field"><span class="type-label">Keywords</span>
          <span class="type-desc">One per line; matched in the English translation and the original text (e.g. DIVERT, MEDICAL, TURB).</span>
          <textarea id="al-keywords" rows="5" spellcheck="false">${esc(p.keywords.join("\n"))}</textarea></label>
        ${check("al-emergency", p.emergency, "Emergency squawks", "Any aircraft squawking 7500 (hijack), 7600 (radio failure) or 7700 (emergency), from VRS")}
        ${check("al-atc", p.atcRequests, "Crew requests to ATC", "CPDLC requests such as climbs, descents and direct routings")}
        ${check("al-maint", p.maintenance, "Maintenance alerts", "Fault and maintenance messages")}
        ${check("al-sound", p.sound, "Play a sound", "A short tone for each alert (three tones for emergencies)")}
      </div>
      <div class="types-foot"><button type="button" class="btn" data-action="close">Cancel</button>
        <button type="button" class="btn primary" data-action="save">Save</button></div>`;
    this.dialog.showModal();
  }

  saveSettings() {
    const lines = (id) => $(id).value.split("\n").map((s) => s.trim()).filter(Boolean);
    this.prefs = { watch: lines("al-watch"), keywords: lines("al-keywords"), emergency: $("al-emergency").checked,
      atcRequests: $("al-atc").checked, maintenance: $("al-maint").checked, sound: $("al-sound").checked };
    savePref("alerts.prefs", this.prefs);
    if (this.prefs.sound) this.beep(false); // also unlocks audio, which browsers require to start from a click
    this.dialog.close();
    this.updateBell();
    this.onSelect(null, true);
  }
}

function displayNameOf(ac, msg) {
  return ac?.callsign || ac?.flight || ac?.reg || msg?.flight || msg?.reg || msg?.icao || "Unknown aircraft";
}
