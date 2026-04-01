import asyncio


# ── Typed state domains ───────────────────────────────────────────────────────

class SystemState:
    """Owned by mode_manager and safety_watchdog."""
    def __init__(self):
        self.current_mode     = None   # None | "manual" | "armed" | "running" | "reverse" | "emergency"
        self.emergency_active = False
        self.system_error     = False  # set by safety_watchdog on driver comms loss


class KinematicState:
    """Owned by sequence_engine and mode_manager."""
    def __init__(self):
        self.speed_mode           = "HIGH"  # "HIGH" | "SLOW"
        self.sequence_stop        = False   # True = AGV should brake and wait
        self.pending_sequence     = None    # name of armed rfid_then_marker sequence
        self.reverse_auto_request = False   # set by Flask to start reverse tape-follow


class PerceptionState:
    """Written by hardware drivers; read by auto_mode and sequence_engine."""
    def __init__(self):
        self.latest_di     = None   # latest DI bits from Modbus
        self.latest_sensor = None   # last CAN frame dict from MGS1600
        self.can_last_rx   = 0.0    # epoch of last CAN message received


class PLCState:
    """Owned by slmp_handler; read by sequence_engine and dashboard."""
    def __init__(self):
        self.plc_inputs                = {}
        self.plc_sequence_request      = None   # int 0-16 → sets M201x; None → all LOW
        self.plc_sequence_pulse_expire = 0.0    # epoch after which request bit is cleared
        self.plc_sequence_complete     = [False] * 17  # M2040–M2056


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
        self.plc        = PLCState()

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

    # ── PerceptionState shims ─────────────────────────────────────────────────

    @property
    def latest_di(self): return self.perception.latest_di
    @latest_di.setter
    def latest_di(self, v): self.perception.latest_di = v

    @property
    def latest_sensor(self): return self.perception.latest_sensor
    @latest_sensor.setter
    def latest_sensor(self, v): self.perception.latest_sensor = v

    @property
    def can_last_rx(self): return self.perception.can_last_rx
    @can_last_rx.setter
    def can_last_rx(self, v): self.perception.can_last_rx = v

    # ── PLCState shims ────────────────────────────────────────────────────────

    @property
    def plc_inputs(self): return self.plc.plc_inputs
    @plc_inputs.setter
    def plc_inputs(self, v): self.plc.plc_inputs = v

    @property
    def plc_sequence_request(self): return self.plc.plc_sequence_request
    @plc_sequence_request.setter
    def plc_sequence_request(self, v): self.plc.plc_sequence_request = v

    @property
    def plc_sequence_pulse_expire(self): return self.plc.plc_sequence_pulse_expire
    @plc_sequence_pulse_expire.setter
    def plc_sequence_pulse_expire(self, v): self.plc.plc_sequence_pulse_expire = v

    @property
    def plc_sequence_complete(self): return self.plc.plc_sequence_complete
    @plc_sequence_complete.setter
    def plc_sequence_complete(self, v): self.plc.plc_sequence_complete = v
