import asyncio
import time

import config
import motion

from debugging.plotter import RunRecorder


# ══════════════════════════════════════════════════════════════════════════════
#  AUTO MODE
# ══════════════════════════════════════════════════════════════════════════════

async def auto_mode(state):
    await motion.idle(state)
    await motion.set_forward(state, 0.0)

    current_target_speed = 0.0
    integral             = 0.0
    last_pv              = 0.0
    filtered_d           = 0.0
    last_speed_reduction = 0.0
    tape_was_lost        = False
    was_sequence_stopped = False

    # alpha is recomputed each cycle from the active TD and N — see PID block.
    # Initialise with HIGH speed values as the starting default.
    alpha = config.DT / ((config.TD / config.N) + config.DT)

    recorder = RunRecorder()
    recorder.start()

    try:
        while True:

            # ── Emergency guard ───────────────────────────────────────────────
            if state.emergency_active:
                await motion.set_brake(state)
                await asyncio.sleep(config.DT)
                continue

            # ── Sequence stop ─────────────────────────────────────────────────
            if state.sequence_stop:
                if not was_sequence_stopped:
                    print("[AUTO] Sequence stop — holding.")
                    await motion.set_brake(state)
                    current_target_speed = 0.0
                    integral             = 0.0
                    last_pv              = 0.0
                    filtered_d           = 0.0
                    last_speed_reduction = 0.0
                    was_sequence_stopped = True
                await asyncio.sleep(config.DT)
                continue

            if was_sequence_stopped:
                print("[AUTO] Sequence stop ended — resuming.")
                await motion.set_forward(state, 0.0)
                was_sequence_stopped = False

            # ── Resolve target speed from RFID speed_mode ────────────────────
            if state.speed_mode == "EXTRA_SLOW":
                target_speed = config.AUTO_TARGET_EXTRA_SLOW_SPEED
            elif state.speed_mode == "SLOW":
                target_speed = config.AUTO_TARGET_SLOW_SPEED
            else:
                target_speed = config.AUTO_TARGET_HIGH_SPEED

            # ── Select PID gains for current speed mode ───────────────────────
            # HIGH uses a lower KP and less derivative to prevent load-induced
            # oscillation at speed. SLOW/EXTRA_SLOW use higher KP for tighter
            # corner tracking where the slower response margin is greater.
            if state.speed_mode == "HIGH":
                kp = config.KP
                td = config.TD
                n  = config.N
            else:
                kp = config.KP_SLOW
                td = config.TD_SLOW
                n  = config.N_SLOW

            # Recompute derivative filter coefficient whenever gains may change.
            # alpha is cheap to compute and must match the active td and n.
            alpha = config.DT / ((td / n) + config.DT)

            # ── Drain sensor queue — keep only the latest frame ───────────────
            sensor = None
            while not state.sensor_queue.empty():
                sensor = await state.sensor_queue.get()

            if sensor is not None:
                print(sensor["left_marker"])
                if not sensor["tape_detected"]:
                    print("LOST TAPE — stopping")
                    await motion.set_brake(state)
                    current_target_speed = 0.0
                    integral             = 0.0
                    last_pv              = 0.0
                    filtered_d           = 0.0
                    last_speed_reduction = 0.0
                    tape_was_lost        = True
                    await asyncio.sleep(0.01)
                    continue

                if tape_was_lost:
                    print("TAPE REACQUIRED — resuming")
                    await motion.set_forward(state, 0.0)
                    tape_was_lost = False

                # ── Acceleration / deceleration ramp ──────────────────────────
                if current_target_speed < target_speed:
                    current_target_speed += config.ACCEL_RATE * config.DT
                    if current_target_speed > target_speed:
                        current_target_speed = target_speed
                elif current_target_speed > target_speed:
                    current_target_speed -= config.ACCEL_RATE * config.DT
                    if current_target_speed < target_speed:
                        current_target_speed = target_speed

                base_rpm = motion.mps_to_rpm(current_target_speed)

                # ── PID ───────────────────────────────────────────────────────
                pv = sensor["left_mm"]
                e  = 0.0 - pv

                p_term = kp * e

                if config.TI is not None:
                    if abs(e) < config.TI_DEADBAND:
                        integral += e * config.DT
                        integral  = max(-config.TI_MAX, min(config.TI_MAX, integral))
                    i_term = kp * (1.0 / config.TI) * integral
                else:
                    i_term = 0.0

                raw_d      = -kp * td * ((pv - last_pv) / config.DT)
                filtered_d = filtered_d + alpha * (raw_d - filtered_d)

                output = p_term + i_term + filtered_d
                output = max(-config.OUTPUT_CLAMP_RPM, min(config.OUTPUT_CLAMP_RPM, output))

                # ── Speed reduction with low-pass filter ──────────────────────
                v_red                = config.V_RED_COEF if state.speed_mode == "HIGH" else config.V_RED_COEF_SLOW
                raw_sr               = abs(e * v_red) + abs((pv - last_pv) / config.DT) * 0.5
                raw_sr               = min(raw_sr, base_rpm * config.SR_CAP)
                speed_reduction      = last_speed_reduction + config.SR_ALPHA * (raw_sr - last_speed_reduction)
                last_speed_reduction = speed_reduction

                left_rpm  = base_rpm - speed_reduction + output
                right_rpm = base_rpm - speed_reduction - output
                left_v    = max(0.0, min(5.0, motion.rpm_to_voltage(left_rpm)))
                right_v   = max(0.0, min(5.0, motion.rpm_to_voltage(right_rpm)))

                await state.ao_queue.put((0, left_v))
                await state.ao_queue.put((1, right_v))

                # recorder.record(error_mm=e, left_rpm=left_rpm, right_rpm=right_rpm)
                recorder.record(error_mm=e, left_rpm=left_rpm, right_rpm=right_rpm, pid_output=output, d_term=filtered_d)

                print(
                    f"[{state.speed_mode}] "
                    f"Target={current_target_speed:.2f}m/s e={e:+.1f}mm "
                    f"P={p_term:+.1f} I={i_term:+.1f} D={filtered_d:+.1f} "
                    f"out={output:+.1f} L={left_rpm:.1f} R={right_rpm:.1f}rpm "
                    f"Vred={speed_reduction:.1f}rpm"
                )

                last_pv = pv

            # ── CAN timeout ───────────────────────────────────────────────────
            if time.time() - state.can_last_rx > config.CAN_TIMEOUT:
                if not tape_was_lost:
                    print("CAN TIMEOUT — sensor lost, stopping AGV")
                    await motion.set_brake(state)
                    current_target_speed = 0.0
                    integral             = 0.0
                    last_pv              = 0.0
                    filtered_d           = 0.0
                    last_speed_reduction = 0.0
                    tape_was_lost        = True

                await asyncio.sleep(config.DT)
                continue

            await asyncio.sleep(config.DT)

    finally:
        recorder.stop()


# ══════════════════════════════════════════════════════════════════════════════
#  MANUAL MODE
# ══════════════════════════════════════════════════════════════════════════════

async def manual_mode(state):
    """
    Handles pendant jogging.
    Emergency is fully owned by mode_manager — this task does not check it.
    """
    v_high = motion.rpm_to_voltage(motion.mps_to_rpm(config.MANUAL_TARGET_HIGH_SPEED))
    v_slow = motion.rpm_to_voltage(motion.mps_to_rpm(config.MANUAL_TARGET_SLOW_SPEED))
    current_motion = None

    while True:
        di = None
        while not state.di_queue.empty():
            di = await state.di_queue.get()

        if di is None:
            await asyncio.sleep(0.01)
            continue

        pb_fwd   = di[config.DI_FWD]
        pb_rvs   = di[config.DI_REV]
        pb_left  = di[config.DI_LEFT]
        pb_right = di[config.DI_RIGHT]

        if pb_fwd and pb_left:
            motion_state = "fwd_left"
        elif pb_fwd and pb_right:
            motion_state = "fwd_right"
        elif pb_rvs and pb_left:
            motion_state = "rvs_left"
        elif pb_rvs and pb_right:
            motion_state = "rvs_right"
        elif pb_fwd:
            motion_state = "forward"
        elif pb_rvs:
            motion_state = "reverse"
        elif pb_left:
            motion_state = "left"
        elif pb_right:
            motion_state = "right"
        else:
            motion_state = "idle"

        if motion_state != current_motion:
            print(f"Manual: {motion_state.upper()}")
            if   motion_state == "fwd_left":  await motion.set_forward_left(state, v_high, v_slow)
            elif motion_state == "fwd_right": await motion.set_forward_right(state, v_high, v_slow)
            elif motion_state == "rvs_left":  await motion.set_reverse_left(state, v_high, v_slow)
            elif motion_state == "rvs_right": await motion.set_reverse_right(state, v_high, v_slow)
            elif motion_state == "forward":   await motion.set_forward(state, v_high)
            elif motion_state == "reverse":   await motion.set_reverse(state, v_high)
            elif motion_state == "left":      await motion.set_left(state, v_slow)
            elif motion_state == "right":     await motion.set_right(state, v_slow)
            elif motion_state == "idle":      await motion.idle(state)
            current_motion = motion_state

        await asyncio.sleep(0.01)


# ══════════════════════════════════════════════════════════════════════════════
#  MODE MANAGER
# ══════════════════════════════════════════════════════════════════════════════

async def mode_manager(state):
    """
    Central state machine.
    States: None | "manual" | "armed" | "running" | "emergency"

    DI conventions (from parameters.json)
    --------------------------------------
    DI_MODE_SWITCH : HIGH = MANUAL, LOW = AUTO
    DI_EMERGENCY   : NO contact — True = triggered, False = safe
    DI_START       : momentary NO — rising edge = start
    DI_RESET       : momentary NO — rising edge = reset
    """

    current_mode = None
    active_task  = None
    last_start   = False
    last_reset   = False

    async def _cancel_active():
        nonlocal active_task
        if active_task:
            active_task.cancel()
            try:
                await active_task
            except asyncio.CancelledError:
                pass
            active_task = None

    async def _flush_and_idle():
        while not state.do_queue.empty():
            state.do_queue.get_nowait()
        while not state.ao_queue.empty():
            state.ao_queue.get_nowait()
        await motion.idle(state)

    async def _flush_and_brake():
        while not state.do_queue.empty():
            state.do_queue.get_nowait()
        while not state.ao_queue.empty():
            state.ao_queue.get_nowait()
        await motion.set_brake(state)

    def _reset_rfid_state():
        state.speed_mode       = "HIGH"
        state.sequence_stop    = False
        state.pending_sequence = None

    while True:
        if state.latest_di is None:
            await asyncio.sleep(0.01)
            continue

        di             = state.latest_di
        emergency_safe = not di[config.DI_EMERGENCY]
        switch_manual  = di[config.DI_MODE_SWITCH]
        btn_start      = di[config.DI_START]
        btn_reset      = di[config.DI_RESET]

        start_rising = btn_start and not last_start
        reset_rising = btn_reset and not last_reset

        last_start = btn_start
        last_reset = btn_reset

        # ══════════════════════════════════════════════════════════════════════
        #  EMERGENCY
        # ══════════════════════════════════════════════════════════════════════

        if not emergency_safe:
            if current_mode != "emergency":
                print("!! EMERGENCY — all motion stopped")
                await _cancel_active()
                await _flush_and_brake()
                _reset_rfid_state()
                state.emergency_active = True
                current_mode = "emergency"
                state.current_mode = current_mode
            await asyncio.sleep(0.01)
            continue

        # ══════════════════════════════════════════════════════════════════════
        #  EMERGENCY RECOVERY
        # ══════════════════════════════════════════════════════════════════════

        if current_mode == "emergency":
            if not reset_rising:
                await asyncio.sleep(0.01)
                continue

            print("Emergency cleared by operator RESET.")
            state.emergency_active = False

            if switch_manual:
                print("Entering MANUAL.")
                await _flush_and_idle()
                active_task  = asyncio.create_task(manual_mode(state))
                current_mode = "manual"
                state.current_mode = current_mode
            else:
                print("Entering ARMED. Press START to run AUTO.")
                await _flush_and_idle()
                current_mode = "armed"
                state.current_mode = current_mode

            await asyncio.sleep(0.01)
            continue

        # ══════════════════════════════════════════════════════════════════════
        #  NORMAL TRANSITIONS
        # ══════════════════════════════════════════════════════════════════════

        if current_mode is None:
            if switch_manual:
                print("Startup: MANUAL")
                active_task  = asyncio.create_task(manual_mode(state))
                current_mode = "manual"
                state.current_mode = current_mode
            else:
                print("Startup: AUTO selector — ARMED. Press START.")
                current_mode = "armed"
                state.current_mode = current_mode

        elif switch_manual and current_mode != "manual":
            print("Mode switch: MANUAL")
            await _cancel_active()
            await _flush_and_idle()
            _reset_rfid_state()
            active_task  = asyncio.create_task(manual_mode(state))
            current_mode = "manual"
            state.current_mode = current_mode

        elif not switch_manual and current_mode == "manual":
            print("Mode switch: AUTO — ARMED. Place AGV on tape and press START.")
            await _cancel_active()
            await _flush_and_idle()
            current_mode = "armed"
            state.current_mode = current_mode

        elif current_mode == "armed" and start_rising:
            tape_present  = False
            latest_sensor = None
            while not state.sensor_queue.empty():
                latest_sensor = state.sensor_queue.get_nowait()
            if latest_sensor is not None:
                tape_present = latest_sensor.get("tape_detected", False)
                await state.sensor_queue.put(latest_sensor)

            if not tape_present:
                print("START ignored — tape not detected. Place AGV on tape first.")
            else:
                print("START — launching AUTO mode.")
                active_task  = asyncio.create_task(auto_mode(state))
                current_mode = "running"
                state.current_mode = current_mode

        elif current_mode == "running" and reset_rising:
            print("RESET — stopping AUTO, returning to ARMED.")
            await _cancel_active()
            await _flush_and_idle()
            _reset_rfid_state()
            current_mode = "armed"
            state.current_mode = current_mode
            print("ARMED. Press START to run again.")

        await asyncio.sleep(0.01)