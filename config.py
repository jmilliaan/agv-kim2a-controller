"""
Configuration loader.

Profile selection
-----------------
Set the AGV_ID environment variable before starting:
    AGV_ID=agv-evo-01 python3 main.py

If AGV_ID is not set, defaults to "agv-evo-01".
Looks for: profiles/{AGV_ID}.json
Falls back to: parameters.json  (backwards-compatibility)
"""

import json
import os

_dir = os.path.dirname(os.path.abspath(__file__))

# ── Profile selection ─────────────────────────────────────────────────────────

AGV_ID = os.environ.get("AGV_ID", "agv-evo-01")
print(AGV_ID)
_profile_path   = os.path.join(_dir, "profiles", f"{AGV_ID}.json")
_fallback_path  = os.path.join(_dir, "parameters.json")

if os.path.exists(_profile_path):
    _param_path = _profile_path
elif os.path.exists(_fallback_path):
    _param_path = _fallback_path
else:
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
MODBUS_PORT = _params["networking"]["MODBUS_PORT"]
DEVICE_ID   = _params["networking"]["DEVICE_ID"]
RFID_IP     = _params["networking"]["RFID_IP"]
RFID_PORT   = _params["networking"]["RFID_PORT"]

# ── IO Mapping ────────────────────────────────────────────────────────────────
DI_FLIPPED     = bool(_params["io_mapping"].get("DI_FLIPPED", 0))
DI_BASE        = _params["io_mapping"]["DI_BASE"]
DO_BASE        = _params["io_mapping"]["DO_BASE"]
NUM_DI         = _params["io_mapping"]["NUM_DI"]
NUM_DO         = _params["io_mapping"]["NUM_DO"]
DI_EMERGENCY   = _params["io_mapping"]["DI_EMERGENCY"]
DI_FWD         = _params["io_mapping"]["DI_FWD"]
DI_REV         = _params["io_mapping"]["DI_REV"]
DI_LEFT        = _params["io_mapping"]["DI_LEFT"]
DI_RIGHT       = _params["io_mapping"]["DI_RIGHT"]
DI_MODE_SWITCH      = _params["io_mapping"]["DI_MODE_SWITCH"]
MODE_SWITCH_INVERT  = bool(_params["io_mapping"].get("MODE_SWITCH_INVERT", 0))
DI_START            = _params["io_mapping"]["DI_START"]
DI_RESET            = _params["io_mapping"]["DI_RESET"]

# ── Motor CAN drive (BLVD-KRD / CiA-402 over CANopen) ────────────────────────
# Per-side {node_id, invert} + bus-level CHANNEL/BITRATE/EDS/ramps/MOTOR_MAX_RPM.
# Motion never hardcodes node ids; it always reads this map. The block is only
# required when MOTOR_CAN_ENABLED is set (see feature flags) — a bench PC with
# no CAN adapter can drop it and run with the drive disabled.
MOTOR_CAN     = _params.get("motor_can", {})
MOTOR_MAX_RPM = int(MOTOR_CAN.get("MOTOR_MAX_RPM", 3000))

# ── Kinematics ────────────────────────────────────────────────────────────────
WHEEL_DIAMETER      = _params["kinematics"]["WHEEL_DIAMETER"]
GEAR_RATIO          = _params["kinematics"]["GEAR_RATIO"]
WHEEL_CIRCUMFERENCE = 3.14159 * WHEEL_DIAMETER

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

# ── CAN Sensor (SICK MLS magnetic line sensor — CANopen TPDO1) ───────────────
# TPDO1 COB-ID = 0x180 + CAN_NODE_ID. The sensor must be pre-configured (SICK MLS
# config tool / LSS) to this node id and 500 kbit/s to share the BLDC bus; the
# bitrate is not set from here. Steering follows the selected line center point
# (LCP2 for a single track — see drivers/can_mls.py).
_can_sensor      = _params.get("can_sensor", {})
SENSOR_COB_ID    = _can_sensor["SENSOR_COB_ID"]
CAN_NODE_ID      = _can_sensor["CAN_NODE_ID"]
CAN_TIMEOUT      = _can_sensor["CAN_TIMEOUT"]
STEERING_LCP     = int(_can_sensor.get("STEERING_LCP", 2))
LCP_INVALID      = int(_can_sensor.get("LCP_INVALID", 0x7FFF))

# ── RFID ──────────────────────────────────────────────────────────────────────
RFID_INIT_CMD       = bytes.fromhex(_params["rfid"]["RFID_INIT_CMD_HEX"])
# RFID_COMMANDS kept for backwards-compat if old parameters.json is used
RFID_COMMANDS       = _params.get("rfid_commands", {})
SEQUENCE_STOP_DELAY = _params["rfid"]["SEQUENCE_STOP_DELAY"]

# ── Sequences (Phase 3 — sequence engine) ────────────────────────────────────
SEQUENCES = _params.get("sequences", [])

# ── Feature flags ────────────────────────────────────────────────────────────
_feat = _params.get("features", {})
DIO_ENABLED        = bool(_feat.get("DIO_ENABLED",        1))  # Modbus DI+DO module
CAN_ENABLED        = bool(_feat.get("CAN_ENABLED",        1))  # SICK MLS sensor (needs the CAN bus)
MOTOR_CAN_ENABLED  = bool(_feat.get("MOTOR_CAN_ENABLED",  1))  # BLVD-KRD wheel drives + the CAN bus
RFID_ENABLED       = bool(_feat.get("RFID_ENABLED",       1))  # RFID TCP reader
LIDAR_STOP_ENABLED = bool(_feat.get("LIDAR_STOP_ENABLED", 1))  # 0 = disable inner lidar stop

# Every hardware subsystem above can be turned off independently so the
# controller boots cleanly on a PC with nothing connected (no connect errors):
# set all of DIO_ENABLED / CAN_ENABLED / MOTOR_CAN_ENABLED / RFID_ENABLED to 0.
# The SICK MLS sensor shares the wheel-drive CAN bus, so CAN_ENABLED has no effect
# unless MOTOR_CAN_ENABLED is also on.

# ── Watchdog timeouts (Phase 4) ───────────────────────────────────────────────
_wd = _params.get("watchdog", {})
WATCHDOG_DI_TIMEOUT_S   = _wd.get("DI_TIMEOUT_S",   1.0)
WATCHDOG_CAN_TIMEOUT_S  = _wd.get("CAN_TIMEOUT_S",  1.0)
WATCHDOG_RFID_TIMEOUT_S = _wd.get("RFID_TIMEOUT_S", 5.0)

# ── Per-profile options ───────────────────────────────────────────────────────
SENSOR_ORIENTATION = int(_params.get("sensor_orientation", 1))   # 1=normal, -1=flipped
DI_LIDAR_OUTER     = _params["io_mapping"].get("DI_LIDAR_OUTER", None)  # outer zone — no speed change, dashboard only
DI_LIDAR_SLOW      = _params["io_mapping"].get("DI_LIDAR_SLOW",  None)  # middle zone — switches to SLOW
DI_LIDAR_STOP      = _params["io_mapping"].get("DI_LIDAR_STOP",  None)  # inner zone  — Cat 1 protective stop
DI_BUMPER          = _params["io_mapping"].get("DI_BUMPER",     None)  # None = no bumper
PUSHER_CHANNELS    = _params.get("pusher_channels", None)  # None = no pusher
HORN_CHANNELS      = _params.get("horn_channels",   None)  # None = no horn
