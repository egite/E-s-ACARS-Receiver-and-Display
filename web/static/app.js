"use strict";
// Flights page: map + flight cards with plain-English message timelines.

const TRAIL_POINTS = 400;
const CARD_TIMELINE_ENTRIES = 4;
const REPORTED_POS_MAX_AGE = 30 * 60; // seconds
const LABEL_MIN_ZOOM = 8;

const state = {
  aircraft: new Map(),   // key -> snapshot
  traffic: [],
  selected: null,
  history: new Map(),    // key -> messages (newest first)
  trails: new Map(),     // key -> [[lat, lon], ...]
  flash: new Map(),      // key -> time of last new message
  openOriginals: new Set(), // message ids with original text expanded
};

const els = {
  cards: $("cards"), stats: $("stats"), conn: $("conn"), cardCount: $("card-count"),
  typesBtn: $("types-btn"), fText: $("f-text"),
  fTraffic: $("f-traffic"), sort: $("card-sort"), fTime: $("f-time"),
  detail: $("detail"), detailHead: $("detail-head"), detailClose: $("detail-close"), history: $("history"),
  fStations: $("f-stations"), fWinds: $("f-winds"), windsLegend: $("winds-legend"), emergency: $("emergency"),
  mapPanel: $("map-panel"), detailMap: $("detail-map"), peek: $("peek"), tabbar: $("tabbar"), tabCount: $("tab-count"),
};

// Phones and narrow tablets get a tabbed layout (Flights | Map) and a full-screen flight window.
const narrowScreen = window.matchMedia("(max-width: 900px)");
const isNarrow = () => narrowScreen.matches;

const alerts = new AlertManager({
  bell: $("alerts-btn"),
  onSelect: (key, settingsChanged) => {
    if (settingsChanged) { renderCards(); renderMap(); }
    else if (key && state.aircraft.has(key)) select(key, true);
  },
});

const types = new TypeFilter({
  prefKey: "flights.types", button: els.typesBtn,
  onChange: () => { renderCards(); renderMap(); },
});

// Remember display choices in this browser. The time filter and search deliberately start fresh on every load.
const SAVED_CHECKBOXES = { fTraffic: "flights.traffic", fStations: "flights.stations" };
for (const [el, key] of Object.entries(SAVED_CHECKBOXES)) {
  els[el].checked = loadPref(key, els[el].checked);
  els[el].addEventListener("change", () => savePref(key, els[el].checked));
}
{
  const winds = loadPref("flights.winds", "off");
  if ([...els.fWinds.options].some((o) => o.value === winds)) els.fWinds.value = winds;
  const sort = loadPref("flights.sort", els.sort.value);
  if ([...els.sort.options].some((o) => o.value === sort)) els.sort.value = sort;
  els.sort.addEventListener("change", () => savePref("flights.sort", els.sort.value));
}

// Oldest message time to show (0 = no limit).
function timeCutoff() {
  const secs = Number(els.fTime.value);
  return secs ? Date.now() / 1000 - secs : 0;
}

// The server only keeps history_hours of messages, so longer windows would be the same as "All".
function limitTimeOptions(timeoutMins) {
  for (const opt of [...els.fTime.options]) {
    if (Number(opt.value) >= timeoutMins * 60) opt.remove();
  }
  const span = timeoutMins >= 120 ? `${timeoutMins / 60} h` : `${timeoutMins} min`;
  els.fTime.options[0].dataset.long = `All messages (${span})`;
  els.fTime.options[0].dataset.short = `All (${span})`;
  applyOptionLabels();
}

// Dropdowns use shorter option text on narrow screens so the selected value isn't cut off.
function applyOptionLabels() {
  for (const opt of document.querySelectorAll("option[data-short]")) {
    opt.dataset.long ??= opt.textContent;
    opt.textContent = isNarrow() ? opt.dataset.short : opt.dataset.long;
  }
}

// Fold consecutive entries with the same English summary into one line with a count.
function collapseRepeats(entries) {
  const out = [];
  for (const e of entries) {
    const prev = out[out.length - 1];
    if (prev && prev.summary === e.summary && prev.category === e.category) {
      prev.count = (prev.count || 1) + (e.count || 1);
      prev.ids.push(e.id);
    } else {
      out.push({ ...e, ids: [e.id] });
    }
  }
  return out;
}

function airportCode(s) {
  return s ? s.split(" ")[0] : "";
}
function acSource(ac) {
  const a = ac.counts?.ACARS || 0, v = ac.counts?.VDL2 || 0;
  return a && v ? "both" : a ? "ACARS" : v ? "VDL2" : "";
}
function displayName(ac) {
  return ac.callsign || ac.flight || ac.reg || ac.icao || ac.key;
}

// ---------------------------------------------------------------------- map

const map = L.map("map", { zoomControl: false, worldCopyJump: true }).setView([40.45, -105.01], 7);
L.control.zoom({ position: "topright" }).addTo(map); // the flight window covers the top left
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 18,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);

const trafficLayer = L.layerGroup().addTo(map);
const trackedLayer = L.layerGroup().addTo(map);
const trailLayer = L.layerGroup().addTo(map);
const markers = new Map();
let homeMarker = null;

const COLORS = { ACARS: "#2dd4bf", VDL2: "#a78bfa", both: "#fbbf24", traffic: "#6b7686", emergency: "#ef4444" };
const PLANE_PATH = "M12 1.5c.8 0 1.3.9 1.3 2.2v5.6l8.2 4.9v2.2l-8.2-2.5v5l2.4 1.8v1.8L12 21.6l-3.7.9v-1.8l2.4-1.8v-5l-8.2 2.5v-2.2l8.2-4.9V3.7c0-1.3.5-2.2 1.3-2.2z";

function planeIcon(color, heading, size, selected, emergency) {
  return L.divIcon({
    className: "plane" + (selected ? " selected" : "") + (emergency ? " emergency" : ""),
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
    html: `<svg width="${size}" height="${size}" viewBox="0 0 24 24" style="transform:rotate(${heading || 0}deg)">` +
      `<path d="${PLANE_PATH}" fill="${color}" stroke="#0d1117" stroke-width="1"/></svg>`,
  });
}

function popupHtml(ac) {
  const v = ac.vrs || {};
  const p = ac.position;
  const latest = ac.recent?.find((r) => types.isOn(r.type));
  return `<b>${esc(displayName(ac))}</b> ${esc(ac.reg || "")}<br>` +
    (v.Mdl || v.Type ? `${esc(v.Mdl || v.Type)}<br>` : "") +
    (v.Op ? `${esc(v.Op)}<br>` : "") +
    (v.From || v.To ? `${esc(airportCode(v.From))} → ${esc(airportCode(v.To))}<br>` : "") +
    (p ? `${p.alt != null ? num(p.alt) + " ft · " : ""}${esc(p.src)}${p.src !== "ADS-B (VRS)" ? " " + ago(p.ts) : ""}<br>` : "") +
    (latest ? `<i>${esc(latest.summary)}</i>` : "");
}

function renderMap() {
  const seen = new Set();
  trackedLayer.clearLayers();
  markers.clear();
  const now = Date.now() / 1000;

  for (const ac of state.aircraft.values()) {
    const p = ac.position;
    if (!p || !isVisibleCard(ac)) continue;
    const reported = p.src !== "ADS-B (VRS)";
    if (reported && now - p.ts > REPORTED_POS_MAX_AGE) continue;
    const emergency = emergencyOf(ac);
    const color = emergency ? COLORS.emergency : COLORS[acSource(ac)] || COLORS.traffic;
    const sel = ac.key === state.selected;
    const marker = reported
      ? L.circleMarker([p.lat, p.lon], { radius: sel ? 9 : 7, color, weight: 2, dashArray: "3 3", fillColor: color, fillOpacity: 0.15 })
      : L.marker([p.lat, p.lon], { icon: planeIcon(color, p.trak, sel || emergency ? 28 : 20, sel, emergency), zIndexOffset: emergency ? 2000 : sel ? 1000 : 500 });
    // Permanent callsign labels only when zoomed in (or selected), so busy airspace stays readable.
    const permanent = sel || map.getZoom() >= LABEL_MIN_ZOOM;
    const tag = emergency ? ` · ${emergency.squawk} ${emergency.meaning}` : alerts.isWatched(ac) ? " ★" : "";
    marker.bindTooltip(esc(displayName(ac) + tag), { permanent: permanent || !!emergency, direction: "right", offset: [10, 0],
      className: "ac-label" + (emergency ? " emergency" : "") });
    if (!isNarrow()) marker.bindPopup(() => popupHtml(ac));
    marker.on("click", () => select(ac.key, false));
    marker.addTo(trackedLayer);
    markers.set(ac.key, marker);
    if (ac.icao) seen.add(ac.icao);
  }

  trafficLayer.clearLayers();
  if (els.fTraffic.checked) {
    for (const t of state.traffic) {
      if (seen.has(t.icao) || t.gnd) continue;
      const emergency = EMERGENCY_SQUAWKS[t.sqk] || (t.help ? "emergency" : null);
      L.marker([t.lat, t.lon], { icon: planeIcon(emergency ? COLORS.emergency : COLORS.traffic, t.trak, emergency ? 26 : 14, false, !!emergency),
          keyboard: false, zIndexOffset: emergency ? 2000 : 0 })
        .bindTooltip(`${esc(t.call || t.icao)}${t.type ? " · " + esc(t.type) : ""}${t.alt != null ? " · " + num(t.alt) + " ft" : ""}` +
          (emergency ? ` · squawk ${esc(t.sqk || "")} ${emergency}` : ""), { permanent: !!emergency, className: emergency ? "ac-label emergency" : "" })
        .addTo(trafficLayer);
    }
  }
  renderTrail();
}

function recordTrails() {
  for (const ac of state.aircraft.values()) {
    const p = ac.position;
    if (!p) continue;
    const trail = state.trails.get(ac.key) || [];
    const last = trail[trail.length - 1];
    if (!last || last[0] !== p.lat || last[1] !== p.lon) {
      trail.push([p.lat, p.lon]);
      if (trail.length > TRAIL_POINTS) trail.shift();
      state.trails.set(ac.key, trail);
    }
  }
}

function renderTrail() {
  trailLayer.clearLayers();
  const trail = state.selected && state.trails.get(state.selected);
  if (trail && trail.length > 1) L.polyline(trail, { color: "#60a5fa", weight: 2, opacity: 0.8 }).addTo(trailLayer);
}

els.fTraffic.addEventListener("change", renderMap);
map.on("zoomend", renderMap);

// -------------------------------------------------------------- flight cards

// Time of this aircraft's latest message among the types currently selected (0 if none).
function lastShownTs(ac) {
  let latest = 0;
  for (const [type, ts] of Object.entries(ac.type_last || {})) if (types.isOn(type) && ts > latest) latest = ts;
  return latest;
}

function isVisibleCard(ac) {
  const last = lastShownTs(ac);
  if (!last || last < timeCutoff()) return false;
  const q = els.fText.value.trim().toUpperCase();
  if (q) {
    const v = ac.vrs || {};
    const hay = [ac.callsign, ac.flight, ac.reg, ac.icao, v.Type, v.Mdl, v.Op, v.From, v.To,
      ...ac.recent.map((r) => r.summary)].join(" ").toUpperCase();
    if (!hay.includes(q)) return false;
  }
  return true;
}

function timelineEntry(e, original) {
  const count = e.count > 1 ? ` <span class="times">×${e.count}</span>` : "";
  const details = e.details?.length ? `<div class="tl-details">${esc(localizeTimes(e.details.join(" · "), e.ts))}</div>` : "";
  return `<li class="tl cat-${e.category}" data-id="${e.id}">
    <time>${clock(e.ts, false)}</time>
    <span class="cat">${CATEGORY_NAMES[e.category] || e.category}</span>
    <div class="tl-body"><div class="tl-summary">${esc(localizeTimes(e.summary, e.ts))}${count}</div>${details}${original || ""}</div>
  </li>`;
}

// Callsign, registration, type/operator and route/altitude lines shared by cards and the flight window.
function flightHeaderHtml(ac) {
  const v = ac.vrs || {};
  const alt = v.Gnd ? "On ground" : v.Alt != null ? (v.Alt >= 18000 ? `FL${Math.round(v.Alt / 100)}` : `${num(v.Alt)} ft`) : null;
  const kin = [alt, v.Spd != null ? `${Math.round(v.Spd)} kt` : null,
    v.Vsi ? `${v.Vsi > 0 ? "↑" : "↓"}${num(Math.abs(v.Vsi))} fpm` : null, v.Sqk ? `squawk ${v.Sqk}` : null].filter(Boolean).join(" · ");
  const typeLine = [v.Mdl || v.Type, v.Op].filter(Boolean).join(" · ");
  const route = v.From || v.To ? `${esc(airportCode(v.From) || "?")} → ${esc(airportCode(v.To) || "?")}` : "";
  const flightAlias = ac.callsign && ac.flight && ac.callsign !== ac.flight ? `<span class="reg">${esc(ac.flight)}</span>` : "";
  const emergency = emergencyOf(ac);
  const flags = (emergency ? `<span class="sq-badge">🚨 Squawk ${esc(emergency.squawk)} · ${esc(emergency.meaning)}</span>` : "") +
    (alerts.isWatched(ac) ? `<span class="watch-star" title="On your watch list">★</span>` : "");
  return `${flags ? `<div class="card-flags">${flags}</div>` : ""}<div class="card-top"><span class="callsign">${esc(displayName(ac))}</span>${flightAlias}
      <span class="reg">${esc(ac.reg || "")}</span>
      <span class="counts">${ac.counts.ACARS ? `<span class="a">ACARS ${num(ac.counts.ACARS)}</span>` : ""}${ac.counts.VDL2 ? `<span class="v">VDL2 ${num(ac.counts.VDL2)}</span>` : ""}</span></div>
    ${typeLine ? `<div class="meta">${esc(typeLine)}</div>` : ""}
    ${route || kin ? `<div class="route">${route ? `<span>${route}</span>` : ""}${route && kin ? '<span class="sep"></span>' : ""}${kin ? `<span class="kin">${esc(kin)}</span>` : ""}</div>` : ""}`;
}

function positionText(ac) {
  if (!ac.position) return "no position";
  return ac.position.src === "ADS-B (VRS)" ? "ADS-B position" : `position ${ago(ac.position.ts)} from ${ac.position.src}`;
}

function cardHtml(ac) {
  const selected = ac.key === state.selected;
  const cutoff = timeCutoff();
  const entries = collapseRepeats(ac.recent.filter((r) => types.isOn(r.type) && r.ts >= cutoff)).slice(0, CARD_TIMELINE_ENTRIES);
  const fresh = Date.now() - (state.flash.get(ac.key) || 0) < 2500;
  const extra = (emergencyOf(ac) ? " emergency" : "") + (alerts.isWatched(ac) ? " watched" : "");
  return `<article class="card ${acSource(ac)}${selected ? " selected" : ""}${fresh ? " fresh" : ""}${extra}" data-key="${esc(ac.key)}">
    ${flightHeaderHtml(ac)}
    ${entries.length ? `<ol class="timeline">${entries.map((e) => timelineEntry(e)).join("")}</ol>` : ""}
    <div class="card-foot"><span>last message ${ago(lastShownTs(ac))}</span><span>${esc(positionText(ac))}</span></div>
  </article>`;
}

function sortedCards() {
  const list = [...state.aircraft.values()].filter(isVisibleCard);
  const total = (a) => (a.counts.ACARS || 0) + (a.counts.VDL2 || 0);
  const by = els.sort.value;
  list.sort((a, b) =>
    by === "count" ? total(b) - total(a) :
    by === "callsign" ? displayName(a).localeCompare(displayName(b)) :
    lastShownTs(b) - lastShownTs(a));
  return list;
}

// While a flight is selected the list holds still: cards keep the order they had when it was selected,
// flights that appear afterwards are added above them, and the scroll position follows the selected card.
function freezeOrder(list) {
  state.frozen = { rank: new Map(list.map((a, i) => [a.key, i])), top: 0 };
}

function orderedCards() {
  const list = sortedCards();
  const frozen = state.selected && state.frozen;
  if (!frozen) return list;
  for (const a of list) if (!frozen.rank.has(a.key)) frozen.rank.set(a.key, --frozen.top); // new arrival: on top
  return list.sort((a, b) => frozen.rank.get(a.key) - frozen.rank.get(b.key));
}

function cardTop(key) {
  const card = key && els.cards.querySelector(`.card[data-key="${CSS.escape(key)}"]`);
  return card ? card.getBoundingClientRect().top : null;
}

function renderCards() {
  const list = orderedCards();
  els.cardCount.textContent = list.length ? `(${num(list.length)})` : "";
  els.tabCount.textContent = list.length ? num(list.length) : "";
  const before = state.selected ? cardTop(state.selected) : null;
  setHtml(els.cards, list.length ? list.map(cardHtml).join("") :
    `<div class="empty">No flights with messages match.</div>`);
  const after = before === null ? null : cardTop(state.selected);
  if (after !== null) els.cards.scrollTop += after - before;
  renderDetail();
}

// Replace an element's contents only when they changed, so buttons aren't swapped out from under the pointer.
function setHtml(el, html) {
  if (el._html !== html) {
    el.innerHTML = html;
    el._html = html;
  }
}

// Live updates can rebuild the element between mouse down and up, so a native "click" never fires.
// Instead, match the press and release by a stable attribute (card key, message id).
function onPress(container, selector, attr, handler) {
  let down = null;
  container.addEventListener("pointerdown", (e) => {
    const el = e.button === 0 && e.target.closest(selector);
    down = el ? { value: el.getAttribute(attr), x: e.clientX, y: e.clientY } : null;
  });
  container.addEventListener("pointerup", (e) => {
    const el = e.target.closest(selector);
    if (down && el && el.getAttribute(attr) === down.value &&
        Math.hypot(e.clientX - down.x, e.clientY - down.y) < 10) {
      handler(down.value, e);
    }
    down = null;
  });
  // Keyboard activation (Enter/Space on a focused button) still arrives as a click with no pointer.
  container.addEventListener("click", (e) => {
    const el = e.target.closest(selector);
    if (el && e.detail === 0) handler(el.getAttribute(attr), e);
  });
}

// ------------------------------------------------------------- flight window

function renderDetail() {
  const ac = state.selected && state.aircraft.get(state.selected);
  // On a phone the window covers everything, so "Show on map" tucks it into a bar at the bottom of the map.
  const peeking = !!ac && state.peek && isNarrow();
  els.detail.hidden = !ac || peeking;
  els.peek.hidden = !peeking;
  if (peeking) {
    const latest = ac.recent.find((r) => types.isOn(r.type));
    els.peek.innerHTML = `<b>${esc(displayName(ac))}</b><span>${esc(latest ? localizeTimes(latest.summary, latest.ts) : positionText(ac))}</span><i>Open</i>`;
  }
  if (!ac) return;
  setHtml(els.detailHead,
    `<div class="detail-flight card ${acSource(ac)}">${flightHeaderHtml(ac)}</div>
     <div class="card-foot"><span>first seen ${clock(ac.first_seen, false)} · last message ${ago(lastShownTs(ac))}</span>
       <span>${esc(positionText(ac))}</span></div>`);
  // New messages are added at the top; keep the reader's place if they have scrolled down.
  const hist = els.history;
  const top = hist.scrollTop, height = hist.scrollHeight;
  renderHistory(hist);
  if (top > 0) hist.scrollTop = top + (hist.scrollHeight - height);
}

function originalHtml(m) {
  const head = [m.source, m.freq != null ? `${Number(m.freq).toFixed(3)} MHz` : null,
    m.label ? `label ${m.label}${m.sublabel ? "/" + m.sublabel : ""}` : null,
    m.level != null ? `${Number(m.level).toFixed(0)} dBFS` : null].filter(Boolean).join(" · ");
  return `<div class="original"><div class="orig-head">${esc(head)}</div>${m.text ? `<pre>${esc(m.text)}</pre>` : ""}</div>`;
}

function renderHistory(el) {
  const msgs = state.history.get(state.selected);
  if (!msgs) { setHtml(el, `<li class="empty">Loading…</li>`); return; }
  const cutoff = timeCutoff();
  const byId = new Map(msgs.map((m) => [m.id, m]));
  const shown = msgs.filter((m) => m.english && types.isOn(m.type) && m.ts >= cutoff);
  const entries = collapseRepeats(shown.slice(0, 300).map((m) =>
    ({ id: m.id, ts: m.ts, category: m.english.category, summary: m.english.summary, details: m.english.details })));
  setHtml(el, entries.map((e) => {
    // A folded group shows its most recent original; the toggle is keyed by that message id.
    const open = state.openOriginals.has(e.id);
    const originals = open ? e.ids.slice(0, 5).map((id) => originalHtml(byId.get(id))).join("") +
      (e.ids.length > 5 ? `<div class="orig-head">…and ${num(e.ids.length - 5)} more</div>` : "") : "";
    const label = e.ids.length > 1 ? `${open ? "Hide" : "Show"} originals` : `${open ? "Hide" : "Show"} original`;
    return timelineEntry(e, originals + `<button class="link-btn" data-toggle="${e.id}">${label}</button>`);
  }).join("") || `<li class="empty">No messages in this time range.</li>`);
}

onPress(els.cards, ".card", "data-key", (key) => select(key === state.selected ? null : key, true));

els.detailClose.addEventListener("click", () => select(null));
els.detailMap.addEventListener("click", () => {
  state.peek = true;
  setView("map");
  renderDetail();
});
els.peek.addEventListener("click", () => {
  state.peek = false;
  renderDetail();
});

function setView(view) {
  state.view = view;
  savePref("flights.view", view);
  document.body.dataset.view = view;
  for (const b of els.tabbar.querySelectorAll("[data-view]")) b.classList.toggle("active", b.dataset.view === view);
  if (view === "map") {
    map.invalidateSize(); // Leaflet needs this after its container was hidden
    panToSelected();
  }
}
els.tabbar.addEventListener("click", (e) => {
  const b = e.target.closest("[data-view]");
  if (b) setView(b.dataset.view);
});
narrowScreen.addEventListener("change", () => {
  applyOptionLabels();
  map.invalidateSize();
  renderMap();
  renderDetail();
});

onPress(els.history, "[data-toggle]", "data-toggle", (value) => {
  const id = Number(value);
  state.openOriginals.has(id) ? state.openOriginals.delete(id) : state.openOriginals.add(id);
  const scroll = els.history.scrollTop;
  renderHistory(els.history);
  els.history.scrollTop = scroll;
});
document.addEventListener("keydown", (e) => {
  // Esc inside the Message types dialog only closes the dialog.
  if (e.key === "Escape" && state.selected && !document.querySelector("dialog[open]")) select(null);
});
// Scrolling or clicking inside the window must not zoom or pan the map underneath.
L.DomEvent.disableClickPropagation(els.detail);
L.DomEvent.disableScrollPropagation(els.detail);
els.sort.addEventListener("change", () => {
  if (state.selected) freezeOrder(sortedCards()); // a new sort order is an explicit request to reorder
  renderCards();
});

async function loadHistory(key) {
  try {
    const resp = await fetch(`/api/aircraft/${encodeURIComponent(key)}/messages`);
    state.history.set(key, await resp.json());
  } catch {
    state.history.set(key, []);
  }
  if (state.selected === key) renderDetail();
}

function select(key, panTo) {
  if (key !== state.selected) {
    state.openOriginals.clear();
    els.history.scrollTop = 0;
  }
  if (!key) state.frozen = null;
  else if (!state.selected) freezeOrder([...els.cards.querySelectorAll(".card")].map((c) => ({ key: c.dataset.key })));
  state.selected = key;
  state.peek = false;
  if (key) loadHistory(key);
  renderCards();
  renderMap();
  if (key && panTo) panToSelected();
}

function panToSelected() {
  const marker = state.selected && markers.get(state.selected);
  if (!marker || (isNarrow() && state.view !== "map")) return;
  // Centre the aircraft in the part of the map not covered by the flight window.
  const covered = !els.detail.hidden && els.detail.offsetWidth < els.mapPanel.offsetWidth ? els.detail.offsetWidth + 10 : 0;
  const point = map.project(marker.getLatLng()).subtract([covered / 2, 0]);
  map.panTo(map.unproject(point));
}

// ---------------------------------------------------------------- live data

function setAircraft(list) {
  state.aircraft = new Map(list.map((a) => [a.key, a]));
  recordTrails();
  for (const key of state.trails.keys()) if (!state.aircraft.has(key)) state.trails.delete(key);
  if (state.selected && !state.aircraft.has(state.selected)) state.selected = null;
}

let renderQueued = false;
function queueRender() {
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(() => { renderQueued = false; renderCards(); });
}

connectSocket("/ws", els.conn, (data) => {
  if (data.type === "hello") {
    types.setTypes(data.message_types);
    if (data.history_mins && !state.timeLimited) {
      limitTimeOptions(data.history_mins);
      state.timeLimited = true;
    }
    setAircraft(data.aircraft);
    state.traffic = data.traffic;
    if (!homeMarker && data.home) {
      homeMarker = L.circleMarker([data.home.lat, data.home.lon], { radius: 5, color: "#fff", weight: 2, fillColor: "#60a5fa", fillOpacity: 1 })
        .bindTooltip(esc(data.home.name || "Receiver")).addTo(map);
      map.setView([data.home.lat, data.home.lon], 7);
    }
    if (state.selected) loadHistory(state.selected);
    alerts.checkAircraft(data.aircraft);
    renderCards(); renderMap(); renderStats(els.stats, data.stats); renderEmergencies();
  } else if (data.type === "message") {
    const ac = data.aircraft;
    if (!ac) return;
    state.aircraft.set(ac.key, ac);
    const m = data.message;
    alerts.checkMessage(m, ac);
    if (state.history.has(ac.key)) state.history.get(ac.key).unshift(m);
    if (types.isOn(m.type)) {
      state.flash.set(ac.key, Date.now());
      queueRender();
    }
  } else if (data.type === "update") {
    // A stored message was re-filed (e.g. a report block joined into its report, or superseded)
    const m = data.message;
    if (data.aircraft) state.aircraft.set(data.aircraft.key, data.aircraft);
    const history = m.key && state.history.get(m.key);
    if (history) {
      const i = history.findIndex((h) => h.id === m.id);
      if (i >= 0) history[i] = m;
    }
    queueRender();
  } else if (data.type === "aircraft") {
    setAircraft(data.aircraft);
    state.traffic = data.traffic;
    alerts.checkAircraft(data.aircraft);
    renderCards(); renderMap(); renderStats(els.stats, data.stats); renderEmergencies();
  }
});

for (const el of [els.fTime]) el.addEventListener("change", () => { renderCards(); renderMap(); });
let textTimer;
els.fText.addEventListener("input", () => { clearTimeout(textTimer); textTimer = setTimeout(() => { renderCards(); renderMap(); }, 150); });

applyOptionLabels();
setView(loadPref("flights.view", "flights"));

// ------------------------------------------------------------ emergencies

// Header notice for any aircraft squawking an emergency code (tracked flights and all ADS-B traffic).
function renderEmergencies() {
  const list = [];
  for (const ac of state.aircraft.values()) {
    const em = emergencyOf(ac);
    if (em) list.push({ key: ac.key, name: displayName(ac), ...em, lat: ac.position?.lat, lon: ac.position?.lon });
  }
  const tracked = new Set(list.map((e) => e.name));
  for (const t of state.traffic) {
    const meaning = EMERGENCY_SQUAWKS[t.sqk] || (t.help ? "emergency" : null);
    if (meaning && !tracked.has(t.call || t.icao)) list.push({ name: t.call || t.icao, squawk: t.sqk || "", meaning, lat: t.lat, lon: t.lon });
  }
  els.emergency.hidden = !list.length;
  els.emergency.innerHTML = list.map((e, i) => `<button type="button" class="emergency-pill" data-i="${i}">🚨 ${esc(e.name)} squawking ${esc(e.squawk)} (${esc(e.meaning)})</button>`).join("");
  els.emergency.onclick = (ev) => {
    const e = list[ev.target.closest("[data-i]")?.dataset.i];
    if (!e) return;
    if (e.key) select(e.key, true);
    else if (e.lat != null) { setView("map"); map.setView([e.lat, e.lon], Math.max(map.getZoom(), 8)); }
  };
}

// -------------------------------------------------------- ground stations

const stationLayer = L.layerGroup();
const TOWER_SVG = `<svg width="22" height="22" viewBox="0 0 24 24"><path d="M12 3l-5 18h2.4l1-3.8h3.2l1 3.8H17L12 3zm-1.1 11.6L12 10l1.1 4.6h-2.2z" fill="#fbbf24" stroke="#0d1117" stroke-width="1"/>
  <path d="M6.5 6.5a7.8 7.8 0 000 7.1M17.5 6.5a7.8 7.8 0 010 7.1" fill="none" stroke="#fbbf24" stroke-width="1.6" stroke-linecap="round"/></svg>`;

async function refreshStations() {
  if (!els.fStations.checked) { stationLayer.remove(); return; }
  try {
    const data = await fetch("/api/ground-stations").then((r) => r.json());
    stationLayer.clearLayers();
    for (const s of data.located) {
      L.marker([s.lat, s.lon], { icon: L.divIcon({ className: "tower", html: TOWER_SVG, iconSize: [22, 22], iconAnchor: [11, 20] }), zIndexOffset: -100 })
        .bindTooltip(`<b>${esc(s.id)}</b> ${esc(s.network)} ground station${s.vdl_freq ? ` · VDL2 ${s.vdl_freq.toFixed(3)} MHz` : ""}<br>heard ${num(s.count)}× · last ${ago(s.last_seen)}`)
        .addTo(stationLayer);
    }
    stationLayer.addTo(map);
  } catch { /* try again next time */ }
}
els.fStations.addEventListener("change", refreshStations);
refreshStations();
setInterval(refreshStations, 60000);

// ------------------------------------------------------------ winds aloft

const windLayer = L.layerGroup();
const WIND_BANDS = { low: [0, 10000], mid: [10000, 25000], high: [25000, 99999], all: [0, 99999] };
const windColor = (alt) => alt < 10000 ? "#0e7fa3" : alt < 25000 ? "#29b6d9" : "#9be7f5";

// Standard wind barb: the staff points toward where the wind comes from; pennant = 50 kt, barb = 10 kt, half = 5 kt.
function windBarbSvg(speed, color) {
  if (speed < 3) return `<svg width="34" height="34" viewBox="0 0 34 34"><circle cx="17" cy="17" r="4" fill="none" stroke="${color}" stroke-width="2"/></svg>`;
  let s = Math.round(speed / 5) * 5, y = 2, parts = "";
  while (s >= 50) { parts += `<path d="M17 ${y}l8 3-8 3z" fill="${color}"/>`; y += 7; s -= 50; }
  while (s >= 10) { parts += `<line x1="17" y1="${y}" x2="25" y2="${y - 3}" stroke="${color}" stroke-width="2"/>`; y += 4; s -= 10; }
  if (s >= 5) { parts += `<line x1="17" y1="${y + (y === 2 ? 3 : 0)}" x2="21" y2="${y + (y === 2 ? 3 : 0) - 1.5}" stroke="${color}" stroke-width="2"/>`; }
  return `<svg width="34" height="34" viewBox="0 0 34 34" style="overflow:visible">
    <line x1="17" y1="17" x2="17" y2="2" stroke="#0d1117" stroke-width="4" stroke-linecap="round"/>
    <line x1="17" y1="17" x2="17" y2="2" stroke="${color}" stroke-width="2" stroke-linecap="round"/>${parts}
    <circle cx="17" cy="17" r="2.5" fill="${color}" stroke="#0d1117" stroke-width="1"/></svg>`;
}

async function refreshWinds() {
  const band = els.fWinds.value;
  savePref("flights.winds", band);
  els.windsLegend.hidden = band === "off";
  if (band === "off") { windLayer.remove(); return; }
  try {
    const obs = await fetch("/api/winds?minutes=60").then((r) => r.json());
    const [lo, hi] = WIND_BANDS[band];
    // Newest observation per ~0.25° cell and altitude band keeps the map readable.
    const cells = new Map();
    for (const o of obs) {
      if (o.alt < lo || o.alt >= hi) continue;
      const cell = `${Math.round(o.lat * 4)}|${Math.round(o.lon * 4)}|${o.alt < 10000 ? 0 : o.alt < 25000 ? 1 : 2}`;
      if (!cells.has(cell) || cells.get(cell).ts < o.ts) cells.set(cell, o);
    }
    windLayer.clearLayers();
    for (const o of cells.values()) {
      const icon = L.divIcon({ className: "wind-barb", iconSize: [34, 34], iconAnchor: [17, 17],
        html: `<div style="transform:rotate(${o.wdir}deg)">${windBarbSvg(o.wspd, windColor(o.alt))}</div>` });
      L.marker([o.lat, o.lon], { icon, keyboard: false, zIndexOffset: -200 })
        .bindTooltip(`${o.alt >= 18000 ? `FL${Math.round(o.alt / 100)}` : `${num(o.alt)} ft`} · wind ${String(o.wdir).padStart(3, "0")}° at ${num(o.wspd)} kt · ${Math.round(o.temp)}°C<br>${clock(o.ts, false)} · ${esc(o.flight || "")}`)
        .addTo(windLayer);
    }
    windLayer.addTo(map);
  } catch { /* try again next time */ }
}
els.fWinds.addEventListener("change", refreshWinds);
refreshWinds();
setInterval(refreshWinds, 30000);
