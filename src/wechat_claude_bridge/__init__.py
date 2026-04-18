"""wechat-claude-bridge: bridge WeChat ↔ Claude Code."""

from .core import claude_respond, handle_poll_batch, load_session_map, save_session_map

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "claude_respond",
    "handle_poll_batch",
    "load_session_map",
    "save_session_map",
]
