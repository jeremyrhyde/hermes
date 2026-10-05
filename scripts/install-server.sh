#!/usr/bin/env bash
# Run the Hermes server in the background, started at boot/login and
# restarted if it crashes.
#
#   Linux / Raspberry Pi: systemd user unit  (deploy/hermes.service)
#   macOS:                launchd user agent (deploy/com.hermes.server.plist)
#
# Usage (or the matching `make service-*` targets):
#   ./scripts/install-server.sh              # install + start
#   ./scripts/install-server.sh --uninstall  # stop + remove
#   ./scripts/install-server.sh --status
#   ./scripts/install-server.sh --logs
#   ./scripts/install-server.sh --restart
#
# Idempotent — running it twice is safe.

set -euo pipefail

ACTION="install"
case "${1:-}" in
  "")          ;;
  --uninstall) ACTION="uninstall" ;;
  --status)    ACTION="status" ;;
  --logs)      ACTION="logs" ;;
  --restart)   ACTION="restart" ;;
  -h|--help)   sed -n '2,17p' "$0" | sed 's/^# \?//'; exit 0 ;;
  *) echo "unknown arg: $1" >&2; exit 2 ;;
esac

source "$(dirname "$0")/_common.sh"

# ------------------------------------------------------------------ Linux
linux() {
  local unit="$SYSTEMD_USER_DIR/$SERVICE_NAME"
  case "$ACTION" in
    install)
      echo "[1/3] uv sync..."
      ( cd "$HERMES_HOME" && "$UV_BIN" sync )
      echo "[2/3] writing $unit..."
      mkdir -p "$SYSTEMD_USER_DIR"
      render_unit "$HERMES_HOME/deploy/hermes.service" "$unit"
      systemctl --user daemon-reload
      systemctl --user enable --now "$SERVICE_NAME"
      # Linger: start at boot without an interactive login.
      if ! loginctl show-user "$USER_NAME" 2>/dev/null | grep -q '^Linger=yes'; then
        echo "  enabling linger (sudo required)..."
        sudo loginctl enable-linger "$USER_NAME"
      fi
      echo "[3/3] status"
      systemctl --user status "$SERVICE_NAME" --no-pager -l || true
      echo
      echo "Server: http://$(hostname -I 2>/dev/null | awk '{print $1}'):8002/"
      echo "Logs:   make service-logs"
      ;;
    uninstall)
      systemctl --user disable --now "$SERVICE_NAME" 2>/dev/null || true
      rm -f "$unit"
      systemctl --user daemon-reload || true
      echo "Removed. Linger left in place (sudo loginctl disable-linger $USER_NAME)."
      ;;
    status)  systemctl --user status "$SERVICE_NAME" --no-pager -l || true ;;
    logs)    journalctl --user -u "$SERVICE_NAME" -f ;;
    restart) systemctl --user restart "$SERVICE_NAME" && echo "Restarted $SERVICE_NAME" ;;
  esac
}

# ------------------------------------------------------------------ macOS
macos() {
  local plist="$LAUNCHD_DIR/$LAUNCHD_LABEL.plist"
  local target="gui/$(id -u)"
  case "$ACTION" in
    install)
      echo "[1/3] uv sync..."
      ( cd "$HERMES_HOME" && "$UV_BIN" sync )
      echo "[2/3] writing $plist..."
      mkdir -p "$LAUNCHD_DIR" "$LOG_DIR"
      render_unit "$HERMES_HOME/deploy/com.hermes.server.plist" "$plist"
      launchctl bootout "$target/$LAUNCHD_LABEL" 2>/dev/null || true
      launchctl bootstrap "$target" "$plist"
      echo "[3/3] status"
      launchctl print "$target/$LAUNCHD_LABEL" | grep -E '^\s*(state|pid) =' || true
      echo
      echo "Server: http://localhost:8002/"
      echo "Logs:   make service-logs  ($LOG_DIR/hermes.log)"
      ;;
    uninstall)
      launchctl bootout "$target/$LAUNCHD_LABEL" 2>/dev/null || true
      rm -f "$plist"
      echo "Removed. Logs left in $LOG_DIR."
      ;;
    status)
      launchctl print "$target/$LAUNCHD_LABEL" 2>/dev/null \
        | grep -E '^\s*(state|pid|last exit code) =' || echo "not installed"
      ;;
    logs)    tail -f "$LOG_DIR/hermes.log" ;;
    restart) launchctl kickstart -k "$target/$LAUNCHD_LABEL" && echo "Restarted $LAUNCHD_LABEL" ;;
  esac
}

case "$OS" in
  Linux)  linux ;;
  Darwin) macos ;;
  *) echo "unsupported OS: $OS" >&2; exit 1 ;;
esac
