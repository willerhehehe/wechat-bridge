"""Minimal JSON-RPC 2.0 client for `codex app-server` over stdio.

One long-running codex process, one thread per WeChat user.
Drastically faster than `codex exec` per message (~1-3s steady state vs. ~8-10s).
"""
from __future__ import annotations

import json
import logging
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

LOG = logging.getLogger("wechat_codex_bridge")


class AppServerError(RuntimeError):
    pass


class AppServerClient:
    """Blocking single-threaded client (JSON-RPC dispatch, background reader)."""

    def __init__(
        self,
        *,
        model: Optional[str],
        sandbox: str,
        approval_policy: str,
        cwd: Path,
        client_name: str = "wechat-codex-bridge",
        client_version: str = "0.1.0",
    ) -> None:
        self.model = model
        self.sandbox = sandbox
        self.approval_policy = approval_policy
        self.cwd = cwd
        self.client_name = client_name
        self.client_version = client_version
        self._proc: Optional[subprocess.Popen[str]] = None
        self._next_id = 1
        self._lines: queue.Queue[Optional[str]] = queue.Queue()
        self._reader: Optional[threading.Thread] = None
        self._call_lock = threading.Lock()

    def start(self) -> None:
        if self._proc is not None:
            return
        self.cwd.mkdir(parents=True, exist_ok=True)
        LOG.info(
            "starting codex app-server (cwd=%s sandbox=%s approval=%s model=%s)",
            self.cwd, self.sandbox, self.approval_policy, self.model or "(default)",
        )
        self._proc = subprocess.Popen(
            ["codex", "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader.start()

        self._call("initialize", {
            "clientInfo": {
                "name": self.client_name,
                "title": self.client_name,
                "version": self.client_version,
            },
        }, timeout_s=30)
        self._notify("initialized", {})

    def stop(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._proc = None

    def start_thread(self) -> str:
        params: dict[str, Any] = {
            "cwd": str(self.cwd),
            "approvalPolicy": self.approval_policy,
            "sandbox": self.sandbox,
        }
        if self.model:
            params["model"] = self.model
        result = self._call("thread/start", params, timeout_s=60)
        return result["thread"]["id"]

    def resume_thread(self, thread_id: str) -> str:
        """Re-attach to a prior thread_id. Raises AppServerError if unknown."""
        params: dict[str, Any] = {"threadId": thread_id}
        if self.model:
            params["model"] = self.model
        result = self._call("thread/resume", params, timeout_s=30)
        return result["thread"]["id"]

    def run_turn(
        self,
        thread_id: str,
        prompt: str,
        *,
        timeout_s: float = 300.0,
    ) -> str:
        """Send one user message, block until `turn/completed`, return agent reply text."""
        with self._call_lock:
            rid = self._next_id
            self._next_id += 1
            self._write({
                "jsonrpc": "2.0",
                "method": "turn/start",
                "id": rid,
                "params": {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": prompt}],
                },
            })
            parts: list[str] = []
            deadline = time.time() + timeout_s
            while True:
                obj = self._read_obj(deadline - time.time())
                if obj is None:
                    raise TimeoutError("turn did not complete in time")
                if obj.get("id") == rid:
                    if "error" in obj:
                        raise AppServerError(f"turn/start error: {obj['error']}")
                    continue  # turn started; keep listening
                m = obj.get("method")
                if m == "item/agentMessage/delta":
                    parts.append(obj["params"]["delta"])
                elif m == "turn/completed":
                    turn = obj["params"]["turn"]
                    if turn.get("status") == "failed":
                        err = turn.get("error") or {}
                        raise AppServerError(f"turn failed: {err}")
                    return "".join(parts)
                elif m == "error":
                    raise AppServerError(f"server error: {obj.get('params')}")

    def _call(self, method: str, params: dict, *, timeout_s: float) -> dict:
        with self._call_lock:
            rid = self._next_id
            self._next_id += 1
            self._write({"jsonrpc": "2.0", "method": method, "id": rid, "params": params})
            deadline = time.time() + timeout_s
            while True:
                obj = self._read_obj(deadline - time.time())
                if obj is None:
                    raise TimeoutError(f"{method} timed out")
                if obj.get("id") == rid:
                    if "error" in obj:
                        raise AppServerError(f"{method} error: {obj['error']}")
                    return obj.get("result", {})
                # ignore notifications arriving during RPC

    def _notify(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _write(self, obj: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise AppServerError("app-server not started")
        self._proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    def _reader_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            for line in self._proc.stdout:
                self._lines.put(line.rstrip("\r\n"))
        finally:
            self._lines.put(None)

    def _read_obj(self, remaining_s: float) -> Optional[dict]:
        wait = max(remaining_s, 0.0)
        try:
            line = self._lines.get(timeout=wait if wait > 0 else 0.001)
        except queue.Empty:
            return None
        if line is None:
            rc = self._proc.poll() if self._proc else None
            raise AppServerError(f"app-server stdout closed (rc={rc})")
        if not line:
            return {}
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            LOG.warning("dropping non-JSON line from app-server: %r", line[:200])
            return {}
