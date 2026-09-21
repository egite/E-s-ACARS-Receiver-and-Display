"use strict";
// Settings page: edits the browser-safe part of config.json through /api/settings.
// Fields are named by their dotted path in the settings object (e.g. "receivers.acars.ppm").

const RX = { acars: { label: "ACARS", decoders: ["acarsdec", "xng"] }, vdl2: { label: "VDL2", decoders: ["dumpvdl2", "xng"] } };
const form = $("form"), statusEl = $("status"), saveBtn = $("save"), revertBtn = $("revert");
let loaded = null;

function receiverCard(name) {
  const p = `receivers.${name}`, rx = RX[name];
  return `<article class="rx-card">
    <header class="rx-head">
      <h3><i class="sw ${name}"></i>${rx.label}</h3>
      <label class="chip ${name}"><input type="checkbox" name="${p}.enabled"> Enabled</label>
    </header>
    <div class="form-grid">
      <label class="field"><span>Decoder</span><select name="${p}.decoder">${rx.decoders.map((d) => `<option>${d}</option>`).join("")}</select></label>
      <label class="field"><span>Dongle</span><input name="${p}.device" autocapitalize="off" spellcheck="false"><small>serial number or index</small></label>
      <label class="field"><span>Gain</span><input name="${p}.gain" inputmode="decimal"><small>dB, or "auto"</small></label>
      <label class="field"><span>ppm correction</span><input name="${p}.ppm" inputmode="numeric"><small>whole ppm</small></label>
      <label class="field wide"><span>Frequencies</span><textarea name="${p}.frequencies" rows="2" inputmode="decimal" spellcheck="false"></textarea><small>MHz, separated by commas or spaces</small></label>
    </div>
  </article>`;
}

// ------------------------------------------------------------ form <-> object

const get = (obj, path) => path.split(".").reduce((o, k) => o?.[k], obj);
function set(obj, path, value) {
  const keys = path.split(".");
  keys.slice(0, -1).reduce((o, k) => (o[k] ??= {}), obj)[keys.at(-1)] = value;
}
const fields = () => [...form.querySelectorAll("[name]")];

function fill(s) {
  for (const el of fields()) {
    const v = get(s, el.name);
    if (el.type === "checkbox") el.checked = !!v;
    else if (Array.isArray(v)) el.value = v.map((f) => Number(f).toFixed(3)).join(", ");
    else el.value = v ?? "";
  }
  clearErrors();
}

function read() {
  const s = {};
  for (const el of fields()) set(s, el.name, el.type === "checkbox" ? el.checked : el.value.trim());
  return s;
}

const snapshot = () => JSON.stringify(fields().map((el) => (el.type === "checkbox" ? el.checked : el.value.trim())));
let clean = "";

function refreshDirty() {
  const dirty = loaded && snapshot() !== clean;
  saveBtn.disabled = revertBtn.disabled = !dirty;
  if (dirty) setStatus("Unsaved changes", "warn");
  else if (statusEl.dataset.kind === "warn") setStatus("", "");
}

function setStatus(text, kind) {
  statusEl.textContent = text;
  statusEl.dataset.kind = kind;
  statusEl.className = kind ? `status-${kind}` : "muted";
}

function clearErrors() {
  form.querySelectorAll(".field-error").forEach((e) => e.remove());
  form.querySelectorAll(".invalid").forEach((e) => e.classList.remove("invalid"));
}

function showErrors(errors) {
  clearErrors();
  let first = null;
  for (const [path, text] of Object.entries(errors)) {
    const el = form.querySelector(`[name="${path}"]`);
    const field = el?.closest(".field");
    if (!field) continue;
    el.classList.add("invalid");
    field.insertAdjacentHTML("beforeend", `<small class="field-error">${esc(text)}</small>`);
    first ??= el;
  }
  first?.focus();
}

// ------------------------------------------------------------------- server

async function load() {
  const r = await fetch("/api/settings");
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const data = await r.json();
  loaded = data.settings;
  $("config-path").textContent = data.config_path;
  fill(loaded);
  clean = snapshot();
  if (statusEl.textContent === "Loading…") setStatus("", "");
  refreshDirty();
}

async function save() {
  saveBtn.disabled = true;
  setStatus("Saving…", "");
  try {
    const r = await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(read()) });
    const data = await r.json();
    if (r.status === 422) {
      showErrors(data.errors);
      setStatus(`Not saved: ${Object.keys(data.errors).length} setting${Object.keys(data.errors).length > 1 ? "s" : ""} to fix`, "error");
      saveBtn.disabled = false;
      return;
    }
    if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
    await load();
    const failed = Object.entries(data.failed);
    if (failed.length) {
      setStatus(`Saved, but ${failed.map(([n, why]) => `${RX[n].label} didn't restart (${why})`).join("; ")}`, "error");
    } else if (!data.saved) {
      setStatus("Nothing changed", "ok");
    } else {
      const rs = data.restarted.map((n) => RX[n].label);
      setStatus(`Saved${rs.length ? ` · restarted ${rs.join(" and ")}` : ""} · ${clock(Date.now() / 1000, false)}`, "ok");
    }
  } catch (e) {
    setStatus(`Save failed: ${e.message}`, "error");
    saveBtn.disabled = false;
  }
}

$("receivers").innerHTML = Object.keys(RX).map(receiverCard).join("");
form.addEventListener("input", refreshDirty);
form.addEventListener("change", refreshDirty);
form.addEventListener("submit", (e) => { e.preventDefault(); if (!saveBtn.disabled) save(); });
form.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.tagName === "INPUT") { e.preventDefault(); if (!saveBtn.disabled) save(); } });
saveBtn.addEventListener("click", save);
revertBtn.addEventListener("click", () => { fill(loaded); refreshDirty(); setStatus("", ""); });
window.addEventListener("beforeunload", (e) => { if (!saveBtn.disabled) e.preventDefault(); });
load().catch((e) => setStatus(`Couldn't load settings: ${e.message}`, "error"));
