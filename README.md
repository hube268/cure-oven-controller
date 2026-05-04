# Cure Oven Controller

A modern, dark-mode Raspberry Pi oven controller with kiosk display and web UI.
Built to replace the original CureControl (Langmuir Systems) Java kiosk app.

## Features

- **Dual thermocouple display** (Type K, TC1 + TC2)
- **PCB-native PID control** — the LS-CURE PCB runs its own PID; Python relays setpoints
- **Cure timer** — PCB-reported elapsed timer shown in real time
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
    "port": "auto",         // or "/dev/ttyACM0"
    "baud_rate": 115200
  },
  "temperature": {
    "units": "F",
    "max_safe_temp": 450
  },
  "commands": {
    "fan_on":          "FanSpeed=255",
    "fan_off":         "FanSpeed=0",
    "fan_speed":       "FanSpeed={value}",
    "light_on":        "Light=1",
    "light_off":       "Light=0",
    "set_temperature": "Temperature={value}",
    "set_cure_time":   "CureTime={hours}:{minutes}:{seconds}",
    "start_cure":      "Start",
    "cancel_cure":     "Cancel"
  }
}
```

---

## PCB Serial Protocol (Langmuir LS-CURE)

Reverse-engineered from `curecontrol.jar`. The PCB speaks a custom ASCII protocol at **115200 baud, 8N1**.

### Status Frame (broadcast ~4×/sec automatically)

```
<STATE|TIMER|IS_F|SETPOINT|TC_AVG (TC1, TC2)|COIL|FAN|FAN_SPEED|LIGHT|INTERNAL_TEMP|PID|STAY_ON|STAY_MIN|DOOR>
```

Live example:
```
<IDLE|0:00:00|1|400|62.92 (62.60, 63.05)|73.40|0|0|0|0|0.00|1|30|0>
```

| Field | Index | Example | Description |
|-------|-------|---------|-------------|
| STATE | [0] | `IDLE` | PCB state machine state |
| TIMER | [1] | `0:00:00` | PCB elapsed cure timer |
| IS_F  | [2] | `1` | 1 = Fahrenheit, 0 = Celsius |
| SETPOINT | [3] | `400` | Target temperature |
| TC_AVG (TC1, TC2) | [4] | `62.92 (62.60, 63.05)` | Avg temp with individual TC readings in parens |
| COIL | [5] | `73.40` | Heating element surface sensor (°F) |
| FAN | [6] | `0` | Fan on/off |
| FAN_SPEED | [7] | `0` | Fan speed 0–255 |
| LIGHT | [8] | `0` | Light on/off |
| INTERNAL_TEMP | [9] | `0` | PCB internal temperature sensor |
| PID | [10] | `0.00` | PCB's own PID output (0–100%) |
| STAY_ON | [11] | `1` | Stay-warm mode enabled |
| STAY_MIN | [12] | `30` | Stay-warm hold duration (minutes) |
| DOOR | [13] | `0` | Door sensor (1=closed, 0=open) |

### Control Commands (confirmed from JAR)

```
FanSpeed=255        → fan on at full speed
FanSpeed=0          → fan off
Light=1             → light on
Light=0             → light off
Temperature=400     → set target temperature (°F)
CureTime=0:30:00    → set cure duration (H:MM:SS)
Start               → begin cure cycle
Cancel              → abort cure cycle
```

> **Note:** `FanSpeed=` and `Light=` are confirmed from the JAR bytecode.
> `Temperature=`, `CureTime=`, `Start`, and `Cancel` are best-guess ASCII commands — verify with a serial terminal if they don't work, and update `config.json → commands` accordingly. No code changes needed.

The PCB runs its **own internal PID controller** (kp/ki/kd stored in EEPROM). Python does not run a PID loop — it only sends setpoint commands and reads state back from broadcasts.

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
