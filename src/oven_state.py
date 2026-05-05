"""
oven_state.py — Thread-Safe Shared Application State

Single source of truth consumed by the oven controller,
the web server, and the SocketIO emitter.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Optional, List
from collections import deque


@dataclass
class TempSample:
    timestamp: float  # epoch seconds
    temp1: Optional[float]
    temp2: Optional[float]


class OvenState:
    """
    Thread-safe container for all oven runtime state.
    Read with state.snapshot() to get an atomic copy.
    """

    HISTORY_SECONDS = 3600   # 1 hour of graph data

    def __init__(self, config: dict):
        self._lock = threading.RLock()
        cfg_temp = config.get("temperature", {})
        self._units: str = cfg_temp.get("units", "F")

        # --- Sensor readings ---
        self.temp1: Optional[float] = None      # TC1 raw reading
        self.temp2: Optional[float] = None      # TC2 raw reading
        self.temp_avg: Optional[float] = None   # averaged TC reading (primary display)
        self.coil_temp: Optional[float] = None  # heating element surface sensor
        self.heater_on: bool = False             # heating coil relay (field [6])
        self.pcb_state: str = "Disconnected"
        self.fan_on: bool = False
        self.fan_speed: float = 0.0
        self.light_on: bool = False
        self.stay_on: bool = False
        self.stay_on_minutes: float = 30.0
        self.door_closed: bool = True

        # --- Setpoint & mode ---
        self.setpoint: float = 0.0
        self.mode: str = "idle"             # idle | heating | curing | cooling | error
        self.pid_output: float = 0.0        # PCB's own PID output (0–100)
        self.pcb_timer: str = "0:00:00"     # PCB-reported elapsed timer string

        # --- Cure timer ---
        self.cure_duration_sec: int = 0
        self.cure_elapsed_sec: float = 0.0
        self.cure_started_at: Optional[float] = None  # epoch

        # --- Connection ---
        self.serial_connected: bool = False
        self.serial_port: Optional[str] = None

        # --- History (ring buffer for graph) ---
        max_samples = self.HISTORY_SECONDS * 2   # ~2 samples/s max
        self._history: deque = deque(maxlen=max_samples)

    # ------------------------------------------------------------------ #
    #  Update helpers (called from GrblSerial callbacks)                   #
    # ------------------------------------------------------------------ #

    def update_from_status(self, status: dict):
        with self._lock:
            # Thermocouple readings
            self.temp1        = status.get("temp1")
            self.temp2        = status.get("temp2")
            self.temp_avg     = status.get("temp_avg")
            self.coil_temp    = status.get("coil_temp")
            self.heater_on    = status.get("heater", False)

            # Output device states
            self.fan_on       = status.get("fan", False)
            self.fan_speed    = status.get("fan_speed", 0.0) or 0.0
            self.light_on     = status.get("light", False)

            # Stay-warm mode
            self.stay_on         = status.get("stay_on", False)
            self.stay_on_minutes = status.get("stay_on_minutes") or self.stay_on_minutes

            # Door sensor
            self.door_closed  = status.get("door_closed", True)

            # PCB state machine
            self.pcb_state    = status.get("state", "Unknown")

            # PCB-reported timer string (e.g. "0:12:34")
            self.pcb_timer = status.get("timer", "0:00:00")

            # Sync setpoint reported by PCB so UI stays consistent
            if status.get("setpoint") is not None:
                self.setpoint = status["setpoint"]

            # PCB's own PID output (0–100)
            if status.get("pid_output") is not None:
                self.pid_output = status["pid_output"]

            # Push to history ring buffer
            self._history.append(TempSample(
                timestamp=time.time(),
                temp1=self.temp1,
                temp2=self.temp2,
            ))

    def set_connected(self, connected: bool, port: Optional[str] = None):
        with self._lock:
            self.serial_connected = connected
            self.serial_port = port
            if not connected:
                self.pcb_state = "Disconnected"
                self.mode = "idle"

    def set_mode(self, mode: str):
        with self._lock:
            self.mode = mode

    def set_setpoint(self, value: float):
        with self._lock:
            self.setpoint = value

    def set_pid_output(self, output: float):
        with self._lock:
            self.pid_output = output

    def start_cure_timer(self, duration_sec: int):
        with self._lock:
            self.cure_duration_sec = duration_sec
            self.cure_elapsed_sec = 0.0
            self.cure_started_at = time.time()

    def tick_cure_timer(self) -> bool:
        """
        Update elapsed time. Returns True when cure is complete.
        Call ~once per second from the control loop.
        """
        with self._lock:
            if self.cure_started_at is None:
                return False
            self.cure_elapsed_sec = time.time() - self.cure_started_at
            return self.cure_elapsed_sec >= self.cure_duration_sec

    def stop_cure_timer(self):
        with self._lock:
            self.cure_started_at = None
            self.cure_elapsed_sec = 0.0

    # ------------------------------------------------------------------ #
    #  Snapshot (atomic read for API/SocketIO)                             #
    # ------------------------------------------------------------------ #

    def snapshot(self) -> dict:
        with self._lock:
            remaining = max(
                0,
                self.cure_duration_sec - int(self.cure_elapsed_sec)
            ) if self.cure_started_at else self.cure_duration_sec

            return {
                # Temperatures
                "temp1":            self.temp1,
                "temp2":            self.temp2,
                "temp_avg":         self.temp_avg,
                "coil_temp":        self.coil_temp,
                "heater_on":        self.heater_on,

                # Target & mode
                "setpoint":         self.setpoint,
                "mode":             self.mode,
                "pcb_state":        self.pcb_state,
                "pcb_timer":        self.pcb_timer,

                # Output devices
                "fan_on":           self.fan_on,
                "fan_speed":        self.fan_speed,
                "light_on":         self.light_on,

                # PCB control loop (0=off, 10000=full power)
                "pid_output":       round(self.pid_output, 1),

                # Stay-warm mode
                "stay_on":          self.stay_on,
                "stay_on_minutes":  self.stay_on_minutes,

                # Door sensor
                "door_closed":      self.door_closed,

                # Connection
                "serial_connected": self.serial_connected,
                "serial_port":      self.serial_port,
                "units":            self._units,

                # Cure timer (Python-side shadow for UI)
                "cure_duration":    self.cure_duration_sec,
                "cure_elapsed":     int(self.cure_elapsed_sec),
                "cure_remaining":   remaining,
            }

    def history_for_chart(self, max_points: int = 300) -> dict:
        """Return temp history formatted for Chart.js."""
        with self._lock:
            samples = list(self._history)

        # Downsample if needed
        if len(samples) > max_points:
            step = len(samples) // max_points
            samples = samples[::step]

        now = time.time()
        labels, t1_data, t2_data = [], [], []
        for s in samples:
            age = int(now - s.timestamp)
            labels.append(f"-{age}s")
            t1_data.append(round(s.temp1, 1) if s.temp1 is not None else None)
            t2_data.append(round(s.temp2, 1) if s.temp2 is not None else None)

        return {"labels": labels, "temp1": t1_data, "temp2": t2_data}
