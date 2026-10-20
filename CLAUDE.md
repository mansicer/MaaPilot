@AGENTS.md

## Claude Code 专用

- 启动钩子（`.claude/settings.json` 的 SessionStart）每次开会话都跑一次启动检查，结果在会话开头；没设置过时还会在屏幕上提示用户"随便说一句就开始"。
- **定时日常**：用 Claude Code 的会话内定时工具（`CronCreate` / `CronList` / `CronDelete`，按 cron 表达式定时发提示；没加载就先用工具搜索找出来），**用一次性的定时接力，不用每天重复的**：
  会话里始终只留一个"下一次日常"的一次性定时（`CronCreate` 的 `recurring: false`，cron 写成下一个时间点的那一天那一分钟，例如 `40 4 21 10 *`），
  提示写成自己就能看懂的一句（到点时前面的对话可能已经压缩掉了）："定时日常（04:40）：按 skill maapilot-daily 做；先定好下一次"（时间写这一次的）。setup 时建第一个；每次定时日常醒来、跑 `daily run` 之前先建下一个（`schedule` 里离现在最近的下一个时间点）；
  改时间就 `CronDelete` 掉还没到的那个、建新的。建之前先 `CronList`，已经有下一次日常的就不再建（只留一个）；建完再列一遍，核对时间对上了再告诉用户。
  - 为什么不用重复的：Claude Code 的重复定时建好 7 天后就过期（再响一次后自己删掉），按天的重复定时还会晚 0–30 分钟才响；一次性的不过期、不推迟。
  - 分钟别选 :00 和 :30（Claude Code 会把这两个时刻的一次性定时提前最多 90 秒）；04:40、16:40 这样的就好。用户选了整点、半点，跟用户说一声、建议挪几分钟。
  - 定时只在这个会话里，会话关了就没了（见 `docs/harnesses.md`）。
- 长命令（几分钟以上的 `daily run`、`maa fight`、`state box`、掉线时等用户回复的 `sleep 300`）用 Bash 的后台运行（`run_in_background`），结束时 Claude Code 会叫醒你；
  要前台跑时（skill 里说前台的）Bash 超时设 600000 毫秒（10 分钟）。
- 用户偏好写进 `private/memory/`（AGENTS.md「用户偏好」），**不要**写进 Claude Code 的自动记忆：自动记忆只在 Claude Code 里有，换 agent 就丢。
  以前写在自动记忆里的，第一次启动检查时已经复制到 `private/memory/` 了；两边不一样时以 `private/memory/` 为准。
- 权限设置拒绝的（森空岛凭据）、没批准的确认被拒就是不让做，不要换个写法再试。
