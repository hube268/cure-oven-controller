"""
pid_controller.py — PID Temperature Controller

Wraps simple-pid with project-specific limits and
a convenience method for duty-cycle output (0–100 %).
"""

import time
import logging
from simple_pid import PID

logger = logging.getLogger(__name__)


class OvenPID:
    """
    PID controller for oven temperature.

    Output is a duty cycle 0–100 that maps linearly to
    heater power (100 = always on, 0 = always off).
    With a relay-switched heater the duty cycle is applied
    via a time-proportioning window; with an SSR/PWM heater
    it maps directly to the PWM percentage.
    """

    def __init__(self, config: dict):
        cfg = config.get("pid", {})
        self._kp  = cfg.get("kp", 2.0)
        self._ki  = cfg.get("ki", 0.05)
        self._kd  = cfg.get("kd", 1.0)
        self._out_min = cfg.get("output_min", 0)
        self._out_max = cfg.get("output_max", 100)
        self._sample  = cfg.get("sample_time", 1.0)

        self._pid = PID(
            self._kp, self._ki, self._kd,
            setpoint=0,
            output_limits=(self._out_min, self._out_max),
            sample_time=self._sample,
            auto_mode=False,
        )

    # ------------------------------------------------------------------ #
    #  Control                                                             #
    # ------------------------------------------------------------------ #

    def set_setpoint(self, target: float):
        self._pid.setpoint = target
        logger.debug("PID setpoint → %.1f", target)

    def enable(self, current_temp: float):
        """Activate the PID, initialising the integrator to avoid bump."""
        self._pid.set_auto_mode(True, last_output=0)
        logger.info("PID enabled. Setpoint=%.1f  Current=%.1f",
                    self._pid.setpoint, current_temp)

    def disable(self):
        """Put the PID into manual mode (output → 0)."""
        self._pid.set_auto_mode(False)
        logger.info("PID disabled")

    def compute(self, current_temp: float) -> float:
        """
        Compute the next output given the current temperature.
        Returns duty cycle 0–100.
        """
        output = self._pid(current_temp)
        return output if output is not None else 0.0

    # ------------------------------------------------------------------ #
    #  Tuning                                                              #
    # ------------------------------------------------------------------ #

    def update_tunings(self, kp: float, ki: float, kd: float):
        self._pid.tunings = (kp, ki, kd)
        logger.info("PID tunings updated: Kp=%.3f Ki=%.3f Kd=%.3f", kp, ki, kd)

    @property
    def tunings(self) -> tuple:
        return self._pid.tunings

    @property
    def setpoint(self) -> float:
        return self._pid.setpoint

    @property
    def components(self) -> tuple:
        """Return (P, I, D) components of last computation (for diagnostics)."""
        return self._pid.components
