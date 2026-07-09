#!/usr/bin/env python3
"""
calibration.py — Terminal wheel-speed MEASUREMENT tool.

Measures the AGV's ACTUAL wheel ground speed (via a CANopen absolute encoder coupled
to one drive wheel by a friction wheel) against the COMMANDED speed, while driving both
wheels straight in an open-loop staircase ramp. It logs commanded-vs-actual to CSV + PNG.

This tool ONLY measures and logs. It does NOT compute or write any calibration constant
— fitting the motor constants and updating the profile is a separate, manual step done
offline from the CSVs afterward.

Why terminal, not the web HMI
-----------------------------
The production controller runs as the systemd unit `agv-controller.service`, which
exclusively owns (a) the CANable USB adapter and (b) the Modbus motor outputs. Two
processes cannot share the serial adapter, and double-driving the motor registers is
unsafe. So this tool REFUSES to run while the service is active.

    sudo systemctl stop  agv-controller.service
    python3 calibration.py r        # encoder on the RIGHT wheel  (or 'l' for LEFT)
    sudo systemctl start agv-controller.service

The wheel argument only labels the output files / attributes the logged voltage to the
side the encoder physically sits on. BOTH wheels are always driven straight; run the tool
once per side. (This codebase has a single shared voltage calibration, so both sides are
commanded the same voltage regardless.)

Safety
------
  - Keep a hand on the physical E-stop. Pressing it (DI_EMERGENCY) aborts the ramp,
    idles the motors, saves the data collected so far, and exits non-zero.
  - Ctrl-C lands in the same cleanup path.
  - The DIO module (which carries the E-stop input) MUST be reachable, or the tool
    refuses to drive: no DI = no software E-stop.

Measurement caveats (they bound accuracy)
-----------------------------------------
  - Friction slip UNDER-reads speed.
  - The encoder spins opposite the wheel (external contact) — a sign flip only,
    handled by DIRECTION_SIGN in drivers/can_encoder.py.
  - The friction-wheel radius (ENCODER_WHEEL_DIAMETER_M) scales speed linearly —
    MEASURE it on the real rig.
"""

import argparse
import asyncio
import csv
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime

# NOTE: matplotlib is imported lazily inside save_png() so that a missing/broken
# matplotlib install cannot stop the tool from driving and saving the CSV. The PNG
# is best-effort; the CSV is the primary artifact.

import config
import motion
from logger import setup_logging
from state import AMRState
from drivers.modbus_ao import AOWriter
from drivers.modbus_do import DOWriter
from drivers.modbus_di import DIReader
from drivers.can_encoder import EncoderReader

logger = logging.getLogger(__name__)

SERVICE_NAME = "agv-controller.service"

# ── Ramp parameters (staircase: 0 -> V_MAX, +V_STEP every DWELL_S) ────────────
CAL_V_START = 0.0
CAL_V_STEP  = 0.02
CAL_V_MAX   = 0.5
CAL_DWELL_S = 2
LOOP_DT     = 0.05               # ~20 Hz sample of the control loop

# ── Startup wait budgets ──────────────────────────────────────────────────────
DI_WAIT_S   = 3.0                # E-stop input MUST arrive within this — else refuse
ENC_WAIT_S  = 3.0               # encoder link is only a warning if not up in time

# ── Cleanup ───────────────────────────────────────────────────────────────────
DRAIN_S     = 0.3                # let AO/DO writers flush the idle (zero) writes

# ── Exit codes ────────────────────────────────────────────────────────────────
EXIT_OK     = 0
EXIT_ESTOP  = 2
EXIT_ABORT  = 130                # Ctrl-C / SIGTERM


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_wheel(value):
    v = value.strip().lower()
    if v in ("l", "left"):  return "left"
    if v in ("r", "right"): return "right"
    raise argparse.ArgumentTypeError(f"wheel must be l/left or r/right, got {value!r}")


def service_active():
    return subprocess.run(
        ["systemctl", "is-active", "--quiet", SERVICE_NAME]).returncode == 0


def emergency_pressed(state):
    di = state.latest_di
    # di[DI_EMERGENCY] is already logical (DI_FLIPPED applied by DIReader); True=triggered
    return bool(di) and len(di) > config.DI_EMERGENCY and di[config.DI_EMERGENCY]


def commanded_speed(elapsed):
    n = int(elapsed // CAL_DWELL_S)
    return min(CAL_V_START + n * CAL_V_STEP, CAL_V_MAX)


def total_ramp_time():
    n_steps = max(1, round((CAL_V_MAX - CAL_V_START) / CAL_V_STEP))
    return (n_steps + 1) * CAL_DWELL_S      # one extra dwell captures the top step


# ── Output ────────────────────────────────────────────────────────────────────

CSV_HEADER = ["t_s", "v_cmd_ms", "voltage_v", "encoder_v_ms", "encoder_rpm", "encoder_count"]


def save_csv(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_HEADER)
        w.writerows(rows)


def save_png(path, rows, wheel):
    # rows: (t, v_cmd, volt, enc_v, enc_rpm, enc_count)
    import matplotlib
    matplotlib.use("Agg")        # non-interactive backend — must precede pyplot
    import matplotlib.pyplot as plt

    t      = [r[0] for r in rows]
    v_cmd  = [r[1] for r in rows]
    enc_v  = [r[3] if r[3] is not None else float("nan") for r in rows]
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(t, v_cmd, "--", label="commanded (m/s)")
    ax.plot(t, enc_v, "-",  label="encoder actual (m/s)")
    ax.set_xlabel("time (s)")
    ax.set_ylabel("speed (m/s)")
    ax.set_title(f"Wheel-speed calibration — {wheel} wheel")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main(wheel, manage_service):
    setup_logging()

    # Timestamp derived ONCE at startup so the folder matches when the run began.
    stamp   = datetime.now().strftime("%d-%m-%Y_%H:%M")
    run_dir = os.path.join("_calibration", f"calibration_{stamp}")
    csv_path = os.path.join(run_dir, f"{wheel}_wheelcal.csv")
    png_path = os.path.join(run_dir, f"{wheel}_wheelcal.png")

    state = AMRState()
    state.encoder_connected = False
    state.encoder_v = state.encoder_rpm = state.encoder_count = None

    loop = asyncio.get_running_loop()
    state.loop = loop

    abort = {"flag": False}
    def _request_abort():
        abort["flag"] = True
    loop.add_signal_handler(signal.SIGINT,  _request_abort)
    loop.add_signal_handler(signal.SIGTERM, _request_abort)

    # ── Spawn the four hardware tasks ─────────────────────────────────────────
    tasks = [
        asyncio.ensure_future(AOWriter().run(state)),
        asyncio.ensure_future(DOWriter().run(state)),
        asyncio.ensure_future(DIReader().run(state)),
        asyncio.ensure_future(EncoderReader().run(state)),
    ]

    rows = []
    reason = "NORMAL"
    exit_code = EXIT_OK
    started_driving = False

    try:
        # ── DI is a HARD precondition (E-stop safety) ─────────────────────────
        di_deadline = time.time() + DI_WAIT_S
        while state.latest_di is None and time.time() < di_deadline and not abort["flag"]:
            await asyncio.sleep(0.05)
        if state.latest_di is None:
            logger.error("No DI from the DIO module (%s) after %.0fs — refusing to drive "
                         "(no DI means no software E-stop).", config.DIO_IP, DI_WAIT_S)
            reason = "NO-DI ABORT"
            exit_code = EXIT_ABORT
            return exit_code, reason, csv_path, png_path, rows, run_dir

        if abort["flag"]:
            reason, exit_code = "CTRL-C", EXIT_ABORT
            return exit_code, reason, csv_path, png_path, rows, run_dir

        # If the E-stop is already pressed, do not start.
        if emergency_pressed(state):
            logger.error("E-stop is ACTIVE at startup — release it before running.")
            reason, exit_code = "EMERGENCY STOP", EXIT_ESTOP
            return exit_code, reason, csv_path, png_path, rows, run_dir

        # ── Encoder link is a WARNING only ────────────────────────────────────
        enc_deadline = time.time() + ENC_WAIT_S
        while not state.encoder_connected and time.time() < enc_deadline and not abort["flag"]:
            await asyncio.sleep(0.05)
        if not state.encoder_connected:
            logger.warning("Encoder not linked after %.0fs — continuing, but encoder_v "
                           "will be blank until it connects.", ENC_WAIT_S)

        # ── Ramp loop ─────────────────────────────────────────────────────────
        logger.info("Driving %s-wheel calibration ramp: 0 -> %.2f m/s, +%.2f every %.1fs "
                    "(~%.0fs total). Hand on the E-stop.",
                    wheel, CAL_V_MAX, CAL_V_STEP, CAL_DWELL_S, total_ramp_time())
        t0 = time.time()
        direction_set = False
        while True:
            elapsed = time.time() - t0
            if elapsed > total_ramp_time():
                break
            if emergency_pressed(state):
                reason, exit_code = "EMERGENCY STOP", EXIT_ESTOP
                logger.critical("EMERGENCY STOP pressed — aborting ramp.")
                break
            if abort["flag"]:
                reason, exit_code = "CTRL-C", EXIT_ABORT
                logger.warning("Interrupt received — aborting ramp.")
                break

            v_cmd = commanded_speed(elapsed)
            rpm   = motion.mps_to_rpm(v_cmd)
            volt  = motion.rpm_to_voltage(rpm)
            # AO_MAX_VOLTAGE cap lives in modes.py (not imported) — enforce it here.
            volt  = max(0.0, min(volt, config.AO_MAX_VOLTAGE))

            if v_cmd <= 0.0:
                await motion.idle(state)
                direction_set = False
            elif not direction_set:
                # First positive step: set direction relays + brakes off + speed.
                await motion.set_forward(state, volt)
                direction_set = True
                started_driving = True
            else:
                # Direction already latched — AO-only update avoids flooding the DO queue.
                await motion.update_voltages(state, volt, volt)

            rows.append((round(elapsed, 3), v_cmd, round(volt, 4),
                         state.encoder_v, state.encoder_rpm, state.encoder_count))

            enc_v = state.encoder_v
            enc_str = f"{enc_v:+.3f}" if isinstance(enc_v, (int, float)) else "  n/a "
            print(f"\rt={elapsed:6.2f}s  cmd={v_cmd:.2f} m/s  V={volt:.3f}  "
                  f"enc={enc_str} m/s   ", end="", flush=True)

            await asyncio.sleep(LOOP_DT)

        print()  # newline after the live line
        return exit_code, reason, csv_path, png_path, rows, run_dir

    finally:
        # ── Cleanup — always runs ─────────────────────────────────────────────
        print()
        try:
            await motion.idle(state)
        except Exception as e:
            logger.error("Error idling motors during cleanup: %s", e)
        # Let AO/DO writers actually flush the zero writes BEFORE we cancel them,
        # otherwise the idle command sits unconsumed and the motors keep their
        # last voltage.
        if started_driving:
            await asyncio.sleep(DRAIN_S)

        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)  # encoder finally frees CANable

        # Save artifacts (CSV first — cheap and reliable; PNG guarded).
        os.makedirs(run_dir, exist_ok=True)
        try:
            save_csv(csv_path, rows)
            logger.info("CSV saved: %s (%d rows)", csv_path, len(rows))
        except Exception as e:
            logger.error("Failed to save CSV: %s", e)
        try:
            if rows:
                await asyncio.to_thread(save_png, png_path, rows, wheel)
                logger.info("PNG saved: %s", png_path)
        except Exception as e:
            logger.error("Failed to save PNG (CSV is still saved): %s", e)

        if manage_service:
            logger.info("Restarting %s ...", SERVICE_NAME)
            subprocess.run(["sudo", "systemctl", "start", SERVICE_NAME])


def cli():
    parser = argparse.ArgumentParser(
        description="Terminal wheel-speed measurement tool (drive straight + log encoder).",
        epilog=(
            "Runbook:\n"
            "  1. Couple the encoder friction wheel to the target AGV wheel.\n"
            "  2. Clear the straight track; keep a hand on the physical E-stop.\n"
            "  3. sudo systemctl stop agv-controller.service\n"
            "  4. python3 calibration.py r     (or l)\n"
            "  5. Ramp is automatic; E-stop or Ctrl-C stops and saves data so far.\n"
            "  6. Output -> _calibration/calibration_[DD-MM-YYYY]_[HH:MM]/\n"
            "  7. Re-mount on the other wheel and repeat.\n"
            "  8. sudo systemctl start agv-controller.service\n"),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("wheel", type=parse_wheel,
                        help="which wheel the encoder is on: l/left or r/right")
    parser.add_argument("--manage-service", action="store_true",
                        help="stop the controller service on entry and restart it on exit "
                             "(requires passwordless sudo). Default: refuse if active.")
    args = parser.parse_args()

    if service_active():
        if args.manage_service:
            logger.info("Stopping %s ...", SERVICE_NAME)
            subprocess.run(["sudo", "systemctl", "stop", SERVICE_NAME])
        else:
            sys.exit(f"Refusing to run: {SERVICE_NAME} is active. "
                     f"Stop it first:  sudo systemctl stop {SERVICE_NAME}")

    exit_code, reason, csv_path, png_path, rows, run_dir = asyncio.run(
        main(args.wheel, args.manage_service))

    print(f"\nRun ended: {reason}")
    print(f"  rows saved : {len(rows)}")
    print(f"  folder     : {run_dir}")
    sys.exit(exit_code)


if __name__ == "__main__":
    cli()
