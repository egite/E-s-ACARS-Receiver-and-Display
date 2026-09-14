"use strict";
// Raw feed page: every decoded frame as it arrives, with the original JSON on demand.

const MAX_MESSAGES = 2000;
const MAX_SHOWN = 500;

const state = { messages: [], paused: false, pending: 0, rawCache: new Map() };
const els = {
  stream: $("stream"), stats: $("stats"), conn: $("conn"), pause: $("pause"), pending: $("pending"),
  typesBtn: $("types-btn"), fText: $("f-text"), fEnglish: $("f-english"),
};

const types = new TypeFilter({ prefKey: "raw.types", button: els.typesBtn, onChange: () => renderAll() });

els.fEnglish.checked = loadPref("raw.showTranslation", true);
document.body.classList.toggle("hide-english", !els.fEnglish.checked);
els.fEnglish.addEventListener("change", () => {
  savePref("raw.showTranslation", els.fEnglish.checked);
  document.body.classList.toggle("hide-english", !els.fEnglish.checked);
});

const isLink = (m) => ["link", "partial", "xid"].includes(m.kind);

function matches(m) {
  if (!types.isOn(m.type)) return false;
  const q = els.fText.value.trim().toUpperCase();
  return !q || [m.flight, m.reg, m.icao, m.label, m.text, m.gs, m.english?.summary].join(" ").toUpperCase().includes(q);
}

function renderMessage(m, fresh) {
  const li = document.createElement("li");
  li.className = `msg src-${m.source}${isLink(m) ? " dim" : ""}${fresh ? " fresh" : ""}`;
  li.dataset.id = m.id;
  const who = m.flight || m.reg || m.icao
    ? `<span class="who">${esc(m.flight || "")} <span class="reg">${esc(m.reg || "")} ${esc(m.icao || "")}</span></span>` : "";
  const typeInfo = types.byKey.get(m.type);
  const kind = typeInfo ? `<span class="badge kind ${m.kind}" title="${esc(typeInfo.description)}">${esc(typeInfo.label)}</span>` : "";
  const label = m.label
    ? `<span class="lbl" title="${esc(labelTitle(m.label, m.sublabel))}">${esc(m.label)}${m.sublabel ? "/" + esc(m.sublabel) : ""}</span>` : "";
  const dir = m.direction === "up" ? `<span class="badge kind">UPLINK</span>` : "";
  const gs = m.gs ? `<span class="freq">GS ${esc(m.gs)}</span>` : "";
  const sig = m.level != null
    ? `<span class="sig">${Number(m.level).toFixed(1)} / ${m.noise != null ? Number(m.noise).toFixed(1) : "?"} dBFS${m.errors ? ` · ${m.errors} corrected` : ""}</span>` : "";
  li.innerHTML =
    `<div class="msg-head"><time>${clock(m.ts)}</time><span class="badge ${m.source}">${m.source}</span>` +
    `<span class="freq">${m.freq != null ? Number(m.freq).toFixed(3) : ""}</span>${who}${label}${kind}${dir}${gs}${sig}</div>` +
    (m.text ? `<pre class="text">${esc(m.text)}</pre>` : "") +
    (m.english ? `<div class="english">${esc(localizeTimes(m.english.summary, m.ts))}` +
      (m.english.details.length ? ` <span class="english-details">— ${esc(localizeTimes(m.english.details.join(" · "), m.ts))}</span>` : "") +
      `</div>` : "");
  return li;
}

function renderAll() {
  const frag = document.createDocumentFragment();
  let shown = 0;
  for (let i = state.messages.length - 1; i >= 0 && shown < MAX_SHOWN; i--) {
    if (!matches(state.messages[i])) continue;
    frag.appendChild(renderMessage(state.messages[i], false));
    shown++;
  }
  els.stream.replaceChildren(frag);
  if (!shown) els.stream.innerHTML = `<li class="empty">No messages match the current filters.</li>`;
}

function addMessage(m) {
  state.messages.push(m);
  if (state.messages.length > MAX_MESSAGES) state.messages.splice(0, state.messages.length - MAX_MESSAGES);
  if (!matches(m)) return;
  if (state.paused) {
    state.pending++;
    els.pending.textContent = `${num(state.pending)} new while paused`;
    return;
  }
  els.stream.querySelector(".empty")?.remove();
  els.stream.prepend(renderMessage(m, true));
  while (els.stream.children.length > MAX_SHOWN) els.stream.lastElementChild.remove();
}

els.stream.addEventListener("click", async (e) => {
  const li = e.target.closest(".msg");
  if (!li) return;
  const existing = li.querySelector(".json");
  if (existing) { existing.remove(); li.classList.remove("expanded"); return; }
  li.classList.add("expanded");
  const id = Number(li.dataset.id);
  if (!state.rawCache.has(id)) {
    try {
      const resp = await fetch(`/api/message/${id}`);
      state.rawCache.set(id, resp.ok ? await resp.json() : null);
    } catch {
      state.rawCache.set(id, null);
    }
  }
  const full = state.rawCache.get(id);
  const pre = document.createElement("pre");
  pre.className = "json";
  pre.textContent = full?.raw ? JSON.stringify(full.raw, null, 2)
    : full ? "Original JSON is not stored for link-layer frames." : "Message not found.";
  li.appendChild(pre);
});

els.pause.addEventListener("click", () => {
  state.paused = !state.paused;
  els.pause.textContent = state.paused ? "Resume" : "Pause";
  els.pause.classList.toggle("active", state.paused);
  if (!state.paused) {
    state.pending = 0;
    els.pending.textContent = "";
    renderAll();
  }
});

connectSocket("/ws?messages=1", els.conn, (data) => {
  if (data.type === "hello") {
    types.setTypes(data.message_types);
    state.messages = data.messages;
    renderAll();
    renderStats(els.stats, data.stats);
  } else if (data.type === "message") {
    addMessage(data.message);
  } else if (data.type === "update") {
    // A stored message was re-filed (report blocks joined or superseded); refresh it if it's on screen.
    const i = state.messages.findIndex((m) => m.id === data.message.id);
    if (i >= 0) {
      state.messages[i] = data.message;
      state.rawCache.delete(data.message.id);
      if (!state.paused) queueRenderAll();
    }
  } else if (data.type === "aircraft") {
    renderStats(els.stats, data.stats);
  }
});

let renderAllQueued = false;
function queueRenderAll() {
  if (renderAllQueued) return;
  renderAllQueued = true;
  setTimeout(() => { renderAllQueued = false; renderAll(); }, 250);
}

let textTimer;
els.fText.addEventListener("input", () => { clearTimeout(textTimer); textTimer = setTimeout(renderAll, 150); });
