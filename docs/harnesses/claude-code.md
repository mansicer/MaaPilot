# Claude Code（命令行、桌面版）

在 Claude Code 里用 MaaPilot 的做法。通用的规矩在 `AGENTS.md` 和 skills 里，这里只写 Claude Code 自己的。

| 项 | 做法 |
|---|---|
| 说明文件 | `CLAUDE.md`：只有一行 `@AGENTS.md`，内容和 `AGENTS.md` 一样 |
| skills | `.claude/skills` 是指向 `.agents/skills` 的链接（Windows 上没建成时 `maapilot doctor --fix` 补上） |
| 启动检查 | `.claude/settings.json` 的 SessionStart 钩子跑 `bin/session-start`：把 `.venv/bin` 加进会话的 PATH，再跑启动检查（内容同 `maapilot doctor --brief`）；结果在会话开头，没设置过时还会在屏幕上提示用户"随便说一句就开始" |
| 权限 | `.claude/settings.json` 的 `permissions`，由程序生成，不要手改：`maapilot` 整个放行，`guard allow` 询问，`private/skland.json` 拒绝读写 |
| PATH | 启动钩子放好了，直接写 `maapilot <命令>` |
| 定时 | 会话内定时工具 `CronCreate` / `CronList` / `CronDelete`，一次性接力（见下） |
| 长命令 | Bash 的后台运行，结束时叫醒你；前台最长 10 分钟 |

## 定时日常

用 `CronCreate` / `CronList` / `CronDelete`（按 cron 表达式定时发提示；没加载就先用工具搜索找出来），**用一次性的定时接力，不用每天重复的**：

- 会话里始终只留一个"下一次日常"的一次性定时：`CronCreate` 的 `recurring: false`，cron 写成下一个时间点的那一天那一分钟，例如 `40 4 21 10 *`。
- 提示写成：`定时日常（04:40）：按 skill maapilot-daily 做；先定好下一次`（时间写这一次的）。
- setup 时建第一个；每次定时日常醒来、跑 `daily run` 之前先建下一个（`schedule` 里离现在最近的下一个时间点）。
- 建之前先 `CronList`，已经有下一次日常的就不再建（只留一个）；建完再列一遍，核对时间对上了再告诉用户。
- 改时间：`CronDelete` 掉还没到的那个，建新的。
- 分钟别选 :00 和 :30：Claude Code 会把这两个时刻的一次性定时提前最多 90 秒。用户选了整点、半点，跟用户说一声、建议挪几分钟。
- 定时只在这个会话里，会话关了就没了。

为什么用一次性的（见 https://code.claude.com/docs/en/scheduled-tasks ）：
- 重复的定时建好 **7 天后过期**（再响最后一次，然后自己删掉），不续就断了；按天的重复定时还会**晚 0–30 分钟**才响。
- 一次性的不过期，也不推迟。

## 长命令

几分钟以上的（`daily run`、`maa fight`、`state box`、掉线时等用户回复的 `sleep 300`）用 Bash 的后台运行（`run_in_background`），结束时 Claude Code 会叫醒你；
skill 里说前台跑的，Bash 超时设 600000 毫秒（10 分钟）。

## 其他

- 用户偏好写进 `private/memory/`（AGENTS.md「用户偏好」），**不要**写进 Claude Code 的自动记忆：自动记忆只在 Claude Code 里有，换 agent 就丢。
  以前写在自动记忆里的，第一次启动检查时已经复制到 `private/memory/` 了；两边不一样时以 `private/memory/` 为准。
- 权限设置拒绝的（森空岛凭据）、没批准的确认被拒就是不让做，不要换个写法再试。

## 实测

- 10-20（macOS 命令行）：会话内定时按时醒来；`maapilot` 命令直接放行、不弹确认；长命令放后台，结束时叫醒。
- 10-22：按 v0.2.0 的流程用一次性定时接力真跑了一次完整的定时日常，正常。
- 待实测：桌面版的会话内定时；电脑睡眠时到点会怎样；`CLAUDE.md` 改成只有 `@AGENTS.md` 后（v0.2.2），setup 和定时醒来时照样先读本文件、按一次性接力建定时。
