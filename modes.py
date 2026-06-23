import asyncio
import logging
import time

import config
import motion
import fleet

from core.pid import PIDController
from _debugging.plotter import RunRecorder

logger = logging.getLogger(__name__)

# [EVO] Below this |rpm| (CiA-402 actual velocity, both wheels) the AGV is treated
# as physically stopped — gates the traffic_hold event ("ACK ≠ stopped").
_STOP_RPM_EPS = 5.0


# ══════════════════════════════════════════════════════════════════════════════
#  AUTO MODE  (forward only — physical reverse removed [EVO])
# ══════════════════════════════════════════════════════════════════════════════

async def auto_mode(state, engine=None):
    """Tape-following PID loop. Forward only — the AGV never drives backward in
    auto ([EVO]; the logical inbound/outbound `direction` flag is NOT a drive
    direction). Sensor at front, full speed-mode logic, RFID sequence stops and
    marker hooks active.

    In FLEET_MODE this loop also honours the non-fault Cat-2 overlays —
    `traffic_hold` (cmd/traffic), `commanded_pause` (cmd/control), and
    `confirm_pending` (mission confirm gate) — holding at zero velocity with no
    electromagnetic brake so it can resume smoothly.

    Args:
        engine: SequenceEngine instance.  Pass None to disable marker hooks.
    """
    _orient = float(config.SENSOR_ORIENTATION)
    await motion.idle(state)
    await motion.set_forward(state, 0.0)
    error_sign = -1.0 * _orient

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
    _traffic_hold_emitted = False  # [EVO] traffic_hold event sent once stopped

    recorder = RunRecorder()
    recorder.start()

    # ── Loop-rate / queue-depth diagnostics ───────────────────────────────────
    # Tracks actual cycle time and queue backlog so we can decide whether
    # lowering DT (e.g. 0.1 → 0.05) is bottlenecked by the controller or by
    # the motor driver / sensor publish rate. Logs a summary every ~2 s.
    _diag_loop      = asyncio.get_event_loop()
    _diag_last_t    = _diag_loop.time()
    _diag_max_dt    = 0.0
    _diag_sum_dt    = 0.0
    _diag_n         = 0
    _diag_max_sf    = 0   # max sensor frames drained per cycle (= sensor rate / PID rate)
    _diag_sum_sf    = 0   # avg sensor frames per cycle
    _diag_zero_sf   = 0   # cycles with 0 frames (sensor starvation — bad)
    _diag_max_aq    = 0   # motor_queue post-enqueue depth
    _diag_max_dq    = 0   # do_queue post-enqueue depth
    _diag_log_every = 20   # cycles between summary lines (~2 s at DT=0.1)

    try:
        while True:

            # ── Emergency guard ───────────────────────────────────────────────
            if state.emergency_active:
                await motion.set_brake(state)
                await asyncio.sleep(config.DT)
                continue

            # ── Fleet overlays [EVO] ──────────────────────────────────────────
            # traffic_hold / commanded_pause = Cat-2 hold (zero velocity, NO
            # electromagnetic brake — resume smoothly). The confirm gate is a
            # human handoff, so it holds with the brake (Cat-1). All are distinct
            # from emergency / system_error. traffic_resumed fires on the falling
            # edge of traffic_hold even if still held by a confirm/pause.
            if _traffic_hold_emitted and not state.traffic_hold:
                fleet.emit_traffic_resumed(state)
                _traffic_hold_emitted = False
                state.log_event("INFO", "TRAFFIC RESUMED — tape-following")
            if state.traffic_hold or state.commanded_pause:
                await motion.idle(state)
                current_target_speed = 0.0
                pid.reset()
                # ACK ≠ stopped: emit the traffic_hold event only once the wheels
                # have actually reached zero (per CiA-402 actual rpm telemetry).
                if state.traffic_hold and not _traffic_hold_emitted:
                    _al = abs(state.motor_actual_rpm.get("left",  0) or 0)
                    _ar = abs(state.motor_actual_rpm.get("right", 0) or 0)
                    if max(_al, _ar) < _STOP_RPM_EPS:
                        fleet.emit_traffic_hold(state)
                        _traffic_hold_emitted = True
                        state.log_event("INFO", "TRAFFIC HOLD — stopped (Cat-2, no brake)")
                await asyncio.sleep(config.DT)
                continue
            if state.confirm_pending:
                # Human handoff gate — hold firmly with the electromagnetic brake
                # until the onboard confirm is pressed (mode_manager advances FSM).
                await motion.set_brake(state)
                current_target_speed = 0.0
                pid.reset()
                await asyncio.sleep(config.DT)
                continue

            # ── Sequence stop ─────────────────────────────────────────────────
            if state.sequence_stop:
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
            if state.speed_mode == "EXTRA_SLOW":
                target_speed = state.auto_extra_slow_speed
            elif state.speed_mode == "SLOW":
                target_speed = state.auto_slow_speed
            else:
                target_speed = state.auto_high_speed

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

            # ── DI snapshot ───────────────────────────────────────────────────
            _di = state.latest_di
            _label = "AUTO"

            # ── Impact bumper ─────────────────────────────────────────────────
            if config.DI_BUMPER is not None and _di is not None and _di[config.DI_BUMPER]:
                logger.warning("[%s] BUMPER HIT — Cat 1 stop", _label)
                state.log_event("WARNING", f"[{_label}] BUMPER HIT — Cat 1 protective stop")
                state.bumper_active = True
                await motion.set_cat1_stop(state)  # Cat 1: decel then brake
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
                await motion.set_forward(state, 0.0)
                tape_was_lost = False
                continue

            # ── Lidar DI checks (no-op when profile has no lidar channels) ─────
            # Outer zone (DI_LIDAR_OUTER): dashboard indicator only — no speed change.
            # Middle zone (DI_LIDAR_SLOW): switch to SLOW speed.
            # Inner zone (DI_LIDAR_STOP): Cat 1 protective stop — decel then brake.
            if state.lidar_stop_enabled and config.DI_LIDAR_STOP is not None and _di is not None and _di[config.DI_LIDAR_STOP]:
                if not tape_was_lost:
                    logger.warning("[%s] LIDAR INNER — obstacle, Cat 1 stop", _label)
                    state.log_event("WARNING", f"[{_label}] LIDAR INNER DETECT — protective stop")
                    await motion.set_cat1_stop(state)  # Cat 1: decel then brake
                    current_target_speed = 0.0
                    pid.reset()
                    tape_was_lost = True
                await asyncio.sleep(config.DT)
                continue
            if state.lidar_slow_enabled and config.DI_LIDAR_SLOW is not None and _di is not None and _di[config.DI_LIDAR_SLOW]:
                if state.speed_mode == "HIGH":
                    state.speed_mode = "SLOW"

            # ── Drain sensor queue — keep only the latest frame ───────────────
            sensor = None
            _sensor_frames_this_cycle = 0
            while not state.sensor_queue.empty():
                sensor = await state.sensor_queue.get()
                _sensor_frames_this_cycle += 1

            if sensor is not None:

                # SICK MLS reports a numeric marker_code, not left/right markers;
                # the left/right marker hooks below stay (always False) for compat
                # and are unused by the current RFID-only profile.
                logger.debug("[AUTO] marker_code=%s", sensor.get("marker_code", 0))

                # ── Tape-loss guard ───────────────────────────────────────────
                if not sensor["tape_detected"]:
                    logger.warning("[%s] LOST TAPE — stopping", _label)
                    state.log_event("WARNING", f"[{_label}] TAPE LOST — AGV stopped")
                    await motion.idle(state)   # no mechanical brake — Cat 2 hold
                    current_target_speed = 0.0
                    pid.reset()
                    tape_was_lost = True
                    await asyncio.sleep(0.01)
                    continue

                if tape_was_lost:
                    logger.info("[%s] TAPE REACQUIRED — resuming", _label)
                    state.log_event("INFO", f"[{_label}] TAPE REACQUIRED — resuming")
                    await motion.set_forward(state, 0.0)
                    tape_was_lost = False

                # ── Marker hook (sequence engine) ──────────────────────────────
                if engine is not None:
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

                # Clamp each wheel to the drive ceiling and queue signed rpm.
                left_rpm  = max(-config.MOTOR_MAX_RPM, min(config.MOTOR_MAX_RPM, left_rpm))
                right_rpm = max(-config.MOTOR_MAX_RPM, min(config.MOTOR_MAX_RPM, right_rpm))

                await motion.update_velocities(state, left_rpm, right_rpm)

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

                label = state.speed_mode
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

            # ── Diagnostics: actual cycle time + sensor rate + queue depths ───
            _now = _diag_loop.time()
            _dt  = _now - _diag_last_t
            _diag_last_t = _now
            _diag_sum_dt += _dt
            if _dt > _diag_max_dt: _diag_max_dt = _dt
            _sf = _sensor_frames_this_cycle  # captured before drain wiped queue
            _diag_sum_sf += _sf
            if _sf > _diag_max_sf: _diag_max_sf = _sf
            if _sf == 0: _diag_zero_sf += 1
            _aq = state.motor_queue.qsize()
            _dq = state.do_queue.qsize()
            if _aq > _diag_max_aq: _diag_max_aq = _aq
            if _dq > _diag_max_dq: _diag_max_dq = _dq
            _diag_n += 1
            if _diag_n >= _diag_log_every:
                _avg_ms = (_diag_sum_dt / _diag_n) * 1000.0
                _max_ms = _diag_max_dt * 1000.0
                _avg_sf = _diag_sum_sf / _diag_n
                logger.info(
                    "[DIAG] cycle avg=%.1fms max=%.1fms (target=%.0fms) | "
                    "sensor frames/cycle avg=%.1f max=%d zero=%d/%d | "
                    "qmax motor=%d do=%d",
                    _avg_ms, _max_ms, config.DT * 1000.0,
                    _avg_sf, _diag_max_sf, _diag_zero_sf, _diag_n,
                    _diag_max_aq, _diag_max_dq,
                )
                _diag_sum_dt = _diag_max_dt = 0.0
                _diag_sum_sf = _diag_max_sf = _diag_zero_sf = 0
                _diag_max_aq = _diag_max_dq = 0
                _diag_n      = 0

            await asyncio.sleep(config.DT)

    finally:
        recorder.stop()


# ══════════════════════════════════════════════════════════════════════════════
#  MANUAL MODE
# ══════════════════════════════════════════════════════════════════════════════

async def manual_mode(state):
    """Handles pendant jogging and web remote.
    Web remote (state.web_manual_command) takes priority over physical DI buttons.
    Emergency is fully owned by mode_manager — this task does not check it.

    Acceleration model:
        - Button held: ramp current speed from 0 toward target at MANUAL_ACCEL_RATE.
        - Button released (state → idle): instant stop, no deceleration ramp.
    The ramp is implemented as a 0..1 fraction multiplied into the target
    rpm, so diagonal moves preserve their inner/outer wheel speed ratio.
    """
    rpm_high_max = motion.mps_to_rpm(config.MANUAL_TARGET_HIGH_SPEED)
    rpm_slow_max = motion.mps_to_rpm(config.MANUAL_TARGET_SLOW_SPEED)

    # Cycle period of this task (matches the asyncio.sleep at the bottom).
    _CYCLE_S = 0.01
    # Fraction increment per cycle: how much of "0 to MANUAL_TARGET_HIGH_SPEED"
    # we cover each tick. Reaching full speed takes ~HIGH/ACCEL seconds.
    _ramp_step = (config.MANUAL_ACCEL_RATE / config.MANUAL_TARGET_HIGH_SPEED) * _CYCLE_S
    fraction = 0.0     # 0..1, scales target rpm during ramp-up

    current_motion = None
    _pusher_task   = None   # track active pusher task to prevent concurrent relay firing

    try:
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

        # ── Map motion_state to signed per-wheel rpm with a smooth ramp.
        # On state change the ramp restarts from 0; while a state is held we
        # re-queue the two motor velocities each cycle until fraction reaches
        # 1.0, then stop emitting writes (the drive holds the last target).
        if motion_state == "idle":
            if current_motion != "idle":
                logger.debug("Manual: IDLE (instant stop, no decel)")
                await motion.idle(state)
                fraction = 0.0
                current_motion = "idle"
        else:
            if motion_state != current_motion:
                logger.debug("Manual: %s (ramp-up at %.2f m/s^2)",
                             motion_state.upper(), config.MANUAL_ACCEL_RATE)
                fraction = 0.0
                current_motion = motion_state

            # Advance the ramp; only emit motor writes while still climbing.
            if fraction < 1.0:
                fraction = min(1.0, fraction + _ramp_step)
                r_h = rpm_high_max * fraction
                r_s = rpm_slow_max * fraction
                # Signed per-wheel Target velocity (+ = forward).
                # See motion.py set_* helpers for the (left, right) convention.
                if   motion_state == "fwd_left":  left_rpm, right_rpm =  r_s,  r_h
                elif motion_state == "fwd_right": left_rpm, right_rpm =  r_h,  r_s
                elif motion_state == "rvs_left":  left_rpm, right_rpm = -r_s, -r_h
                elif motion_state == "rvs_right": left_rpm, right_rpm = -r_h, -r_s
                elif motion_state == "forward":   left_rpm, right_rpm =  r_h,  r_h
                elif motion_state == "reverse":   left_rpm, right_rpm = -r_h, -r_h
                elif motion_state == "left":      left_rpm, right_rpm = -r_s,  r_s
                elif motion_state == "right":     left_rpm, right_rpm =  r_s, -r_s
                else:                             left_rpm, right_rpm =  0.0,  0.0
                await motion.update_velocities(state, left_rpm, right_rpm)

        # ── Web pusher request (hold: up/down energises, clear de-energises) ───
        pusher_req = state.web_pusher_request
        if pusher_req is not None:
            state.web_pusher_request = None
            # Cancel any in-flight task first — prevents the 200ms relay delay
            # from re-energising relays after a clear has been requested.
            if _pusher_task and not _pusher_task.done():
                _pusher_task.cancel()
            if pusher_req == "up":
                logger.info("[MANUAL] Pusher UP — hold")
                _pusher_task = asyncio.create_task(motion.pusher_up(state))
            elif pusher_req == "down":
                logger.info("[MANUAL] Pusher DOWN — hold")
                _pusher_task = asyncio.create_task(motion.pusher_down(state))
            elif pusher_req == "clear":
                logger.info("[MANUAL] Pusher CLEAR — released")
                _pusher_task = asyncio.create_task(motion.pusher_clear(state))

        await asyncio.sleep(0.01)

    except asyncio.CancelledError:
        if _pusher_task and not _pusher_task.done():
            _pusher_task.cancel()
        raise


# ══════════════════════════════════════════════════════════════════════════════
#  MODE MANAGER
# ══════════════════════════════════════════════════════════════════════════════

async def mode_manager(state, engine=None):
    """Central state machine.

    States: None | "manual" | "armed" | "running" | "emergency"
    (The physical "reverse" auto mode is removed [EVO]; `state.direction` is a
    logical inbound/outbound flag only.)

    DI conventions (from profile)
    ------------------------------------------------
    DI_MODE_SWITCH : physical HIGH = MANUAL by default.
                     Set MODE_SWITCH_INVERT=1 in profile to flip (HIGH = AUTO).
    DI_EMERGENCY   : True = emergency triggered
    DI_START       : momentary NO — rising edge = start auto (standalone)
    DI_RESET       : momentary NO — rising edge = stop / reset
    DI_CONFIRM     : momentary NO — rising edge = mission confirm (fleet) [EVO]

    Fleet overlays (FLEET_MODE only): an accepted cmd/mission triggers
    armed → running (not DI_START); cmd/control estop/reset map onto
    emergency / armed; confirm gating + traffic-hold are overlays on running.

    engine: SequenceEngine. Passed through to auto_mode and used for
            cancel_armed() on mode transitions.
    """

    current_mode  = None
    active_task   = None
    startup_task  = None   # ARMED→running pusher-up routine (deterministic start-from-home)
    last_start    = False
    last_reset    = False
    last_confirm  = False  # [EVO] DI_CONFIRM rising-edge tracker
    confirm_alarm_raised = False  # [EVO] 100 s advisory alarm fired for the open gate

    # Hardened start-from-home: when RFID sequences are enabled, every START
    # press deterministically drives the pusher UP and holds the AGV for 5 s,
    # regardless of which (or whether any) tag is read. This replaces the
    # previous behavior where pusher-up depended on a tag10-departure sequence
    # firing — which could miss when the AGV was already sitting on the tag at
    # start time, or when the cooldown/at_home flag wasn't aligned.
    START_FROM_HOME_HOLD_S = 5.0

    async def _start_from_home_routine():
        try:
            await motion.pusher_up(state)
            await asyncio.sleep(START_FROM_HOME_HOLD_S)
            state.at_home = False
        finally:
            # Always release the hold even if cancelled — never leave the bot
            # frozen with sequence_stop=True after a reset.
            state.sequence_stop = False

    async def _cancel_startup():
        nonlocal startup_task
        if startup_task and not startup_task.done():
            startup_task.cancel()
            try:
                await startup_task
            except asyncio.CancelledError:
                pass
        startup_task = None

    async def _cancel_active():
        nonlocal active_task
        # Always cancel the start-from-home task too — its lifetime is bounded
        # by the active auto task, and we never want it to keep sequence_stop
        # held after a reset / emergency / end-cycle.
        await _cancel_startup()
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
        while not state.motor_queue.empty():
            state.motor_queue.get_nowait()
        await motion.idle(state)

    async def _flush_and_brake():
        while not state.do_queue.empty():
            state.do_queue.get_nowait()
        while not state.motor_queue.empty():
            state.motor_queue.get_nowait()
        await motion.set_cat1_stop(state)  # Cat 1: Target velocity→0 + CiA-402 Quick stop

    def _reset_sequence_state():
        state.speed_mode       = "SLOW"
        state.sequence_stop    = False
        state.pending_sequence = None
        if engine is not None:
            engine.cancel_armed()
            engine.cancel_active_sequence()
            engine.cancel_cooldowns()

    def _abort_mission():
        """[EVO] Drop any active mission and reset the mission FSM to safe idle.
        Called on every halting transition back to armed / emergency, so a restart
        never resumes a mission (the store re-issues cmd/mission)."""
        nonlocal confirm_alarm_raised
        confirm_alarm_raised = False
        if not config.FLEET_MODE:
            return
        state.mission = None
        state.mission_start_request = False
        state.commanded_pause = False
        state.traffic_hold = False
        state.confirm_pending = False
        state.web_confirm_request = False
        if state.mission_fsm is not None:
            state.mission_fsm.abort()

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

        # ── Confirm button rising edge (fleet mission gating) [EVO] ────────────
        btn_confirm = (config.DI_CONFIRM is not None
                       and len(di) > config.DI_CONFIRM and di[config.DI_CONFIRM])
        confirm_rising = btn_confirm and not last_confirm
        last_confirm = btn_confirm

        # ── Fold commanded control (cmd/control) into the physical triggers ────
        # estop → emergency overlay; reset → back to armed (clears mission).
        emergency_trig = (not emergency_safe) or (config.FLEET_MODE and state.cmd_estop)
        reset_trig     = reset_rising or (config.FLEET_MODE and state.control_reset_request)
        # armed → running: a physical START press standalone; an accepted
        # cmd/mission in fleet mode (the store dispatches, not the panel).
        start_trig = (state.mission_start_request if config.FLEET_MODE else start_rising)

        # ── System error guard (watchdog — Phase 4) ───────────────────────────
        # Critical driver lost (DIO) — stop everything including manual
        if state.system_error and current_mode not in ("emergency", None):
            detail = state.system_error_detail or "unknown"
            logger.error("SYSTEM ERROR — hardware driver lost [%s], stopping", detail)
            state.log_event("ERROR", f"SYSTEM ERROR — driver lost: {detail}")
            await _cancel_active()
            await _flush_and_brake()
            _reset_sequence_state()
            _abort_mission()
            await asyncio.sleep(0.01)
            continue

        # ── End-cycle request (sequence-triggered return to ARMED) ───────────
        if state.end_cycle_request and current_mode == "running":
            logger.info("END CYCLE — sequence requested return to ARMED")
            state.log_event("INFO", "End cycle — returning to ARMED")
            state.end_cycle_request = False
            await _cancel_active()
            await _flush_and_idle()
            _reset_sequence_state()
            _abort_mission()
            current_mode       = "armed"
            state.current_mode = current_mode
            await asyncio.sleep(0.01)
            continue

        # Sensor driver lost (CAN/RFID) — only stop auto modes; manual stays up
        if state.sensor_error and current_mode in ("running", "armed"):
            detail = state.sensor_error_detail or "unknown"
            logger.warning("SENSOR ERROR — sensor driver lost [%s], returning to armed", detail)
            await _cancel_active()
            await _flush_and_idle()
            _reset_sequence_state()
            _abort_mission()
            current_mode       = "armed"
            state.current_mode = current_mode

        # ══════════════════════════════════════════════════════════════════════
        #  EMERGENCY
        # ══════════════════════════════════════════════════════════════════════

        if emergency_trig:
            if current_mode != "emergency":
                logger.critical("!! EMERGENCY — all motion stopped")
                state.log_event("CRITICAL", "EMERGENCY STOP — all motion halted")
                await _cancel_active()
                await _flush_and_brake()
                _reset_sequence_state()
                _abort_mission()
                state.emergency_active = True
                current_mode           = "emergency"
                state.current_mode     = current_mode
                if config.FLEET_MODE:
                    fleet.emit_fault_raised(state, "emergency",
                                            "estop" if state.cmd_estop else "di_emergency")
            await asyncio.sleep(0.01)
            continue

        # ══════════════════════════════════════════════════════════════════════
        #  EMERGENCY RECOVERY
        # ══════════════════════════════════════════════════════════════════════

        if current_mode == "emergency":
            if not reset_trig:
                await asyncio.sleep(0.01)
                continue

            logger.info("Emergency cleared by RESET.")
            state.log_event("INFO", "Emergency cleared by RESET")
            state.emergency_active = False
            # Consume the commanded latches so they don't immediately re-trigger.
            state.cmd_estop = False
            state.control_reset_request = False
            if config.FLEET_MODE:
                fleet.emit_fault_cleared(state, "emergency", "reset")

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
            _abort_mission()
            active_task        = asyncio.create_task(manual_mode(state))
            current_mode       = "manual"
            state.current_mode = current_mode

        elif not switch_manual and current_mode == "manual":
            logger.info("Mode switch: AUTO — ARMED. Place AGV on tape and press START.")
            await _cancel_active()
            await _flush_and_idle()
            current_mode       = "armed"
            state.current_mode = current_mode

        elif current_mode == "armed" and start_trig:
            tape_present  = False
            latest_sensor = None
            while not state.sensor_queue.empty():
                latest_sensor = state.sensor_queue.get_nowait()

            _trig_src = "cmd/mission" if config.FLEET_MODE else "DI_START"
            logger.info(
                "START (%s) — sensor_queue had data: %s | sensor_error: %s "
                "| can_last_rx: %.2fs ago",
                _trig_src,
                latest_sensor is not None,
                state.sensor_error,
                time.time() - state.can_last_rx,
            )

            if latest_sensor is not None:
                tape_present = latest_sensor.get("tape_detected", False)
                logger.info(
                    "START sensor frame — tape_detected: %s | left_mm: %s "
                    "| sensor_failure: %s",
                    latest_sensor.get("tape_detected"),
                    latest_sensor.get("left_mm"),
                    latest_sensor.get("sensor_failure"),
                )
                await state.sensor_queue.put(latest_sensor)
            else:
                logger.warning(
                    "START ignored — sensor queue empty. "
                    "CAN sensor may be offline or no frame received yet."
                )

            if latest_sensor is not None and not tape_present:
                logger.warning("START ignored — tape not detected. Place AGV on tape first.")
                # In fleet mode a tapeless mission start is refused; drop the
                # request so it doesn't spin (the store will re-dispatch).
                if config.FLEET_MODE:
                    state.mission_start_request = False
            elif tape_present:
                logger.info("START — tape confirmed, launching AUTO mode.")
                # If RFID sequences are enabled, hold the AGV in place from the
                # very first cycle of auto_mode so the pusher-up routine can run
                # before any motion. Set sequence_stop BEFORE launching auto_mode.
                if state.rfid_enabled:
                    state.sequence_stop = True
                active_task        = asyncio.create_task(auto_mode(state, engine=engine))
                current_mode       = "running"
                state.current_mode = current_mode
                # [EVO] Begin the mission FSM (DEPART_HOME, outbound).
                if config.FLEET_MODE:
                    state.mission_start_request = False
                    confirm_alarm_raised = False
                    if state.mission_fsm is not None:
                        state.mission_fsm.start(state.mission)
                if state.rfid_enabled:
                    logger.info("START from home — pusher UP, holding %.1fs", START_FROM_HOME_HOLD_S)
                    state.log_event("INFO",
                        f"START from home — pusher UP, holding {START_FROM_HOME_HOLD_S:.1f}s")
                    startup_task = asyncio.create_task(_start_from_home_routine())

        elif current_mode == "running" and reset_trig:
            logger.info("RESET — stopping AUTO, returning to ARMED.")
            await _cancel_active()
            await _flush_and_idle()
            _reset_sequence_state()
            _abort_mission()
            state.control_reset_request = False
            current_mode       = "armed"
            state.current_mode = current_mode
            logger.info("ARMED. Awaiting %s.", "cmd/mission" if config.FLEET_MODE else "START")

        # ── Confirm gating (fleet running) [EVO] ──────────────────────────────
        if config.FLEET_MODE and current_mode == "running" and state.mission_fsm is not None:
            # A press (physical DI_CONFIRM rising edge, or the web fallback) closes
            # the open handoff gate and advances the mission FSM.
            if state.web_confirm_request:
                state.web_confirm_request = False
                if state.mission_fsm.on_confirm():
                    confirm_alarm_raised = False
            elif confirm_rising:
                if state.mission_fsm.on_confirm():
                    confirm_alarm_raised = False
                    logger.info("[MISSION] CONFIRM pressed at %s", state.confirm_location)

            # 100 s advisory alarm — the AGV keeps waiting; this only notifies.
            if (state.confirm_pending and not confirm_alarm_raised
                    and (time.time() - state.confirm_ts) > config.FLEET_CONFIRM_ALARM_S):
                confirm_alarm_raised = True
                msg = f"CONFIRM not pressed within {config.FLEET_CONFIRM_ALARM_S:.0f}s at {state.confirm_location}"
                logger.warning("[MISSION] %s — advisory alarm", msg)
                state.log_event("WARNING", f"MISSION: {msg}")
                fleet.emit_event(state, "confirm_alarm",
                                 location=state.confirm_location)

        await asyncio.sleep(0.01)
