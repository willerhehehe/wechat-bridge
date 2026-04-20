"""CLI for wechat-codex-bridge.

Drives `codex app-server` (JSON-RPC over stdio) — one long-running codex
process shared across all WeChat users, one thread per user.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from . import __version__
from .appserver import AppServerClient
from .core import handle_poll_batch, load_session_map

LOG = logging.getLogger("wechat_codex_bridge")
CONFIG_DIR = Path.home() / ".wechat-codex-bridge"
DEFAULT_SESSION_FILE = CONFIG_DIR / "sessions.json"
DEFAULT_WORKDIR = CONFIG_DIR / "workdir"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant replying on WeChat. "
    "Keep replies concise (about one short paragraph, plain text, no markdown)."
)
# Curated list of user-visible codex models (from codex-rs/models-manager/models.json,
# `visibility: list` entries). Update when codex ships new models.
MODEL_CHOICES = [
    ("gpt-5.4", "GPT-5.4 — default, most capable"),
    ("gpt-5.3-codex", "GPT-5.3 Codex"),
    ("gpt-5.2-codex", "GPT-5.2 Codex"),
    ("gpt-5.2", "GPT-5.2"),
    ("gpt-5.1-codex-max", "GPT-5.1 Codex Max"),
    ("gpt-5.1-codex-mini", "GPT-5.1 Codex Mini — fastest"),
]
SANDBOX_CHOICES = ("read-only", "workspace-write", "danger-full-access")
APPROVAL_CHOICES = ("never", "unlessTrusted", "always")
DEFAULT_SANDBOX = "danger-full-access"
DEFAULT_APPROVAL = "never"


def _load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception:
        return {}


def _save_config(config: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2))


def _prompt_for_model() -> str:
    print("Which codex model should the bridge use?")
    for idx, (model_id, desc) in enumerate(MODEL_CHOICES, 1):
        print(f"  [{idx}] {model_id}  — {desc}")
    print(f"  [0] (use codex default from ~/.codex/config.toml)")
    while True:
        raw = input(f"choice [0-{len(MODEL_CHOICES)}, default 1]: ").strip() or "1"
        if raw == "0":
            return ""
        if raw.isdigit() and 1 <= int(raw) <= len(MODEL_CHOICES):
            return MODEL_CHOICES[int(raw) - 1][0]
        print(f"please enter a number 0-{len(MODEL_CHOICES)}")


def _resolve_model(provided: str | None) -> str | None:
    """Pick model: CLI flag > saved config > interactive prompt > codex default (None)."""
    config = _load_config()
    if provided is not None:
        config["model"] = provided
        _save_config(config)
        return provided or None
    if "model" in config:
        saved = config["model"]
        if saved:
            LOG.info("using model=%s (from %s)", saved, CONFIG_FILE)
        else:
            LOG.info("using codex default model (saved in %s)", CONFIG_FILE)
        return saved or None
    if not sys.stdin.isatty():
        return None
    chosen = _prompt_for_model()
    config["model"] = chosen
    _save_config(config)
    print(f"saved to {CONFIG_FILE} — re-run without --model to reuse, or pass --model to override")
    return chosen or None


def cmd_login(_args: argparse.Namespace) -> int:
    try:
        from weixin_sdk.cli import main as weixin_main
    except ImportError:
        print("weixin_sdk not installed. Re-run install.sh.", file=sys.stderr)
        return 1
    return weixin_main(["login"])


def _resolve_account_id(provided: str | None) -> str | None:
    if provided:
        return provided
    try:
        from weixin_sdk.store import StateStore
    except ImportError:
        return None
    accounts = StateStore().list_accounts()
    if len(accounts) == 1:
        LOG.info("using account=%s (only one stored)", accounts[0].account_id)
        return accounts[0].account_id
    if not accounts:
        print("No WeChat account stored. Run `wechat-codex-bridge login` first.", file=sys.stderr)
        return None
    print("Multiple accounts stored — pick one with --account-id:", file=sys.stderr)
    for acct in accounts:
        print(f"  {acct.account_id}", file=sys.stderr)
    return None


def cmd_run(args: argparse.Namespace) -> int:
    try:
        from weixin_sdk.client import AccountClient
        from weixin_sdk.exceptions import WeixinApiError, WeixinError
    except ImportError as e:
        print(f"weixin_sdk not available: {e}", file=sys.stderr)
        return 1

    account_id = _resolve_account_id(args.account_id)
    if not account_id:
        return 1
    args.account_id = account_id
    args.model = _resolve_model(args.model)

    try:
        acct = AccountClient.from_store(account_id)
    except WeixinError as e:
        LOG.error("account load failed: %s", e)
        LOG.error("run `wechat-codex-bridge login` first")
        return 1

    session_path = Path(args.session_file)
    sessions = load_session_map(session_path)
    allowed = set(args.allowed_users.split(",")) if args.allowed_users else None

    codex = AppServerClient(
        model=args.model,
        sandbox=args.sandbox,
        approval_policy=args.approval_policy,
        cwd=Path(args.workdir),
        client_version=__version__,
    )
    try:
        codex.start()
    except FileNotFoundError:
        print("codex binary not found on PATH. Install codex-cli first.", file=sys.stderr)
        return 1

    LOG.info(
        "bridge ready account=%s user=%s model=%s sandbox=%s approval=%s%s",
        args.account_id,
        acct.credentials.user_id,
        args.model or "(codex default)",
        args.sandbox,
        args.approval_policy,
        f" allowed={len(allowed)}" if allowed else "",
    )

    try:
        while True:
            try:
                poll = acct.poll_once(timeout_s=args.poll_timeout_s)
            except WeixinApiError as e:
                LOG.error("session expired: %s — re-run `wechat-codex-bridge login`", e)
                return 1
            except KeyboardInterrupt:
                LOG.info("interrupted, exiting")
                return 0
            except Exception as e:  # noqa: BLE001
                LOG.warning("poll error: %s (retry in 5s)", e)
                time.sleep(5)
                continue

            if poll.messages:
                handle_poll_batch(
                    acct,
                    codex,
                    poll.messages,
                    sessions,
                    session_path,
                    system_prompt=args.system_prompt,
                    allowed_users=allowed,
                )
    finally:
        codex.stop()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wechat-codex-bridge",
        description="Bridge WeChat messages to codex (via `codex app-server`).",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--log-level", default="INFO", help="Python logging level (default: INFO)")
    sub = p.add_subparsers(dest="cmd", required=False)

    sub_login = sub.add_parser("login", help="Scan the WeChat QR to register a bot account")
    sub_login.set_defaults(func=cmd_login)

    sub_run = sub.add_parser("run", help="Start the bridge loop (default command)")
    sub_run.add_argument(
        "--account-id",
        default=None,
        help="weixin-sdk account id (omit to auto-use the only stored account)",
    )
    sub_run.add_argument(
        "--model",
        default=None,
        help=(
            "Codex model slug (e.g. gpt-5.4). Omit on first run for an interactive picker "
            f"— saved to {CONFIG_FILE}. Use '' to fall back to codex's config.toml default."
        ),
    )
    sub_run.add_argument(
        "--sandbox",
        choices=SANDBOX_CHOICES,
        default=DEFAULT_SANDBOX,
        help=(
            "codex sandbox mode. Default danger-full-access gives codex full agent "
            "capabilities; tighten to read-only or workspace-write if WeChat users are "
            "untrusted (codex will still receive their text verbatim)."
        ),
    )
    sub_run.add_argument(
        "--approval-policy",
        choices=APPROVAL_CHOICES,
        default=DEFAULT_APPROVAL,
        help="codex approval policy (default: never — auto-approve everything the sandbox allows)",
    )
    sub_run.add_argument(
        "--workdir",
        default=str(DEFAULT_WORKDIR),
        help=f"cwd for codex threads (default: {DEFAULT_WORKDIR})",
    )
    sub_run.add_argument(
        "--session-file",
        default=str(DEFAULT_SESSION_FILE),
        help=f"Per-user codex thread map (default: {DEFAULT_SESSION_FILE})",
    )
    sub_run.add_argument("--system-prompt", default=DEFAULT_SYSTEM_PROMPT)
    sub_run.add_argument("--poll-timeout-s", type=float, default=25.0)
    sub_run.add_argument(
        "--allowed-users",
        default="",
        help="Comma-separated allow-list of WeChat from_user_id values (omit to allow everyone)",
    )
    sub_run.set_defaults(func=cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in {"login", "run", "-h", "--help", "--version"}:
        argv = ["run", *argv]

    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(message)s")
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
