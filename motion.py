import asyncio
import config

# ── Kinematic Math ────────────────────────────────────────────────────────────

def mps_to_rpm(v_meter_per_second):
    v_meter_per_minute = v_meter_per_second * 60
    wheel_rpm = v_meter_per_minute / config.WHEEL_CIRCUMFERENCE
    motor_rpm = wheel_rpm * config.GEAR_RATIO
    return motor_rpm

# ── Motion Commands ───────────────────────────────────────────────────────────
# Wheels are driven over CANopen / CiA-402 Profile Velocity. Each helper emits a
# *signed* logical Target velocity (r/min; + = forward) per side onto
# motor_queue. Per-wheel mirror inversion (motor_can.invert) is applied in the
# CANMotorDriver, never here — see drivers/can_bldc.py.

async def _drive(state, left_rpm: float, right_rpm: float):
    """Core drive primitive — queue signed per-wheel Target velocity."""
    await state.motor_queue.put(("left",  left_rpm))
    await state.motor_queue.put(("right", right_rpm))

async def set_forward(state, rpm):
    await _drive(state, rpm, rpm)

async def set_reverse(state, rpm):
    # Negative-velocity reverse survives only for the manual jog (no auto reverse).
    await _drive(state, -rpm, -rpm)

async def set_left(state, rpm):
    # spin left: left wheel reverse, right wheel forward
    await _drive(state, -rpm, rpm)

async def set_right(state, rpm):
    # spin right: left wheel forward, right wheel reverse
    await _drive(state, rpm, -rpm)

async def set_forward_left(state, rpm_fast, rpm_slow):
    # both wheels forward, right (outer) faster than left (inner)
    await _drive(state, rpm_slow, rpm_fast)

async def set_forward_right(state, rpm_fast, rpm_slow):
    # both wheels forward, left (outer) faster than right (inner)
    await _drive(state, rpm_fast, rpm_slow)

async def set_reverse_left(state, rpm_fast, rpm_slow):
    # both wheels reverse, right (outer) faster than left (inner)
    await _drive(state, -rpm_slow, -rpm_fast)

async def set_reverse_right(state, rpm_fast, rpm_slow):
    # both wheels reverse, left (outer) faster than right (inner)
    await _drive(state, -rpm_fast, -rpm_slow)

async def update_velocities(state, left_rpm: float, right_rpm: float):
    """Fast path: re-queue only the two signed motor Target velocities.

    Used by the auto PID loop and the manual ramp. A velocity command also
    implicitly releases a prior brake (handled in CANMotorDriver).
    """
    await state.motor_queue.put(("left",  left_rpm))
    await state.motor_queue.put(("right", right_rpm))

async def set_cat1_stop(state, decel_wait: float = 0.0):
    """Category 1 stop (IEC 60204-1): zero Target velocity and request a
    CiA-402 Quick stop — the drive decelerates on QUICKSTOP_DECEL (0x6085)
    then holds with its electromagnetic brake.
    Use for: emergency transitions, LiDAR inner zone, impact bumper.
    """
    await state.motor_queue.put(("brake", True))
    if decel_wait:
        await asyncio.sleep(decel_wait)

async def set_brake(state):
    """Zero Target velocity + CiA-402 Quick stop (electromagnetic brake holds)."""
    await state.motor_queue.put(("brake", True))

async def idle(state):
    """Cat-2 hold: command 0 r/min, drives stay OPERATION ENABLED (no brake —
    the AGV can be pushed by hand)."""
    await state.motor_queue.put(("left",  0))
    await state.motor_queue.put(("right", 0))


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

async def pusher_up(state):
    """Drive the actuator UP (extend). Clears retract relays then waits 200ms before energizing extend relays."""
    if config.PUSHER_CHANNELS is None:
        return
    for ch in config.PUSHER_CHANNELS["retract"]:
        await state.do_queue.put((ch, False))
    await asyncio.sleep(0.2)   # relay de-energization delay — do not remove
    for ch in config.PUSHER_CHANNELS["extend"]:
        await state.do_queue.put((ch, True))

async def pusher_down(state):
    """Drive the actuator DOWN (retract). Clears extend relays then waits 200ms before energizing retract relays."""
    if config.PUSHER_CHANNELS is None:
        return
    for ch in config.PUSHER_CHANNELS["extend"]:
        await state.do_queue.put((ch, False))
    await asyncio.sleep(0.2)   # relay de-energization delay — do not remove
    for ch in config.PUSHER_CHANNELS["retract"]:
        await state.do_queue.put((ch, True))
