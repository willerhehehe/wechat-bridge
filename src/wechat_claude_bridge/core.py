"""Core bridge logic: Claude invocation + per-user session tracking + poll handling."""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

LOG = logging.getLogger("wechat_claude_bridge")


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


def claude_respond(
    prompt: str,
    session_id: str | None,
    *,
    model: str | None,
    system_prompt: str | None,
    timeout_s: float = 300.0,
) -> tuple[str, str | None]:
    """Invoke `claude --print` in JSON output mode and return (reply, session_id)."""
    args: list[str] = [
        "claude",
        "--print",
        "--output-format",
        "json",
        "--dangerously-skip-permissions",
    ]
    if model:
        args += ["--model", model]
    if system_prompt:
        args += ["--append-system-prompt", system_prompt]
    if session_id:
        args += ["--resume", session_id]
    args.append(prompt)
    LOG.info("claude run session=%s prompt=%r", session_id, prompt[:80])
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout_s
        )
    except subprocess.TimeoutExpired:
        return "[claude timed out]", session_id
    if proc.returncode != 0:
        LOG.error("claude exited %s stderr=%s", proc.returncode, proc.stderr[:500])
        return f"[claude error rc={proc.returncode}]", session_id
    last_line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "{}"
    try:
        data = json.loads(last_line)
    except json.JSONDecodeError:
        return (proc.stdout.strip()[:1000] or "(no reply)"), session_id
    reply = str(data.get("result") or "").strip() or "(no reply)"
    new_sid = data.get("session_id") or session_id
    return reply, new_sid


def handle_poll_batch(
    account_client: Any,
    messages: list[dict],
    sessions: dict[str, str],
    session_path: Path,
    *,
    model: str | None,
    system_prompt: str | None,
    allowed_users: set[str] | None = None,
) -> None:
    """Consume a batch of WeChat messages: call Claude per sender, send replies back."""
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
        sid = sessions.get(from_user)
        reply, new_sid = claude_respond(
            text, sid, model=model, system_prompt=system_prompt
        )
        if new_sid and new_sid != sid:
            sessions[from_user] = new_sid
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
