"""
Configuration loader.

Profile selection
-----------------
Set the AGV_ID environment variable before starting:
    AGV_ID=agv1_kim python3 main.py

If AGV_ID is not set, defaults to "agv1_kim".
Looks for: profiles/{AGV_ID}.json
Falls back to: parameters.json  (backwards-compatibility)
"""

import json
import os
import time

_dir = os.path.dirname(os.path.abspath(__file__))

# ── Profile selection ─────────────────────────────────────────────────────────

AGV_ID = os.environ.get("AGV_ID", "agv1_kim")
_profile_path   = os.path.join(_dir, "profiles", f"{AGV_ID}.json")
_fallback_path  = os.path.join(_dir, "parameters.json")

# Retry the lookup a few times — a transient FS hiccup at boot (slow SD card,
# NFS mount still settling) should not kill the controller before it starts.
_param_path = None
for _attempt in range(3):
    if os.path.exists(_profile_path):
        _param_path = _profile_path
        break
    if os.path.exists(_fallback_path):
        _param_path = _fallback_path
        break
    if _attempt < 2:
        time.sleep(1.0)

if _param_path is None:
    raise FileNotFoundError(
        f"No profile found for AGV_ID='{AGV_ID}'. "
        f"Expected: {_profile_path} or {_fallback_path}"
    )

try:
    with open(_param_path, "r") as f:
        _params = json.load(f)
except json.JSONDecodeError as e:
    raise ValueError(f"CRITICAL: {_param_path} is malformed: {e}")

# ── Networking ────────────────────────────────────────────────────────────────
LOCAL_IP    = _params["networking"].get("LOCAL_IP", "0.0.0.0")
DIO_IP      = _params["networking"]["DIO_IP"]
AO_IP       = _params["networking"]["AO_IP"]
MODBUS_PORT = _params["networking"]["MODBUS_PORT"]
DEVICE_ID   = _params["networking"]["DEVICE_ID"]
RFID_IP     = _params["networking"]["RFID_IP"]
RFID_PORT   = _params["networking"]["RFID_PORT"]
SLMP_IP     = _params["networking"]["SLMP_IP"]
SLMP_PORT   = _params["networking"]["SLMP_PORT"]

# ── IO Mapping ────────────────────────────────────────────────────────────────
DI_FLIPPED     = bool(_params["io_mapping"].get("DI_FLIPPED", 0))
DI_BASE        = _params["io_mapping"]["DI_BASE"]
DO_BASE        = _params["io_mapping"]["DO_BASE"]
NUM_DI         = _params["io_mapping"]["NUM_DI"]
NUM_DO         = _params["io_mapping"]["NUM_DO"]
AO_BASE        = _params["io_mapping"]["AO_BASE"]
NUM_AO         = _params["io_mapping"]["NUM_AO"]
DI_EMERGENCY   = _params["io_mapping"]["DI_EMERGENCY"]
DI_FWD         = _params["io_mapping"]["DI_FWD"]
DI_REV         = _params["io_mapping"]["DI_REV"]
DI_LEFT        = _params["io_mapping"]["DI_LEFT"]
DI_RIGHT       = _params["io_mapping"]["DI_RIGHT"]
DI_MODE_SWITCH      = _params["io_mapping"]["DI_MODE_SWITCH"]
MODE_SWITCH_INVERT  = bool(_params["io_mapping"].get("MODE_SWITCH_INVERT", 0))
DI_START            = _params["io_mapping"]["DI_START"]
DI_RESET            = _params["io_mapping"]["DI_RESET"]

# ── Motor Channels (parameterized — avoids hardcoded DO 0-5 in motion.py) ────
# Falls back to the hardcoded mapping if the profile doesn't have this section
# (backwards-compat with old parameters.json).
MOTOR_CHANNELS = _params.get("motor_channels", {
    "left":  {"do_fwd": 0, "do_rev": 1, "do_brake": 2, "ao_speed": 0},
    "right": {"do_fwd": 3, "do_rev": 4, "do_brake": 5, "ao_speed": 1},
})

# ── Hardware Specs ────────────────────────────────────────────────────────────
V_RANGE = _params["hardware"]["V_RANGE"]
DAC_RES = _params["hardware"]["DAC_RES"]

# ── Kinematics ────────────────────────────────────────────────────────────────
WHEEL_DIAMETER      = _params["kinematics"]["WHEEL_DIAMETER"]
GEAR_RATIO          = _params["kinematics"]["GEAR_RATIO"]
WHEEL_CIRCUMFERENCE = 3.14159 * WHEEL_DIAMETER

# ── Motor voltage↔rpm calibration (per-profile) ───────────────────────────────
# motor_rpm = RPM_PER_VOLT * V + RPM_VOLT_OFFSET  (motion.voltage_to_rpm / rpm_to_voltage).
# Defaults reproduce the historical shared constants, so profiles without a
# "motor_cal" block (agv1_kim, agv2_kim, parameters.json) are unchanged.
_motor_cal      = _params.get("motor_cal", {})
RPM_PER_VOLT    = float(_motor_cal.get("RPM_PER_VOLT",    646.59))
RPM_VOLT_OFFSET = float(_motor_cal.get("RPM_VOLT_OFFSET", -101.2))

# ── Speeds ────────────────────────────────────────────────────────────────────
MANUAL_TARGET_HIGH_SPEED     = _params["speeds"]["MANUAL_TARGET_HIGH_SPEED"]
MANUAL_TARGET_SLOW_SPEED     = _params["speeds"]["MANUAL_TARGET_SLOW_SPEED"]
AUTO_TARGET_HIGH_SPEED       = _params["speeds"]["AUTO_TARGET_HIGH_SPEED"]
AUTO_TARGET_SLOW_SPEED       = _params["speeds"]["AUTO_TARGET_SLOW_SPEED"]
AUTO_TARGET_EXTRA_SLOW_SPEED = _params["speeds"]["AUTO_TARGET_EXTRA_SLOW_SPEED"]
ACCEL_RATE                   = _params["speeds"]["ACCEL_RATE"]
MANUAL_ACCEL_RATE            = _params["speeds"].get("MANUAL_ACCEL_RATE", 2.0 * _params["speeds"]["ACCEL_RATE"])

# ── PID Tuning ────────────────────────────────────────────────────────────────
KP = _params["pid_tuning"]["KP"]
TD = _params["pid_tuning"]["TD"]
N  = _params["pid_tuning"]["N"]

KP_SLOW = _params["pid_tuning"]["KP_SLOW"]
TD_SLOW = _params["pid_tuning"]["TD_SLOW"]
N_SLOW  = _params["pid_tuning"]["N_SLOW"]

TI          = _params["pid_tuning"]["TI"]
TI_DEADBAND = _params["pid_tuning"]["TI_DEADBAND"]
TI_MAX      = _params["pid_tuning"]["TI_MAX"]
DT          = _params["pid_tuning"]["DT"]

V_RED_COEF       = _params["pid_tuning"]["V_RED_COEF"]
V_RED_COEF_SLOW  = _params["pid_tuning"]["V_RED_COEF_SLOW"]
OUTPUT_CLAMP_RPM = _params["pid_tuning"]["OUTPUT_CLAMP_RPM"]
SR_ALPHA         = _params["pid_tuning"]["SR_ALPHA"]
SR_CAP           = _params["pid_tuning"]["SR_CAP"]

# ── CAN Sensor ────────────────────────────────────────────────────────────────
SENSOR_COB_ID    = _params["can_sensor"]["SENSOR_COB_ID"]
FLAG_TAPE_DETECT  = _params["can_sensor"]["FLAG_TAPE_DETECT"]
FLAG_LEFT_MARKER  = _params["can_sensor"]["FLAG_LEFT_MARKER"]
FLAG_RIGHT_MARKER = _params["can_sensor"]["FLAG_RIGHT_MARKER"]
FLAG_SENSOR_FAIL  = _params["can_sensor"]["FLAG_SENSOR_FAIL"]
CAN_TIMEOUT      = _params["can_sensor"]["CAN_TIMEOUT"]
CAN_NODE_ID      = _params["can_sensor"]["CAN_NODE_ID"]

# ── RFID ──────────────────────────────────────────────────────────────────────
RFID_INIT_CMD       = bytes.fromhex(_params["rfid"]["RFID_INIT_CMD_HEX"])
# RFID_COMMANDS kept for backwards-compat if old parameters.json is used
RFID_COMMANDS       = _params.get("rfid_commands", {})
SEQUENCE_STOP_DELAY = _params["rfid"]["SEQUENCE_STOP_DELAY"]

# ── Sequences (Phase 3 — sequence engine) ────────────────────────────────────
SEQUENCES = _params.get("sequences", [])

# ── Feature flags ────────────────────────────────────────────────────────────
_feat = _params.get("features", {})
DIO_ENABLED        = bool(_feat.get("DIO_ENABLED",        1))
CAN_ENABLED        = bool(_feat.get("CAN_ENABLED",        1))
RFID_ENABLED       = bool(_feat.get("RFID_ENABLED",       1))
SLMP_ENABLED       = bool(_feat.get("SLMP_ENABLED",       0))
LIDAR_STOP_ENABLED = bool(_feat.get("LIDAR_STOP_ENABLED", 1))  # 0 = disable inner lidar stop

# ── Watchdog timeouts (Phase 4) ───────────────────────────────────────────────
_wd = _params.get("watchdog", {})
WATCHDOG_DI_TIMEOUT_S   = _wd.get("DI_TIMEOUT_S",   1.0)
WATCHDOG_CAN_TIMEOUT_S  = _wd.get("CAN_TIMEOUT_S",  1.0)
WATCHDOG_RFID_TIMEOUT_S = _wd.get("RFID_TIMEOUT_S", 5.0)

# ── AGV B (TN) — per-profile options (backwards-compatible defaults = AGV A values) ──
AO_MAX_VOLTAGE     = float(_params.get("ao_max_voltage", 10.0))
SENSOR_ORIENTATION = int(_params.get("sensor_orientation", 1))   # 1=normal, -1=flipped
DI_LIDAR_OUTER     = _params["io_mapping"].get("DI_LIDAR_OUTER", None)  # outer zone — no speed change, dashboard only
DI_LIDAR_SLOW      = _params["io_mapping"].get("DI_LIDAR_SLOW",  None)  # middle zone — switches to SLOW
DI_LIDAR_STOP      = _params["io_mapping"].get("DI_LIDAR_STOP",  None)  # inner zone  — Cat 1 protective stop
DI_BUMPER          = _params["io_mapping"].get("DI_BUMPER",     None)  # None = no bumper
PUSHER_CHANNELS    = _params.get("pusher_channels", None)  # None = no pusher
HORN_CHANNELS      = _params.get("horn_channels",   None)  # None = no horn
