import asyncio
import logging
import time

import config
import motion

from core.pid import PIDController
from _debugging.plotter import RunRecorder

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  AUTO MODE  (forward and reverse unified)
# ══════════════════════════════════════════════════════════════════════════════

async def auto_mode(state, direction="forward", engine=None):
    """Tape-following PID loop.

    Args:
        direction: "forward" — sensor at front, speed modes active, sequence
                               stops active, RFID marker hooks active.
                   "reverse" — sensor at rear (error sign inverted), fixed SLOW
                               speed, no sequence stops, no RFID.
        engine:    SequenceEngine instance (Phase 3).  Pass None until then;
                   the marker-hook calls are guarded with `if engine`.
    """
    _orient = float(config.SENSOR_ORIENTATION)
    if direction == "forward":
        await motion.idle(state)
        await motion.set_forward(state, 0.0)
        error_sign = -1.0 * _orient
    else:
        await motion.idle(state)
        await motion.set_reverse(state, 0.0)
        error_sign = 1.0 * _orient

    # Initialise PID with SLOW gains; forward mode will switch to HIGH on first
    # cycle if speed_mode == "HIGH".
    pid = PIDController(
        kp           = config.KP_SLOW,
        td           = config.TD_SLOW,
        n            = config.N_SLOW,
        ti           = config.TI,
        ti_deadband  = config.TI_DEADBAND,
        ti_max       = config.TI_MAX,
        dt           = config.DT,
        output_clamp = config.OUTPUT_CLAMP_RPM,
        v_red_coef   = config.V_RED_COEF_SLOW,
        sr_alpha     = config.SR_ALPHA,
        sr_cap       = config.SR_CAP,
    )

    current_target_speed = 0.0
    tape_was_lost        = False
    was_sequence_stopped = False
    last_speed_mode      = None   # tracks when gain set must change

    recorder = RunRecorder()
    recorder.start()

    try:
        while True:

            # ── Emergency guard ───────────────────────────────────────────────
            if state.emergency_active:
                await motion.set_brake(state)
                await asyncio.sleep(config.DT)
                continue

            # ── Sequence stop (forward only) ──────────────────────────────────
            if direction == "forward" and state.sequence_stop:
                if not was_sequence_stopped:
                    logger.info("[AUTO] Sequence stop — holding.")
                    await motion.set_brake(state)
                    current_target_speed = 0.0
                    pid.reset()
                    was_sequence_stopped = True
                await asyncio.sleep(config.DT)
                continue

            if was_sequence_stopped:
                logger.info("[AUTO] Sequence stop ended — resuming.")
                await motion.set_forward(state, 0.0)
                was_sequence_stopped = False

            # ── Target speed and PID gain selection ───────────────────────────
            if direction == "forward":
                if state.speed_mode == "EXTRA_SLOW":
                    target_speed = config.AUTO_TARGET_EXTRA_SLOW_SPEED
                elif state.speed_mode == "SLOW":
                    target_speed = config.AUTO_TARGET_SLOW_SPEED
                else:
                    target_speed = config.AUTO_TARGET_HIGH_SPEED

                # Only switch gains when speed_mode actually changes — avoids
                # redundant recomputation of the derivative filter coefficient.
                if state.speed_mode != last_speed_mode:
                    if state.speed_mode == "HIGH":
                        pid.update_gains(config.KP, config.TD, config.N,
                                         config.V_RED_COEF)
                    else:
                        pid.update_gains(config.KP_SLOW, config.TD_SLOW,
                                         config.N_SLOW, config.V_RED_COEF_SLOW)
                    last_speed_mode = state.speed_mode
            else:
                target_speed = config.AUTO_TARGET_SLOW_SPEED   # reverse: fixed

            # ── DI snapshot ───────────────────────────────────────────────────
            _di = state.latest_di
            _label = "AUTO" if direction == "forward" else "REVERSE"

            # ── Impact bumper ─────────────────────────────────────────────────
            if config.DI_BUMPER is not None and _di is not None and _di[config.DI_BUMPER]:
                logger.warning("[%s] BUMPER HIT — braking", _label)
                state.log_event("WARNING", f"[{_label}] BUMPER HIT — motors braked")
                state.bumper_active = True
                await motion.set_brake(state)
                current_target_speed = 0.0
                pid.reset()
                # Hold until bumper signal clears (or emergency overrides)
                while True:
                    if state.emergency_active:
                        break
                    _di_now = state.latest_di
                    if _di_now is None or not _di_now[config.DI_BUMPER]:
                        break
                    await asyncio.sleep(0.01)
                state.bumper_active = False
                if state.emergency_active:
                    await asyncio.sleep(config.DT)
                    continue
                logger.info("[%s] BUMPER CLEARED — waiting 2 s before resume", _label)
                state.log_event("INFO", f"[{_label}] BUMPER CLEARED — resuming in 2 s")
                await asyncio.sleep(2.0)
                if direction == "forward":
                    await motion.set_forward(state, 0.0)
                else:
                    await motion.set_reverse(state, 0.0)
                tape_was_lost = False
                continue

            # ── Lidar DI checks (no-op when profile has no lidar channels) ─────
            if config.DI_LIDAR_STOP is not None and _di is not None and _di[config.DI_LIDAR_STOP]:
                if not tape_was_lost:
                    logger.warning("[%s] LIDAR STOP — obstacle detected, braking", _label)
                    state.log_event("WARNING", f"[{_label}] LIDAR STOP — obstacle detected")
                    await motion.set_brake(state)
                    current_target_speed = 0.0
                    pid.reset()
                    tape_was_lost = True
                await asyncio.sleep(config.DT)
                continue
            if config.DI_LIDAR_SLOW is not None and _di is not None and _di[config.DI_LIDAR_SLOW]:
                if state.speed_mode == "HIGH":
                    state.speed_mode = "SLOW"

            # ── Drain sensor queue — keep only the latest frame ───────────────
            sensor = None
            while not state.sensor_queue.empty():
                sensor = await state.sensor_queue.get()

            if sensor is not None:

                if direction == "forward":
                    logger.debug("[AUTO] left_marker=%s", sensor["left_marker"])

                # ── Tape-loss guard ───────────────────────────────────────────
                if not sensor["tape_detected"]:
                    logger.warning("[%s] LOST TAPE — stopping", _label)
                    state.log_event("WARNING", f"[{_label}] TAPE LOST — AGV stopped")
                    await motion.set_brake(state)
                    current_target_speed = 0.0
                    pid.reset()
                    tape_was_lost = True
                    await asyncio.sleep(0.01)
                    continue

                if tape_was_lost:
                    logger.info("[%s] TAPE REACQUIRED — resuming", _label)
                    state.log_event("INFO", f"[{_label}] TAPE REACQUIRED — resuming")
                    if direction == "forward":
                        await motion.set_forward(state, 0.0)
                    else:
                        await motion.set_reverse(state, 0.0)
                    tape_was_lost = False

                # ── Marker hook (forward only — sequence engine, Phase 3) ──────
                if direction == "forward" and engine is not None:
                    if sensor["left_marker"]:
                        await engine.on_marker("left")
                    if sensor["right_marker"]:
                        await engine.on_marker("right")

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

                # ── PID compute ───────────────────────────────────────────────
                pv = sensor["left_mm"]
                left_rpm, right_rpm, dbg = pid.compute(pv, base_rpm, error_sign)

                left_v  = max(0.0, min(config.AO_MAX_VOLTAGE, motion.rpm_to_voltage(left_rpm)))
                right_v = max(0.0, min(config.AO_MAX_VOLTAGE, motion.rpm_to_voltage(right_rpm)))

                await state.ao_queue.put((0, left_v))
                await state.ao_queue.put((1, right_v))

                state.motion_telemetry = {
                    "left_rpm":   left_rpm,
                    "right_rpm":  right_rpm,
                    "pid_error":  dbg["e"],
                    "pid_p":      dbg["p"],
                    "pid_i":      dbg["i"],
                    "pid_d":      dbg["d"],
                    "pid_output": dbg["output"],
                }

                recorder.record(error_mm=dbg["e"], left_rpm=left_rpm,
                                right_rpm=right_rpm, pid_output=dbg["output"],
                                d_term=dbg["d"])

                label = state.speed_mode if direction == "forward" else "REVERSE"
                logger.debug(
                    "[%s] Target=%.2fm/s e=%+.1fmm P=%+.1f I=%+.1f D=%+.1f "
                    "out=%+.1f L=%.1f R=%.1frpm Vred=%.1frpm",
                    label, current_target_speed, dbg["e"],
                    dbg["p"], dbg["i"], dbg["d"], dbg["output"],
                    left_rpm, right_rpm, dbg["speed_reduction"],
                )

            # ── CAN timeout ───────────────────────────────────────────────────
            if time.time() - state.can_last_rx > config.CAN_TIMEOUT:
                if not tape_was_lost:
                    logger.warning("[%s] CAN TIMEOUT — sensor lost, stopping", _label)
                    state.log_event("ERROR", f"[{_label}] CAN TIMEOUT — sensor comms lost")
                    await motion.set_brake(state)
                    current_target_speed = 0.0
                    pid.reset()
                    tape_was_lost = True

                await asyncio.sleep(config.DT)
                continue

            await asyncio.sleep(config.DT)

    finally:
        recorder.stop()


# ══════════════════════════════════════════════════════════════════════════════
#  MANUAL MODE
# ══════════════════════════════════════════════════════════════════════════════

async def manual_mode(state):
    """Handles pendant jogging and web remote.
    Web remote (state.web_manual_command) takes priority over physical DI buttons.
    Emergency is fully owned by mode_manager — this task does not check it."""
    v_high = motion.rpm_to_voltage(motion.mps_to_rpm(config.MANUAL_TARGET_HIGH_SPEED))
    v_slow = motion.rpm_to_voltage(motion.mps_to_rpm(config.MANUAL_TARGET_SLOW_SPEED))
    current_motion = None

    while True:
        # ── Drain DI queue — keep only the latest frame ───────────────────────
        di = None
        while not state.di_queue.empty():
            di = await state.di_queue.get()

        # ── Web remote takes priority; fall back to physical DI ───────────────
        # Auto-clear stale web commands: if the client stopped sending keepalives
        # (network lag, tab closed, crash) treat it as a release after 500 ms.
        _WEB_CMD_TIMEOUT_S = 0.5
        web_cmd = state.web_manual_command
        if web_cmd is not None and (time.time() - state.web_manual_command_ts) > _WEB_CMD_TIMEOUT_S:
            state.web_manual_command = None
            web_cmd = None

        if web_cmd is not None:
            motion_state = web_cmd
        elif di is not None:
            pb_fwd   = di[config.DI_FWD]
            pb_rvs   = di[config.DI_REV]
            pb_left  = di[config.DI_LEFT]
            pb_right = di[config.DI_RIGHT]

            if pb_fwd and pb_left:       motion_state = "fwd_left"
            elif pb_fwd and pb_right:    motion_state = "fwd_right"
            elif pb_rvs and pb_left:     motion_state = "rvs_left"
            elif pb_rvs and pb_right:    motion_state = "rvs_right"
            elif pb_fwd:                 motion_state = "forward"
            elif pb_rvs:                 motion_state = "reverse"
            elif pb_left:                motion_state = "left"
            elif pb_right:               motion_state = "right"
            else:                        motion_state = "idle"
        else:
            await asyncio.sleep(0.01)
            continue

        if motion_state != current_motion:
            logger.debug("Manual: %s", motion_state.upper())
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

async def mode_manager(state, engine=None):
    """Central state machine.

    States: None | "manual" | "armed" | "running" | "reverse" | "emergency"

    DI conventions (from profile)
    ------------------------------------------------
    DI_MODE_SWITCH : physical HIGH = MANUAL by default.
                     Set MODE_SWITCH_INVERT=1 in profile to flip (HIGH = AUTO).
    DI_EMERGENCY   : True = emergency triggered
    DI_START       : momentary NO — rising edge = start auto
    DI_RESET       : momentary NO — rising edge = stop / reset

    engine: SequenceEngine (Phase 3). Passed through to auto_mode and used for
            cancel_armed() on mode transitions. None until Phase 3 is wired up.
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

    def _reset_sequence_state():
        state.speed_mode       = "HIGH"
        state.sequence_stop    = False
        state.pending_sequence = None
        if engine is not None:
            engine.cancel_armed()

    while True:
        if state.latest_di is None:
            await asyncio.sleep(0.01)
            continue

        di             = state.latest_di
        emergency_safe = not di[config.DI_EMERGENCY]
        switch_manual  = bool(di[config.DI_MODE_SWITCH]) ^ config.MODE_SWITCH_INVERT
        btn_start      = di[config.DI_START]
        btn_reset      = di[config.DI_RESET]

        start_rising = btn_start and not last_start
        reset_rising = btn_reset and not last_reset

        last_start = btn_start
        last_reset = btn_reset

        # ── System error guard (watchdog — Phase 4) ───────────────────────────
        # Critical driver lost (DIO) — stop everything including manual
        if state.system_error and current_mode not in ("emergency", None):
            detail = state.system_error_detail or "unknown"
            logger.error("SYSTEM ERROR — hardware driver lost [%s], stopping", detail)
            state.log_event("ERROR", f"SYSTEM ERROR — driver lost: {detail}")
            await _cancel_active()
            await _flush_and_brake()
            _reset_sequence_state()
            await asyncio.sleep(0.01)
            continue

        # Sensor driver lost (CAN/RFID) — only stop auto modes; manual stays up
        if state.sensor_error and current_mode in ("running", "reverse", "armed"):
            detail = state.sensor_error_detail or "unknown"
            logger.warning("SENSOR ERROR — sensor driver lost [%s], returning to armed", detail)
            await _cancel_active()
            await _flush_and_idle()
            _reset_sequence_state()
            current_mode       = "armed"
            state.current_mode = current_mode

        # ══════════════════════════════════════════════════════════════════════
        #  EMERGENCY
        # ══════════════════════════════════════════════════════════════════════

        if not emergency_safe:
            if current_mode != "emergency":
                logger.critical("!! EMERGENCY — all motion stopped")
                state.log_event("CRITICAL", "EMERGENCY STOP — all motion halted")
                await _cancel_active()
                await _flush_and_brake()
                _reset_sequence_state()
                state.emergency_active = True
                current_mode           = "emergency"
                state.current_mode     = current_mode
            await asyncio.sleep(0.01)
            continue

        # ══════════════════════════════════════════════════════════════════════
        #  EMERGENCY RECOVERY
        # ══════════════════════════════════════════════════════════════════════

        if current_mode == "emergency":
            if not reset_rising:
                await asyncio.sleep(0.01)
                continue

            logger.info("Emergency cleared by operator RESET.")
            state.log_event("INFO", "Emergency cleared by operator RESET")
            state.emergency_active = False

            if switch_manual:
                logger.info("Entering MANUAL.")
                await _flush_and_idle()
                active_task        = asyncio.create_task(manual_mode(state))
                current_mode       = "manual"
                state.current_mode = current_mode
            else:
                logger.info("Entering ARMED. Press START to run AUTO.")
                await _flush_and_idle()
                current_mode       = "armed"
                state.current_mode = current_mode

            await asyncio.sleep(0.01)
            continue

        # ══════════════════════════════════════════════════════════════════════
        #  NORMAL TRANSITIONS
        # ══════════════════════════════════════════════════════════════════════

        if current_mode is None:
            if switch_manual:
                logger.info("Startup: MANUAL")
                active_task        = asyncio.create_task(manual_mode(state))
                current_mode       = "manual"
                state.current_mode = current_mode
            else:
                logger.info("Startup: AUTO selector — ARMED. Press START.")
                current_mode       = "armed"
                state.current_mode = current_mode

        elif switch_manual and current_mode != "manual":
            logger.info("Mode switch: MANUAL")
            await _cancel_active()
            await _flush_and_idle()
            _reset_sequence_state()
            active_task        = asyncio.create_task(manual_mode(state))
            current_mode       = "manual"
            state.current_mode = current_mode

        elif not switch_manual and current_mode == "manual":
            logger.info("Mode switch: AUTO — ARMED. Place AGV on tape and press START.")
            await _cancel_active()
            await _flush_and_idle()
            current_mode       = "armed"
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
                logger.warning("START ignored — tape not detected. Place AGV on tape first.")
            else:
                logger.info("START — launching AUTO mode.")
                active_task        = asyncio.create_task(auto_mode(state, engine=engine))
                current_mode       = "running"
                state.current_mode = current_mode

        elif current_mode == "armed" and state.reverse_auto_request:
            tape_present  = False
            latest_sensor = None
            while not state.sensor_queue.empty():
                latest_sensor = state.sensor_queue.get_nowait()
            if latest_sensor is not None:
                tape_present = latest_sensor.get("tape_detected", False)
                await state.sensor_queue.put(latest_sensor)

            if not tape_present:
                logger.warning("REVERSE START ignored — tape not detected.")
                state.reverse_auto_request = False
            else:
                logger.info("REVERSE START — launching reverse auto mode.")
                active_task        = asyncio.create_task(
                    auto_mode(state, direction="reverse"))
                current_mode       = "reverse"
                state.current_mode = current_mode

        elif current_mode == "running" and reset_rising:
            logger.info("RESET — stopping AUTO, returning to ARMED.")
            await _cancel_active()
            await _flush_and_idle()
            _reset_sequence_state()
            current_mode       = "armed"
            state.current_mode = current_mode
            logger.info("ARMED. Press START to run again.")

        elif current_mode == "reverse" and (reset_rising or not state.reverse_auto_request):
            logger.info("STOP — stopping reverse auto, returning to ARMED.")
            await _cancel_active()
            await _flush_and_idle()
            state.reverse_auto_request = False
            current_mode               = "armed"
            state.current_mode         = current_mode
            logger.info("ARMED.")

        await asyncio.sleep(0.01)
