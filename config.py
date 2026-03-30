import json
import os

# ── Construct Absolute Path ──────────────────────────────────────────────────
_dir = os.path.dirname(os.path.abspath(__file__))
_param_path = os.path.join(_dir, "parameters.json")

# ── Load JSON Data ───────────────────────────────────────────────────────────
try:
    with open(_param_path, "r") as f:
        _params = json.load(f)
except FileNotFoundError:
    raise FileNotFoundError(f"CRITICAL: parameters.json not found at {_param_path}")
except json.JSONDecodeError as e:
    raise ValueError(f"CRITICAL: parameters.json is malformed: {e}")

# ── Networking ───────────────────────────────────────────────────────────────
DIO_IP      = _params["networking"]["DIO_IP"]
AO_IP       = _params["networking"]["AO_IP"]
MODBUS_PORT = _params["networking"]["MODBUS_PORT"]
DEVICE_ID   = _params["networking"]["DEVICE_ID"]
RFID_IP     = _params["networking"]["RFID_IP"]
RFID_PORT   = _params["networking"]["RFID_PORT"]
SLMP_IP     = _params["networking"]["SLMP_IP"]
SLMP_PORT   = _params["networking"]["SLMP_PORT"]

# ── IO Mapping ───────────────────────────────────────────────────────────────
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

# ── Hardware Specs ───────────────────────────────────────────────────────────
V_RANGE = _params["hardware"]["V_RANGE"]
DAC_RES = _params["hardware"]["DAC_RES"]

# ── Kinematics ───────────────────────────────────────────────────────────────
WHEEL_DIAMETER      = _params["kinematics"]["WHEEL_DIAMETER"]
GEAR_RATIO          = _params["kinematics"]["GEAR_RATIO"]
WHEEL_CIRCUMFERENCE = 3.14159 * WHEEL_DIAMETER

# ── Speeds ───────────────────────────────────────────────────────────────────
MANUAL_TARGET_HIGH_SPEED     = _params["speeds"]["MANUAL_TARGET_HIGH_SPEED"]
MANUAL_TARGET_SLOW_SPEED     = _params["speeds"]["MANUAL_TARGET_SLOW_SPEED"]
AUTO_TARGET_HIGH_SPEED       = _params["speeds"]["AUTO_TARGET_HIGH_SPEED"]
AUTO_TARGET_SLOW_SPEED       = _params["speeds"]["AUTO_TARGET_SLOW_SPEED"]
AUTO_TARGET_EXTRA_SLOW_SPEED = _params["speeds"]["AUTO_TARGET_EXTRA_SLOW_SPEED"]
ACCEL_RATE                   = _params["speeds"]["ACCEL_RATE"]

# ── PID Tuning — HIGH speed (0.4 m/s, loaded) ────────────────────────────────
KP = _params["pid_tuning"]["KP"]
TD = _params["pid_tuning"]["TD"]
N  = _params["pid_tuning"]["N"]

# ── PID Tuning — SLOW / EXTRA_SLOW speed (≤0.25 m/s) ────────────────────────
KP_SLOW = _params["pid_tuning"]["KP_SLOW"]
TD_SLOW = _params["pid_tuning"]["TD_SLOW"]
N_SLOW  = _params["pid_tuning"]["N_SLOW"]

# ── PID Tuning — Shared (integral + timing) ───────────────────────────────────
TI          = _params["pid_tuning"]["TI"]
TI_DEADBAND = _params["pid_tuning"]["TI_DEADBAND"]
TI_MAX      = _params["pid_tuning"]["TI_MAX"]
DT          = _params["pid_tuning"]["DT"]

# ── PID Tuning — Speed reduction ─────────────────────────────────────────────
V_RED_COEF       = _params["pid_tuning"]["V_RED_COEF"]
V_RED_COEF_SLOW  = _params["pid_tuning"]["V_RED_COEF_SLOW"]
OUTPUT_CLAMP_RPM = _params["pid_tuning"]["OUTPUT_CLAMP_RPM"]
SR_ALPHA         = _params["pid_tuning"]["SR_ALPHA"]
SR_CAP           = _params["pid_tuning"]["SR_CAP"]

# ── CAN Sensor ───────────────────────────────────────────────────────────────
SENSOR_COB_ID    = _params["can_sensor"]["SENSOR_COB_ID"]
FLAG_TAPE_DETECT  = _params["can_sensor"]["FLAG_TAPE_DETECT"]
FLAG_LEFT_MARKER  = _params["can_sensor"]["FLAG_LEFT_MARKER"]
FLAG_RIGHT_MARKER = _params["can_sensor"]["FLAG_RIGHT_MARKER"]
FLAG_SENSOR_FAIL  = _params["can_sensor"]["FLAG_SENSOR_FAIL"]
CAN_TIMEOUT      = _params["can_sensor"]["CAN_TIMEOUT"]
CAN_NODE_ID      = _params["can_sensor"]["CAN_NODE_ID"]

# ── RFID ─────────────────────────────────────────────────────────────────────
RFID_INIT_CMD       = bytes.fromhex(_params["rfid"]["RFID_INIT_CMD_HEX"])
RFID_COMMANDS       = _params["rfid_commands"]
SEQUENCE_STOP_DELAY = _params["rfid"]["SEQUENCE_STOP_DELAY"]