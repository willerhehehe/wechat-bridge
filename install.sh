#!/usr/bin/env bash
# One-command installer for wechat-claude-bridge.
#
# The repo is self-contained — the openclaw-weixin SDK is vendored alongside
# the bridge, so a fresh clone + this script is all you need.
#
#   wechat-claude-bridge login
#   wechat-claude-bridge --account-id <bot_id>
#
# Usage:
#   ./install.sh
#   ./install.sh --upgrade    # re-install over existing
#
# Behavior:
#   - If run inside an active venv ($VIRTUAL_ENV set), installs into it via
#     plain `pip install .`. This is what you want when you've already made
#     a venv for this project.
#   - Otherwise installs globally via pipx (isolated app venv, CLIs on PATH).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

if ! command -v claude >/dev/null 2>&1; then
  echo "WARNING: \`claude\` CLI not on PATH. Install it from https://claude.com/download"
fi

UPGRADE=""
if [[ "${1:-}" == "--upgrade" ]]; then
  UPGRADE="1"
fi

if [[ -n "${VIRTUAL_ENV:-}" ]]; then
  echo "detected active venv: $VIRTUAL_ENV"
  echo "installing into venv via pip (pipx not used inside a venv)"
  if [[ -n "$UPGRADE" ]]; then
    pip install --force-reinstall "$SCRIPT_DIR"
  else
    pip install "$SCRIPT_DIR"
  fi
  INSTALL_MODE="venv"
else
  if ! command -v pipx >/dev/null 2>&1; then
    echo "pipx not found — installing via \`python3 -m pip install --user pipx\`"
    python3 -m pip install --user pipx
    python3 -m pipx ensurepath
    export PATH="$HOME/.local/bin:$PATH"
  fi
  echo "installing wechat-claude-bridge globally via pipx from $SCRIPT_DIR"
  if [[ -n "$UPGRADE" ]]; then
    pipx install --force "$SCRIPT_DIR"
  else
    pipx install "$SCRIPT_DIR"
  fi
  INSTALL_MODE="pipx"
fi

cat <<EOF

done. (install mode: $INSTALL_MODE)

Next steps:
  wechat-claude-bridge login              # scan QR on your phone
  wechat-claude-bridge --account-id <id>  # run the bridge loop
  wechat-claude-bridge --help             # full usage
  wxcc --help                             # short alias
  weixin-sdk --help                       # vendored low-level SDK CLI
EOF
