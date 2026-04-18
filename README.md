# wechat-claude-bridge

Self-contained one-command bridge from WeChat to Claude Code. Polls WeChat,
invokes `claude --print` per inbound message with a resumed session per
sender, and sends the reply back.

**No external dependencies on other repos** — the WeChat SDK is vendored in
`src/weixin_sdk/`. Clone this directory, run `install.sh`, done.

## Install (one command from a fresh clone)

```bash
git clone <this-repo>     # or cd into it
cd wechat-bridge
./install.sh
```

That:
1. installs `pipx` if missing,
2. runs `pipx install .` — which registers three CLIs globally:
   - `wechat-claude-bridge` — the bridge (main)
   - `wxcc` — short alias
   - `weixin-sdk` — the lower-level vendored SDK CLI (optional, for diagnostics)

The Claude CLI must be on `PATH` separately: <https://claude.com/download>.

Re-install over an existing install: `./install.sh --upgrade`.

If you prefer plain pip in a venv:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install .
```

## Use

```bash
wechat-claude-bridge login                            # scan the QR on your phone (one time)
wechat-claude-bridge --account-id bot-xxxxxxxx        # start the bridge (sonnet-4-6)
wechat-claude-bridge --account-id bot-xxxxxxxx --model claude-opus-4-7     # switch model
wechat-claude-bridge --account-id bot-xxxxxxxx --allowed-users u1@im.wechat,u2@im.wechat
wxcc --account-id bot-xxxxxxxx                        # short alias
```

`wechat-claude-bridge --help` / `wechat-claude-bridge run --help` for every flag.

## Design

Claude Code's one-shot turn model (`claude --print --output-format json`) fits
a message-bus bridge perfectly: run one turn per inbound message and capture
the `session_id` to resume the same conversation on the next turn.

```
WeChat phone
    │
    ▼ (Tencent iLink — reverse-engineered, vendored in src/weixin_sdk/)
weixin_sdk.AccountClient.poll_once()   ←── long-poll (25s)
    │
    ▼
wechat_claude_bridge.core.handle_poll_batch:
    for each inbound text:
      sid = sessions[from_user_id]                        # or None
      reply, sid' = claude --print --resume <sid> <text>  # subprocess
      sessions[from_user_id] = sid'                       # persist
      acct.send_text(from_user_id, reply)
sessions map: ~/.wechat-claude-bridge/sessions.json
    │
    ▼
claude CLI → Anthropic API → returns { result, session_id }
```

Key properties:

- **One Claude session per WeChat user** — coherent conversations across turns.
- **Bridge itself is stateless** — restart freely; state is in `sessions.json` + each user's Claude session store.
- **Model-agnostic sessions** — `--resume <sid>` works even if you switch model. Opus, Sonnet, Haiku all interoperate on the same conversation.
- **WeChat context token is forwarded** — preserves server-side WeChat conversation continuity.

## Flags

| Flag | Default | Purpose |
|------|---------|---------|
| `--account-id` | *(required)* | weixin-sdk bot account id |
| `--model` | `claude-sonnet-4-6` | Claude model |
| `--session-file` | `~/.wechat-claude-bridge/sessions.json` | Per-user session map |
| `--system-prompt` | (concise WeChat bot prompt) | Appended to Claude's system prompt |
| `--poll-timeout-s` | `25` | WeChat long-poll timeout |
| `--allowed-users` | *(empty = everyone)* | Comma-separated `from_user_id` allow-list |
| `--log-level` | `INFO` | Python logging level |

## Not implemented

- Media (images, voice, files, video) — `iter_media_items(msg)` is available but the bridge ignores it. Add `--image <path>` / transcription if needed.
- Per-user concurrency cap — if ten WeChat users message simultaneously, ten `claude` subprocesses run in parallel. Add a per-user `asyncio.Lock` if that bothers you.
- Admin interface — no runtime ops, no dashboard. It's a script.

## Layout

```
wechat-bridge/
├── install.sh                            # one-command bootstrap (pipx)
├── pyproject.toml                        # single package, three entry points
├── CREDITS.md                            # attribution for vendored SDK
├── bridge.py                             # legacy shim → package CLI
└── src/
    ├── wechat_claude_bridge/
    │   ├── __init__.py
    │   ├── __main__.py                   # python -m wechat_claude_bridge
    │   ├── cli.py                        # argparse, subcommands (login/run)
    │   └── core.py                       # claude_respond, handle_poll_batch
    └── weixin_sdk/                       # VENDORED — see CREDITS.md
        ├── client.py
        ├── login.py
        ├── messages.py
        └── ...
```
