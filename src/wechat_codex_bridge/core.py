"""Core bridge logic: Codex CLI invocation + per-user thread tracking + poll handling."""
from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any

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


def _build_prompt(prompt: str, thread_id: str | None, system_prompt: str | None) -> str:
    """Prepend system instructions only on the very first turn (no thread_id yet)."""
    if thread_id or not system_prompt:
        return prompt
    return f"SYSTEM INSTRUCTIONS:\n{system_prompt}\n\nUSER:\n{prompt}"


def _run_codex_once(
    prompt: str,
    thread_id: str | None,
    *,
    model: str | None,
    timeout_s: float,
) -> tuple[subprocess.CompletedProcess[str] | None, str, str | None]:
    """Invoke `codex exec` (or `codex exec resume`). Returns (proc, last_msg, new_thread_id)."""
    last_msg_file = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    )
    last_msg_file.close()
    last_msg_path = last_msg_file.name

    args: list[str] = ["codex", "exec"]
    if thread_id:
        args.append("resume")
    args += [
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--json",
        "-o",
        last_msg_path,
    ]
    if model:
        args += ["-m", model]
    if thread_id:
        args.append(thread_id)
    args.append(prompt)

    LOG.info("codex run thread=%s prompt=%r", thread_id, prompt[:80])
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        _unlink_quiet(last_msg_path)
        return None, "", None

    new_thread_id = thread_id
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if evt.get("type") == "thread.started" and evt.get("thread_id"):
            new_thread_id = str(evt["thread_id"])
            break

    try:
        last_msg = Path(last_msg_path).read_text(encoding="utf-8")
    except OSError:
        last_msg = ""
    _unlink_quiet(last_msg_path)
    return proc, last_msg, new_thread_id


def _unlink_quiet(path: str) -> None:
    try:
        Path(path).unlink()
    except OSError:
        pass


def codex_respond(
    prompt: str,
    thread_id: str | None,
    *,
    model: str | None,
    system_prompt: str | None,
    timeout_s: float = 300.0,
) -> tuple[str, str | None]:
    """Invoke `codex exec` and return (reply, thread_id).

    If `exec resume` fails (stale thread_id from prior run, wiped sessions dir,
    etc.), retry once without resume so the caller can drop the stale mapping
    and start fresh.
    """
    full_prompt = _build_prompt(prompt, thread_id, system_prompt)
    proc, last_msg, new_tid = _run_codex_once(
        full_prompt, thread_id, model=model, timeout_s=timeout_s
    )
    if proc is None:
        return "[codex timed out]", thread_id

    if proc.returncode != 0 and thread_id:
        LOG.warning(
            "resume failed for thread=%s rc=%s stderr=%s stdout-tail=%s — retrying without resume",
            thread_id,
            proc.returncode,
            (proc.stderr or "").strip()[:300],
            (proc.stdout or "").strip()[-300:],
        )
        full_prompt = _build_prompt(prompt, None, system_prompt)
        proc, last_msg, new_tid = _run_codex_once(
            full_prompt, None, model=model, timeout_s=timeout_s
        )
        if proc is None:
            return "[codex timed out]", None
        thread_id = None

    if proc.returncode != 0:
        LOG.error(
            "codex exited %s stderr=%s stdout-tail=%s",
            proc.returncode,
            (proc.stderr or "").strip()[:500],
            (proc.stdout or "").strip()[-500:],
        )
        return f"[codex error rc={proc.returncode}]", new_tid or thread_id

    reply = (last_msg or "").strip() or "(no reply)"
    return reply, new_tid or thread_id


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
    """Consume a batch of WeChat messages: call Codex per sender, send replies back."""
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
            text, tid, model=model, system_prompt=system_prompt
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
