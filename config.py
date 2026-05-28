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

_dir = os.path.dirname(os.path.abspath(__file__))

# ── Profile selection ─────────────────────────────────────────────────────────

AGV_ID = os.environ.get("AGV_ID", "agv1_kim")
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
DI_MODE_SWITCH = _params["io_mapping"]["DI_MODE_SWITCH"]
DI_START       = _params["io_mapping"]["DI_START"]
DI_RESET       = _params["io_mapping"]["DI_RESET"]

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

# Motor speed calibration: rpm = RPM_PER_VOLT * volts + RPM_VOLT_OFFSET.
# Per-AGV (drive/motor specific) — defaults preserve the previous hardcoded fit.
RPM_PER_VOLT    = _params["hardware"].get("RPM_PER_VOLT", 646.59)
RPM_VOLT_OFFSET = _params["hardware"].get("RPM_VOLT_OFFSET", -101.2)

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

# ── PID Tuning ────────────────────────────────────────────────────────────────
KP = _params["pid_tuning"]["KP"]
TD = _params["pid_tuning"]["TD"]
N  = _params["pid_tuning"]["N"]

KP_SLOW = _params["pid_tuning"]["KP_SLOW"]
TD_SLOW = _params["pid_tuning"]["TD_SLOW"]
N_SLOW  = _params["pid_tuning"]["N_SLOW"]

KP_EXTRA_SLOW = _params["pid_tuning"]["KP_EXTRA_SLOW"]
TD_EXTRA_SLOW = _params["pid_tuning"]["TD_EXTRA_SLOW"]
N_EXTRA_SLOW  = _params["pid_tuning"]["N_EXTRA_SLOW"]

TI          = _params["pid_tuning"]["TI"]
TI_DEADBAND = _params["pid_tuning"]["TI_DEADBAND"]
TI_MAX      = _params["pid_tuning"]["TI_MAX"]
DT          = _params["pid_tuning"]["DT"]

V_RED_COEF            = _params["pid_tuning"]["V_RED_COEF"]
V_RED_COEF_SLOW       = _params["pid_tuning"]["V_RED_COEF_SLOW"]
V_RED_COEF_EXTRA_SLOW = _params["pid_tuning"]["V_RED_COEF_EXTRA_SLOW"]
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

# Sensor sanity / slew limits for the lateral offset (left_mm) fed to the PID.
# Reject readings beyond ±SENSOR_MAX_MM (sensor half-width is ~80 mm); clamp
# per-cycle jumps larger than SENSOR_MAX_STEP_MM to reject glitch frames.
SENSOR_MAX_MM      = _params["can_sensor"].get("SENSOR_MAX_MM", 100.0)
SENSOR_MAX_STEP_MM = _params["can_sensor"].get("SENSOR_MAX_STEP_MM", 40.0)

# ── RFID ──────────────────────────────────────────────────────────────────────
RFID_INIT_CMD       = bytes.fromhex(_params["rfid"]["RFID_INIT_CMD_HEX"])
# RFID_COMMANDS kept for backwards-compat if old parameters.json is used
RFID_COMMANDS       = _params.get("rfid_commands", {})
SEQUENCE_STOP_DELAY = _params["rfid"]["SEQUENCE_STOP_DELAY"]

# ── Sequences (Phase 3 — sequence engine) ────────────────────────────────────
SEQUENCES = _params.get("sequences", [])

# ── Feature flags ────────────────────────────────────────────────────────────
_feat = _params.get("features", {})
DIO_ENABLED  = bool(_feat.get("DIO_ENABLED",  1))
CAN_ENABLED  = bool(_feat.get("CAN_ENABLED",  1))
RFID_ENABLED = bool(_feat.get("RFID_ENABLED", 1))
SLMP_ENABLED = bool(_feat.get("SLMP_ENABLED", 0))

# ── Watchdog timeouts (Phase 4) ───────────────────────────────────────────────
_wd = _params.get("watchdog", {})
WATCHDOG_DI_TIMEOUT_S   = _wd.get("DI_TIMEOUT_S",   1.0)
WATCHDOG_CAN_TIMEOUT_S  = _wd.get("CAN_TIMEOUT_S",  1.0)
WATCHDOG_RFID_TIMEOUT_S = _wd.get("RFID_TIMEOUT_S", 5.0)


# ── Config validation (fail-fast at boot) ─────────────────────────────────────
class ConfigError(ValueError):
    """Raised at import time when a profile parameter is out of safe range."""


_MAX_SPEED_MPS = 5.0   # sanity ceiling for any commanded speed


def _validate():
    """Range-check critical parameters so the controller refuses to start on a
    bad profile rather than dividing by zero or running away mid-motion."""
    errors = []

    def check(cond, msg):
        if not cond:
            errors.append(msg)

    # Division-by-zero / runaway guards
    check(WHEEL_DIAMETER > 0, f"WHEEL_DIAMETER must be > 0 (got {WHEEL_DIAMETER})")
    check(GEAR_RATIO    > 0, f"GEAR_RATIO must be > 0 (got {GEAR_RATIO})")
    check(DT            > 0, f"DT must be > 0 (got {DT})")
    check(TI is None or TI > 0, f"TI must be > 0 (used as 1/TI) (got {TI})")
    check(V_RANGE       > 0, f"V_RANGE must be > 0 (got {V_RANGE})")
    check(DAC_RES       > 0, f"DAC_RES must be > 0 (got {DAC_RES})")
    check(RPM_PER_VOLT != 0, f"RPM_PER_VOLT must be != 0 (got {RPM_PER_VOLT})")
    check(OUTPUT_CLAMP_RPM > 0, f"OUTPUT_CLAMP_RPM must be > 0 (got {OUTPUT_CLAMP_RPM})")
    check(N      > 0, f"N must be > 0 (got {N})")
    check(N_SLOW > 0, f"N_SLOW must be > 0 (got {N_SLOW})")
    check(N_EXTRA_SLOW > 0, f"N_EXTRA_SLOW must be > 0 (got {N_EXTRA_SLOW})")

    # Speeds: non-negative and within a sane ceiling
    for name, val in [
        ("MANUAL_TARGET_HIGH_SPEED", MANUAL_TARGET_HIGH_SPEED),
        ("MANUAL_TARGET_SLOW_SPEED", MANUAL_TARGET_SLOW_SPEED),
        ("AUTO_TARGET_HIGH_SPEED",   AUTO_TARGET_HIGH_SPEED),
        ("AUTO_TARGET_SLOW_SPEED",   AUTO_TARGET_SLOW_SPEED),
        ("AUTO_TARGET_EXTRA_SLOW_SPEED", AUTO_TARGET_EXTRA_SLOW_SPEED),
    ]:
        check(0 <= val <= _MAX_SPEED_MPS,
              f"{name} must be in [0, {_MAX_SPEED_MPS}] m/s (got {val})")
    check(ACCEL_RATE > 0, f"ACCEL_RATE must be > 0 (got {ACCEL_RATE})")

    # IO mapping sanity
    check(NUM_DI >= 0, f"NUM_DI must be >= 0 (got {NUM_DI})")
    check(NUM_DO >= 0, f"NUM_DO must be >= 0 (got {NUM_DO})")
    check(NUM_AO >= 0, f"NUM_AO must be >= 0 (got {NUM_AO})")

    # Network ports
    for name, val in [("MODBUS_PORT", MODBUS_PORT), ("RFID_PORT", RFID_PORT),
                      ("SLMP_PORT", SLMP_PORT)]:
        check(1 <= val <= 65535, f"{name} must be in 1..65535 (got {val})")

    # Sensor limits
    check(SENSOR_MAX_MM      > 0, f"SENSOR_MAX_MM must be > 0 (got {SENSOR_MAX_MM})")
    check(SENSOR_MAX_STEP_MM > 0, f"SENSOR_MAX_STEP_MM must be > 0 (got {SENSOR_MAX_STEP_MM})")

    if errors:
        raise ConfigError(
            f"Invalid profile '{_param_path}':\n  - " + "\n  - ".join(errors))


_validate()
