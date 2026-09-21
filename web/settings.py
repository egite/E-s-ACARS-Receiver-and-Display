"""Settings page support: which config.json values the web page may change, checking them,
and writing config.json back without losing the rest of the file.

Only settings that are safe to change from a browser are editable here. Ports and the
listen address need the web server itself restarted, and binaries, sample rates and the
librtlsdr preload are install-level details - those stay hand-edited.
"""
import json
import math
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import receiver  # noqa: E402  - reuse the decoder's own checks so a saved config always starts

RECEIVERS = ("acars", "vdl2")
# Keys of receivers.<name> the page edits; changing any of them restarts that decoder.
RECEIVER_KEYS = ("enabled", "decoder", "device", "gain", "ppm", "frequencies")
BAND_MHZ = {"acars": (118.0, 137.0), "vdl2": (118.0, 137.0)}


def editable(cfg):
    """The subset of config the settings page shows, with defaults filled in."""
    health = cfg.get("health") or {}
    silence = health.get("silence_minutes") or {}
    return {
        "home": {k: (cfg.get("home") or {}).get(k) for k in ("name", "lat", "lon")},
        "receivers": {name: {
            "enabled": rx.get("enabled", True),
            "decoder": rx.get("decoder") or receiver.DEFAULT_DECODER[name],
            "device": rx.get("device"),
            "gain": rx.get("gain", "auto") if rx.get("gain") is not None else "auto",
            "ppm": rx.get("ppm", 0),
            "frequencies": rx.get("frequencies") or [],
        } for name in RECEIVERS for rx in [(cfg.get("receivers") or {}).get(name) or {}]},
        "vrs_url": cfg.get("vrs_url") or "",
        "vrs_feed": cfg.get("vrs_feed"),
        "vrs_poll_secs": cfg.get("vrs_poll_secs", 5),
        "history_hours": cfg.get("history_hours", 2),
        "max_position_km": cfg.get("max_position_km", 1000),
        "health": {
            "silence_minutes": {name: silence.get(name, d) for name, d in (("acars", 30), ("vdl2", 5))},
            "ppm_tolerance": health.get("ppm_tolerance", 3),
            "auto_ppm": bool(health.get("auto_ppm", False)),
        },
    }


class Invalid(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def _number(errors, key, value, lo, hi, integer=False):
    try:
        n = float(value)
    except (TypeError, ValueError):
        errors[key] = "must be a number"
        return None
    if not math.isfinite(n) or not lo <= n <= hi:
        errors[key] = f"must be between {lo} and {hi}"
        return None
    if integer and n != int(n):
        errors[key] = "must be a whole number"
        return None
    return int(n) if n == int(n) else n  # keep config.json's 3, not 3.0


def validate(new, current):
    """Check a settings payload from the page. Returns the cleaned values or raises Invalid,
    with errors keyed by dotted path so the page can put each one next to its field."""
    errors, out = {}, {}
    home = new.get("home") or {}
    out["home"] = {
        "name": str(home.get("name") or "").strip()[:60] or "My receiver",
        "lat": _number(errors, "home.lat", home.get("lat"), -90, 90),
        "lon": _number(errors, "home.lon", home.get("lon"), -180, 180),
    }

    out["receivers"] = {}
    for name in RECEIVERS:
        rx, p = (new.get("receivers") or {}).get(name) or {}, f"receivers.{name}"
        clean = {"enabled": bool(rx.get("enabled"))}
        clean["decoder"] = rx.get("decoder")
        if clean["decoder"] not in (receiver.DEFAULT_DECODER[name], "xng"):
            errors[f"{p}.decoder"] = f"must be {receiver.DEFAULT_DECODER[name]} or xng"
        clean["device"] = str(rx.get("device") or "").strip()
        if not clean["device"]:
            errors[f"{p}.device"] = "required (dongle serial number or index)"
        gain = rx.get("gain")
        clean["gain"] = "auto" if gain in (None, "", "auto") else _number(errors, f"{p}.gain", gain, 0, 60)
        clean["ppm"] = _number(errors, f"{p}.ppm", rx.get("ppm", 0), -200, 200, integer=True)
        freqs = rx.get("frequencies")
        if isinstance(freqs, str):
            freqs = [f for f in re.split(r"[\s,;]+", freqs) if f]
        lo, hi = BAND_MHZ[name]
        try:
            clean["frequencies"] = sorted({round(float(f), 3) for f in freqs or []})
            if not clean["frequencies"]:
                errors[f"{p}.frequencies"] = "at least one frequency is needed"
            elif not all(lo <= f <= hi for f in clean["frequencies"]):
                errors[f"{p}.frequencies"] = f"frequencies are in MHz, between {lo:g} and {hi:g}"
        except (TypeError, ValueError):
            errors[f"{p}.frequencies"] = "list of frequencies in MHz, e.g. 131.550, 130.025"
        if clean["enabled"] and not any(k.startswith(p) for k in errors):
            # Ask receiver.py exactly what it would ask at startup (xng capture width, etc.),
            # merged over the keys the page doesn't edit (sample_rate, binaries).
            merged = {**((current.get("receivers") or {}).get(name) or {}), **clean}
            try:
                receiver.decoder_for(name, merged)
                if clean["decoder"] == "xng":
                    receiver.xng_capture_plan(name, merged)
            except SystemExit as exc:
                msg = str(exc).removeprefix("config.json: ").replace(f"receivers.{name}.frequencies ", "these channels ")
                msg = msg.replace(f"set receivers.{name}.sample_rate", f"xng needs receivers.{name}.sample_rate in config.json")
                errors[f"{p}.frequencies" if "channels" in msg else f"{p}.decoder"] = msg
        out["receivers"][name] = clean

    out["vrs_url"] = str(new.get("vrs_url") or "").strip()
    if out["vrs_url"] and not re.match(r"https?://[^\s/]+", out["vrs_url"]):
        errors["vrs_url"] = "must start with http:// or https://"
    out["vrs_feed"] = str(new.get("vrs_feed") or "").strip() or None
    out["vrs_poll_secs"] = _number(errors, "vrs_poll_secs", new.get("vrs_poll_secs"), 1, 300, integer=True)
    out["history_hours"] = _number(errors, "history_hours", new.get("history_hours"), 0.25, 48)
    out["max_position_km"] = _number(errors, "max_position_km", new.get("max_position_km"), 10, 20000, integer=True)

    health = new.get("health") or {}
    silence = health.get("silence_minutes") or {}
    out["health"] = {
        "silence_minutes": {name: _number(errors, f"health.silence_minutes.{name}", silence.get(name), 1, 1440,
                                          integer=True) for name in RECEIVERS},
        "ppm_tolerance": _number(errors, "health.ppm_tolerance", health.get("ppm_tolerance"), 0.5, 50),
        "auto_ppm": bool(health.get("auto_ppm")),
    }
    if errors:
        raise Invalid(errors)
    return out


def merge(file_cfg, clean):
    """config.json with the page's values applied. Keys the page doesn't manage are kept."""
    cfg = json.loads(json.dumps(file_cfg))
    cfg["home"] = {**(cfg.get("home") or {}), **clean["home"]}
    shown = editable(file_cfg)["receivers"]
    for name, rx in clean["receivers"].items():
        old = cfg.setdefault("receivers", {}).get(name) or {}
        # Only write values that actually changed, so gain null vs "auto", a missing
        # "enabled" or a hand-ordered channel list don't count as edits (or restarts).
        cfg["receivers"][name] = {**old, **{k: v for k, v in rx.items() if _same(k, shown[name][k], v) is False}}
    for key in ("vrs_url", "vrs_feed", "vrs_poll_secs", "history_hours", "max_position_km"):
        cfg[key] = clean[key]
    health = cfg.get("health") or {}
    cfg["health"] = {**health, **clean["health"],
                     "silence_minutes": {**(health.get("silence_minutes") or {}), **clean["health"]["silence_minutes"]}}
    return cfg


def _same(key, a, b):
    return sorted(a) == sorted(b) if key == "frequencies" else a == b


def changed_receivers(before, after):
    old, new = editable(before)["receivers"], editable(after)["receivers"]
    return [name for name in RECEIVERS if not all(_same(k, old[name][k], new[name][k]) for k in RECEIVER_KEYS)]


def _dump(value, indent, key=None):
    """JSON in config.json's hand-written style: containers stay on one line when they fit."""
    if key == "frequencies" and isinstance(value, list):
        return "[" + ", ".join(f"{f:.3f}" for f in value) + "]"  # 130.450, not 130.45
    flat = json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    if not isinstance(value, (dict, list)) or len(flat) + indent <= 100 or not value:
        return flat
    pad = " " * (indent + 2)
    if isinstance(value, list):
        items = [pad + _dump(v, indent + 2) for v in value]
    else:
        items = [f"{pad}{json.dumps(k)}: {_dump(v, indent + 2, k)}" for k, v in value.items()]
    return ("[" if isinstance(value, list) else "{") + "\n" + ",\n".join(items) + "\n" + " " * indent + \
        ("]" if isinstance(value, list) else "}")


def render(cfg, previous_text=""):
    """Format config.json, keeping the blank lines that grouped its top-level keys."""
    spaced = set(re.findall(r'\n[ \t]*\n[ \t]*"([^"]+)"\s*:', previous_text))
    lines = []
    for i, (key, value) in enumerate(cfg.items()):
        lines.append(("\n" if key in spaced and i else "") + f'  {json.dumps(key)}: {_dump(value, 2)}')
    return "{\n" + ",\n".join(lines) + "\n}\n"


def write(path, cfg):
    """Replace config.json atomically, so a crash mid-write can't leave the decoders a broken file."""
    path = Path(path)
    text = render(cfg, path.read_text() if path.exists() else "")
    json.loads(text)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    if path.exists():
        os.chmod(tmp, path.stat().st_mode & 0o7777)
    os.replace(tmp, path)
