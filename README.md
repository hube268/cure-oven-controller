# Cure Oven Controller

A modern, dark-mode Raspberry Pi oven controller with kiosk display and web UI.
Built to replace the original CureControl (Langmuir Systems) Java kiosk app.

## Features

- **Dual thermocouple display** (Type K, TC1 + TC2)
- **PID temperature control** — smooth, overshoot-resistant
- **Cure timer** — starts automatically once target temp is reached
- **Fan & Light control**
- **Real-time temperature graph** (Chart.js, 1-hour history)
- **WiFi management** — scan SSIDs, enter credentials from the UI
- **Web UI** — accessible from any browser on your network
- **Kiosk display** — Chromium boots full-screen on the 7" HDMI display
- **OTA updates** — update button pulls latest code from GitHub

---

## Hardware

| Component | Detail |
|-----------|--------|
| SBC | Raspberry Pi 4B |
| OS  | Raspberry Pi OS 64-bit (Bookworm) |
| Display | 7" HDMI touchscreen (800×480) |
| PCB | Langmuir CureControl custom PCB |
| Interface | USB-serial (GRBL-derived protocol, 115200 baud) |
| Thermocouples | Type K × 2 |

---

## Quick Install (on the Raspberry Pi)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/hube268/cure-oven-controller/main/install.sh)
```

Or manually:

```bash
git clone https://github.com/hube268/cure-oven-controller.git
cd cure-oven-controller
bash install.sh
```

Then reboot:

```bash
sudo reboot
```

---

## Manual Start (development)

```bash
cd cure-oven-controller
source venv/bin/activate
python src/main.py
```

Web UI → `http://localhost:5000`

---

## Configuration

Edit `config.json` before first run:

```json
{
  "serial": {
    "port": "auto",         // or "/dev/ttyUSB0"
    "baud_rate": 115200
  },
  "temperature": {
    "units": "F",           // "F" or "C"
    "max_safe_temp": 300
  },
  "pid": {
    "kp": 2.0,
    "ki": 0.05,
    "kd": 1.0
  }
}
```

---

## ⚠️ Protocol Calibration (Important First Step)

The CureControl PCB speaks a GRBL-derived serial protocol.
The exact command set needs to be confirmed by observation.

**To discover your PCB's commands:**

1. Connect the PCB via USB
2. Open a serial terminal (e.g. `screen /dev/ttyUSB0 115200` or PuTTY)
3. Press `?` and observe the status response
4. Note the exact key names for temperature, heater, fan, light fields
5. Update `config.json` → `"parsing"` and `"commands"` sections to match

**Expected status format** (adjust `parsing` keys to match your firmware):
```
<Idle|T1:175.2|T2:173.8|Heat:1|Fan:0|Light:1>
```

**Expected control commands** (adjust `commands` to match your firmware):
```
M104 S200   → set heater target to 200°
M106 S255   → fan on
M107        → fan off
M355 S1     → light on
M355 S0     → light off
```

If your PCB uses different command syntax, update `config.json` — no code changes needed.

---

## Logs

```bash
# Live backend log
journalctl -u oven-control -f

# Kiosk display log
journalctl -u oven-kiosk -f

# File log
tail -f /tmp/oven-control.log
```

---

## OTA Updates

From the UI: click **↑ Update** → **Check** → **Apply Update**

From the command line:
```bash
cd /home/pi/cure-oven-controller
git pull
sudo systemctl restart oven-control
```

---

## Project Structure

```
cure-oven-controller/
├── config.json              # All configuration
├── requirements.txt         # Python dependencies
├── install.sh               # One-command installer
├── src/
│   ├── main.py              # Entry point
│   ├── grbl_serial.py       # USB serial communication (pyserial)
│   ├── pid_controller.py    # PID temperature controller
│   ├── oven_state.py        # Thread-safe shared state
│   ├── oven_controller.py   # Main control loop + cure timer
│   ├── web_server.py        # Flask + SocketIO API
│   └── wifi_manager.py      # WiFi via nmcli
├── templates/
│   └── index.html           # Full dark-mode UI
└── systemd/
    ├── oven-control.service # Backend service
    └── oven-kiosk.service   # Chromium kiosk service
```

---

## License

MIT — use freely, modify as needed.
