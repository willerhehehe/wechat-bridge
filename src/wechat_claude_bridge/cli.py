"""Command-line interface for wechat-claude-bridge."""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from . import __version__
from .core import handle_poll_batch, load_session_map

LOG = logging.getLogger("wechat_claude_bridge")
DEFAULT_SESSION_FILE = Path.home() / ".wechat-claude-bridge" / "sessions.json"
DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant replying on WeChat. "
    "Keep replies concise (about one short paragraph, plain text, no markdown)."
)


def cmd_login(_args: argparse.Namespace) -> int:
    """Delegate to `weixin-sdk login` so users don't need to know about it."""
    try:
        from weixin_sdk.cli import main as weixin_main
    except ImportError:
        print(
            "weixin_sdk not installed. Install with:\n"
            "  pipx inject wechat-claude-bridge openclaw-weixin-python\n"
            "or re-run install.sh.",
            file=sys.stderr,
        )
        return 1
    return weixin_main(["login"])


def _resolve_account_id(provided: str | None) -> str | None:
    """If the user didn't pass --account-id, auto-pick the sole stored account.

    Returns the resolved id, or None (caller prints the error) if ambiguous or empty.
    """
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
        print(
            "No WeChat account stored. Run `wechat-claude-bridge login` first.",
            file=sys.stderr,
        )
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
        print("run `wechat-claude-bridge login` first (re-run install.sh if needed).", file=sys.stderr)
        return 1

    account_id = _resolve_account_id(args.account_id)
    if not account_id:
        return 1
    args.account_id = account_id

    try:
        acct = AccountClient.from_store(account_id)
    except WeixinError as e:
        LOG.error("account load failed: %s", e)
        LOG.error("run `wechat-claude-bridge login` first")
        return 1

    session_path = Path(args.session_file)
    sessions = load_session_map(session_path)
    allowed = set(args.allowed_users.split(",")) if args.allowed_users else None
    LOG.info(
        "bridge ready account=%s user=%s model=%s%s",
        args.account_id,
        acct.credentials.user_id,
        args.model,
        f" allowed={len(allowed)}" if allowed else "",
    )

    while True:
        try:
            poll = acct.poll_once(timeout_s=args.poll_timeout_s)
        except WeixinApiError as e:
            LOG.error("session expired: %s — re-run `wechat-claude-bridge login`", e)
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
                poll.messages,
                sessions,
                session_path,
                model=args.model,
                system_prompt=args.system_prompt,
                allowed_users=allowed,
            )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wechat-claude-bridge",
        description="Bridge WeChat messages to Claude Code (and back).",
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
    sub_run.add_argument("--model", default="claude-sonnet-4-6", help="Claude model (default: claude-sonnet-4-6)")
    sub_run.add_argument(
        "--session-file",
        default=str(DEFAULT_SESSION_FILE),
        help=f"Per-user Claude session map (default: {DEFAULT_SESSION_FILE})",
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
    # Default to `run` when no subcommand is given (including bare invocation)
    # and --help/--version isn't requested.
    if not argv or argv[0] not in {"login", "run", "-h", "--help", "--version"}:
        argv = ["run", *argv]

    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
