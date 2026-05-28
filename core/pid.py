"""
PID controller with filtered derivative, integral deadband, and speed reduction.

Extracted from modes.py to be reusable across all auto-mode variants (forward,
reverse, future docking modes, etc.).
"""

import time


class PIDController:

    def __init__(self, kp, td, n, ti, ti_deadband, ti_max, dt, output_clamp,
                 v_red_coef, sr_alpha, sr_cap):
        self._kp          = kp
        self._td          = td
        self._n           = n
        self._ti          = ti
        self._ti_deadband = ti_deadband
        self._ti_max      = ti_max
        self._dt          = dt
        self._output_clamp = output_clamp
        self._v_red_coef  = v_red_coef
        self._sr_alpha    = sr_alpha
        self._sr_cap      = sr_cap
        self.reset()

    # ── State management ──────────────────────────────────────────────────────

    def reset(self):
        """Zero all integrator / filter state. Call on tape loss, emergency, or
        mode change — replaces the 6-variable reset block scattered through the
        original auto_mode."""
        self.integral        = 0.0
        self.last_pv         = 0.0
        self.filtered_d      = 0.0
        self.speed_reduction = 0.0
        self._last_t         = None   # perf_counter ts of previous compute()

    def update_gains(self, kp, td, n, v_red_coef):
        """Live gain switching (e.g. HIGH speed ↔ SLOW speed).

        Does NOT reset integrator or filter state — the switch should be smooth.
        The derivative filter coefficient is recomputed per cycle from the
        measured dt, so nothing to precompute here."""
        self._kp         = kp
        self._td         = td
        self._n          = n
        self._v_red_coef = v_red_coef

    # ── Compute ───────────────────────────────────────────────────────────────

    def compute(self, pv, base_rpm, error_sign=-1.0):
        """One PID cycle.

        Args:
            pv:          process variable — sensor left_mm (lateral offset in mm)
            base_rpm:    current target RPM from the acceleration ramp
            error_sign:  -1.0 for forward (sensor at front),
                         +1.0 for reverse  (sensor at rear — geometry inverted)

        Returns:
            (left_rpm, right_rpm, debug_dict)
        """
        e = error_sign * pv

        # ── Measured loop period ──────────────────────────────────────────────
        # Use the real elapsed time, not the nominal config.DT — frame timing
        # jitters and the loop only runs on fresh sensor frames. Clamp to a sane
        # band so a scheduling hiccup or a resume-after-stop gap can't blow up
        # the I/D terms.
        now = time.perf_counter()
        if self._last_t is None:
            dt = self._dt
        else:
            dt = now - self._last_t
            dt = max(0.2 * self._dt, min(5.0 * self._dt, dt))
        self._last_t = now

        # ── P ─────────────────────────────────────────────────────────────────
        p_term = self._kp * e

        # ── I (with deadband) ─────────────────────────────────────────────────
        if self._ti is not None:
            if abs(e) < self._ti_deadband:
                self.integral += e * dt
                self.integral  = max(-self._ti_max, min(self._ti_max, self.integral))
            i_term = self._kp * (1.0 / self._ti) * self.integral
        else:
            i_term = 0.0

        # ── D (low-pass filtered) ─────────────────────────────────────────────
        alpha            = dt / ((self._td / self._n) + dt)
        raw_d            = -self._kp * self._td * ((pv - self.last_pv) / dt)
        self.filtered_d += alpha * (raw_d - self.filtered_d)

        # ── Output clamp ──────────────────────────────────────────────────────
        output = p_term + i_term + self.filtered_d
        output = max(-self._output_clamp, min(self._output_clamp, output))

        # ── Speed reduction (low-pass filtered, capped) ───────────────────────
        raw_sr               = abs(e * self._v_red_coef) + abs((pv - self.last_pv) / dt) * 0.5
        raw_sr               = min(raw_sr, base_rpm * self._sr_cap)
        self.speed_reduction += self._sr_alpha * (raw_sr - self.speed_reduction)

        left_rpm  = base_rpm - self.speed_reduction + output
        right_rpm = base_rpm - self.speed_reduction - output

        self.last_pv = pv

        return left_rpm, right_rpm, {
            "e":               e,
            "p":               p_term,
            "i":               i_term,
            "d":               self.filtered_d,
            "output":          output,
            "speed_reduction": self.speed_reduction,
        }
