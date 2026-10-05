#!/usr/bin/env bash
# Shared setup for the install scripts. Sourced, not executed:
#   source "$(dirname "$0")/_common.sh"
#
# Resolves paths, locates `uv` (systemd and launchd run with a minimal PATH,
# so the absolute path is baked into the unit), and defines render_unit.
# Adapted from hestia/scripts/_pi-common.sh.

HERMES_HOME="$(cd "$(dirname "${BASH_SOURCE[1]}")/.." && pwd)"
USER_NAME="$(id -un)"
USER_HOME="$HOME"
OS="$(uname -s)"                       # Linux | Darwin
SYSTEMD_USER_DIR="$USER_HOME/.config/systemd/user"
LAUNCHD_DIR="$USER_HOME/Library/LaunchAgents"
LOG_DIR="$USER_HOME/Library/Logs/hermes"

SERVICE_NAME="hermes.service"
LAUNCHD_LABEL="com.hermes.server"

UV_BIN="$(command -v uv || true)"
if [[ -z "$UV_BIN" ]]; then
  for cand in "$USER_HOME/.local/bin/uv" "$USER_HOME/.cargo/bin/uv"; do
    [[ -x "$cand" ]] && UV_BIN="$cand" && break
  done
fi
if [[ -z "$UV_BIN" ]]; then
  echo "ERROR: 'uv' not found. Run 'make setup' first." >&2
  exit 1
fi

render_unit() {
  # render_unit <src> <dest> — copy with @HERMES_HOME@/@UV_BIN@/@LOG_DIR@ filled in.
  sed \
    -e "s|@HERMES_HOME@|$HERMES_HOME|g" \
    -e "s|@UV_BIN@|$UV_BIN|g" \
    -e "s|@LOG_DIR@|$LOG_DIR|g" \
    "$1" > "$2"
}
