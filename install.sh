#!/bin/bash
# ============================================================
#  Cure Oven Controller — One-Command Installer
#  Run as: bash install.sh
# ============================================================
set -e

REPO_URL="https://github.com/hube268/cure-oven-controller.git"
SERVICE_USER="$(logname 2>/dev/null || whoami)"
INSTALL_DIR="/home/$SERVICE_USER/cure-oven-controller"

echo ""
echo "╔═══════════════════════════════════════════╗"
echo "║   Cure Oven Controller — Installer        ║"
echo "╚═══════════════════════════════════════════╝"
echo ""

# ── 1. System dependencies ───────────────────────────────────
echo "[1/7] Installing system packages…"
sudo apt-get update -qq
sudo apt-get install -y -qq \
  python3 python3-pip python3-venv \
  git \
  chromium \
  network-manager \
  libopenblas-dev

# ── 2. Clone or update repo ──────────────────────────────────
echo "[2/7] Fetching source code from GitHub…"
if [ -d "$INSTALL_DIR/.git" ]; then
  echo "  → Existing repo found — pulling latest…"
  cd "$INSTALL_DIR"
  git pull
else
  git clone "$REPO_URL" "$INSTALL_DIR"
  cd "$INSTALL_DIR"
fi

# ── 3. Python virtual environment ───────────────────────────
echo "[3/7] Creating Python virtual environment…"
python3 -m venv venv
source venv/bin/activate

# ── 4. Python dependencies ───────────────────────────────────
echo "[4/7] Installing Python packages…"
pip install --upgrade pip -q
pip install -r requirements.txt -q

# ── 5. Dialout group (serial port access) ────────────────────
echo "[5/7] Adding user to dialout group (serial access)…"
sudo usermod -aG dialout "$SERVICE_USER"

# ── 6. Systemd services ──────────────────────────────────────
echo "[6/7] Installing systemd services…"

# Backend service
sudo cp systemd/oven-control.service /etc/systemd/system/
sudo sed -i "s|/home/pi/cure-oven-controller|$INSTALL_DIR|g" \
  /etc/systemd/system/oven-control.service

# Kiosk service
sudo cp systemd/oven-kiosk.service /etc/systemd/system/
sudo sed -i "s|/home/pi/cure-oven-controller|$INSTALL_DIR|g" \
  /etc/systemd/system/oven-kiosk.service

sudo systemctl daemon-reload
sudo systemctl enable oven-control.service
sudo systemctl enable oven-kiosk.service

# ── 7. Sudoers for OTA restart ───────────────────────────────
echo "[7/7] Configuring OTA update permissions…"
SUDOERS_LINE="$SERVICE_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart oven-control"
if ! sudo grep -qF "$SUDOERS_LINE" /etc/sudoers; then
  echo "$SUDOERS_LINE" | sudo tee -a /etc/sudoers > /dev/null
fi

# ── Done ─────────────────────────────────────────────────────
echo ""
echo "╔═══════════════════════════════════════════╗"
echo "║          Installation complete!           ║"
echo "╚═══════════════════════════════════════════╝"
echo ""
echo "  Start now:   sudo systemctl start oven-control"
echo "               sudo systemctl start oven-kiosk"
echo ""
echo "  Web UI:      http://$(hostname -I | awk '{print $1}'):5000"
echo ""
echo "  Logs:        journalctl -u oven-control -f"
echo ""
echo "  NOTE: You may need to reboot for dialout group"
echo "        membership to take effect."
echo ""
