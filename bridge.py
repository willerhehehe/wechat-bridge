#!/usr/bin/env python3
"""Backwards-compatible entry point.

`wechat-bridge/bridge.py` used to be the whole bridge. It is now a tiny shim
that delegates to the `wechat_claude_bridge` package (same behavior). Prefer
the installed `wechat-claude-bridge` CLI (see `install.sh`).
"""
from __future__ import annotations

import sys

from wechat_claude_bridge.cli import main

if __name__ == "__main__":
    sys.exit(main())
