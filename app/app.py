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

_Y_LABELS = ["Y0","Y1","Y2","Y3","Y4","Y5","Y6","Y7",
             "Y10","Y11","Y12","Y13","Y14","Y15","Y16","Y17","Y20"]
_X_LABELS = ["X0","X1","X2","X3","X4","X5","X6","X7",
             "X10","X11","X12","X13"]

def _build_di_names():
    import config
    names = ["[SPARE]"] * config.NUM_DI
    mapping = {
        config.DI_START:       "START",
        config.DI_RESET:       "RESET",
        config.DI_MODE_SWITCH: "MODE SWITCH",
        config.DI_EMERGENCY:   "EMERGENCY",
        config.DI_FWD:         "FWD",
        config.DI_REV:         "REV",
        config.DI_LEFT:        "LEFT",
        config.DI_RIGHT:       "RIGHT",
    }
    for idx, label in mapping.items():
        if 0 <= idx < config.NUM_DI:
            names[idx] = label
    return names

def _build_do_names():
    import config
    names = ["[SPARE]"] * config.NUM_DO
    mc = config.MOTOR_CHANNELS
    do_map = {
        mc["left"]["do_fwd"]:    "LEFT FWD",
        mc["left"]["do_rev"]:    "LEFT REV",
        mc["left"]["do_brake"]:  "LEFT BRAKE",
        mc["right"]["do_fwd"]:   "RIGHT FWD",
        mc["right"]["do_rev"]:   "RIGHT REV",
        mc["right"]["do_brake"]: "RIGHT BRAKE",
    }
    for idx, label in do_map.items():
        if 0 <= idx < config.NUM_DO:
            names[idx] = label
    return names


_TROLLEY_WRITABLE = {
    "M2104","M2105","M2106","M2107","M2109",
    "M2124","M2125","M2126","M2127","M2129",
    "M2212","M2213",
}


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


@app.route("/io")
def io_monitor():
    return render_template("io_monitor.html")


@app.route("/params")
def params():
    return render_template("params.html")


@app.route("/api/io")
def api_io():
    import config
    if _state is None:
        return jsonify({"error": "state not initialised"}), 503
    di = _state.latest_di or [False] * config.NUM_DI
    do_raw = _state.latest_do
    return jsonify({
        "di":       list(di[:config.NUM_DI]),
        "di_names": _build_di_names(),
        "do":       [do_raw.get(i, False) for i in range(config.NUM_DO)],
        "do_names": _build_do_names(),
        "num_di":   config.NUM_DI,
        "num_do":   config.NUM_DO,
    })


@app.route("/api/params")
def api_params():
    import config, math
    return jsonify({
        "Networking": {
            "AGV ID":        config.AGV_ID,
            "Local IP":      config.LOCAL_IP,
            "DIO IP":        config.DIO_IP,
            "AO IP":         config.AO_IP,
            "Modbus Port":   config.MODBUS_PORT,
            "RFID IP":       config.RFID_IP,
            "RFID Port":     config.RFID_PORT,
            "SLMP IP":       config.SLMP_IP,
            "SLMP Port":     config.SLMP_PORT,
        },
        "IO Mapping": {
            "DI Base":        config.DI_BASE,
            "DO Base":        config.DO_BASE,
            "Num DI":         config.NUM_DI,
            "Num DO":         config.NUM_DO,
            "AO Base":        config.AO_BASE,
            "Num AO":         config.NUM_AO,
            "DI Flipped":     config.DI_FLIPPED,
            "DI Emergency":   config.DI_EMERGENCY,
            "DI Fwd":         config.DI_FWD,
            "DI Rev":         config.DI_REV,
            "DI Left":        config.DI_LEFT,
            "DI Right":       config.DI_RIGHT,
            "DI Mode Switch": config.DI_MODE_SWITCH,
            "DI Start":       config.DI_START,
            "DI Reset":       config.DI_RESET,
        },
        "Kinematics": {
            "Wheel Diameter (m)":      config.WHEEL_DIAMETER,
            "Gear Ratio":              config.GEAR_RATIO,
            "Wheel Circumference (m)": round(config.WHEEL_CIRCUMFERENCE, 4),
        },
        "Speeds": {
            "Manual High (m/s)":      config.MANUAL_TARGET_HIGH_SPEED,
            "Manual Slow (m/s)":      config.MANUAL_TARGET_SLOW_SPEED,
            "Auto High (m/s)":        config.AUTO_TARGET_HIGH_SPEED,
            "Auto Slow (m/s)":        config.AUTO_TARGET_SLOW_SPEED,
            "Auto Extra Slow (m/s)":  config.AUTO_TARGET_EXTRA_SLOW_SPEED,
            "Accel Rate (m/s²)":      config.ACCEL_RATE,
        },
        "PID — High Speed": {
            "KP":              config.KP,
            "TD":              config.TD,
            "N":               config.N,
        },
        "PID — Slow Speed": {
            "KP Slow":         config.KP_SLOW,
            "TD Slow":         config.TD_SLOW,
            "N Slow":          config.N_SLOW,
        },
        "PID — Shared": {
            "TI":              config.TI,
            "TI Deadband (mm)":config.TI_DEADBAND,
            "TI Max":          config.TI_MAX,
            "DT (s)":          config.DT,
            "Output Clamp RPM":config.OUTPUT_CLAMP_RPM,
            "V Red Coef (High)":config.V_RED_COEF,
            "V Red Coef (Slow)":config.V_RED_COEF_SLOW,
            "SR Alpha":        config.SR_ALPHA,
            "SR Cap":          config.SR_CAP,
        },
        "CAN Sensor": {
            "COB ID":          config.SENSOR_COB_ID,
            "Timeout (s)":     config.CAN_TIMEOUT,
            "Node ID":         config.CAN_NODE_ID,
            "Flag Tape Detect":config.FLAG_TAPE_DETECT,
            "Flag Left Marker":config.FLAG_LEFT_MARKER,
            "Flag Right Marker":config.FLAG_RIGHT_MARKER,
            "Flag Sensor Fail":config.FLAG_SENSOR_FAIL,
        },
        "Watchdog": {
            "DI Timeout (s)":   config.WATCHDOG_DI_TIMEOUT_S,
            "CAN Timeout (s)":  config.WATCHDOG_CAN_TIMEOUT_S,
            "RFID Timeout (s)": config.WATCHDOG_RFID_TIMEOUT_S,
        },
        "Features": {
            "DIO Enabled":  config.DIO_ENABLED,
            "CAN Enabled":  config.CAN_ENABLED,
            "RFID Enabled": config.RFID_ENABLED,
            "SLMP Enabled": config.SLMP_ENABLED,
        },
        "Hardware": {
            "V Range":  config.V_RANGE,
            "DAC Res":  config.DAC_RES,
        },
    })


@app.route("/trolley")
def trolley():
    return render_template("trolley.html")


@app.route("/api/trolley/state")
def api_trolley_state():
    import config
    if _state is None:
        return jsonify({"error": "state not initialised"}), 503

    t = _state.trolley
    top_labels = ["M2104","M2105","M2106","M2107","M2108","M2109"]
    bot_labels = ["M2124","M2125","M2126","M2127","M2128","M2129"]

    return jsonify({
        "slmp_enabled": config.SLMP_ENABLED,
        "agv_mode":     _state.current_mode,
        "y_bits":       dict(zip(_Y_LABELS, t.y_bits)),
        "x_bits":       dict(zip(_X_LABELS, t.x_bits)),
        "top_m_bits":   dict(zip(top_labels, t.top_m_bits)),
        "bot_m_bits":   dict(zip(bot_labels, t.bot_m_bits)),
        "write_bits":   dict(t.write_bits),
    })


@app.route("/api/trolley/write", methods=["POST"])
def api_trolley_write():
    import config
    if _state is None:
        return jsonify({"error": "state not initialised"}), 503
    if not config.SLMP_ENABLED:
        return jsonify({"error": "SLMP disabled"}), 403
    if _state.current_mode != "manual":
        return jsonify({"error": f"AGV not in manual mode (current: {_state.current_mode})"}), 403

    data    = request.get_json(silent=True) or {}
    address = data.get("address")
    value   = data.get("value")

    if address not in _TROLLEY_WRITABLE:
        return jsonify({"error": f"address not writable: {address}"}), 400
    if value not in (0, 1):
        return jsonify({"error": "value must be 0 or 1"}), 400

    _state.trolley.write_bits[address] = value
    return jsonify({"ok": True, "address": address, "value": value})


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
