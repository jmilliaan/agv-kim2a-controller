import asyncio
import config

# ── Kinematic Math ────────────────────────────────────────────────────────────

def voltage_to_rpm(voltage, wheel="left"):
    # Per-wheel motor calibration (config defaults reproduce the old 646.59 / -101.2).
    a, b = config.MOTOR_CAL[wheel]
    return a * voltage + b

def rpm_to_voltage(rpm, wheel="left"):
    a, b = config.MOTOR_CAL[wheel]
    return (rpm - b) / a

def mps_to_rpm(v_meter_per_second):
    v_meter_per_minute = v_meter_per_second * 60
    wheel_rpm = v_meter_per_minute / config.WHEEL_CIRCUMFERENCE
    motor_rpm = wheel_rpm * config.GEAR_RATIO
    return motor_rpm

# ── Motion Commands ───────────────────────────────────────────────────────────

async def _drive(state, left_fwd: bool, left_v: float, right_fwd: bool, right_v: float):
    """
    Core drive primitive. Sets direction DO and speed AO for both wheels.
    Channel mapping is read from config.MOTOR_CHANNELS (set in profile JSON).
    """
    lch = config.MOTOR_CHANNELS["left"]
    rch = config.MOTOR_CHANNELS["right"]

    await state.do_queue.put((lch["do_fwd"],   left_fwd))
    await state.do_queue.put((lch["do_rev"],   not left_fwd))
    await state.do_queue.put((lch["do_brake"], False))

    await state.do_queue.put((rch["do_fwd"],   right_fwd))
    await state.do_queue.put((rch["do_rev"],   not right_fwd))
    await state.do_queue.put((rch["do_brake"], False))

    await state.ao_queue.put((lch["ao_speed"], left_v))
    await state.ao_queue.put((rch["ao_speed"], right_v))

async def set_forward(state, v):
    await _drive(state, True, v, True, v)

async def set_reverse(state, v):
    await _drive(state, False, v, False, v)

async def set_left(state, v):
    # spin left: left wheel reverse, right wheel forward
    await _drive(state, False, v, True, v)

async def set_right(state, v):
    # spin right: left wheel forward, right wheel reverse
    await _drive(state, True, v, False, v)

async def set_forward_left(state, v_fast, v_slow):
    # both wheels forward, right (outer) faster than left (inner)
    await _drive(state, True, v_slow, True, v_fast)

async def set_forward_right(state, v_fast, v_slow):
    # both wheels forward, left (outer) faster than right (inner)
    await _drive(state, True, v_fast, True, v_slow)

async def set_reverse_left(state, v_fast, v_slow):
    # both wheels reverse, right (outer) faster than left (inner)
    await _drive(state, False, v_slow, False, v_fast)

async def set_reverse_right(state, v_fast, v_slow):
    # both wheels reverse, left (outer) faster than right (inner)
    await _drive(state, False, v_fast, False, v_slow)

async def update_voltages(state, left_v: float, right_v: float):
    """AO-only speed update. Direction relays unchanged.

    Use during smooth acceleration: the direction was set by a prior set_* call,
    so re-issuing DO writes every cycle would flood the DO queue (each Modbus
    coil write is ~10 ms). This helper queues only the two AO writes.
    """
    lch = config.MOTOR_CHANNELS["left"]
    rch = config.MOTOR_CHANNELS["right"]
    await state.ao_queue.put((lch["ao_speed"], left_v))
    await state.ao_queue.put((rch["ao_speed"], right_v))

async def set_cat1_stop(state, decel_wait: float = 0.3):
    """Category 1 stop (IEC 60204-1): zero the speed reference so the drive
    decelerates on its own ramp, then apply the mechanical brake after
    decel_wait seconds once the AGV has slowed to a standstill.
    Use for: emergency transitions, LiDAR inner zone, impact bumper.
    """
    lch = config.MOTOR_CHANNELS["left"]
    rch = config.MOTOR_CHANNELS["right"]
    # Phase 1 — command zero speed; drive decelerates on its internal ramp
    await state.ao_queue.put((lch["ao_speed"], 0.0))
    await state.ao_queue.put((rch["ao_speed"], 0.0))
    # Phase 2 — wait for the AGV to reach standstill
    await asyncio.sleep(decel_wait)
    # Phase 3 — apply mechanical brake and clear direction commands
    await state.do_queue.put((lch["do_fwd"],   False))
    await state.do_queue.put((lch["do_rev"],   False))
    await state.do_queue.put((lch["do_brake"], True))
    await state.do_queue.put((rch["do_fwd"],   False))
    await state.do_queue.put((rch["do_rev"],   False))
    await state.do_queue.put((rch["do_brake"], True))

async def set_brake(state):
    lch = config.MOTOR_CHANNELS["left"]
    rch = config.MOTOR_CHANNELS["right"]
    await state.ao_queue.put((lch["ao_speed"], 0.0))
    await state.ao_queue.put((rch["ao_speed"], 0.0))
    await state.do_queue.put((lch["do_fwd"],   False))
    await state.do_queue.put((lch["do_rev"],   False))
    await state.do_queue.put((lch["do_brake"], True))
    await state.do_queue.put((rch["do_fwd"],   False))
    await state.do_queue.put((rch["do_rev"],   False))
    await state.do_queue.put((rch["do_brake"], True))

async def idle(state):
    lch = config.MOTOR_CHANNELS["left"]
    rch = config.MOTOR_CHANNELS["right"]
    await state.ao_queue.put((lch["ao_speed"], 0.0))
    await state.ao_queue.put((rch["ao_speed"], 0.0))
    await state.do_queue.put((lch["do_fwd"],   False))
    await state.do_queue.put((lch["do_rev"],   False))
    await state.do_queue.put((lch["do_brake"], False))
    await state.do_queue.put((rch["do_fwd"],   False))
    await state.do_queue.put((rch["do_rev"],   False))
    await state.do_queue.put((rch["do_brake"], False))


# ── Pusher (linear actuator) ──────────────────────────────────────────────────
# Relay truth table (from wiring diagram):
#   DO8  + DO11 → Pole1(+), Pole2(-) → UP   (extend)
#   DO9  + DO10 → Pole1(-), Pole2(+) → DOWN (retract)
# Fatal combinations (short-circuit): DO8+DO9 or DO10+DO11
# Safe but inactive:                  DO8+DO10 or DO9+DO11
#
# The 0.2s sleep between clear and set is mandatory — it gives the de-energizing
# relay time to physically open before the opposing relay closes.  Without it,
# the queue can deliver both writes to the Modbus device within the same
# scan cycle, causing a momentary short-circuit on Pole 1 or Pole 2.

async def pusher_clear(state):
    """De-energize all pusher relay channels. Safe to call at any time."""
    if config.PUSHER_CHANNELS is None:
        return
    for ch in config.PUSHER_CHANNELS["extend"] + config.PUSHER_CHANNELS["retract"]:
        await state.do_queue.put((ch, False))

def _pusher_all_off_nowait(state):
    """Fail-safe: enqueue OFF for every pusher channel without yielding to the loop.

    Used from cancellation handlers where an `await` would itself be cancelled.
    Drops queue items silently if the queue is bounded/full — better to lose a
    cleanup write than to raise during cleanup.
    """
    if config.PUSHER_CHANNELS is None:
        return
    for ch in (config.PUSHER_CHANNELS["extend"] + config.PUSHER_CHANNELS["retract"]):
        try:
            state.do_queue.put_nowait((ch, False))
        except Exception:
            pass

async def pusher_up(state):
    """Drive the actuator UP (extend). Clears retract relays then waits 200ms before energizing extend relays.

    On cancellation (mode switch, new pusher request) the except-block enqueues
    OFF for every pusher channel so the actuator never lands in a half-energised
    state.
    """
    if config.PUSHER_CHANNELS is None:
        return
    try:
        for ch in config.PUSHER_CHANNELS["retract"]:
            await state.do_queue.put((ch, False))
        await asyncio.sleep(0.2)   # relay de-energization delay — do not remove
        for ch in config.PUSHER_CHANNELS["extend"]:
            await state.do_queue.put((ch, True))
    except asyncio.CancelledError:
        _pusher_all_off_nowait(state)
        raise

async def pusher_down(state):
    """Drive the actuator DOWN (retract). Clears extend relays then waits 200ms before energizing retract relays.

    On cancellation, all pusher channels are forced OFF.
    """
    if config.PUSHER_CHANNELS is None:
        return
    try:
        for ch in config.PUSHER_CHANNELS["extend"]:
            await state.do_queue.put((ch, False))
        await asyncio.sleep(0.2)   # relay de-energization delay — do not remove
        for ch in config.PUSHER_CHANNELS["retract"]:
            await state.do_queue.put((ch, True))
    except asyncio.CancelledError:
        _pusher_all_off_nowait(state)
        raise
