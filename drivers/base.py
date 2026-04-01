"""
Abstract base classes for hardware drivers.

Every sensor driver must implement:
  - run(state)       — async task that reads hardware and updates state
  - get_health()     — returns health dict consumed by safety_watchdog

Every actuator driver (output) only needs:
  - run(state)       — async task that consumes from a state queue and writes HW

The safety watchdog only monitors sensor/input drivers (DI, CAN, RFID) because
those are the ones whose staleness could cause the AGV to act on bad data.
Output drivers (DO, AO, SLMP) fail visibly as "motion not responding" and do
not need watchdog supervision.
"""

import time
from abc import ABC, abstractmethod


class SensorDriver(ABC):

    def __init__(self, name: str):
        self._name    = name
        self._last_rx = 0.0   # epoch of last successful hardware read
        self._ok      = False  # True once first successful read

    @abstractmethod
    async def run(self, state) -> None:
        """Async task body.  Should loop forever, updating state and
        calling self._record_rx() on every successful hardware read."""
        ...

    def _record_rx(self):
        """Call inside run() on every successful read to keep watchdog happy."""
        self._last_rx = time.time()
        self._ok      = True

    def get_health(self) -> dict:
        """Called by safety_watchdog every 50 ms.

        Returns:
            {
                "ok":      bool   — False until first read, or after unrecoverable failure
                "last_rx": float  — epoch of last successful read  (0.0 = never)
                "detail":  str    — human-readable identifier for log messages
            }
        """
        return {
            "ok":      self._ok,
            "last_rx": self._last_rx,
            "detail":  self._name,
        }


class ActuatorDriver(ABC):

    @abstractmethod
    async def run(self, state) -> None:
        """Async task body.  Should loop forever consuming from a state queue."""
        ...
