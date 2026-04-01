import config

# ── Kinematic Math ────────────────────────────────────────────────────────────

def voltage_to_rpm(voltage):
    rpm = 646.59 * voltage - 101.2
    return rpm

def rpm_to_voltage(rpm):
    voltage = (rpm + 101.2) / 646.59
    return voltage

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
