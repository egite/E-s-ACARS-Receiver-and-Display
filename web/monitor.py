"""Receiver health, ground stations and aircraft weather observations for the web display."""
import asyncio
import json
import logging
import re
import shutil
import statistics
import time
from collections import Counter, deque
from pathlib import Path

from airlines import airline_name

log = logging.getLogger("acarsweb")

SOURCES = {"acars": "ACARS", "vdl2": "VDL2"}  # config receiver name -> message source
DEFAULT_SILENCE_MINS = {"acars": 30, "vdl2": 5}
PPM_MIN_SAMPLES = 60       # VDL2 frames needed before judging the tuning error
PPM_MIN_SNR_DB = 10        # ignore weak frames; their frequency estimate is noisy
AUTO_PPM_HOLD_SECS = 1800  # drift must persist this long before auto-correcting
AUTO_PPM_COOLDOWN_SECS = 3600


def health_settings(cfg):
    h = cfg.get("health") or {}
    return {
        "silence_mins": {**DEFAULT_SILENCE_MINS, **(h.get("silence_minutes") or {})},
        "ppm_tolerance": h.get("ppm_tolerance", 3),
        "auto_ppm": bool(h.get("auto_ppm", False)),
    }


class ReceiverStats:
    """Rolling per-receiver numbers: frame rate, signal, errors and (VDL2) frequency error."""

    def __init__(self, name):
        self.name = name
        self.frames = deque()          # (ts, level, noise, errors, is_content)
        self.skew = deque(maxlen=400)  # (ts, residual ppm) from dumpvdl2 freq_skew
        self.last_frame = None
        self.service = {"state": "unknown"}
        self.restarts_seen = deque()   # (ts, NRestarts) samples
        self.drift_since = None

    def note(self, msg, raw):
        now = time.time()
        self.last_frame = now
        self.frames.append((now, msg.get("level"), msg.get("noise"), msg.get("errors") or 0,
                            msg.get("kind") not in ("link", "partial", "xid")))
        if msg["source"] == "VDL2":
            v = (raw or {}).get("vdl2") or {}
            skew, sig, noise = v.get("freq_skew"), v.get("sig_level"), v.get("noise_level")
            if skew is not None and sig is not None and noise is not None and sig - noise >= PPM_MIN_SNR_DB:
                self.skew.append((now, skew))

    def trim(self, now, keep_secs):
        while self.frames and self.frames[0][0] < now - keep_secs:
            self.frames.popleft()

    def ppm_residual(self):
        values = [s for _, s in self.skew]
        return round(statistics.median(values), 1) if len(values) >= PPM_MIN_SAMPLES else None

    def report(self, now, rx_cfg, settings):
        hour = [f for f in self.frames if f[0] >= now - 3600]
        recent = hour[-300:]
        levels = [f[1] for f in recent if f[1] is not None]
        noises = [f[2] for f in recent if f[2] is not None]
        residual = self.ppm_residual() if self.name == "vdl2" else None
        return {
            "name": self.name, "source": SOURCES[self.name],
            "enabled": bool(rx_cfg and rx_cfg.get("enabled", True)),
            "device": rx_cfg.get("device") if rx_cfg else None,
            "ppm_setting": rx_cfg.get("ppm") if rx_cfg else None,
            "gain": rx_cfg.get("gain") if rx_cfg else None,
            "frequencies": rx_cfg.get("frequencies") if rx_cfg else [],
            "service": self.service,
            "last_frame": self.last_frame,
            "frames_5min": sum(1 for f in hour if f[0] >= now - 300),
            "frames_hour": len(hour),
            "content_hour": sum(1 for f in hour if f[4]),
            "avg_level": round(statistics.fmean(levels), 1) if levels else None,
            "avg_noise": round(statistics.fmean(noises), 1) if noises else None,
            "corrected_pct": round(100 * sum(1 for f in recent if f[3]) / len(recent), 1) if recent else None,
            "ppm_residual": residual,
            "ppm_samples": len(self.skew),
            "ppm_suggested": round((rx_cfg.get("ppm", 0) if rx_cfg else 0) - residual) if residual is not None else None,
            "ppm_trend": [[round(t), round(s, 1)] for t, s in list(self.skew)[::10]],
        }


class HealthMonitor:
    def __init__(self, cfg, config_path):
        self.cfg = cfg
        self.config_path = Path(config_path)
        self.receivers = {name: ReceiverStats(name) for name in SOURCES}
        self.started = time.time()
        self.has_systemctl = shutil.which("systemctl") is not None
        self.events = deque(maxlen=50)  # things the monitor did, e.g. automatic ppm corrections
        self.config_mtime = self.config_path.stat().st_mtime if self.config_path.exists() else 0

    def reload_config(self):
        """Pick up receiver and health settings edited in config.json (by hand or by auto-ppm) without a restart."""
        try:
            mtime = self.config_path.stat().st_mtime
            if mtime == self.config_mtime:
                return
            fresh = json.loads(self.config_path.read_text())
        except (OSError, ValueError) as exc:
            log.warning("config reload skipped: %s", exc)
            return
        self.config_mtime = mtime
        old_ppm = {name: (rx or {}).get("ppm") for name, rx in (self.cfg.get("receivers") or {}).items()}
        self.cfg["receivers"] = fresh.get("receivers", {})
        self.cfg["health"] = fresh.get("health", {})
        for name, rx in self.cfg["receivers"].items():
            if name in self.receivers and rx.get("ppm") != old_ppm.get(name):
                # Samples measured with the old correction no longer describe the dongle.
                self.receivers[name].skew.clear()
                self.receivers[name].drift_since = None
                self.events.append({"ts": time.time(), "kind": "ppm_change",
                                    "text": f"{SOURCES[name]} ppm setting changed {old_ppm.get(name)} → {rx.get('ppm')}"})
        log.info("reloaded receiver/health settings from %s", self.config_path)

    def note(self, msg, raw):
        name = "acars" if msg["source"] == "ACARS" else "vdl2"
        self.receivers[name].note(msg, raw)

    def rx_cfg(self, name):
        return (self.cfg.get("receivers") or {}).get(name)

    async def poll_services(self):
        for name, rx in self.receivers.items():
            if not self.has_systemctl:
                rx.service = {"state": "unknown"}
                continue
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "show", f"acars-decoder@{name}.service", "-p", "LoadState,ActiveState,SubState,NRestarts",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await proc.communicate()
            props = dict(line.split("=", 1) for line in out.decode().splitlines() if "=" in line)
            if props.get("LoadState") != "loaded":
                rx.service = {"state": "unmanaged"}  # not installed as a systemd service
                continue
            restarts = int(props.get("NRestarts") or 0)
            now = time.time()
            rx.restarts_seen.append((now, restarts))
            while rx.restarts_seen and rx.restarts_seen[0][0] < now - 3600:
                rx.restarts_seen.popleft()
            rx.service = {"state": props.get("ActiveState"), "sub": props.get("SubState"), "restarts": restarts,
                          "restarts_hour": restarts - rx.restarts_seen[0][1]}

    def report(self):
        now = time.time()
        settings = health_settings(self.cfg)
        receivers, alerts = [], []
        for name, rx in self.receivers.items():
            rx.trim(now, 3600)
            r = rx.report(now, self.rx_cfg(name), settings)
            receivers.append(r)
            if not r["enabled"]:
                continue
            label = r["source"]
            state = r["service"].get("state")
            if state in ("failed", "inactive"):
                alerts.append({"level": "error", "receiver": name,
                               "text": f"{label} decoder is not running (service {state})"})
            elif state == "activating":
                alerts.append({"level": "warn", "receiver": name, "text": f"{label} decoder is restarting"})
            silence = now - (r["last_frame"] or self.started)
            limit = settings["silence_mins"].get(name, 10) * 60
            if silence > limit and now - self.started > limit:
                alerts.append({"level": "warn", "receiver": name,
                               "text": f"{label} receiver has decoded nothing for {int(silence // 60)} min"})
            if r["service"].get("restarts_hour", 0) >= 3:
                alerts.append({"level": "warn", "receiver": name,
                               "text": f"{label} decoder restarted {r['service']['restarts_hour']} times in the last hour"})
            if r["ppm_residual"] is not None and abs(r["ppm_residual"]) >= settings["ppm_tolerance"]:
                alerts.append({"level": "warn", "receiver": name,
                               "text": f"{label} dongle tuning has drifted {r['ppm_residual']:+.1f} ppm; "
                                       f"suggested ppm setting {r['ppm_suggested']} (now {r['ppm_setting']})"})
        return {"receivers": receivers, "alerts": alerts, "auto_ppm": settings["auto_ppm"],
                "ppm_tolerance": settings["ppm_tolerance"], "events": list(self.events)}

    async def maybe_auto_correct_ppm(self):
        """If enabled, apply a persistent VDL2 tuning drift to config.json and restart that decoder."""
        settings = health_settings(self.cfg)
        rx = self.receivers["vdl2"]
        residual = rx.ppm_residual()
        now = time.time()
        if residual is None or abs(residual) < settings["ppm_tolerance"]:
            rx.drift_since = None
            return
        rx.drift_since = rx.drift_since or now
        last_fix = next((e["ts"] for e in reversed(self.events) if e.get("kind") == "auto_ppm"), 0)
        if not settings["auto_ppm"] or now - rx.drift_since < AUTO_PPM_HOLD_SECS or now - last_fix < AUTO_PPM_COOLDOWN_SECS:
            return
        old = (self.rx_cfg("vdl2") or {}).get("ppm", 0)
        new = round(old - residual)
        if not self.write_ppm("vdl2", new):
            return
        # The decoder only reads config.json at startup, so the new value has to be on
        # disk before the restart. If the restart then fails, the running decoder is still
        # on the old value and the file would be a lie - so put it back. Without this the
        # correction is rewritten every cooldown and config.json ratchets away from the
        # value actually in use (seen on a host where passwordless sudo wasn't available).
        ok, reason = await self.restart_decoder("vdl2")
        if not ok:
            self.write_ppm("vdl2", old)
            self.events.append({"ts": now, "kind": "auto_ppm",
                                "text": f"VDL2 ppm still {old}; wanted {new} but the restart failed "
                                        f"({reason}) - restart the decoder manually"})
            log.warning("auto ppm: VDL2 %s -> %s reverted (restart failed: %s)", old, new, reason)
            rx.drift_since = None
            return
        self.events.append({"ts": now, "kind": "auto_ppm", "text": f"VDL2 ppm changed {old} → {new}"})
        log.warning("auto ppm: VDL2 %s -> %s (restart ok)", old, new)
        rx.skew.clear()
        rx.drift_since = None

    async def restart_decoder(self, name):
        """Restart a decoder so it rereads config.json. Returns (ok, reason if not)."""
        if not self.has_systemctl:
            return False, "systemctl not available"
        proc = await asyncio.create_subprocess_exec("sudo", "-n", "systemctl", "restart", f"acars-decoder@{name}.service",
                                                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        return proc.returncode == 0, err.decode().strip()[:80]

    def write_ppm(self, name, value):
        """Change receivers.<name>.ppm in config.json in place, keeping the file's layout."""
        text = self.config_path.read_text()
        block = re.search(rf'"{name}"\s*:\s*\{{[^{{}}]*\}}', text)
        if not block or not re.search(r'"ppm"\s*:\s*-?\d+(\.\d+)?', block.group(0)):
            log.warning("auto ppm: couldn't find receivers.%s.ppm in %s", name, self.config_path)
            return False
        new_block = re.sub(r'("ppm"\s*:\s*)-?\d+(\.\d+)?', rf"\g<1>{value}", block.group(0), count=1)
        new_text = text[:block.start()] + new_block + text[block.end():]
        json.loads(new_text)  # never write a broken config
        self.config_path.write_text(new_text)
        self.cfg["receivers"][name]["ppm"] = value
        self.config_mtime = self.config_path.stat().st_mtime
        return True


class GroundStations:
    """Ground stations: located ones from squitter broadcasts, plus VDL2 station addresses aircraft talk to."""

    def __init__(self):
        self.squitters = {}  # id -> station info + last_seen, count
        self.vdl2 = {}       # address -> {last_seen, frames, aircraft:set}

    def note(self, msg, squitter):
        now = msg["ts"]
        if squitter:
            s = self.squitters.setdefault(squitter["id"], {**squitter, "count": 0, "first_seen": now})
            s.update(squitter, last_seen=now, count=s["count"] + 1)
        if msg["source"] == "VDL2" and msg.get("gs"):
            g = self.vdl2.setdefault(msg["gs"], {"address": msg["gs"], "frames": 0, "aircraft": set(), "first_seen": now})
            g["frames"] += 1
            g["last_seen"] = now
            if msg.get("icao"):
                g["aircraft"].add(msg["icao"])

    def expire(self, cutoff):
        for table in (self.squitters, self.vdl2):
            for key in [k for k, v in table.items() if v["last_seen"] < cutoff]:
                del table[key]

    def report(self):
        return {
            "located": sorted(self.squitters.values(), key=lambda s: -s["last_seen"]),
            "vdl2": sorted(({**g, "aircraft": len(g["aircraft"])} for g in self.vdl2.values()),
                           key=lambda g: -g["frames"]),
        }


class WeatherStore:
    """Wind/temperature observations reported by aircraft, for the winds-aloft map layer."""

    def __init__(self):
        self.obs = deque()  # (received ts, observation dict)

    def add(self, msg, observations):
        who = msg.get("callsign") or msg.get("flight") or msg.get("reg")
        for o in observations:
            self.obs.append((msg["ts"], {**o, "flight": who, "source": msg["source"]}))

    def expire(self, cutoff):
        while self.obs and self.obs[0][0] < cutoff:
            self.obs.popleft()

    def recent(self, seconds):
        cutoff = time.time() - seconds
        return [o for ts, o in self.obs if o["ts"] >= cutoff]


def stats_summary(store, tracker, cfg, now):
    """Traffic breakdowns for the stats page, computed from the messages in memory."""
    bucket_secs = 300
    start = now - cfg["history_hours"] * 3600
    buckets = {}
    freqs, airlines, types = {}, Counter(), Counter()
    for msg, _raw in store.messages.values():
        if msg["ts"] < start:
            continue
        content = msg["type"].split(":")[1] not in ("linktest", "status", "partial", "ack", "handoff", "connection", "network")
        b = buckets.setdefault(int((msg["ts"] - start) // bucket_secs), Counter())
        b[f"{msg['source']}:{'content' if content else 'link'}"] += 1
        types[msg["type"]] += 1
        if msg.get("freq") is not None:
            f = freqs.setdefault((msg["source"], round(msg["freq"], 3)),
                                 {"source": msg["source"], "freq": round(msg["freq"], 3), "frames": 0, "content": 0,
                                  "levels": []})
            f["frames"] += 1
            f["content"] += content
            if msg.get("level") is not None and len(f["levels"]) < 2000:
                f["levels"].append(msg["level"])
        if content and msg.get("flight"):
            airlines[msg["flight"][:2]] += 1
    timeline = [{"t": start + i * bucket_secs, **dict(buckets.get(i, {}))}
                for i in range(int((now - start) // bucket_secs) + 1)]
    freq_rows = []
    for f in sorted(freqs.values(), key=lambda f: (f["source"], f["freq"])):
        levels = f.pop("levels")
        freq_rows.append({**f, "avg_level": round(statistics.fmean(levels), 1) if levels else None})
    operators = {}
    for a in tracker.aircraft.values():
        v = tracker.vrs.by_icao.get(a.get("icao") or "") or {}
        if a.get("flight") and (v.get("OpIcao") or v.get("Op")):
            operators.setdefault(a["flight"][:2], Counter())[(v.get("OpIcao") or None, v.get("Op"))] += 1
    airline_rows = [{"code": code, "messages": n, "name": airline_name(code, operators.get(code))}
                    for code, n in airlines.most_common(15)]
    return {"bucket_secs": bucket_secs, "timeline": timeline, "frequencies": freq_rows,
            "airlines": airline_rows, "types": dict(types)}


def process_memory_mb():
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    return None
