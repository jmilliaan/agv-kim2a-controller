"""
PID controller with filtered derivative, integral deadband, and speed reduction.

Extracted from modes.py to be reusable across all auto-mode variants (forward,
reverse, future docking modes, etc.).
"""


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
        self._alpha       = dt / ((td / n) + dt)
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

    def update_gains(self, kp, td, n, v_red_coef):
        """Live gain switching (e.g. HIGH speed ↔ SLOW speed).

        Does NOT reset integrator or filter state — the switch should be smooth.
        Only the derivative filter coefficient needs recomputing."""
        self._kp         = kp
        self._td         = td
        self._n          = n
        self._v_red_coef = v_red_coef
        self._alpha      = self._dt / ((td / n) + self._dt)

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

        # ── P ─────────────────────────────────────────────────────────────────
        p_term = self._kp * e

        # ── I (with deadband) ─────────────────────────────────────────────────
        if self._ti is not None:
            if abs(e) < self._ti_deadband:
                self.integral += e * self._dt
                self.integral  = max(-self._ti_max, min(self._ti_max, self.integral))
            i_term = self._kp * (1.0 / self._ti) * self.integral
        else:
            i_term = 0.0

        # ── D (low-pass filtered) ─────────────────────────────────────────────
        # Derivative must honour error_sign: the P term acts on e = error_sign*pv,
        # so the derivative has to be sign-matched or it becomes ANTI-damping.
        # For error_sign=-1 (front sensor) this equals the old -kp*td*dpv/dt.
        # For error_sign=+1 (AGV B rear sensor, SENSOR_ORIENTATION=-1) it flips to
        # the correct sign — the previous code omitted error_sign here and made
        # the derivative feed the weave instead of damping it.
        raw_d           = self._kp * self._td * error_sign * ((pv - self.last_pv) / self._dt)
        self.filtered_d += self._alpha * (raw_d - self.filtered_d)

        # ── Output clamp ──────────────────────────────────────────────────────
        output = p_term + i_term + self.filtered_d
        output = max(-self._output_clamp, min(self._output_clamp, output))

        # ── Speed reduction (low-pass filtered, capped) ───────────────────────
        raw_sr               = abs(e * self._v_red_coef) + abs((pv - self.last_pv) / self._dt) * 0.5
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
