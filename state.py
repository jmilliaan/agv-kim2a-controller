import asyncio
import collections
import time

import config

_EVENT_LOG_MAX = 200

# Hard bounds for runtime-tunable auto speeds (m/s).
# Prevents an operator from typing a runaway value into the dashboard.
AUTO_SPEED_MIN = 0.05
AUTO_SPEED_MAX = 1.50


# ── Typed state domains ───────────────────────────────────────────────────────

class SystemState:
    """Owned by mode_manager and safety_watchdog."""
    def __init__(self):
        self.current_mode     = None   # None | "manual" | "armed" | "running" | "emergency"
        self.emergency_active    = False
        self.system_error        = False   # critical driver lost (DIO) — blocks all modes
        self.system_error_detail = ""
        self.sensor_error        = False   # non-critical driver lost (CAN/RFID) — blocks auto only
        self.sensor_error_detail = ""
        self.bumper_active       = False   # True while impact bumper is triggered
        self.event_log           = []      # list of {ts, level, msg} — max _EVENT_LOG_MAX entries


class KinematicState:
    """Owned by sequence_engine and mode_manager."""
    def __init__(self):
        self.speed_mode           = "SLOW"  # "HIGH" | "SLOW" | "EXTRA_SLOW"
        self.sequence_stop        = False   # True = AGV should brake and wait
        self.pending_sequence     = None    # name of armed rfid_then_marker sequence
        self.web_manual_command    = None    # set by Flask remote; "forward"|"reverse"|"left"|"right"|"fwd_left"|"fwd_right"|"rvs_left"|"rvs_right"|None
        self.web_manual_command_ts = 0.0    # epoch of last web command update
        self.motion_telemetry     = None    # dict: left_rpm, right_rpm, pid_error, pid_p/i/d, pid_output
        self.last_rfid_tag        = None    # last 4-char hex tag string read by RFID reader
        self.last_rfid_tag_ts     = 0.0     # epoch of last valid RFID tag received
        self.web_pusher_request   = None    # "up" | "down" | None — set by Flask, consumed by manual_mode
        self.at_home              = False   # False = treat next tag-10 as arrival; True = treat as departure
        self.end_cycle_request    = False   # set by end_cycle sequence action; mode_manager transitions to ARMED
        self.unmapped_rfid_log    = collections.deque(maxlen=50)  # (timestamp, tag_hex) for unmapped tags


class PerceptionState:
    """Written by hardware drivers; read by auto_mode and sequence_engine."""
    def __init__(self):
        self.latest_di     = None   # latest DI bits from Modbus (list of bool)
        self.latest_do     = None   # latest commanded DO state  (list of bool)
        self.latest_sensor = None   # last CAN frame dict from the SICK MLS
        self.can_last_rx   = 0.0    # epoch of last CAN message received

        # ── BLVD-KRD drive telemetry (written by CANMotorDriver) ──────────────
        self.motor_actual_rpm = {"left": 0.0, "right": 0.0}  # CiA-402 0x606C readback
        self.motor_statusword = {"left": 0,   "right": 0}    # CiA-402 0x6041 readback
        self.motor_fault      = None  # None | "left" | "right" — drive in CiA-402 FAULT


class TuningState:
    """Runtime-tunable feature toggles and parameters.

    These mirror selected fields from `config` so the operator can flip them
    from the dashboard without restarting the controller. Defaults are taken
    from the active profile JSON at startup.
    """
    def __init__(self):
        self.mapping_reload_pending = False  # set by save endpoint; cleared on ARMED reload

        # Feature toggles — default ENABLED at boot regardless of profile flags.
        # The profile's LIDAR_STOP_ENABLED / RFID_ENABLED are no longer consulted
        # for the runtime gate; they remain only as deployment-time hints.
        self.lidar_stop_enabled = True
        self.lidar_slow_enabled = True
        self.rfid_enabled       = True

        # Auto target speeds (m/s) — bounded by AUTO_SPEED_MIN/MAX.
        self.auto_high_speed       = float(config.AUTO_TARGET_HIGH_SPEED)
        self.auto_slow_speed       = float(config.AUTO_TARGET_SLOW_SPEED)
        self.auto_extra_slow_speed = float(config.AUTO_TARGET_EXTRA_SLOW_SPEED)


class FleetState:
    """EVO fleet (MQTT) domain — only meaningful when config.FLEET_MODE is on.

    Written by the MQTT client thread (single-field, GIL-safe) and the
    mode_manager / mission FSM. The MQTT layer never touches hardware; it only
    reads/writes these fields and puts onto mqtt_out_queue.
    """
    def __init__(self):
        # Mission assignment from the store (cmd/mission). None = no active mission.
        #   {loop, active_stops:[{order,tag,trolley_type}], loading:{front,rear}, trip_id}
        self.mission        = None
        self.mission_state  = "IDLE_HOME"   # EVO mission FSM state (core/mission.py)
        self.direction      = "outbound"     # logical inbound/outbound flag (NOT a drive direction)
        self.current_stop   = None           # tag id of the stop being serviced (retained state snapshot)

        # Traffic / pause overlays (commanded Cat-2 holds — non-fault, no brake).
        self.traffic_hold   = False          # set by cmd/traffic=stop, cleared by =go
        self.commanded_pause = False         # set by cmd/control=pause, cleared by =resume

        # Commanded control requests, consumed by mode_manager (rising-edge style).
        self.cmd_estop      = False          # cmd/control=estop → emergency overlay
        self.control_reset_request = False   # cmd/control=reset → back to armed (clears mission)
        self.mission_start_request = False   # an accepted cmd/mission asks armed→running

        # Human confirm gating at each handoff (AT_ATTACH / AT_STOP_n / AT_HOME_UNLOAD).
        self.confirm_pending = False
        self.confirm_ts      = 0.0           # epoch the current confirm gate opened (100 s alarm)
        self.confirm_location = None         # str describing the gated location (for events)
        self.web_confirm_request = False     # web manual fallback can satisfy a confirm

        # Command de-dup + broker link.
        self.last_applied_seq = -1           # per-AGV monotonic; rejects stale/replayed commands
        self.store_link_ok    = False        # broker connected (NOT a motion fault when False)


# ── Shared state object ───────────────────────────────────────────────────────

class AMRState:
    """
    Holds all shared queues and state domains for the AMR.
    Must be instantiated inside an active asyncio event loop.

    All fields are also accessible as flat attributes via property shims
    so existing code continues to work without changes.
    """

    def __init__(self):
        # ── Queues for cross-task communication ───────────────────────────────
        self.di_queue     = asyncio.Queue()  # DI readings
        self.rfid_queue   = asyncio.Queue()  # RFID tag reads
        self.sensor_queue = asyncio.Queue()  # CAN magnetic sensor readings
        self.do_queue     = asyncio.Queue()  # commands: (channel_no, state)
        self.motor_queue  = asyncio.Queue()  # commands: (side, target_rpm) | ("brake", bool)
        # The single asyncio→MQTT bridge. Items: (topic, payload_dict, qos, retain).
        # Drained by the MQTT client thread; nothing else publishes. [EVO]
        self.mqtt_out_queue = asyncio.Queue()

        # ── Typed domains ──────────────────────────────────────────────────────
        self.system     = SystemState()
        self.kinematic  = KinematicState()
        self.perception = PerceptionState()
        self.tuning     = TuningState()
        self.fleet      = FleetState()

        # asyncio event loop reference — set by main.py after loop starts.
        # Used by Flask thread to dispatch reload_sequences via call_soon_threadsafe.
        self.loop = None

        # EVO mission FSM instance — set by main.py when FLEET_MODE is on; shared
        # by mode_manager (confirm/advance) and rfid_processor (on_tag). None when
        # standalone. [EVO]
        self.mission_fsm = None

    # ── SystemState shims ─────────────────────────────────────────────────────

    @property
    def current_mode(self): return self.system.current_mode
    @current_mode.setter
    def current_mode(self, v): self.system.current_mode = v

    @property
    def emergency_active(self): return self.system.emergency_active
    @emergency_active.setter
    def emergency_active(self, v): self.system.emergency_active = v

    @property
    def system_error(self): return self.system.system_error
    @system_error.setter
    def system_error(self, v): self.system.system_error = v

    @property
    def system_error_detail(self): return self.system.system_error_detail
    @system_error_detail.setter
    def system_error_detail(self, v): self.system.system_error_detail = v

    @property
    def sensor_error(self): return self.system.sensor_error
    @sensor_error.setter
    def sensor_error(self, v): self.system.sensor_error = v

    @property
    def sensor_error_detail(self): return self.system.sensor_error_detail
    @sensor_error_detail.setter
    def sensor_error_detail(self, v): self.system.sensor_error_detail = v

    @property
    def bumper_active(self): return self.system.bumper_active
    @bumper_active.setter
    def bumper_active(self, v): self.system.bumper_active = v

    @property
    def event_log(self): return self.system.event_log

    def log_event(self, level: str, msg: str):
        """Append a timestamped event to the in-memory log (thread-safe under GIL)."""
        self.system.event_log.append({"ts": time.time(), "level": level, "msg": msg})
        if len(self.system.event_log) > _EVENT_LOG_MAX:
            del self.system.event_log[0]

    # ── KinematicState shims ──────────────────────────────────────────────────

    @property
    def speed_mode(self): return self.kinematic.speed_mode
    @speed_mode.setter
    def speed_mode(self, v): self.kinematic.speed_mode = v

    @property
    def sequence_stop(self): return self.kinematic.sequence_stop
    @sequence_stop.setter
    def sequence_stop(self, v): self.kinematic.sequence_stop = v

    @property
    def pending_sequence(self): return self.kinematic.pending_sequence
    @pending_sequence.setter
    def pending_sequence(self, v): self.kinematic.pending_sequence = v

    @property
    def web_manual_command(self): return self.kinematic.web_manual_command
    @web_manual_command.setter
    def web_manual_command(self, v): self.kinematic.web_manual_command = v

    @property
    def web_manual_command_ts(self): return self.kinematic.web_manual_command_ts
    @web_manual_command_ts.setter
    def web_manual_command_ts(self, v): self.kinematic.web_manual_command_ts = v

    @property
    def motion_telemetry(self): return self.kinematic.motion_telemetry
    @motion_telemetry.setter
    def motion_telemetry(self, v): self.kinematic.motion_telemetry = v

    @property
    def last_rfid_tag(self): return self.kinematic.last_rfid_tag
    @last_rfid_tag.setter
    def last_rfid_tag(self, v): self.kinematic.last_rfid_tag = v

    @property
    def last_rfid_tag_ts(self): return self.kinematic.last_rfid_tag_ts
    @last_rfid_tag_ts.setter
    def last_rfid_tag_ts(self, v): self.kinematic.last_rfid_tag_ts = v

    @property
    def web_pusher_request(self): return self.kinematic.web_pusher_request
    @web_pusher_request.setter
    def web_pusher_request(self, v): self.kinematic.web_pusher_request = v

    @property
    def at_home(self): return self.kinematic.at_home
    @at_home.setter
    def at_home(self, v): self.kinematic.at_home = v

    @property
    def end_cycle_request(self): return self.kinematic.end_cycle_request
    @end_cycle_request.setter
    def end_cycle_request(self, v): self.kinematic.end_cycle_request = v

    @property
    def unmapped_rfid_log(self): return self.kinematic.unmapped_rfid_log

    # ── TuningState shims (mapping) ───────────────────────────────────────────

    @property
    def mapping_reload_pending(self): return self.tuning.mapping_reload_pending
    @mapping_reload_pending.setter
    def mapping_reload_pending(self, v): self.tuning.mapping_reload_pending = bool(v)

    # ── PerceptionState shims ─────────────────────────────────────────────────

    @property
    def latest_di(self): return self.perception.latest_di
    @latest_di.setter
    def latest_di(self, v): self.perception.latest_di = v

    @property
    def latest_do(self): return self.perception.latest_do
    @latest_do.setter
    def latest_do(self, v): self.perception.latest_do = v

    @property
    def latest_sensor(self): return self.perception.latest_sensor
    @latest_sensor.setter
    def latest_sensor(self, v): self.perception.latest_sensor = v

    @property
    def can_last_rx(self): return self.perception.can_last_rx
    @can_last_rx.setter
    def can_last_rx(self, v): self.perception.can_last_rx = v

    @property
    def motor_actual_rpm(self): return self.perception.motor_actual_rpm
    @motor_actual_rpm.setter
    def motor_actual_rpm(self, v): self.perception.motor_actual_rpm = v

    @property
    def motor_statusword(self): return self.perception.motor_statusword
    @motor_statusword.setter
    def motor_statusword(self, v): self.perception.motor_statusword = v

    @property
    def motor_fault(self): return self.perception.motor_fault
    @motor_fault.setter
    def motor_fault(self, v): self.perception.motor_fault = v

    # ── TuningState shims ─────────────────────────────────────────────────────

    @property
    def lidar_stop_enabled(self): return self.tuning.lidar_stop_enabled
    @lidar_stop_enabled.setter
    def lidar_stop_enabled(self, v): self.tuning.lidar_stop_enabled = bool(v)

    @property
    def lidar_slow_enabled(self): return self.tuning.lidar_slow_enabled
    @lidar_slow_enabled.setter
    def lidar_slow_enabled(self, v): self.tuning.lidar_slow_enabled = bool(v)

    @property
    def rfid_enabled(self): return self.tuning.rfid_enabled
    @rfid_enabled.setter
    def rfid_enabled(self, v): self.tuning.rfid_enabled = bool(v)

    @property
    def auto_high_speed(self): return self.tuning.auto_high_speed
    @auto_high_speed.setter
    def auto_high_speed(self, v): self.tuning.auto_high_speed = float(v)

    @property
    def auto_slow_speed(self): return self.tuning.auto_slow_speed
    @auto_slow_speed.setter
    def auto_slow_speed(self, v): self.tuning.auto_slow_speed = float(v)

    @property
    def auto_extra_slow_speed(self): return self.tuning.auto_extra_slow_speed
    @auto_extra_slow_speed.setter
    def auto_extra_slow_speed(self, v): self.tuning.auto_extra_slow_speed = float(v)

    # ── FleetState shims [EVO] ────────────────────────────────────────────────

    @property
    def mission(self): return self.fleet.mission
    @mission.setter
    def mission(self, v): self.fleet.mission = v

    @property
    def mission_state(self): return self.fleet.mission_state
    @mission_state.setter
    def mission_state(self, v): self.fleet.mission_state = v

    @property
    def direction(self): return self.fleet.direction
    @direction.setter
    def direction(self, v): self.fleet.direction = v

    @property
    def current_stop(self): return self.fleet.current_stop
    @current_stop.setter
    def current_stop(self, v): self.fleet.current_stop = v

    @property
    def traffic_hold(self): return self.fleet.traffic_hold
    @traffic_hold.setter
    def traffic_hold(self, v): self.fleet.traffic_hold = bool(v)

    @property
    def commanded_pause(self): return self.fleet.commanded_pause
    @commanded_pause.setter
    def commanded_pause(self, v): self.fleet.commanded_pause = bool(v)

    @property
    def cmd_estop(self): return self.fleet.cmd_estop
    @cmd_estop.setter
    def cmd_estop(self, v): self.fleet.cmd_estop = bool(v)

    @property
    def control_reset_request(self): return self.fleet.control_reset_request
    @control_reset_request.setter
    def control_reset_request(self, v): self.fleet.control_reset_request = bool(v)

    @property
    def mission_start_request(self): return self.fleet.mission_start_request
    @mission_start_request.setter
    def mission_start_request(self, v): self.fleet.mission_start_request = bool(v)

    @property
    def confirm_pending(self): return self.fleet.confirm_pending
    @confirm_pending.setter
    def confirm_pending(self, v): self.fleet.confirm_pending = bool(v)

    @property
    def confirm_ts(self): return self.fleet.confirm_ts
    @confirm_ts.setter
    def confirm_ts(self, v): self.fleet.confirm_ts = float(v)

    @property
    def confirm_location(self): return self.fleet.confirm_location
    @confirm_location.setter
    def confirm_location(self, v): self.fleet.confirm_location = v

    @property
    def web_confirm_request(self): return self.fleet.web_confirm_request
    @web_confirm_request.setter
    def web_confirm_request(self, v): self.fleet.web_confirm_request = bool(v)

    @property
    def last_applied_seq(self): return self.fleet.last_applied_seq
    @last_applied_seq.setter
    def last_applied_seq(self, v): self.fleet.last_applied_seq = int(v)

    @property
    def store_link_ok(self): return self.fleet.store_link_ok
    @store_link_ok.setter
    def store_link_ok(self, v): self.fleet.store_link_ok = bool(v)
