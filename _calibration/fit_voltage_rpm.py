#!/usr/bin/env python3
"""
fit_voltage_rpm.py — derive INDEPENDENT per-wheel voltage->rpm calibration from encoder runs.

Reproducible record of how the agv_tn per-wheel motor_cal constants
(left/right RPM_PER_VOLT / RPM_VOLT_OFFSET) were fitted. Not part of the runtime.

Method
------
For each valid sample the measured ground speed |encoder_v| is converted to motor rpm using
the AGV wheel geometry (same as motion.mps_to_rpm), then regressed on the commanded voltage:

    motor_rpm = RPM_PER_VOLT * voltage_v + RPM_VOLT_OFFSET

Filtering:
  - drop the low-speed deadband region (v_cmd <= 0.03) — unreliable
  - use magnitude (the RIGHT-wheel runs read negative — mounting sign flip)
  - robust per-bin outlier rejection: within each commanded-speed bin, drop samples deviating
    >20% from the bin median. This removes the encoder-disconnect glitches (near-zero readings
    and 2x reconnection spikes, e.g. the 15:09 run around 0.40/0.48/0.50 m/s) without dropping
    the good high-speed points that anchor the slope.

Result (2026-07-09, post-shared-cal runs):
    left  (15:16): RPM_PER_VOLT = 1261.69, RPM_VOLT_OFFSET = -59.88  (R^2 0.994)
    right (15:09): RPM_PER_VOLT = 1254.54, RPM_VOLT_OFFSET = -93.41  (R^2 0.991)

Run:  python3 _calibration/fit_voltage_rpm.py
"""

import csv
import glob
import math
import os
import statistics as st

BASE = os.path.dirname(os.path.abspath(__file__))

# AGV wheel geometry — must match config kinematics / motion.mps_to_rpm.
WHEEL_DIAMETER = 0.18
GEAR_RATIO     = 30
WHEEL_CIRC     = math.pi * WHEEL_DIAMETER

# Latest per-wheel runs (folder suffix -> wheel side).
RUNS      = {"15:16": "left", "15:09": "right"}
V_CMD_MIN = 0.03    # m/s — ignore the deadband region
OUTLIER   = 0.20    # drop samples >20% off the per-bin median


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
            if vc <= V_CMD_MIN:
                continue
            pts.append((round(vc, 2), V, abs(ev)))   # magnitude handles the sign flip
    return pts


def reject_outliers(pts):
    """Per commanded-speed bin, drop samples >OUTLIER off the bin median."""
    from collections import defaultdict
    bins = defaultdict(list)
    for vc, V, a in pts:
        bins[vc].append((V, a))
    keep, dropped = [], 0
    for vc, items in bins.items():
        med = st.median([a for _, a in items])
        for V, a in items:
            if med > 0 and abs(a - med) / med <= OUTLIER:
                keep.append((V, a))
            else:
                dropped += 1
    return keep, dropped


def fit(va_pairs):
    """Least squares motor_rpm = A*V + B. Returns (A, B, R^2, n)."""
    xy = [(V, mps_to_motor_rpm(a)) for V, a in va_pairs]
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
    print(f"{'wheel':<6}{'run':<7}{'RPM_PER_VOLT':>14}{'OFFSET':>10}{'R^2':>8}{'n':>6}{'dropped':>9}")
    for name, side in RUNS.items():
        clean, dropped = reject_outliers(load(name))
        A, B, r2, n = fit(clean)
        print(f"{side:<6}{name:<7}{A:>14.2f}{B:>10.2f}{r2:>8.4f}{n:>6}{dropped:>9}")
    print("\n=> profile motor_cal:")
    print('   "left":  { "RPM_PER_VOLT": 1261.69, "RPM_VOLT_OFFSET": -59.88 }')
    print('   "right": { "RPM_PER_VOLT": 1254.54, "RPM_VOLT_OFFSET": -93.41 }')


if __name__ == "__main__":
    main()
