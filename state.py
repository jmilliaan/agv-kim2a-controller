import asyncio

class AMRState:
    """
    Holds all shared queues and variables for the AMR.
    Must be instantiated inside an active asyncio event loop.
    """
    def __init__(self):
        # ── Queues for cross-task communication ───────────────────────────────
        self.di_queue     = asyncio.Queue()  # DI readings
        self.rfid_queue   = asyncio.Queue()  # RFID tag reads
        self.sensor_queue = asyncio.Queue()  # CAN magnetic sensor readings
        self.do_queue     = asyncio.Queue()  # commands: (channel_no, state)
        self.ao_queue     = asyncio.Queue()  # commands: (channel_no, voltage)

        # ── Hardware state ────────────────────────────────────────────────────
        self.latest_di     = None
        self.latest_sensor = None  # last CAN frame dict from MGS1600
        self.can_last_rx   = 0.0   # timestamp of last CAN message received

        # ── Emergency flag ────────────────────────────────────────────────────
        # Set True by mode_manager when DI_EMERGENCY is triggered.
        # Cleared only after emergency is released AND operator presses RESET.
        # Read by auto_mode as a secondary guard. mode_manager is the primary
        # owner — it cancels active tasks and calls set_brake directly.
        self.emergency_active = False

        self.speed_mode         = "HIGH"   # "HIGH" | "SLOW"
        self.sequence_stop      = False    # True = AGV should brake and wait
        self.pending_sequence   = None     # int = approaching seq N (slowing, marker not yet seen)

        # ── PLC / SLMP state ──────────────────────────────────────────────────
        # current_mode mirrors mode_manager's local variable so the SLMP task
        # can compute the correct M2000-M2005 write values without coupling.
        self.current_mode          = None   # None | "manual" | "armed" | "running" | "emergency"
        self.plc_inputs             = {}     # latest M0-M4 values read from the FX5U PLC
        self.plc_sequence_request      = None  # int 0-16 → sets M201x; None → all sequence bits LOW
        self.plc_sequence_pulse_expire = 0.0   # epoch time after which the request bit is auto-cleared
        self.plc_sequence_complete     = [False] * 17  # M2040-M2056: True when PLC signals sequence N done
        