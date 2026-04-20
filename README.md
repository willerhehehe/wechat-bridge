# wechat-bridge

Self-contained one-command bridge from WeChat to your coding agent. Polls
WeChat, invokes the agent CLI (`claude --print` **or** `codex exec`) per
inbound message with a resumed session per sender, and sends the reply back.
Pick either bridge per invocation — they share the WeChat login and account
store, just talk to different agents.

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
2. runs `pipx install .` — which registers five CLIs globally:
   - `wechat-claude-bridge` / `wxcc` — bridge to Claude Code
   - `wechat-codex-bridge` / `wxcx` — bridge to the Codex CLI
   - `weixin-sdk` — lower-level vendored SDK CLI (optional, for diagnostics)

Whichever agent CLI you plan to use must be on `PATH` separately:
- Claude: <https://claude.com/download>
- Codex: installed via Homebrew / the Codex installer, binary name `codex`.

Re-install over an existing install: `./install.sh --upgrade`.

If you prefer plain pip in a venv:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install .
```

## Use

Log in once (shared by both bridges — both read the same weixin-sdk account store):

```bash
wechat-claude-bridge login     # or: wechat-codex-bridge login — either works
```

Run whichever agent you want:

```bash
# Claude bridge
wechat-claude-bridge                       # first run: interactive model picker, then starts
wechat-claude-bridge --model claude-opus-4-7
wxcc                                       # short alias

# Codex bridge
wechat-codex-bridge                        # uses ~/.codex/config.toml default model
wechat-codex-bridge --model gpt-5-codex    # per-run override
wxcx                                       # short alias
```

Common flags (both bridges):

```bash
--account-id <id>           # omit if only one bot is stored
--allowed-users u1,u2       # comma-separated from_user_id allow-list
```

**Model selection differs between bridges:**

- Claude bridge prompts once for the model (Opus / Sonnet / Haiku), saves it to
  `~/.wechat-claude-bridge/config.json`, and reuses it. `--model <id>` overrides
  and re-saves.
- Codex bridge has no interactive picker — it uses whatever is in
  `~/.codex/config.toml`. `--model <id>` overrides per run only.

`<cmd> --help` / `<cmd> run --help` for every flag.

## Design

Both agents expose a one-shot turn model that fits a message-bus bridge: run
one turn per inbound WeChat message, capture the agent's conversation handle,
resume it on the next turn.

| Bridge | Subprocess | Handle | Persisted in |
|--------|------------|--------|--------------|
| Claude | `claude --print --output-format json [--resume <sid>]` | `session_id` (from JSON `result.session_id`) | `~/.wechat-claude-bridge/sessions.json` |
| Codex  | `codex exec [resume <tid>] --json -o <file>` | `thread_id` (from JSONL `thread.started` event) | `~/.wechat-codex-bridge/sessions.json` |

```
WeChat phone
    │
    ▼ (Tencent iLink — reverse-engineered, vendored in src/weixin_sdk/)
weixin_sdk.AccountClient.poll_once()   ←── long-poll (25s)
    │
    ▼
handle_poll_batch (per-bridge):
    for each inbound text:
      handle = sessions[from_user_id]                     # session_id or thread_id; or None
      reply, handle' = claude --print --resume <handle>   # or: codex exec resume <handle>
      sessions[from_user_id] = handle'                    # persist
      acct.send_text(from_user_id, reply)
    │
    ▼
claude CLI / codex CLI → respective backend → returns handle + final message
```

Key properties:

- **One agent conversation per WeChat user** — coherent turns across messages.
- **Bridge itself is stateless** — restart freely; state is in `sessions.json` + each agent's own session store.
- **Self-healing resume** — if a stored handle is stale (agent session wiped, etc.), the bridge retries once without resume and drops the stale mapping.
- **WeChat context token is forwarded** — preserves server-side WeChat conversation continuity.
- **Both bridges share the WeChat account store** — log in once, run either bridge.

## Flags

Flags common to both bridges (`wechat-claude-bridge` / `wechat-codex-bridge`):

| Flag | Default | Purpose |
|------|---------|---------|
| `--account-id` | *(auto-pick if only one stored)* | weixin-sdk bot account id |
| `--session-file` | `~/.wechat-<agent>-bridge/sessions.json` | Per-user session/thread map |
| `--system-prompt` | (concise WeChat bot prompt) | Prepended to the agent on the first turn |
| `--poll-timeout-s` | `25` | WeChat long-poll timeout |
| `--allowed-users` | *(empty = everyone)* | Comma-separated `from_user_id` allow-list |
| `--log-level` | `INFO` | Python logging level |

`--model` behaves differently per bridge:

| Bridge | Behavior |
|--------|----------|
| Claude | Interactive picker on first run, saved to `~/.wechat-claude-bridge/config.json`; `--model <id>` overrides and re-saves. |
| Codex  | No picker. Defaults to `~/.codex/config.toml`; `--model <id>` overrides per run only. |

## Not implemented

- Media (images, voice, files, video) — `iter_media_items(msg)` is available but both bridges ignore it. Add `--image <path>` / transcription if needed.
- Per-user concurrency cap — if ten WeChat users message simultaneously, ten agent subprocesses run in parallel. Add a per-user `asyncio.Lock` if that bothers you.
- Admin interface — no runtime ops, no dashboard. It's a script.

## Layout

```
wechat-bridge/
├── install.sh                            # one-command bootstrap (pipx)
├── pyproject.toml                        # one package, five entry points
├── CREDITS.md                            # attribution for vendored SDK
├── bridge.py                             # legacy shim → claude bridge CLI
└── src/
    ├── wechat_claude_bridge/
    │   ├── __init__.py
    │   ├── __main__.py                   # python -m wechat_claude_bridge
    │   ├── cli.py                        # argparse, subcommands (login/run)
    │   └── core.py                       # claude_respond, handle_poll_batch
    ├── wechat_codex_bridge/
    │   ├── __init__.py
    │   ├── __main__.py                   # python -m wechat_codex_bridge
    │   ├── cli.py                        # mirrors claude cli.py
    │   └── core.py                       # codex_respond, handle_poll_batch
    └── weixin_sdk/                       # VENDORED — see CREDITS.md, shared by both bridges
        ├── client.py
        ├── login.py
        ├── messages.py
        └── ...
```
