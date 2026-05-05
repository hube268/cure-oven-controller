"""
oven_controller.py — Oven Control Orchestrator

The LS-CURE PCB has its own built-in PID controller and state machine.
This layer:
  1. Relays user commands (start/stop/setpoint/fan/light) to the PCB
  2. Keeps a local cure-timer shadow for the UI (sourced from PCB broadcasts)
  3. Enforces a software safety cutoff in case the PCB reports over-temp
  4. Manages the serial connection lifecycle

Architecture note: we do NOT run a Python PID loop — the PCB handles
heating internally.  We simply send settings + start/cancel commands
and read state back from the ~4 Hz status broadcasts.
"""

import threading
import time
import logging
from typing import Optional

from .grbl_serial import GrblSerial
from .oven_state import OvenState

logger = logging.getLogger(__name__)


class OvenController:
    """
    Thin command layer between the Flask web server and the GrblSerial driver.

    Public API (called from Flask routes):
        connect(port)            — open serial connection
        disconnect()             — close serial connection
        set_setpoint(temp)       — update target temperature
        start_cure(temp, mins)   — configure + start a cure cycle on the PCB
        stop_cure()              — send cancel command to PCB
        set_fan(on)              — toggle fan
        set_fan_speed(level)     — set fan speed 0–255
        set_light(on)            — toggle light
        available_ports()        — list serial ports for UI dropdown
    """

    LOOP_HZ = 1          # state-check loop rate (PCB streams at ~4 Hz; we check at 1 Hz)
    MAX_TEMP_OVERSHOOT = 10.0  # °F above setpoint that triggers software safety cutoff

    def __init__(self, config: dict, state: OvenState):
        self._config   = config
        self._state    = state
        self._cmds     = config.get("commands", {})
        self._max_temp = config.get("temperature", {}).get("max_safe_temp", 450)

        self._serial = GrblSerial(
            config,
            on_status=self._on_status,
            on_line=self._on_line,
        )

        self._loop_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ------------------------------------------------------------------ #
    #  Lifecycle                                                           #
    # ------------------------------------------------------------------ #

    def start(self):
        """Start the background monitoring loop."""
        self._stop.clear()
        self._loop_thread = threading.Thread(
            target=self._monitor_loop, name="oven-monitor", daemon=True
        )
        self._loop_thread.start()
        logger.info("Oven controller started")

    def stop(self):
        """Stop the monitoring loop and disconnect serial."""
        self._stop.set()
        self._serial.disconnect()
        if self._loop_thread:
            self._loop_thread.join(timeout=5.0)
        logger.info("Oven controller stopped")

    # ------------------------------------------------------------------ #
    #  Connection management                                               #
    # ------------------------------------------------------------------ #

    def connect(self, port: Optional[str] = None) -> bool:
        ok = self._serial.connect(port)
        self._state.set_connected(ok, self._serial.port_name if ok else None)
        return ok

    def disconnect(self):
        self._serial.disconnect()
        self._state.set_connected(False)

    def available_ports(self) -> list:
        return self._serial.list_ports()

    # ------------------------------------------------------------------ #
    #  Oven commands (relayed to PCB)                                     #
    # ------------------------------------------------------------------ #

    def set_setpoint(self, temp: float):
        """Send temperature setpoint to PCB."""
        temp = min(float(temp), float(self._max_temp))
        self._state.set_setpoint(temp)
        cmd = self._cmds.get("set_temperature", "Temperature={value}").format(
            value=round(temp, 1)
        )
        self._serial.send_command(cmd)
        logger.info("Setpoint → %.1f°", temp)

    def warm_up(self, target_temp: float):
        """
        Phase 1 — send setpoint and start heating.
        PCB transitions: IDLE → WARMING UP → AT TEMP (holds until start_cure() called).
        Confirmed command: WarmUp (from pcb_output2.txt / pcb_output4.txt).
        """
        target_temp = min(float(target_temp), float(self._max_temp))
        self.set_setpoint(target_temp)
        warm_cmd = self._cmds.get("warm_up", "WarmUp")
        self._serial.send_command(warm_cmd)
        self._state.set_mode("heating")
        logger.info("Warm up started: target=%.1f°", target_temp)

    def start_cure(self, duration_minutes: float):
        """
        Phase 2 — begin cure countdown.
        Call after PCB has reached AT TEMP state.
        Sends cure time then Start; PCB transitions AT TEMP → CURING.
        After CURING completes PCB returns to AT TEMP (holds at setpoint).
        Confirmed command: Start (from pcb_output4.txt).
        """
        duration_minutes = float(duration_minutes)
        total_secs = int(duration_minutes * 60)
        h = total_secs // 3600
        m = (total_secs % 3600) // 60
        s = total_secs % 60
        time_cmd = self._cmds.get("set_cure_time", "CureTime={hours}:{minutes}:{seconds}").format(
            hours=h, minutes=f"{m:02d}", seconds=f"{s:02d}"
        )
        self._serial.send_command(time_cmd)
        start_cmd = self._cmds.get("start_cure", "Start")
        self._serial.send_command(start_cmd)
        self._state.start_cure_timer(total_secs)
        self._state.set_mode("curing")
        logger.info("Cure started: duration=%.0f min", duration_minutes)

    def stop_cure(self):
        """Send cancel command to PCB and reset local state."""
        cancel_cmd = self._cmds.get("cancel_cure", "Cancel")
        self._serial.send_command(cancel_cmd)
        self._state.stop_cure_timer()
        self._state.set_mode("idle")
        logger.info("Cure stopped / cancelled")

    def set_stay_warm(self, on: bool):
        """Toggle stay-warm mode on the PCB."""
        cmd = self._cmds.get("stay_warm_on", "StayOn") if on \
              else self._cmds.get("stay_warm_off", "StayOff")
        self._serial.send_command(cmd)
        logger.debug("Stay warm → %s", "ON" if on else "OFF")

    def set_fan(self, on: bool):
        """Toggle fan on or off (preserves last speed when turning on)."""
        cmd = self._cmds.get("fan_on", "FanOn") if on \
              else self._cmds.get("fan_off", "FanOff")
        self._serial.send_command(cmd)
        logger.debug("Fan → %s", "ON" if on else "OFF")

    def set_fan_speed(self, level: int):
        """Set fan speed directly (0–255)."""
        level = max(0, min(255, int(level)))
        cmd = self._cmds.get("fan_speed", "FanSpeed={value}").format(value=level)
        self._serial.send_command(cmd)
        logger.debug("FanSpeed → %d", level)

    def set_light(self, on: bool):
        """Toggle the oven light."""
        cmd = self._cmds.get("light_on", "LightOn") if on \
              else self._cmds.get("light_off", "LightOff")
        self._serial.send_command(cmd)
        logger.debug("Light → %s", "ON" if on else "OFF")

    # ------------------------------------------------------------------ #
    #  Background monitor loop                                             #
    # ------------------------------------------------------------------ #

    def _monitor_loop(self):
        """
        Low-rate loop: enforces safety limits and keeps the local cure
        timer shadow ticking for the UI when the PCB hasn't sent a fresh
        status update yet.
        """
        interval = 1.0 / self.LOOP_HZ
        while not self._stop.is_set():
            t_start = time.monotonic()
            try:
                self._monitor_tick()
            except Exception as exc:
                logger.error("Monitor loop exception: %s", exc)
            elapsed = time.monotonic() - t_start
            self._stop.wait(max(0.0, interval - elapsed))

    def _monitor_tick(self):
        snap = self._state.snapshot()
        mode = snap["mode"]
        temp = snap["temp_avg"] or snap["temp1"]

        if mode in ("heating", "ready", "curing") and temp is not None:
            # Software safety cutoff — belt-and-suspenders in case PCB doesn't stop
            if temp > self._max_temp + self.MAX_TEMP_OVERSHOOT:
                logger.error(
                    "OVER-TEMP safety cutoff: %.1f°  >  %.1f° — sending Cancel",
                    temp, self._max_temp
                )
                self.stop_cure()
                self._state.set_mode("error")
                return

        # Tick the Python-side cure timer shadow
        if mode == "curing" and snap["cure_duration"] > 0:
            done = self._state.tick_cure_timer()
            if done:
                logger.info("Cure timer complete (Python shadow) — waiting for PCB AT TEMP")

        # Mirror PCB state into our mode field (secondary to _on_status which runs more often)
        pcb = snap.get("pcb_state", "").upper()
        if pcb == "AT TEMP" and mode == "heating":
            self._state.set_mode("ready")
        elif pcb == "CURING" and mode in ("heating", "ready"):
            self._state.set_mode("curing")
        elif pcb == "AT TEMP" and mode == "curing":
            # Cure completed — PCB returned to AT TEMP holding state
            self._state.stop_cure_timer()
            self._state.set_mode("ready")
        elif pcb == "IDLE" and mode in ("heating", "ready", "curing"):
            self._state.stop_cure_timer()
            self._state.set_mode("idle")

    # ------------------------------------------------------------------ #
    #  Serial callbacks                                                    #
    # ------------------------------------------------------------------ #

    def _on_status(self, status: dict):
        """Called ~4×/sec from grbl_serial reader thread."""
        self._state.update_from_status(status)

        # Sync mode from confirmed PCB state strings (pcb_output4.txt, May 2026)
        pcb = status.get("state", "").upper()
        current_mode = self._state.mode
        if pcb == "WARMING UP" and current_mode == "idle":
            self._state.set_mode("heating")
        elif pcb == "AT TEMP" and current_mode == "heating":
            # Reached setpoint — waiting for Start command
            self._state.set_mode("ready")
        elif pcb == "CURING" and current_mode in ("heating", "ready"):
            self._state.set_mode("curing")
        elif pcb == "AT TEMP" and current_mode == "curing":
            # Cure completed — PCB holds at setpoint
            self._state.stop_cure_timer()
            self._state.set_mode("ready")
        elif pcb == "IDLE" and current_mode in ("heating", "ready", "curing"):
            self._state.stop_cure_timer()
            self._state.set_mode("idle")
        elif pcb == "ERROR" and current_mode != "error":
            self._state.set_mode("error")

    def _on_line(self, line: str):
        """Called for every non-status line received (ok/error/alarm/welcome)."""
        ul = line.upper()
        if "{OVER_TEMP}" in ul:
            # PCB informational warning when temp exceeds setpoint — not a fault state
            logger.debug("PCB over-temp warning (normal during curing): %s", line)
        elif "ALARM" in ul:
            logger.warning("PCB ALARM: %s", line)
            self._state.set_mode("error")
        elif ul.startswith("ERROR"):
            logger.warning("PCB error response: %s", line)
        elif line.startswith("[LS-CURE"):
            # Welcome / version message — PCB is ready
            logger.info("PCB welcome: %s", line)
            self._state.set_connected(True, self._serial.port_name)
