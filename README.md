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

**Model selection (both bridges prompt on first run and persist the choice):**

- Claude: picker lists Opus / Sonnet / Haiku → saved to `~/.wechat-claude-bridge/config.json`.
- Codex: picker lists the current user-facing codex slugs (`gpt-5.4`, `gpt-5.3-codex`, `gpt-5.2-codex`, `gpt-5.2`, `gpt-5.1-codex-max`, `gpt-5.1-codex-mini`, plus "use codex default") → saved to `~/.wechat-codex-bridge/config.json`. Pick "use codex default" to fall back to `~/.codex/config.toml`.

`--model <id>` overrides and re-saves.

**Codex sandbox — default is `read-only`.** Because WeChat users can inject arbitrary text into codex prompts, the codex bridge defaults to `--sandbox read-only` + `--approval-policy never`: codex can reason about files under its workdir but cannot write or execute commands. Override with `--sandbox workspace-write` / `danger-full-access` if you understand the risk.

`<cmd> --help` / `<cmd> run --help` for every flag.

## Design

Both agents expose a one-shot turn model that fits a message-bus bridge: run
one turn per inbound WeChat message, capture the agent's conversation handle,
resume it on the next turn.

| Bridge | Backend | Handle | Persisted in |
|--------|---------|--------|--------------|
| Claude | `claude --print --output-format json [--resume <sid>]` — one subprocess per message | `session_id` (from JSON `result.session_id`) | `~/.wechat-claude-bridge/sessions.json` |
| Codex  | `codex app-server --listen stdio://` — **one long-running process**, JSON-RPC 2.0 | `thread_id` (from `thread/start` response, one per WeChat user) | `~/.wechat-codex-bridge/sessions.json` |

The codex side uses `app-server` instead of `codex exec` so we only pay the
codex cold-start cost once. Steady-state turn latency drops from ~8-10s to
~1-3s. On bridge restart we `thread/resume` stored thread ids; if codex
doesn't recognize one (wiped rollouts, etc.), we fall back to `thread/start`.

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
      # Claude: fork a new `claude --print` subprocess per message
      # Codex:  reuse the long-running app-server, call thread/resume + turn/start
      reply, handle' = <agent>.respond(text, handle)
      sessions[from_user_id] = handle'                    # persist
      acct.send_text(from_user_id, reply)
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

Both bridges share the same `--model` ergonomics: interactive picker on first run, saved to `~/.wechat-<agent>-bridge/config.json`, `--model <id>` overrides and re-saves. Pass `--model ""` on the codex side to fall back to `~/.codex/config.toml`.

**Codex-only flags:**

| Flag | Default | Purpose |
|------|---------|---------|
| `--sandbox` | `read-only` | codex sandbox (`read-only` / `workspace-write` / `danger-full-access`) |
| `--approval-policy` | `never` | codex approval policy (`never` / `unlessTrusted` / `always`) |
| `--workdir` | `~/.wechat-codex-bridge/workdir` | cwd codex threads run inside |

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
    │   ├── cli.py                        # argparse, subcommands (login/run)
    │   ├── appserver.py                  # JSON-RPC client for `codex app-server`
    │   └── core.py                       # codex_respond, handle_poll_batch
    └── weixin_sdk/                       # VENDORED — see CREDITS.md, shared by both bridges
        ├── client.py
        ├── login.py
        ├── messages.py
        └── ...
```
