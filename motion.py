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
    left_fwd/right_fwd: True = forward, False = reverse.
    AO channel 0 = left wheel, AO channel 1 = right wheel.
    DO 0/1/2 = left fwd/rev/brake, DO 3/4/5 = right fwd/rev/brake.
    """
    await state.do_queue.put((0, left_fwd))
    await state.do_queue.put((1, not left_fwd))
    await state.do_queue.put((2, False))

    await state.do_queue.put((3, right_fwd))
    await state.do_queue.put((4, not right_fwd))
    await state.do_queue.put((5, False))
    
    await state.ao_queue.put((0, left_v))
    await state.ao_queue.put((1, right_v))

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
    await state.ao_queue.put((0, 0.0))
    await state.ao_queue.put((1, 0.0))
    
    await state.do_queue.put((0, False))
    await state.do_queue.put((1, False))
    await state.do_queue.put((2, True))
    
    await state.do_queue.put((3, False))
    await state.do_queue.put((4, False))
    await state.do_queue.put((5, True))

async def idle(state):
    await state.ao_queue.put((0, 0.0))
    await state.ao_queue.put((1, 0.0))
    
    await state.do_queue.put((0, False))
    await state.do_queue.put((1, False))
    await state.do_queue.put((2, False))
    
    await state.do_queue.put((3, False))
    await state.do_queue.put((4, False))
    await state.do_queue.put((5, False))    