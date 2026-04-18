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


def cmd_run(args: argparse.Namespace) -> int:
    try:
        from weixin_sdk.client import AccountClient
        from weixin_sdk.exceptions import WeixinApiError, WeixinError
    except ImportError as e:
        print(f"weixin_sdk not available: {e}", file=sys.stderr)
        print("run `wechat-claude-bridge login` first (re-run install.sh if needed).", file=sys.stderr)
        return 1

    try:
        acct = AccountClient.from_store(args.account_id)
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
    sub_run.add_argument("--account-id", required=True, help="weixin-sdk account id")
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
    # Default to `run` if no subcommand is given and --help isn't requested.
    if argv and argv[0] not in {"login", "run", "-h", "--help", "--version"}:
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
