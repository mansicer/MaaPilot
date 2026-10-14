@AGENTS.md

## Claude Code 专用

- 启动钩子（`.claude/settings.json` 的 SessionStart）每次开会话都跑一次启动检查，结果在会话开头；没设置过时还会在屏幕上提示用户"随便说一句就开始"。
- **盯着日常**：用 Bash 的后台运行（`run_in_background`，超时设 7200000 毫秒）挂 `maapilot daily watch`。它一结束 Claude Code 就会通知你，按 AGENTS.md「盯着日常」处理。
- 长命令前台跑时 Bash 超时设 600000 毫秒（10 分钟）。
- 用户偏好写进 `private/memory/`（AGENTS.md「用户偏好」），**不要**写进 Claude Code 的自动记忆：自动记忆只在 Claude Code 里有，换 agent 就丢。
  以前写在自动记忆里的，第一次启动检查时已经复制到 `private/memory/` 了；两边不一样时以 `private/memory/` 为准。
- 权限设置里禁止的命令（直接跑 `maa`、`adb shell input`等）被拒就是不让做，不要换个写法再试。
