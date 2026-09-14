"use strict";
// Stats page: receiver health, traffic breakdowns and ground stations. Charts are plain SVG.

// Chart series colors: same hues as the rest of the page, stepped for the dark surface and checked with the
// dataviz palette validator (lightness band, CVD separation ΔE 16.3, contrast >= 3:1 on #151b23).
const SERIES = { ACARS: "#14a595", VDL2: "#9170f0" };
const REFRESH_MS = 15000;

const els = { alerts: $("alerts"), receivers: $("receivers"), timeline: $("timeline"), frequencies: $("frequencies"),
  airlines: $("airlines"), types: $("types"), stations: $("stations"), server: $("server"), stats: $("stats"),
  span: $("traffic-span"), includeLink: $("include-link"), tip: $("tip") };
let last = null;

const fmtAgo = (ts) => ts ? ago(ts) : "never";
const durationText = (secs) => secs < 3600 ? `${Math.floor(secs / 60)} min` : `${Math.floor(secs / 3600)} h ${Math.floor((secs % 3600) / 60)} min`;

// ------------------------------------------------------------------ tooltip

function bindTips(root) {
  root.querySelectorAll("[data-tip]").forEach((el) => {
    el.addEventListener("pointermove", (e) => {
      els.tip.innerHTML = el.dataset.tip;
      els.tip.hidden = false;
      const w = els.tip.offsetWidth, h = els.tip.offsetHeight;
      els.tip.style.left = `${Math.min(e.clientX + 14, window.innerWidth - w - 8)}px`;
      els.tip.style.top = `${Math.max(e.clientY - h - 10, 8)}px`;
    });
    el.addEventListener("pointerleave", () => { els.tip.hidden = true; });
  });
}

// ------------------------------------------------------------------- alerts

function renderAlerts(health) {
  const alerts = health.alerts;
  const events = (health.events || []).slice(-3).reverse();
  els.alerts.innerHTML = (alerts.length ? alerts.map((a) =>
    `<div class="alert ${a.level}"><span class="alert-icon" aria-hidden="true">${a.level === "error" ? "✕" : "⚠"}</span>
      <span class="alert-level">${a.level === "error" ? "Problem" : "Warning"}</span><span>${esc(a.text)}</span></div>`).join("")
    : `<div class="alert ok"><span class="alert-icon" aria-hidden="true">✓</span><span class="alert-level">All good</span>
       <span>Both receivers are running and decoding.</span></div>`) +
    events.map((e) => `<div class="alert info"><span class="alert-icon" aria-hidden="true">ℹ</span>
      <span class="alert-level">${clock(e.ts, false)}</span><span>${esc(e.text)}</span></div>`).join("");
}

// ----------------------------------------------------------------- receivers

function statusOf(r, alerts) {
  if (!r.enabled) return { cls: "off", icon: "–", text: "Disabled" };
  const mine = alerts.filter((a) => a.receiver === r.name);
  if (mine.some((a) => a.level === "error")) return { cls: "error", icon: "✕", text: "Not running" };
  if (mine.length) return { cls: "warn", icon: "⚠", text: "Needs attention" };
  return { cls: "ok", icon: "●", text: "Running" };
}

function tile(label, value, sub) {
  return `<div class="tile"><div class="tile-label">${label}</div><div class="tile-value">${value}</div>${sub ? `<div class="tile-sub">${sub}</div>` : ""}</div>`;
}

function ppmChart(r, tolerance) {
  const pts = r.ppm_trend;
  if (!pts || pts.length < 2) {
    return `<div class="muted small">Tuning error appears after ${num(60)} strong VDL2 frames (${num(r.ppm_samples)} so far).</div>`;
  }
  const W = 320, H = 110, P = { l: 34, r: 8, t: 8, b: 18 };
  const t0 = pts[0][0], t1 = pts[pts.length - 1][0] || t0 + 1;
  const maxAbs = Math.max(tolerance * 1.5, ...pts.map((p) => Math.abs(p[1]))) ;
  const x = (t) => P.l + (W - P.l - P.r) * ((t - t0) / Math.max(t1 - t0, 1));
  const y = (v) => P.t + (H - P.t - P.b) * (1 - (v + maxAbs) / (2 * maxAbs));
  const path = pts.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
  const band = `<rect x="${P.l}" y="${y(tolerance)}" width="${W - P.l - P.r}" height="${y(-tolerance) - y(tolerance)}" class="band"/>`;
  const hits = pts.map((p) => `<circle cx="${x(p[0])}" cy="${y(p[1])}" r="7" class="hit" data-tip="${esc(`${clock(p[0])}<br><b>${p[1] > 0 ? "+" : ""}${p[1]} ppm</b> residual`)}"/>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" class="chart" role="img" aria-label="VDL2 tuning error over time">
    ${band}
    <line x1="${P.l}" x2="${W - P.r}" y1="${y(0)}" y2="${y(0)}" class="axis"/>
    <text x="${P.l - 6}" y="${y(tolerance) + 4}" class="tick" text-anchor="end">+${tolerance}</text>
    <text x="${P.l - 6}" y="${y(0) + 4}" class="tick" text-anchor="end">0</text>
    <text x="${P.l - 6}" y="${y(-tolerance) + 4}" class="tick" text-anchor="end">−${tolerance}</text>
    <path d="${path}" fill="none" stroke="${SERIES.VDL2}" stroke-width="2" stroke-linejoin="round"/>
    <text x="${P.l}" y="${H - 4}" class="tick">${clock(t0, false)}</text>
    <text x="${W - P.r}" y="${H - 4}" class="tick" text-anchor="end">${clock(t1, false)}</text>
    ${hits}
  </svg>`;
}

function renderReceivers(health) {
  els.receivers.innerHTML = health.receivers.map((r) => {
    const st = statusOf(r, health.alerts);
    const snr = r.avg_level != null && r.avg_noise != null ? `${(r.avg_level - r.avg_noise).toFixed(1)} dB SNR` : "";
    const svc = r.service.state === "unmanaged" ? "not a systemd service" :
      r.service.state === "unknown" ? "" : `service ${r.service.state}${r.service.restarts ? ` · ${num(r.service.restarts)} restarts` : ""}`;
    const tuning = r.name === "vdl2"
      ? `<div class="rx-tuning">
          <div class="rx-tuning-head"><b>Tuning error</b>
            <span>${r.ppm_residual == null ? "measuring…" : `${r.ppm_residual > 0 ? "+" : ""}${r.ppm_residual} ppm residual`}</span>
            ${r.ppm_suggested != null && Math.abs(r.ppm_residual) >= health.ppm_tolerance ? `<span class="suggest">suggested ppm ${r.ppm_suggested}</span>` : ""}
            <span class="muted">auto-correct ${health.auto_ppm ? "on" : "off"}</span></div>
          ${ppmChart(r, health.ppm_tolerance)}
        </div>`
      : `<div class="rx-tuning muted small">Tuning error can't be measured on ACARS (acarsdec doesn't report it).</div>`;
    return `<article class="rx-card">
      <header class="rx-head">
        <h3><i class="sw ${r.source.toLowerCase()}"></i>${r.source}</h3>
        <span class="status ${st.cls}"><span aria-hidden="true">${st.icon}</span> ${st.text}</span>
      </header>
      <div class="rx-meta">Dongle <b>${esc(r.device ?? "?")}</b> · gain ${esc(r.gain ?? "auto")} · ppm ${esc(r.ppm_setting ?? 0)}
        · ${(r.frequencies || []).map((f) => Number(f).toFixed(3)).join(", ")} MHz${svc ? ` · ${esc(svc)}` : ""}</div>
      <div class="tile-row">
        ${tile("Last frame", fmtAgo(r.last_frame))}
        ${tile("Frames, 5 min", num(r.frames_5min))}
        ${tile("Frames, hour", num(r.frames_hour), `${num(r.content_hour)} with content`)}
        ${tile("Signal", r.avg_level != null ? `${r.avg_level}<small> dBFS</small>` : "—", snr)}
        ${tile("Error-corrected", r.corrected_pct != null ? `${r.corrected_pct}%` : "—", "of recent frames")}
      </div>
      ${tuning}
    </article>`;
  }).join("");
  bindTips(els.receivers);
}

// ------------------------------------------------------------------- charts

function renderTimeline(traffic) {
  const withLink = els.includeLink.checked;
  const rows = traffic.timeline.map((b) => ({
    t: b.t,
    ACARS: (b["ACARS:content"] || 0) + (withLink ? b["ACARS:link"] || 0 : 0),
    VDL2: (b["VDL2:content"] || 0) + (withLink ? b["VDL2:link"] || 0 : 0),
  }));
  const W = Math.max(els.timeline.clientWidth, 300), H = 190, P = { l: 40, r: 10, t: 10, b: 22 };
  const max = Math.max(4, ...rows.map((r) => r.ACARS + r.VDL2));
  const nice = Math.ceil(max / Math.pow(10, Math.floor(Math.log10(max)))) * Math.pow(10, Math.floor(Math.log10(max)));
  const slot = (W - P.l - P.r) / rows.length;
  const bw = Math.max(2, slot - 2); // 2px surface gap between columns
  const y = (v) => P.t + (H - P.t - P.b) * (1 - v / nice);
  const grid = [0, 0.5, 1].map((f) => `<line x1="${P.l}" x2="${W - P.r}" y1="${y(nice * f)}" y2="${y(nice * f)}" class="${f ? "grid" : "axis"}"/>
    <text x="${P.l - 6}" y="${y(nice * f) + 4}" class="tick" text-anchor="end">${num(nice * f)}</text>`).join("");
  const bars = rows.map((r, i) => {
    const x = P.l + i * slot + 1;
    const vH = y(0) - y(r.VDL2), aH = y(0) - y(r.ACARS);
    // Stack: VDL2 on the baseline, ACARS on top, 2px gap between segments; rounded data end only on the top segment
    const vdl = r.VDL2 ? `<rect x="${x}" y="${y(r.VDL2)}" width="${bw}" height="${Math.max(vH, 1)}" fill="${SERIES.VDL2}" rx="${r.ACARS ? 0 : Math.min(4, bw / 2)}"/>` : "";
    const acY = y(r.VDL2) - aH - (r.VDL2 && r.ACARS ? 2 : 0);
    const acars = r.ACARS ? `<rect x="${x}" y="${acY}" width="${bw}" height="${Math.max(aH, 1)}" fill="${SERIES.ACARS}" rx="${Math.min(4, bw / 2)}"/>` : "";
    const tip = `${clock(r.t, false)}–${clock(r.t + traffic.bucket_secs, false)}<br><i class="sw acars"></i>ACARS <b>${num(r.ACARS)}</b><br><i class="sw vdl2"></i>VDL2 <b>${num(r.VDL2)}</b>`;
    return `${vdl}${acars}<rect x="${P.l + i * slot}" y="${P.t}" width="${slot}" height="${H - P.t - P.b}" class="hit" data-tip="${esc(tip)}"/>`;
  }).join("");
  const labels = rows.filter((_, i) => i % Math.ceil(rows.length / 6) === 0)
    .map((r) => `<text x="${P.l + rows.indexOf(r) * slot}" y="${H - 6}" class="tick">${clock(r.t, false)}</text>`).join("");
  els.timeline.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" class="chart" role="img"
      aria-label="Messages per 5 minutes, ACARS and VDL2">${grid}${bars}${labels}</svg>`;
  bindTips(els.timeline);
}

function hbars(rows, { valueKey, label, color, sub, tip }) {
  if (!rows.length) return `<div class="muted small">Nothing yet.</div>`;
  const max = Math.max(...rows.map((r) => r[valueKey]), 1);
  return `<div class="hbars">${rows.map((r) => `
    <div class="hbar" data-tip="${esc(tip(r))}">
      <span class="hbar-label">${label(r)}</span>
      <span class="hbar-track"><span class="hbar-fill" style="width:${Math.max(1.5, 100 * r[valueKey] / max)}%;background:${color(r)}"></span></span>
      <span class="hbar-value">${num(r[valueKey])}${sub ? `<span class="muted"> ${sub(r)}</span>` : ""}</span>
    </div>`).join("")}</div>`;
}

function renderBreakdowns(data) {
  const t = data.traffic;
  els.frequencies.innerHTML = hbars(t.frequencies, {
    valueKey: "frames", color: (r) => SERIES[r.source],
    label: (r) => `${Number(r.freq).toFixed(3)} <span class="muted">${r.source}</span>`,
    sub: (r) => r.avg_level != null ? `· ${r.avg_level} dBFS` : "",
    tip: (r) => `${r.source} ${Number(r.freq).toFixed(3)} MHz<br><b>${num(r.frames)}</b> frames · ${num(r.content)} with content`,
  });
  bindTips(els.frequencies);
  els.airlines.innerHTML = hbars(t.airlines, {
    valueKey: "messages", color: () => "#5b8def",
    label: (r) => `<b>${esc(r.code)}</b> <span class="muted">${esc(r.name || "")}</span>`,
    tip: (r) => `${esc(r.name || r.code)}<br><b>${num(r.messages)}</b> content messages`,
  });
  bindTips(els.airlines);
  const typeRows = Object.entries(t.types).map(([key, n]) => {
    const [source] = key.split(":");
    const info = (data.typeCatalog || []).find((c) => c.key === key);
    return { key, source, n, label: info ? info.label : key };
  }).sort((a, b) => b.n - a.n).slice(0, 14);
  els.types.innerHTML = hbars(typeRows, {
    valueKey: "n", color: (r) => SERIES[r.source],
    label: (r) => `${esc(r.label)} <span class="muted">${r.source}</span>`,
    tip: (r) => `${r.source} · ${esc(r.label)}<br><b>${num(r.n)}</b> frames`,
  });
  bindTips(els.types);
}

function renderStations(gs) {
  const located = gs.located.length ? `<table class="data-table"><thead><tr><th>Station</th><th>Network</th><th>Location</th><th>VDL2</th><th>Heard</th></tr></thead><tbody>
    ${gs.located.map((s) => `<tr><td><b>${esc(s.id)}</b> <span class="muted">${esc(s.iata)}</span></td><td>${esc(s.network)}</td>
      <td>${Math.abs(s.lat).toFixed(2)}°${s.lat >= 0 ? "N" : "S"} ${Math.abs(s.lon).toFixed(2)}°${s.lon >= 0 ? "E" : "W"}</td>
      <td>${s.vdl_freq ? s.vdl_freq.toFixed(3) : "—"}</td><td>${num(s.count)}× · ${fmtAgo(s.last_seen)}</td></tr>`).join("")}</tbody></table>`
    : `<div class="muted small">No ground station squitters heard yet. Ground stations broadcast their location occasionally; they appear here and on the map when heard.</div>`;
  const vdl = gs.vdl2.slice(0, 12);
  const addrs = vdl.length ? `<table class="data-table"><thead><tr><th>VDL2 station address</th><th>Frames</th><th>Aircraft</th><th>Last</th></tr></thead><tbody>
    ${vdl.map((g) => `<tr><td class="mono">${esc(g.address)}</td><td>${num(g.frames)}</td><td>${num(g.aircraft)}</td><td>${fmtAgo(g.last_seen)}</td></tr>`).join("")}
    </tbody></table>${gs.vdl2.length > vdl.length ? `<div class="muted small">…and ${num(gs.vdl2.length - vdl.length)} more</div>` : ""}` : "";
  els.stations.innerHTML = `<h4 class="sub-title">Located (from squitters)</h4>${located}
    <h4 class="sub-title">VDL2 stations aircraft are talking to</h4>${addrs || `<div class="muted small">None yet.</div>`}`;
}

function renderServer(s) {
  els.server.innerHTML = tile("Memory", s.memory_mb != null ? `${s.memory_mb} MB` : "—", "web server process") +
    tile("Messages in memory", num(s.messages_in_memory), `last ${last.history_hours} h`) +
    tile("Aircraft tracked", num(s.aircraft_tracked)) +
    tile("Uptime", durationText(s.uptime)) +
    tile("VRS", !s.vrs_configured ? "not configured" : s.vrs_ok ? `${num(s.vrs_aircraft)} aircraft` : "offline");
}

async function refresh() {
  try {
    const [full, types] = await Promise.all([
      fetch("/api/stats/full").then((r) => r.json()),
      last?.typeCatalog ? Promise.resolve(null) : fetch("/api/types").then((r) => r.json()),
    ]);
    full.typeCatalog = types ? types.types : last?.typeCatalog;
    last = full;
    $("conn").classList.add("up");
    els.span.textContent = `· last ${full.history_hours} h`;
    renderAlerts(full.health);
    renderReceivers(full.health);
    renderTimeline(full.traffic);
    renderBreakdowns(full);
    renderStations(full.ground_stations);
    renderServer(full.server);
    const s = full.server, rx = Object.fromEntries(full.health.receivers.map((r) => [r.source, r]));
    renderStats(els.stats, {
      last_hour: { VDL2: rx.VDL2?.frames_hour }, last_hour_content: { ACARS: rx.ACARS?.content_hour, VDL2: rx.VDL2?.content_hour },
      aircraft: s.aircraft_tracked, vrs_ok: s.vrs_ok, vrs_configured: s.vrs_configured, vrs_aircraft: s.vrs_aircraft,
      health_alerts: full.health.alerts,
    });
  } catch {
    $("conn").classList.remove("up");
  }
}

els.includeLink.addEventListener("change", () => last && renderTimeline(last.traffic));
window.addEventListener("resize", () => last && renderTimeline(last.traffic));
refresh();
setInterval(refresh, REFRESH_MS);
