"""
app/app.py — AGV Web Monitoring Dashboard
==========================================
Flask server running in a daemon thread alongside the asyncio main loop.
Shares the AMRState object directly (GIL-safe for reads; single-attribute
write for plc_sequence_request).

Access from any device on 192.168.2.x: http://192.168.2.100:5000
"""

import os
import re
import subprocess
import time
import threading
from flask import Flask, render_template, jsonify, request
import logging

logger = logging.getLogger(__name__)

# Flask looks for templates relative to the app.py file location
_here = os.path.dirname(os.path.abspath(__file__))
app   = Flask(__name__, template_folder=os.path.join(_here, "templates"))

_state  = None   # set once by run_server()
_engine = None   # set once by run_server()

_NUM_SEQ_BITS = 17


def _build_state_snapshot():
    """Derive the full JSON payload from the shared state object."""
    s = _state
    mode = s.current_mode

    # ── AGV summary ───────────────────────────────────────────────────────────
    agv = {
        "mode":                 mode,
        "emergency":            s.emergency_active,
        "system_error":         s.system_error,
        "speed_mode":           s.speed_mode,
        "sequence_stop":        s.sequence_stop,
        "sequence_request":     s.plc_sequence_request,
        "seq_pulse_active":     s.plc_sequence_request is not None,
        "reverse_auto_request": s.reverse_auto_request,
    }

    # ── What the AGV is writing to the PLC right now ──────────────────────────
    req = s.plc_sequence_request
    plc_writes = {
        "M2000_READY":             mode not in (None, "emergency"),
        "M2001_EMERGENCY":         s.emergency_active,
        "M2002_TROLLEY_EMERGENCY": s.emergency_active,
        "M2003_MANUAL":            mode == "manual",
        "M2004_AUTO":              mode in ("armed", "running", "reverse"),
        "M2005_RUNNING":           mode in ("running", "reverse"),
    }
    for i in range(_NUM_SEQ_BITS):
        plc_writes[f"M{2010 + i}_SEQ{i}"] = (req == i)

    # ── What the AGV is reading from the PLC ─────────────────────────────────
    plc_reads = dict(s.plc_inputs)   # M0–M4
    complete  = s.plc_sequence_complete
    for i in range(_NUM_SEQ_BITS):
        plc_reads[f"M{2040 + i}_SEQ{i}_COMPLETE"] = complete[i] if i < len(complete) else False

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

    return {
        "agv":        agv,
        "plc_writes": plc_writes,
        "plc_reads":  plc_reads,
        "sensor":     sensor,
        "sequences":  sequences,
    }


_VALID_COMMANDS = {"forward", "fwd_left", "fwd_right", "reverse",
                   "rvs_left", "rvs_right", "left", "right"}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/manual")
def manual():
    return render_template("manual.html")

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

    _state.web_manual_command = cmd
    return jsonify({"ok": True, "command": cmd})


@app.route("/api/state")
def api_state():
    if _state is None:
        return jsonify({"error": "state not initialised"}), 503
    return jsonify(_build_state_snapshot())


@app.route("/api/sequence", methods=["POST"])
def api_sequence():
    if _state is None:
        return jsonify({"error": "state not initialised"}), 503

    inputs      = _state.plc_inputs
    master_on   = inputs.get("M4_MASTER_ON",  False)
    manual_mode = inputs.get("M1_MODE_MAN",   False)

    data = request.get_json(silent=True) or {}
    seq  = data.get("sequence")

    if seq is None:
        # CLEAR — requires PLC manual mode
        if not manual_mode:
            return jsonify({"error": "CLEAR requires PLC MANUAL MODE (M1)"}), 403
        _state.plc_sequence_request = None
        return jsonify({"ok": True, "sequence": None})

    # SET — requires MASTER ON and PLC manual mode
    if not master_on:
        return jsonify({"error": "SET requires MASTER ON (M4)"}), 403
    if not manual_mode:
        return jsonify({"error": "SET requires PLC MANUAL MODE (M1)"}), 403
    if not isinstance(seq, int) or not (0 <= seq < _NUM_SEQ_BITS):
        return jsonify({"error": f"sequence must be int 0–{_NUM_SEQ_BITS - 1} or null"}), 400

    _state.plc_sequence_request      = seq
    _state.plc_sequence_pulse_expire = time.time() + 1.0   # 1-second pulse
    return jsonify({"ok": True, "sequence": seq})


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


def run_server(state, engine=None):
    global _state, _engine
    import config
    _state  = state
    _engine = engine
    logger.info("[Flask] Dashboard at http://%s:5000", config.LOCAL_IP)
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
