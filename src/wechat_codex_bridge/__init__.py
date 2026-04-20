"""Bridge WeChat messages to the Codex CLI (and back).

Mirrors `wechat_claude_bridge` but invokes `codex exec` / `codex exec resume`
and tracks a per-WeChat-user `thread_id` instead of Claude's `session_id`.
"""
from __future__ import annotations

from .core import codex_respond, handle_poll_batch

__version__ = "0.1.0"
__all__ = ["codex_respond", "handle_poll_batch", "__version__"]
