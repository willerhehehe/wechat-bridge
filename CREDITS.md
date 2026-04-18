# Credits

The `src/weixin_sdk/` package is vendored from the research repository at
`willer/wechat-channel` (Python extract of the reverse-engineered OpenClaw
WeChat iLink protocol). It was copied in verbatim so this repo is
self-contained and installable from a fresh clone.

Upstream:
- `src/weixin_sdk/` ← `wechat-channel/src/weixin_sdk/`
- Protocol notes: `wechat-channel/docs/openclaw-weixin-protocol.md`
- Research snapshots (not vendored): `wechat-channel/research/`

If the upstream SDK grows incompatible changes, the vendor step is a
straightforward copy — no patches were applied here.
