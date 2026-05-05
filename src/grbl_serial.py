"""
grbl_serial.py — CureControl PCB Serial Communication

Replicates the behaviour of JSerialDeviceChannel.class from the original
CureControl software using pyserial instead of jSerialComm.

Protocol: GRBL-derived ASCII over USB-serial (115200 8N1).
  - Commands  →  ASCII string + '\\n'
  - Real-time →  single byte (no newline), e.g. b'?' for status
  - Status    ←  '<State|Key:Val|Key:Val|...>'
  - ACK       ←  'ok' or 'error:N'
"""

import re
import serial
import serial.tools.list_ports
import threading
import time
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class GrblSerial:
    """
    Thread-safe GRBL serial interface.

    Usage:
        gs = GrblSerial(config, on_status=my_status_cb, on_line=my_line_cb)
        gs.connect()
        gs.send_command("M106 S255")   # fan on
        gs.disconnect()

    Callbacks:
        on_status(dict)  — called every time a '<...>' status frame arrives
        on_line(str)     — called for every other line received (ok/error/alarm)
    """

    POLL_INTERVAL = 0.5  # seconds between '?' polls

    def __init__(self, config: dict,
                 on_status: Callable[[dict], None],
                 on_line: Callable[[str], None]):
        self._config = config
        self._on_status = on_status
        self._on_line = on_line

        self._port: Optional[serial.Serial] = None
        self._port_name: Optional[str] = None
        self._connected = False

        self._write_lock = threading.Lock()
        self._stop = threading.Event()
        self._read_thread: Optional[threading.Thread] = None
        self._poll_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def port_name(self) -> Optional[str]:
        return self._port_name

    def find_port(self) -> Optional[str]:
        """
        Locate the CureControl PCB serial port.
        Priority:
          1. Explicit port in config
          2. Match by USB VID/PID
          3. First available ttyUSB / ttyACM port
        """
        cfg = self._config.get("serial", {})
        explicit = cfg.get("port", "auto")
        if explicit and explicit != "auto":
            return explicit

        ports = serial.tools.list_ports.comports()

        vid = cfg.get("vid")
        pid = cfg.get("pid_usb")
        if vid and pid:
            for p in ports:
                if p.vid == vid and p.pid == pid:
                    logger.info("Found PCB by VID/PID at %s", p.device)
                    return p.device

        # Fall back: any USB-serial looking port
        candidates = [
            p.device for p in ports
            if "USB" in (p.description or "") or
               "ttyUSB" in p.device or
               "ttyACM" in p.device or
               "Arduino" in (p.description or "")
        ]
        if candidates:
            logger.info("Auto-detected serial port: %s", candidates[0])
            return candidates[0]

        logger.warning("No serial port found automatically.")
        return None

    def list_ports(self) -> list:
        """Return all serial ports as [{device, description}] for the UI."""
        return [
            {"device": p.device, "description": p.description or p.device}
            for p in serial.tools.list_ports.comports()
        ]

    def connect(self, port_name: Optional[str] = None) -> bool:
        """Open the serial connection and start background threads."""
        self.disconnect()

        self._port_name = port_name or self.find_port()
        if not self._port_name:
            logger.error("connect() — no port available")
            return False

        cfg = self._config.get("serial", {})
        try:
            self._port = serial.Serial(
                port=self._port_name,
                baudrate=cfg.get("baud_rate", 115200),
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=cfg.get("timeout", 2.0),
            )
        except serial.SerialException as exc:
            logger.error("Failed to open %s: %s", self._port_name, exc)
            return False

        # GRBL resets when DTR is toggled on open; wait for boot message
        time.sleep(2.0)
        self._port.reset_input_buffer()

        self._connected = True
        self._stop.clear()

        self._read_thread = threading.Thread(
            target=self._read_loop, name="grbl-reader", daemon=True
        )
        self._read_thread.start()

        # The CureControl PCB auto-broadcasts status continuously —
        # no polling needed. Poll thread is disabled unless config
        # sets "auto_poll": true.
        if self._config.get("serial", {}).get("auto_poll", False):
            self._poll_thread = threading.Thread(
                target=self._poll_loop, name="grbl-poller", daemon=True
            )
            self._poll_thread.start()

        logger.info(
            "Connected to PCB at %s @ %d baud",
            self._port_name,
            cfg.get("baud_rate", 115200),
        )
        return True

    def disconnect(self):
        """Close the connection and stop background threads."""
        self._stop.set()
        self._connected = False

        if self._port and self._port.is_open:
            try:
                self._port.close()
            except Exception:
                pass
        self._port = None

        if self._read_thread:
            self._read_thread.join(timeout=2.0)
        if self._poll_thread:
            self._poll_thread.join(timeout=2.0)

        self._read_thread = None
        self._poll_thread = None
        logger.info("Disconnected from PCB")

    def send_command(self, command: str) -> bool:
        """Send a GRBL command string (newline appended automatically)."""
        if not self._connected or not self._port:
            logger.warning("send_command('%s') — not connected", command)
            return False
        try:
            with self._write_lock:
                self._port.write((command.strip() + "\n").encode("utf-8"))
            logger.debug("TX: %s", command.strip())
            return True
        except serial.SerialException as exc:
            logger.error("Write error: %s", exc)
            self._connected = False
            return False

    def send_realtime(self, byte: bytes) -> bool:
        """Send a GRBL real-time single-byte command (no newline)."""
        if not self._connected or not self._port:
            return False
        try:
            with self._write_lock:
                self._port.write(byte)
            return True
        except serial.SerialException as exc:
            logger.error("Realtime write error: %s", exc)
            return False

    # ------------------------------------------------------------------ #
    #  Background threads                                                  #
    # ------------------------------------------------------------------ #

    def _read_loop(self):
        """Continuously read lines from the serial port."""
        buf = b""
        while not self._stop.is_set():
            try:
                if self._port and self._port.in_waiting:
                    buf += self._port.read(self._port.in_waiting)
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        text = line.decode("utf-8", errors="replace").strip()
                        if text:
                            self._dispatch(text)
                else:
                    time.sleep(0.01)
            except serial.SerialException:
                logger.error("Serial read error — disconnecting")
                self._connected = False
                break
            except Exception as exc:
                logger.error("Read loop exception: %s", exc)

    def _poll_loop(self):
        """Periodically send '?' to request a status frame."""
        poll_cmd = self._config.get("commands", {}).get("status_poll", "?")
        while not self._stop.is_set():
            if self._connected:
                if poll_cmd == "?":
                    self.send_realtime(b"?")
                else:
                    self.send_command(poll_cmd)
            self._stop.wait(self.POLL_INTERVAL)

    # ------------------------------------------------------------------ #
    #  Parsing                                                             #
    # ------------------------------------------------------------------ #

    def _dispatch(self, line: str):
        logger.debug("RX: %s", line)
        if line.startswith("<") and line.endswith(">"):
            status = self._parse_status(line)
            if status:
                self._on_status(status)
        else:
            self._on_line(line)

    def _parse_status(self, frame: str) -> Optional[dict]:
        """
        Parse the CureControl PCB status frame.

        Confirmed positional pipe-delimited format (14 fields, 0-indexed):
          <STATE|TIMER|IS_F|SETPOINT|TC_AVG (TC1, TC2)|COIL|FAN|FAN_SPEED|LIGHT|INTERNAL_TEMP|PID|STAY_ON|STAY_MIN|DOOR>
           [0]   [1]   [2]   [3]          [4]           [5]  [6]   [7]      [8]     [9]        [10]  [11]    [12]   [13]

        Confirmed PCB states (from pcb_output4.txt full-cycle capture, May 2026):
          IDLE        — heater off, not running; field [1] shows preset duration
          WARMING UP  — heater relay on (field [6]=1), PID=10000 (full blast); timer frozen
          AT TEMP     — at setpoint, waiting for Start command; also state after cure completes
                        heater off (field [6]=0), PID=0; timer shows preset duration
          CURING      — cure countdown active; heater managed by PCB PID; timer counts DOWN
          {OVER_TEMP} — non-frame warning line emitted when temp exceeds setpoint (not an error)

        Canonical logical field names (from CureMachineStatus.java / MachineStatusMessage.java):
          state; time; isFahrenheit; targetTemperature; temperatureReading;
          temperature1; temperature2;  ← embedded in field [4] parentheses
          coil; fan; fanSpeed; light; internalTemperature; pidOutput;
          stayOn; stayWarmMinutes; doorClosed

        Live examples from PCB:
          <IDLE|0:03:00|1|200|75.82 (72.50, 79.25)|66.54|0|0|0|0|10000.00|1|30|1>
          <WARMING UP|0:03:00|1|200|76.29 (72.95, 79.70)|66.54|1|0|0|0|10000.00|1|30|1>
          <AT TEMP|0:03:00|1|200|207.84 (186.80, 229.55)|66.65|0|0|0|0|0.00|1|30|1>
          <CURING|0:02:59|1|200|207.84 (187.25, 230.00)|66.65|0|0|0|0|0.00|1|30|1>

        Notes:
          - Field [4] = "avg (tc1, tc2)" e.g. "62.92 (62.60, 63.05)"
            TC1 and TC2 are parsed from the parenthetical; avg is the first token.
          - Field [5] = coil temp — heating element surface sensor (°F, room temp at idle)
          - Field [6] = fan on/off boolean
          - Field [7] = fan speed (0–255 or level enum)
          - Field [8] = light on/off boolean
          - Field [9] = PCB internal temperature sensor
          - Field [10] = PCB's own PID output (0=off, 10000=full power; observed 10000.00 during WARMING UP)
          - Field [11] = stay-warm enable flag
          - Field [12] = stay-warm hold duration in minutes
          - Field [13] = door sensor (1=closed, 0=open)

        Commands confirmed from JAR reverse-engineering:
          FanSpeed=<int>   — set fan speed (0=off, 255=full)
          Light=<0|1>      — set light state
        """
        try:
            content = frame[1:-1]       # strip < >
            parts = content.split("|")

            if len(parts) < 9:
                logger.debug("Short status frame (%d fields): %s", len(parts), frame)
                return None

            def fpart(idx: int) -> Optional[float]:
                """Parse float from parts[idx], taking only the first whitespace-delimited token."""
                try:
                    return float(parts[idx].strip().split()[0])
                except (IndexError, ValueError):
                    return None

            def bpart(idx: int) -> bool:
                """Parse boolean from parts[idx]: anything other than '0' or '' is True."""
                try:
                    return parts[idx].strip() not in ("0", "")
                except IndexError:
                    return False

            def parse_tc_field(idx: int):
                """
                Parse field [4] which looks like "62.92 (62.60, 63.05)".
                Returns (avg, tc1, tc2). Falls back gracefully if format differs.
                """
                try:
                    s = parts[idx].strip()
                    avg = float(s.split()[0])
                    m = re.search(r'\(([^,]+),\s*([^)]+)\)', s)
                    if m:
                        return avg, float(m.group(1)), float(m.group(2))
                    return avg, avg, None
                except (IndexError, ValueError):
                    return None, None, None

            tc_avg, tc1, tc2 = parse_tc_field(4)

            return {
                # PCB state machine
                "state":             parts[0].strip(),
                "timer":             parts[1].strip() if len(parts) > 1 else "0:00:00",
                "is_fahrenheit":     bpart(2),

                # Temperatures
                "setpoint":          fpart(3),
                "temp_avg":          tc_avg,      # average of both TCs
                "temp1":             tc1,          # TC1 raw reading
                "temp2":             tc2,          # TC2 raw reading
                "coil_temp":         fpart(5),     # heating element surface sensor
                "internal_temp":     fpart(9),     # PCB internal temperature sensor

                # Output devices — field mapping confirmed from live PCB capture May 2026
                # [6] = heater relay on/off (0 when idle)
                # [7] = FAN on/off  (confirmed: FanOn sets this to 1)
                # [8] = FAN_SPEED   (0–255, always 0 in idle capture)
                # [9] = LIGHT on/off (confirmed: LightOn sets this to 1)
                "heater":            bpart(6),
                "fan":               bpart(7),
                "fan_speed":         fpart(8),
                "light":             bpart(9),

                # PCB control loop
                "pid_output":        fpart(10),    # PCB's own PID output (0–10000; 10000=full power confirmed from live capture)

                # Stay-warm mode
                "stay_on":           bpart(11),
                "stay_on_minutes":   fpart(12),

                # Door sensor
                "door_closed":       bpart(13),

                # Raw for debugging
                "raw":               parts,
            }
        except Exception as exc:
            logger.error("Status parse error on '%s': %s", frame, exc)
            return None
