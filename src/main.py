"""
main.py — Entry Point

Loads config, wires up all components, starts the Flask/SocketIO server.
"""

import json
import logging
import os
import sys

# Ensure src/ is importable when run as `python src/main.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.oven_state      import OvenState
from src.oven_controller import OvenController
from src.web_server      import create_app

# ------------------------------------------------------------------ #
#  Logging                                                             #
# ------------------------------------------------------------------ #

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/tmp/oven-control.log"),
    ],
)
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
#  Config                                                              #
# ------------------------------------------------------------------ #

def load_config(path: str = None) -> dict:
    if path is None:
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config.json"
        )
    with open(path) as f:
        return json.load(f)


# ------------------------------------------------------------------ #
#  Main                                                                #
# ------------------------------------------------------------------ #

def main():
    logger.info("=== Cure Oven Controller starting ===")

    config = load_config()

    state      = OvenState(config)
    controller = OvenController(config, state)

    # Start control loop (background thread)
    controller.start()

    # Auto-connect on boot if a port is explicitly configured
    serial_cfg = config.get("serial", {})
    if serial_cfg.get("port", "auto") != "auto":
        logger.info("Auto-connecting to configured port %s", serial_cfg["port"])
        controller.connect(serial_cfg["port"])
    else:
        # Try auto-detect; don't block if no device plugged in yet
        logger.info("Attempting auto-detect of PCB serial port...")
        controller.connect()

    # Build and run Flask app
    app, socketio = create_app(controller, state, config)

    server_cfg = config.get("server", {})
    host = server_cfg.get("host", "0.0.0.0")
    port = server_cfg.get("port", 5000)

    logger.info("Web UI available at http://%s:%d", host, port)
    logger.info("Kiosk display → http://localhost:%d", port)

    try:
        socketio.run(app, host=host, port=port, debug=False, allow_unsafe_werkzeug=True)
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    finally:
        controller.stop()
        logger.info("=== Cure Oven Controller stopped ===")


if __name__ == "__main__":
    main()
