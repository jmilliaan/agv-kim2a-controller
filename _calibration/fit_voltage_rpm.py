#!/usr/bin/env python3
"""
fit_voltage_rpm.py — derive the motor voltage->rpm calibration from the encoder runs.

Reproducible record of how the agv_tn motor_cal constants (RPM_PER_VOLT / RPM_VOLT_OFFSET)
were fitted from the wheel-speed measurement runs in this folder. Not part of the runtime.

Method
------
For each valid sample the measured ground speed |encoder_v| is converted to motor rpm using
the AGV wheel geometry (same as motion.mps_to_rpm), then regressed on the commanded voltage:

    motor_rpm = RPM_PER_VOLT * voltage_v + RPM_VOLT_OFFSET

Filtering:
  - drop samples with |encoder_v| < 0.02 m/s  (encoder momentarily off the wheel)
  - use magnitude (the RIGHT-wheel runs read negative — mounting sign flip)
  - exclude run 14:20 (contaminated: sign flips + contact loss at the top of the ramp)

Result (2026-07-09): RPM_PER_VOLT = 1259.29, RPM_VOLT_OFFSET = -82.54  (R^2 ~ 0.89, n=2287)

Run:  python3 _calibration/fit_voltage_rpm.py
"""

import csv
import glob
import math
import os

BASE = os.path.dirname(os.path.abspath(__file__))

# AGV wheel geometry — must match config kinematics / motion.mps_to_rpm.
WHEEL_DIAMETER = 0.18
GEAR_RATIO     = 30
WHEEL_CIRC     = math.pi * WHEEL_DIAMETER

# Clean runs only (folder suffix -> wheel side). 14:20 excluded (contaminated).
CLEAN = {"14:23": "left", "14:26": "left", "14:43": "right", "14:44": "right"}
DROP  = 0.02   # m/s — below this while driving = encoder off the wheel


def mps_to_motor_rpm(v):
    return v * 60.0 / WHEEL_CIRC * GEAR_RATIO


def load(name):
    d = glob.glob(os.path.join(BASE, f"*{name}"))[0]
    p = glob.glob(os.path.join(d, "*.csv"))[0]
    pts = []
    with open(p) as f:
        for r in csv.DictReader(f):
            try:
                vc = float(r["v_cmd_ms"]); V = float(r["voltage_v"]); ev = float(r["encoder_v_ms"])
            except (ValueError, TypeError):
                continue
            if vc <= 0.001:
                continue
            a = abs(ev)               # magnitude handles the right-side sign flip
            if a < DROP:
                continue              # drop encoder-off-wheel samples
            pts.append((V, mps_to_motor_rpm(a)))
    return pts


def fit(xy):
    """Least squares y = A*x + B  (x = voltage, y = motor rpm). Returns (A, B, R^2, n)."""
    n = len(xy)
    sx = sum(x for x, _ in xy); sy = sum(y for _, y in xy)
    sxx = sum(x * x for x, _ in xy); sxy = sum(x * y for x, y in xy)
    A = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    B = (sy - A * sx) / n
    ybar = sy / n
    ss_res = sum((y - (A * x + B)) ** 2 for x, y in xy)
    ss_tot = sum((y - ybar) ** 2 for _, y in xy)
    return A, B, (1 - ss_res / ss_tot if ss_tot else 0.0), n


def main():
    allpts, byside = [], {"left": [], "right": []}
    print(f"{'run':<7}{'side':<6}{'RPM_PER_VOLT':>14}{'OFFSET':>10}{'R^2':>8}{'n':>6}")
    for name, side in CLEAN.items():
        pts = load(name)
        A, B, r2, n = fit(pts)
        print(f"{name:<7}{side:<6}{A:>14.2f}{B:>10.2f}{r2:>8.3f}{n:>6}")
        allpts += pts; byside[side] += pts
    print("-" * 51)
    for side in ("left", "right"):
        A, B, r2, n = fit(byside[side])
        print(f"pooled {side:<6}{A:>13.2f}{B:>10.2f}{r2:>8.3f}{n:>6}")
    A, B, r2, n = fit(allpts)
    print(f"pooled ALL  {A:>13.2f}{B:>10.2f}{r2:>8.3f}{n:>6}")
    print(f"\n=> motor_cal: RPM_PER_VOLT = {A:.2f}, RPM_VOLT_OFFSET = {B:.2f}")


if __name__ == "__main__":
    main()
