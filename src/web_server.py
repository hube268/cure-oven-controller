"""
web_server.py — Flask + SocketIO Web Server

Serves the dark-mode UI and exposes a REST + WebSocket API
consumed by both the kiosk Chromium window and remote browsers.

REST endpoints:
  GET  /api/state           — current snapshot
  GET  /api/history         — chart data
  POST /api/connect         — connect to PCB serial port
  POST /api/disconnect      — disconnect from PCB
  POST /api/setpoint        — set target temperature
  POST /api/start_cure      — begin a cure cycle
  POST /api/stop_cure       — abort cure
  POST /api/fan             — toggle fan
  POST /api/light           — toggle light
  GET  /api/ports           — list serial ports
  GET  /api/wifi/scan       — scan WiFi SSIDs
  POST /api/wifi/connect    — connect to WiFi
  GET  /api/wifi/status     — current WiFi info
  POST /api/update          — git pull OTA update

SocketIO event:
  server → client  'state_update'  — pushed every second
"""

import json
import socket as _socket
import subprocess
import threading
import time
import logging
import os

from flask import Flask, jsonify, request, render_template
from flask_socketio import SocketIO

from . import wifi_manager

logger = logging.getLogger(__name__)

# These are injected by main.py after construction
_controller = None
_state      = None
_config     = None


def create_app(controller, state, config: dict):
    global _controller, _state, _config
    _controller = controller
    _state      = state
    _config     = config

    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "..", "templates"),
        static_folder=os.path.join(os.path.dirname(__file__), "..", "static"),
    )
    app.config["SECRET_KEY"] = "cure-oven-secret-key"

    socketio = SocketIO(
        app,
        async_mode="threading",
        cors_allowed_origins="*",
        logger=False,
        engineio_logger=False,
    )

    # ------------------------------------------------------------------ #
    #  UI route                                                            #
    # ------------------------------------------------------------------ #

    @app.route("/")
    def index():
        return render_template("index.html")

    # ------------------------------------------------------------------ #
    #  State & history                                                     #
    # ------------------------------------------------------------------ #

    @app.route("/api/state")
    def api_state():
        return jsonify(_state.snapshot())

    @app.route("/api/history")
    def api_history():
        return jsonify(_state.history_for_chart())

    # ------------------------------------------------------------------ #
    #  Serial connection                                                   #
    # ------------------------------------------------------------------ #

    @app.route("/api/ports")
    def api_ports():
        return jsonify(_controller.available_ports())

    @app.route("/api/connect", methods=["POST"])
    def api_connect():
        body = request.get_json(silent=True) or {}
        port = body.get("port")
        ok = _controller.connect(port or None)
        return jsonify({"success": ok,
                        "port": _state.serial_port if ok else None})

    @app.route("/api/disconnect", methods=["POST"])
    def api_disconnect():
        _controller.disconnect()
        return jsonify({"success": True})

    # ------------------------------------------------------------------ #
    #  Temperature & cure                                                  #
    # ------------------------------------------------------------------ #

    @app.route("/api/setpoint", methods=["POST"])
    def api_setpoint():
        body = request.get_json(silent=True) or {}
        temp = body.get("temp")
        if temp is None:
            return jsonify({"error": "missing 'temp'"}), 400
        _controller.set_setpoint(float(temp))
        return jsonify({"success": True, "setpoint": float(temp)})

    @app.route("/api/start_cure", methods=["POST"])
    def api_start_cure():
        body = request.get_json(silent=True) or {}
        target   = body.get("target_temp")
        duration = body.get("duration_minutes")
        if target is None or duration is None:
            return jsonify({"error": "missing 'target_temp' or 'duration_minutes'"}), 400
        _controller.start_cure(float(target), float(duration))
        return jsonify({"success": True})

    @app.route("/api/stop_cure", methods=["POST"])
    def api_stop_cure():
        _controller.stop_cure()
        return jsonify({"success": True})

    # ------------------------------------------------------------------ #
    #  Accessories                                                         #
    # ------------------------------------------------------------------ #

    @app.route("/api/fan", methods=["POST"])
    def api_fan():
        body = request.get_json(silent=True) or {}
        on = bool(body.get("on", False))
        _controller.set_fan(on)
        return jsonify({"success": True, "fan": on})

    @app.route("/api/fan_speed", methods=["POST"])
    def api_fan_speed():
        body = request.get_json(silent=True) or {}
        level = body.get("level")
        if level is None:
            return jsonify({"error": "missing 'level' (0–255)"}), 400
        _controller.set_fan_speed(int(level))
        return jsonify({"success": True, "level": int(level)})

    @app.route("/api/light", methods=["POST"])
    def api_light():
        body = request.get_json(silent=True) or {}
        on = bool(body.get("on", False))
        _controller.set_light(on)
        return jsonify({"success": True, "light": on})

    # ------------------------------------------------------------------ #
    #  WiFi                                                                #
    # ------------------------------------------------------------------ #

    @app.route("/api/wifi/scan")
    def api_wifi_scan():
        networks = wifi_manager.scan_networks()
        return jsonify(networks)

    @app.route("/api/wifi/connect", methods=["POST"])
    def api_wifi_connect():
        body = request.get_json(silent=True) or {}
        ssid     = body.get("ssid")
        password = body.get("password")
        if not ssid:
            return jsonify({"error": "missing 'ssid'"}), 400
        ok, msg = wifi_manager.connect(ssid, password)
        return jsonify({"success": ok, "message": msg})

    @app.route("/api/wifi/status")
    def api_wifi_status():
        return jsonify(wifi_manager.current_connection())

    # ------------------------------------------------------------------ #
    #  OTA update                                                          #
    # ------------------------------------------------------------------ #

    @app.route("/api/update", methods=["POST"])
    def api_update():
        repo_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        try:
            result = subprocess.run(
                ["git", "pull"],
                capture_output=True, text=True, timeout=60, cwd=repo_dir
            )
            success = result.returncode == 0
            output  = result.stdout.strip() or result.stderr.strip()
            if success and "Already up to date" not in output:
                # Restart the service after update
                subprocess.Popen(["sudo", "systemctl", "restart", "oven-control"])
            return jsonify({"success": success, "output": output})
        except Exception as exc:
            return jsonify({"success": False, "output": str(exc)})

    @app.route("/api/server_info")
    def api_server_info():
        try:
            s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
        except Exception:
            ip = _socket.gethostname()
        port = _config.get("server", {}).get("port", 5000)
        return jsonify({"ip": ip, "port": port, "url": f"http://{ip}:{port}"})

    @app.route("/api/check_update")
    def api_check_update():
        repo_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        try:
            subprocess.run(["git", "fetch"], cwd=repo_dir, timeout=10,
                           capture_output=True)
            result = subprocess.run(
                ["git", "rev-list", "--count", "HEAD..@{u}"],
                capture_output=True, text=True, timeout=5, cwd=repo_dir
            )
            behind = int(result.stdout.strip() or "0")
            return jsonify({"update_available": behind > 0, "commits_behind": behind})
        except Exception as exc:
            return jsonify({"update_available": False, "error": str(exc)})

    # ------------------------------------------------------------------ #
    #  SocketIO — push state to all connected clients every second        #
    # ------------------------------------------------------------------ #

    def push_loop():
        while True:
            try:
                socketio.emit("state_update", _state.snapshot())
            except Exception:
                pass
            time.sleep(1.0)

    push_thread = threading.Thread(target=push_loop, daemon=True, name="socketio-push")
    push_thread.start()

    return app, socketio
