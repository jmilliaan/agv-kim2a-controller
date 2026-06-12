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
        self.current_mode     = None   # None | "manual" | "armed" | "running" | "reverse" | "emergency"
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
        self.reverse_auto_request = False   # set by Flask to start reverse tape-follow
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
        self.latest_sensor = None   # last CAN frame dict from MGS1600
        self.can_last_rx   = 0.0    # epoch of last CAN message received


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
        self.ao_queue     = asyncio.Queue()  # commands: (channel_no, voltage)

        # ── Typed domains ──────────────────────────────────────────────────────
        self.system     = SystemState()
        self.kinematic  = KinematicState()
        self.perception = PerceptionState()
        self.tuning     = TuningState()

        # asyncio event loop reference — set by main.py after loop starts.
        # Used by Flask thread to dispatch reload_sequences via call_soon_threadsafe.
        self.loop = None

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
    def reverse_auto_request(self): return self.kinematic.reverse_auto_request
    @reverse_auto_request.setter
    def reverse_auto_request(self, v): self.kinematic.reverse_auto_request = v

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
