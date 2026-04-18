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
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

if ! command -v claude >/dev/null 2>&1; then
  echo "WARNING: \`claude\` CLI not on PATH. Install it from https://claude.com/download"
fi

if ! command -v pipx >/dev/null 2>&1; then
  echo "pipx not found — installing via \`python3 -m pip install --user pipx\`"
  python3 -m pip install --user pipx
  python3 -m pipx ensurepath
  export PATH="$HOME/.local/bin:$PATH"
fi

FORCE=()
if [[ "${1:-}" == "--upgrade" ]]; then
  FORCE=(--force)
fi

echo "installing wechat-claude-bridge from $SCRIPT_DIR"
pipx install "${FORCE[@]}" "$SCRIPT_DIR"

cat <<EOF

done.

Next steps:
  wechat-claude-bridge login              # scan QR on your phone
  wechat-claude-bridge --account-id <id>  # run the bridge loop
  wechat-claude-bridge --help             # full usage
  wxcc --help                             # short alias
  weixin-sdk --help                       # vendored low-level SDK CLI
EOF
