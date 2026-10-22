# 各 harness 怎么接 MaaPilot

MaaPilot 只做和游戏相关的部分（说明文档、skills、`maapilot` 工具）；定时、叫醒会话、通知用户由 agent 所在的 harness 提供（`docs/design.md` 第 1 条设计哲学）。
这份文档写每个 harness 的说明文件、skills、权限、定时怎么建、实测情况。没实测过的标"待实测"。

## 要 harness 提供什么

| 要求 | 用来做什么 |
|---|---|
| 读图 | 看截图判断界面 |
| 跑命令、读写文件，命令能跑很久（放后台等结束时叫醒，或前台够长） | 调 `maapilot`；`daily run` 要 20–60 分钟 |
| 读项目里的说明和 skills | `AGENTS.md`、`.agents/skills/` |
| **会话里能按时醒来**（会话内定时任务） | 定时日常：到点在用户的会话里醒来，跑日常、当场汇报 |
| （可选）项目级权限规则 | 护栏：`guard allow` 每次弹确认、不读森空岛凭据 |

结果就在会话里当场说，不需要另外的通知功能。会话一直开着是前提：会话关了、定时过期了，由用户和 harness 处理。

## 定时日常怎么定（各 harness 通用）

- **一次性定时接力**（harness 有一次性定时的都这样）：会话里始终只留一个"下一次日常"的一次性定时，时间是 `config.yaml` 的 `schedule` 里离现在最近的下一个时间点。
  setup 时建第一个；每次定时日常醒来、跑 `daily run` 之前先建下一个，这样这次出错、没收尾，下一次也照样会来。改时间：删掉还没到的那个，建新的。
  建之前先列一遍已有的定时，别建重复的。
- 没有一次性定时、只有重复的 harness：每个时间点建一个每天重复的定时；有过期的，在下面那一节写清多久要续。
- **提示要自足**：到点时前面的对话可能已经被压缩掉了，单看这一句就要知道做什么。推荐：
  `定时日常（04:40）：按 skill maapilot-daily 做；先定好下一次`（括号里写这一次的时间）。
- 分钟别选 :00、:30（见下面 Claude Code 一节）；04:40、16:40 这样的就好。
- 同一时间只在一个会话里定，同一个账号只在一台电脑上定。

## Claude Code（命令行、桌面版）

| 项 | 做法 |
|---|---|
| 说明文件 | `CLAUDE.md`：第一行 `@AGENTS.md` 引入总说明，再加 Claude Code 专用的几条 |
| skills | `.claude/skills` 是指向 `.agents/skills` 的链接（Windows 上没建成时 `maapilot doctor --fix` 补上） |
| 启动检查 | `.claude/settings.json` 的 SessionStart 钩子跑 `bin/session-start`：把 `.venv/bin` 加进会话的 PATH，再跑 `maapilot doctor --hook` |
| 权限 | `.claude/settings.json` 的 `permissions`，由 `maapilot/policy.py` 生成（`maapilot dev rules`），不要手改：`maapilot` 整个放行，`guard allow` 询问，`private/skland.json` 拒绝读写 |
| 定时 | 会话内定时工具 `CronCreate` / `CronList` / `CronDelete`：一次性（`recurring: false`，cron 写成那一天那一分钟，例如 `40 4 21 10 *`）接力 |
| 长命令 | Bash 的后台运行（`run_in_background`），结束时叫醒 agent；前台最长 10 分钟 |

为什么用一次性的（见 https://code.claude.com/docs/en/scheduled-tasks ）：
- 重复的定时建好 **7 天后过期**（再响最后一次，然后自己删掉），不续就断了；按天的重复定时还会**晚 0–30 分钟**才响。
- 一次性的不过期，也不推迟；只是定在 :00、:30 的会**提前最多 90 秒**响，所以分钟别选这两个。

实测（10-20，macOS 命令行）：会话内定时按时醒来；`maapilot` 命令直接放行、不弹确认；长命令放后台，结束时叫醒。
10-22 按 v0.2.0 的流程用一次性定时接力真跑了一次完整的定时日常，正常。
待实测：桌面版的会话内定时；电脑睡眠时到点会怎样。

## Codex 桌面版

| 项 | 做法 |
|---|---|
| 说明文件 | `AGENTS.md`（Codex 直接读） |
| skills | `.agents/skills/`（Codex 直接读） |
| 启动检查 | 没有启动钩子：`AGENTS.md` 让 agent 开头自己跑一次 `maapilot doctor --brief` |
| 权限 | `.codex/rules/maapilot.rules`，同样由 `maapilot/policy.py` 生成。Codex 只在**信任这个文件夹**后才加载项目规则：setup 时征得同意后 `maapilot host install codex`（写进 Codex 的用户设置），`maapilot host status` 看 `trusted` |
| PATH | 没有钩子放 `.venv/bin`：`maapilot` 找不到时整个会话写 `.venv/bin/maapilot`（Windows 是 `.venv\Scripts\maapilot.exe`），规则里都列了 |
| 定时 | 会话内的定时任务，有一次性的，照上面接力；结果回到同一个会话；应用要开着 |
| 长命令 | 能跑长任务（10-21 用户实测） |

实测（用户在 Windows 上）：会话内定时任务可用（10-20）；长任务可以（10-21）；有一次性的定时任务，按上面的接力做法、按 v0.2.0 的流程真跑了一次完整的定时日常，正常（10-21）。
待实测：守住项目规则。

## Codex 命令行

没有会话内定时（10-20 确认），**不支持定时日常**，只能在对话里用（"跑一次日常"、练干员……）。
说明文件、skills、权限、PATH 同 Codex 桌面版。想要定时日常，改用 Codex 桌面版或 Claude Code。

## 从 v0.1 升级

v0.1 用系统的定时任务启动日常、无头叫起 agent、把结果推进会话；v0.2.0 起改成上面的会话内定时。升级的用户：
1. 删掉旧的系统定时（crontab 里本项目的那一行、Windows "任务计划程序" `MaaPilot` 文件夹里本项目的任务）：启动检查和 `doctor` 会报 `legacy_schedule`，
   agent 先 `maapilot daily unschedule --dry-run` 给用户看，用户同意后 `maapilot daily unschedule`。
2. 按 skill `maapilot-setup`「定时日常」在会话里定好第一个定时。
3. 升级前就开着的会话：让 agent 重新读一遍 `AGENTS.md`，停掉它还在后台挂着的 `daily watch`（这个命令已经没有了）；最好直接重开一个会话。
4. `config.yaml` 里的 `agent`、`schedule_options` 用不到了，可以删（`doctor` 的 `agent_config` 会提示）。
5. 在 Windows 上靠 Codex 命令行无头跑定时日常的：改用 Codex 桌面版。

## 接一个新的 harness

1. **核对要求**：读图、跑长命令、读 `AGENTS.md` 和 skills、会话内定时（对照最上面的表）。没有会话内定时的只能在对话里用。
2. **说明文件**：harness 读 `AGENTS.md` 就不用另写；读别的文件名的（例如 `CODEBUDDY.md`），写一个引入 `AGENTS.md` 的，只加这个 harness 专用的几条（定时工具叫什么、长命令怎么等）。
3. **skills**：harness 不读 `.agents/skills/` 的，建一个指向它的链接（像 `.claude/skills`）。
4. **权限**（可选）：在 `maapilot/policy.py` 加生成这个 harness 规则文件的部分，`maapilot dev rules` 生成，`tests/test_policy.py` 核对；规则要信任文件夹才加载的，在 `host install` 里加上。
5. **实测**，结果写进本文件这个 harness 的一节：会话内定时按时醒来（一次性的有没有、重复的会不会过期、准不准）；`maapilot` 命令放行、`guard allow` 弹确认；
   长命令怎么等结束；读图；真跑一次完整的定时日常。
6. README「支持哪些 Agent」的表里加一行。
