"""
app/app.py — AGV Web Monitoring Dashboard
==========================================
Flask server running in a daemon thread alongside the asyncio main loop.
Shares the AMRState object directly (GIL-safe for reads and for the
single-attribute writes performed from request handlers).

Access from any device on 192.168.2.x: http://192.168.2.100:5000
"""

import os
import re
import socket
import subprocess
import time
import threading
from flask import Flask, render_template, jsonify, request
from werkzeug.serving import make_server
import logging

logger = logging.getLogger(__name__)

# Flask looks for templates relative to the app.py file location
_here = os.path.dirname(os.path.abspath(__file__))
app   = Flask(__name__, template_folder=os.path.join(_here, "templates"))

_state  = None   # set once by run_server()
_engine = None   # set once by run_server()
_config = None   # set once by run_server()
_server = None   # werkzeug BaseWSGIServer instance


def _build_state_snapshot():
    """Derive the full JSON payload from the shared state object."""
    s = _state
    mode = s.current_mode

    # ── AGV summary ───────────────────────────────────────────────────────────
    agv = {
        "mode":                 mode,
        "emergency":            s.emergency_active,
        "system_error":         s.system_error,
        "sensor_error":         s.sensor_error,
        "sensor_error_detail":  s.sensor_error_detail,
        "speed_mode":           s.speed_mode,
        "sequence_stop":        s.sequence_stop,
        "reverse_auto_request": s.reverse_auto_request,
    }

    # ── Magnetic sensor (MGS1600 via CAN) ────────────────────────────────────
    raw = s.latest_sensor
    sensor = {
        "left_mm":        raw["left_mm"]        if raw else None,
        "tape_detected":  raw["tape_detected"]   if raw else False,
        "left_marker":    raw["left_marker"]     if raw else False,
        "right_marker":   raw["right_marker"]    if raw else False,
        "sensor_failure": raw["sensor_failure"]  if raw else False,
    }

    # ── Sequence engine status ────────────────────────────────────────────────
    sequences = _engine.status() if _engine is not None else {
        "active": None, "armed": None, "cooldowns": {}
    }
    # Expire the displayed tag 3 s after the last read (reader's recv timeout is 2 s,
    # so a card that left range will naturally stop updating within that window).
    _rfid_fresh = (time.time() - s.last_rfid_tag_ts) < 3.0
    sequences["last_rfid"] = s.last_rfid_tag if _rfid_fresh else None

    # Recent unmapped RFID tags (last 5) for dashboard pill
    _unmapped_recent = [
        {"ts": ts, "tag": tag}
        for ts, tag in list(s.unmapped_rfid_log)[-5:]
        if (time.time() - ts) < 60.0
    ]
    sequences["unmapped_recent"] = _unmapped_recent
    sequences["mapping_reload_pending"] = s.mapping_reload_pending

    # ── Motion telemetry (RPM + PID — populated during auto/reverse modes) ────
    tel = s.motion_telemetry or {}
    motion_tel = {
        "left_rpm":   tel.get("left_rpm"),
        "right_rpm":  tel.get("right_rpm"),
        "pid_error":  tel.get("pid_error"),
        "pid_p":      tel.get("pid_p"),
        "pid_i":      tel.get("pid_i"),
        "pid_d":      tel.get("pid_d"),
        "pid_output": tel.get("pid_output"),
    }

    # ── Safety indicators ─────────────────────────────────────────────────────
    di = s.latest_di or []
    def _di_flag(ch):
        return bool(di[ch]) if ch is not None and len(di) > ch else False

    safety = {
        "bumper":      s.bumper_active,
        "lidar_outer": _di_flag(_config.DI_LIDAR_OUTER),
        "lidar_slow":  _di_flag(_config.DI_LIDAR_SLOW),
        "lidar_stop":  _di_flag(_config.DI_LIDAR_STOP),
    }

    return {
        "agv":        agv,
        "sensor":     sensor,
        "sequences":  sequences,
        "motion":     motion_tel,
        "safety":     safety,
    }


_VALID_COMMANDS = {"forward", "fwd_left", "fwd_right", "reverse",
                   "rvs_left", "rvs_right", "left", "right"}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/manual")
def manual():
    return render_template("manual.html")

def _build_io_names():
    """Derive DI / DO channel labels from the active profile.

    Single source of truth: profile JSON. If a channel isn't referenced by any
    profile field, it's labelled [SPARE].
    """
    cfg = _config

    # ── DI labels ────────────────────────────────────────────────────────────
    di_map = {
        cfg.DI_EMERGENCY:   "EPB EMERGENCY",
        cfg.DI_FWD:         "PB FORWARD",
        cfg.DI_REV:         "PB REVERSE",
        cfg.DI_LEFT:        "PB LEFT",
        cfg.DI_RIGHT:       "PB RIGHT",
        cfg.DI_START:       "PB START AUTO",
        cfg.DI_RESET:       "PB RESET / STOP",
        cfg.DI_MODE_SWITCH: "SS MAN / AUTO",
    }
    if cfg.DI_LIDAR_OUTER is not None:
        di_map[cfg.DI_LIDAR_OUTER] = "LIDAR OUTER"
    if cfg.DI_LIDAR_SLOW  is not None:
        di_map[cfg.DI_LIDAR_SLOW]  = "LIDAR MIDDLE"
    if cfg.DI_LIDAR_STOP  is not None:
        di_map[cfg.DI_LIDAR_STOP]  = "LIDAR INNER"
    if cfg.DI_BUMPER      is not None:
        di_map[cfg.DI_BUMPER]      = "IMPACT BUMPER"

    di_names = [di_map.get(i, "[SPARE]") for i in range(cfg.NUM_DI)]

    # ── DO labels ────────────────────────────────────────────────────────────
    do_map = {}
    for side, ch in cfg.MOTOR_CHANNELS.items():
        side_u = side.upper()
        if "do_fwd"   in ch: do_map[ch["do_fwd"]]   = f"{side_u} FWD"
        if "do_rev"   in ch: do_map[ch["do_rev"]]   = f"{side_u} REV"
        if "do_brake" in ch: do_map[ch["do_brake"]] = f"{side_u} BRAKE"

    if cfg.PUSHER_CHANNELS:
        for i, ch in enumerate(cfg.PUSHER_CHANNELS.get("extend",  []) or []):
            do_map[ch] = "PUSHER EXTEND"  + (f" {i+1}" if i else "")
        for i, ch in enumerate(cfg.PUSHER_CHANNELS.get("retract", []) or []):
            do_map[ch] = "PUSHER RETRACT" + (f" {i+1}" if i else "")

    if cfg.HORN_CHANNELS:
        if cfg.HORN_CHANNELS.get("regular_horn") is not None:
            do_map[cfg.HORN_CHANNELS["regular_horn"]] = "REGULAR HORN"
        if cfg.HORN_CHANNELS.get("alarm_horn") is not None:
            do_map[cfg.HORN_CHANNELS["alarm_horn"]]   = "ALARM HORN"

    do_names = [do_map.get(i, "[SPARE]") for i in range(cfg.NUM_DO)]
    return di_names, do_names


@app.route("/io")
def io_monitor():
    di_names, do_names = _build_io_names()
    return render_template("io_monitor.html",
                           di_names=di_names,
                           do_names=do_names)

@app.route("/params")
def params_page():
    return render_template("params.html",
                           agv_id=_config.AGV_ID,
                           profile=_config._params)

@app.route("/errors")
def errors_page():
    return render_template("errors.html")

@app.route("/mappings")
def mappings_page():
    return render_template("mappings.html", agv_id=_config.AGV_ID)

@app.route("/api/errors")
def api_errors():
    if _state is None:
        return jsonify({"error": "not initialised"}), 503
    return jsonify({"events": list(reversed(_state.event_log))})

@app.route("/api/errors/clear", methods=["POST"])
def api_errors_clear():
    if _state is None:
        return jsonify({"error": "not initialised"}), 503
    _state.event_log.clear()
    return jsonify({"ok": True})

@app.route("/api/manual/command", methods=["POST"])
def api_manual_command():
    if _state is None:
        return jsonify({"error": "state not initialised"}), 503
    if _state.current_mode != "manual":
        return jsonify({"error": f"AGV not in manual mode (current: {_state.current_mode})"}), 403

    data = request.get_json(silent=True) or {}
    cmd  = data.get("command")   # None = stop

    if cmd is not None and cmd not in _VALID_COMMANDS:
        return jsonify({"error": f"unknown command: {cmd}"}), 400

    _state.web_manual_command    = cmd
    _state.web_manual_command_ts = time.time()
    return jsonify({"ok": True, "command": cmd})


@app.route("/api/manual/pusher", methods=["POST"])
def api_manual_pusher():
    if _state is None:
        return jsonify({"error": "state not initialised"}), 503
    if _state.current_mode != "manual":
        return jsonify({"error": f"AGV not in manual mode (current: {_state.current_mode})"}), 403
    if _config.PUSHER_CHANNELS is None:
        return jsonify({"error": "no pusher configured in profile"}), 400

    data   = request.get_json(silent=True) or {}
    action = data.get("action")
    if action not in ("up", "down", "clear"):
        return jsonify({"error": "action must be 'up', 'down', or 'clear'"}), 400

    _state.web_pusher_request = action
    return jsonify({"ok": True, "action": action})


@app.route("/api/state")
def api_state():
    if _state is None:
        return jsonify({"error": "state not initialised"}), 503
    return jsonify(_build_state_snapshot())


@app.route("/api/reverse_auto", methods=["POST"])
def api_reverse_auto():
    if _state is None:
        return jsonify({"error": "state not initialised"}), 503

    data    = request.get_json(silent=True) or {}
    running = data.get("running")

    if running is True:
        mode = _state.current_mode
        if mode != "armed":
            return jsonify({"error": f"Cannot start reverse auto in mode: {mode}"}), 403
        _state.reverse_auto_request = True
        return jsonify({"ok": True, "running": True})

    elif running is False:
        _state.reverse_auto_request = False
        return jsonify({"ok": True, "running": False})

    return jsonify({"error": "running must be true or false"}), 400


@app.route("/api/io")
def api_io():
    if _state is None or _config is None:
        return jsonify({"error": "not initialised"}), 503
    di = _state.latest_di
    do = _state.latest_do
    return jsonify({
        "di":     [bool(b) for b in di] if di is not None else [False] * _config.NUM_DI,
        "do":     [bool(b) for b in do] if do is not None else [False] * _config.NUM_DO,
        "num_di": _config.NUM_DI,
        "num_do": _config.NUM_DO,
    })


@app.route("/api/tuning", methods=["GET"])
def api_tuning_get():
    if _state is None:
        return jsonify({"error": "state not initialised"}), 503
    import state as _state_mod
    return jsonify({
        "lidar_stop_enabled":   _state.lidar_stop_enabled,
        "lidar_slow_enabled":   _state.lidar_slow_enabled,
        "rfid_enabled":         _state.rfid_enabled,
        "auto_high_speed":      _state.auto_high_speed,
        "auto_slow_speed":      _state.auto_slow_speed,
        "auto_extra_slow_speed":_state.auto_extra_slow_speed,
        "speed_min":            _state_mod.AUTO_SPEED_MIN,
        "speed_max":            _state_mod.AUTO_SPEED_MAX,
    })


_TOGGLE_FIELDS = {"lidar_stop_enabled", "lidar_slow_enabled", "rfid_enabled"}

@app.route("/api/tuning/toggle", methods=["POST"])
def api_tuning_toggle():
    if _state is None:
        return jsonify({"error": "state not initialised"}), 503
    data    = request.get_json(silent=True) or {}
    feature = data.get("feature")
    enabled = data.get("enabled")
    if feature not in _TOGGLE_FIELDS:
        return jsonify({"error": f"unknown feature: {feature}"}), 400
    if not isinstance(enabled, bool):
        return jsonify({"error": "enabled must be true or false"}), 400
    setattr(_state, feature, enabled)
    _state.log_event("INFO", f"TUNING: {feature} -> {'ENABLED' if enabled else 'DISABLED'}")
    logger.info("[TUNING] %s = %s", feature, enabled)
    return jsonify({"ok": True, "feature": feature, "enabled": enabled})


@app.route("/api/tuning/speeds", methods=["POST"])
def api_tuning_speeds():
    if _state is None:
        return jsonify({"error": "state not initialised"}), 503
    import state as _state_mod
    data = request.get_json(silent=True) or {}

    # Accept partial updates; missing keys keep current value.
    try:
        new_high  = float(data.get("auto_high_speed",       _state.auto_high_speed))
        new_slow  = float(data.get("auto_slow_speed",       _state.auto_slow_speed))
        new_xslow = float(data.get("auto_extra_slow_speed", _state.auto_extra_slow_speed))
    except (TypeError, ValueError):
        return jsonify({"error": "speeds must be numeric (m/s)"}), 400

    lo, hi = _state_mod.AUTO_SPEED_MIN, _state_mod.AUTO_SPEED_MAX
    for name, v in (("auto_high_speed", new_high),
                    ("auto_slow_speed", new_slow),
                    ("auto_extra_slow_speed", new_xslow)):
        if not (lo <= v <= hi):
            return jsonify({"error": f"{name}={v} out of bounds [{lo}, {hi}]"}), 400

    if not (new_high >= new_slow >= new_xslow):
        return jsonify({"error": "must satisfy HIGH >= SLOW >= EXTRA_SLOW"}), 400

    _state.auto_high_speed       = new_high
    _state.auto_slow_speed       = new_slow
    _state.auto_extra_slow_speed = new_xslow
    _state.log_event("INFO",
        f"TUNING: auto speeds -> HIGH={new_high:.2f} SLOW={new_slow:.2f} XSLOW={new_xslow:.2f} m/s")
    logger.info("[TUNING] auto speeds updated: H=%.2f S=%.2f XS=%.2f",
                new_high, new_slow, new_xslow)
    return jsonify({
        "ok": True,
        "auto_high_speed":       new_high,
        "auto_slow_speed":       new_slow,
        "auto_extra_slow_speed": new_xslow,
    })


@app.route("/api/mappings", methods=["GET"])
def api_mappings_get():
    if _state is None or _config is None:
        return jsonify({"error": "not initialised"}), 503
    from core.mapping_store import load_rules, decompile_profile_sequences
    rules = load_rules(_config.AGV_ID)
    first_open = rules is None
    if first_open:
        # Import profile sequences on first open — mandatory, no skip path
        profile_seqs = _config._params.get("sequences", [])
        rules, raw = decompile_profile_sequences(profile_seqs)
    else:
        raw = []
    return jsonify({
        "rules":                rules or [],
        "raw_sequences":        raw,
        "first_open":           first_open,
        "reload_pending":       _state.mapping_reload_pending,
        "rfid_enabled":         _state.rfid_enabled,
    })


@app.route("/api/mappings", methods=["POST"])
def api_mappings_save():
    if _state is None or _config is None:
        return jsonify({"error": "not initialised"}), 503
    from core.mapping_store import validate, save_rules, compile_to_sequences
    data  = request.get_json(silent=True) or {}
    rules = data.get("rules")
    if not isinstance(rules, list):
        return jsonify({"error": "rules must be a list"}), 400

    # Assign IDs to any new rule that doesn't have one
    import uuid
    for r in rules:
        if not r.get("id"):
            r["id"] = str(uuid.uuid4())

    errors = validate(rules)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    save_rules(_config.AGV_ID, rules, source="ui")
    _state.mapping_reload_pending = True
    _state.log_event("INFO", f"MAPPING: {len(rules)} rule(s) saved — reload pending")
    logger.info("[MAPPING] %d rules saved via UI", len(rules))
    return jsonify({"ok": True, "rule_count": len(rules), "reload_pending": True})


@app.route("/api/mappings/apply_now", methods=["POST"])
def api_mappings_apply_now():
    if _state is None or _engine is None or _config is None:
        return jsonify({"error": "not initialised"}), 503
    mode = _state.current_mode
    if mode == "running":
        return jsonify({"error": "Cannot apply while AGV is RUNNING — wait for ARMED entry"}), 409

    from core.mapping_store import load_rules, compile_to_sequences
    rules = load_rules(_config.AGV_ID)
    if rules is None:
        return jsonify({"error": "No override file saved yet"}), 400

    new_seqs = compile_to_sequences(rules)

    # Dispatch onto the asyncio loop from this Flask (daemon) thread
    loop = getattr(_state, "loop", None)
    if loop is None or not loop.is_running():
        return jsonify({"error": "asyncio loop not available"}), 503

    result = {"ok": None}
    ev = threading.Event()

    def _do_reload():
        ok = _engine.reload_sequences(new_seqs)
        result["ok"] = ok
        ev.set()

    loop.call_soon_threadsafe(_do_reload)
    ev.wait(timeout=2.0)

    if result["ok"] is None:
        return jsonify({"error": "Reload timed out"}), 503
    if not result["ok"]:
        return jsonify({"error": "Engine refused — a sequence is still running"}), 503

    _state.mapping_reload_pending = False
    _state.log_event("INFO", f"MAPPING: apply_now — {len(rules)} rules loaded immediately")
    logger.info("[MAPPING] apply_now: %d rules reloaded", len(rules))
    return jsonify({"ok": True, "seq_count": len(new_seqs)})


@app.route("/api/mappings/history", methods=["GET"])
def api_mappings_history():
    if _config is None:
        return jsonify({"error": "not initialised"}), 503
    from core.mapping_store import read_audit_log
    limit = min(int(request.args.get("limit", 50)), 200)
    return jsonify({"entries": read_audit_log(_config.AGV_ID, limit=limit)})


@app.route("/api/mappings/backups", methods=["GET"])
def api_mappings_backups():
    if _config is None:
        return jsonify({"error": "not initialised"}), 503
    from core.mapping_store import list_backups
    return jsonify({"backups": list_backups(_config.AGV_ID)})


@app.route("/api/mappings/restore", methods=["POST"])
def api_mappings_restore():
    if _state is None or _config is None:
        return jsonify({"error": "not initialised"}), 503
    data = request.get_json(silent=True) or {}
    slot = data.get("slot")
    if not isinstance(slot, int) or not (1 <= slot <= 5):
        return jsonify({"error": "slot must be integer 1–5"}), 400
    from core.mapping_store import restore_backup
    rules, errors = restore_backup(_config.AGV_ID, slot)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    _state.mapping_reload_pending = True
    _state.log_event("INFO", f"MAPPING: restored from backup slot {slot} — reload pending")
    return jsonify({"ok": True, "rule_count": len(rules), "reload_pending": True})


@app.route("/api/mappings/export", methods=["GET"])
def api_mappings_export():
    if _config is None:
        return jsonify({"error": "not initialised"}), 503
    from core.mapping_store import export_bundle
    import json as _json
    bundle = export_bundle(_config.AGV_ID)
    from flask import Response
    return Response(
        _json.dumps(bundle, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition":
                 f"attachment; filename={_config.AGV_ID}_sequences.json"},
    )


@app.route("/api/mappings/import", methods=["POST"])
def api_mappings_import():
    if _state is None or _config is None:
        return jsonify({"error": "not initialised"}), 503
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Expected JSON body"}), 400
    from core.mapping_store import import_bundle
    errors = import_bundle(_config.AGV_ID, data)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    _state.mapping_reload_pending = True
    rule_count = len(data.get("rules", []))
    _state.log_event("INFO", f"MAPPING: imported {rule_count} rules — reload pending")
    return jsonify({"ok": True, "rule_count": rule_count, "reload_pending": True})


@app.route("/api/mappings/unmapped", methods=["GET"])
def api_mappings_unmapped():
    if _state is None:
        return jsonify({"error": "not initialised"}), 503
    entries = [
        {"ts": ts, "tag": tag, "tag_dec": int(tag, 16)}
        for ts, tag in list(_state.unmapped_rfid_log)
    ]
    return jsonify({"entries": list(reversed(entries))})


@app.route("/api/wifi")
def api_wifi():
    try:
        out = subprocess.check_output(
            ["nmcli", "-f", "IN-USE,SIGNAL,SSID", "dev", "wifi"],
            timeout=2, text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            if line.strip().startswith("*"):
                parts = line.split()
                signal = int(parts[1])
                ssid   = " ".join(parts[2:])
                return jsonify({"ok": True, "signal": signal, "ssid": ssid})
        return jsonify({"ok": False, "signal": None, "ssid": None})
    except Exception as e:
        return jsonify({"ok": False, "signal": None, "ssid": None, "error": str(e)})


def stop_server():
    """Signal the werkzeug server to stop serving. Safe to call from any thread."""
    global _server
    if _server is not None:
        _server.shutdown()


def run_server(state, engine=None):
    global _state, _engine, _config, _server
    import config
    _state  = state
    _engine = engine
    _config = config

    _server = make_server("0.0.0.0", 5000, app)
    # SO_REUSEADDR: allows immediate rebind after the process dies (TIME_WAIT)
    # SO_REUSEPORT: allows a new process to grab the port while the old one is
    #               still in teardown — useful for fast service restarts.
    _server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        _server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

    logger.info("[Flask] Dashboard at http://%s:5000", config.LOCAL_IP)
    _server.serve_forever()   # blocks until stop_server() calls shutdown()
