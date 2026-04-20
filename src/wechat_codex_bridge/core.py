"""Bridge core: long-running codex app-server + per-user thread_id map."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .appserver import AppServerClient, AppServerError

LOG = logging.getLogger("wechat_codex_bridge")


def load_session_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def save_session_map(path: Path, sessions: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sessions, ensure_ascii=False, indent=2))


def _ensure_thread(
    client: AppServerClient,
    stored_tid: str | None,
    *,
    system_prompt: str | None,
) -> tuple[str, bool]:
    """Resolve a thread id usable for turn/start. Returns (thread_id, is_new).

    - If we have a stored id, try thread/resume; fall back to thread/start on failure.
    - If new, seed the thread with system instructions as the first user turn.
    """
    if stored_tid:
        try:
            tid = client.resume_thread(stored_tid)
            return tid, False
        except AppServerError as e:
            LOG.warning("resume failed for thread=%s (%s) — starting fresh", stored_tid, e)
    tid = client.start_thread()
    if system_prompt:
        try:
            client.run_turn(tid, f"SYSTEM INSTRUCTIONS:\n{system_prompt}", timeout_s=60)
        except Exception as e:  # noqa: BLE001
            LOG.warning("system-prompt seed turn failed: %s", e)
    return tid, True


def codex_respond(
    client: AppServerClient,
    prompt: str,
    stored_tid: str | None,
    *,
    system_prompt: str | None,
    timeout_s: float = 300.0,
) -> tuple[str, str | None]:
    """One user turn. Returns (reply, new_thread_id)."""
    try:
        tid, _ = _ensure_thread(client, stored_tid, system_prompt=system_prompt)
    except AppServerError as e:
        LOG.error("cannot obtain thread: %s", e)
        return f"[codex init error: {e}]", stored_tid
    try:
        reply = client.run_turn(tid, prompt, timeout_s=timeout_s)
    except TimeoutError:
        return "[codex timed out]", tid
    except AppServerError as e:
        LOG.error("run_turn failed: %s", e)
        return f"[codex error: {e}]", tid
    return (reply.strip() or "(no reply)"), tid


def handle_poll_batch(
    account_client: Any,
    codex_client: AppServerClient,
    messages: list[dict],
    sessions: dict[str, str],
    session_path: Path,
    *,
    system_prompt: str | None,
    allowed_users: set[str] | None = None,
) -> None:
    """Consume a batch of WeChat messages: one codex turn per sender, send replies back."""
    from weixin_sdk.exceptions import WeixinError
    from weixin_sdk.messages import extract_text_body

    for msg in messages:
        from_user = msg.get("from_user_id")
        text = extract_text_body(msg)
        if not isinstance(from_user, str) or not text:
            continue
        if allowed_users and from_user not in allowed_users:
            LOG.info("skip from=%s (not in allowed list)", from_user)
            continue
        LOG.info("in  from=%s text=%r", from_user, text[:120])
        tid = sessions.get(from_user)
        reply, new_tid = codex_respond(
            codex_client, text, tid, system_prompt=system_prompt
        )
        if new_tid != tid:
            if new_tid:
                sessions[from_user] = new_tid
            else:
                sessions.pop(from_user, None)
            save_session_map(session_path, sessions)
        try:
            account_client.send_text(
                to_user_id=from_user,
                text=reply,
                context_token=msg.get("context_token"),
            )
            LOG.info("out to=%s len=%d", from_user, len(reply))
        except WeixinError as e:
            LOG.error("send_text failed: %s", e)
