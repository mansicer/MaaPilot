# ZCode

在 ZCode 里用 MaaPilot 的做法。通用的规矩在 `AGENTS.md` 和 skills 里，这里只写 ZCode 自己的。

| 项 | 做法 |
|---|---|
| 说明文件 | 直接读项目根的 `AGENTS.md`，不用别的引入文件 |
| skills | 直接读 `.agents/skills/`，不用建链接 |
| 启动检查 | 没有启动钩子：会话开头没有"MaaPilot 启动检查"那段时，按 `AGENTS.md` 自己跑一次 `maapilot doctor --brief` |
| 权限 | 没有项目级权限规则文件，弹不弹确认跟用户在 ZCode 里的权限设置走。用户开全自动（不弹确认）模式时 `guard allow` 也不弹窗，`private/skland.json` 也拦不住：都靠你自己守 `AGENTS.md` 的硬规则；程序自己的对账照样在 |
| PATH | 没有钩子代管：`maapilot` 提示找不到命令时，整个会话都写 `.venv/bin/maapilot <命令>`（Windows 上是 `.venv\Scripts\maapilot.exe <命令>`） |
| 定时 | 定时自动化 `CronCreate` / `CronList` / `CronUpdate` / `CronDelete`，**每天重复的定时**（见下，不用一次性接力） |
| 长命令 | Bash 的后台运行（`run_in_background`），结束时叫醒你；前台最长 10 分钟，超时了自动转后台接着跑，不会被杀掉 |

## setup 时要多做的

- 跟用户说清：ZCode 里弹不弹确认，跟用户给这个工作区设的权限模式走；开着全自动（不弹确认）模式时，`guard allow` 也不弹窗，合成玉、源石只剩程序对账兜底。
- 跟用户说清：到点时 ZCode 要开着、这个会话不要关；应用重启（包括更新）会结束会话里跑着的后台任务（见「长命令」）。

## 定时日常

用 ZCode 的定时自动化（没加载就先用工具搜索找出来，有时要重新搜一遍才回来）。**用每天重复的定时，不用一次性接力**：

- ZCode 的定时是**工作区持久的自动化**：跨应用重启存活，到点自动触发，删掉才消失。
- 接力做不了：定时契约禁止"定时任务再建下一个"，而且定时触发的那一轮里 `CronCreate` / `CronDelete` / `CronUpdate` 会被摘掉（只剩 `CronPauseSelf`）。一次性（`recurring: false`）到点就自动结束，不适合定时日常。
- **一个会话同时只能绑一条自动化**：会话已被占着时再 `CronCreate` 会报错，把占着的那条 `CronDelete` 掉才能建。同一时间只在一个地方定（`AGENTS.md` 通用规矩）。
- 把 `config.yaml` `schedule` 的所有时间点写进**一条**多时点 cron，例如 04:40、16:40 就是 `40 4,16 * * *`。分钟别选 :00、:30。建完 `CronList` 核对各时间点都在；之后每次醒来确认还在、不再建。
- **提示要自包含**：到点时前面的对话可能已经被压缩掉了，单看提示就要能干活。写法同 `docs/harnesses/README.md`「定时日常怎么定」，例如 `定时日常（04:40）：按 skill maapilot-daily 做；这是每天重复的定时，不用再建`（括号里写这一次的时间）。
- 要查模拟器连没连上，用完整的 `maapilot doctor`（不带 `--brief`）：`--brief` 不做逐项检查，模拟器连不上时它照样 `ok: true`。

## 长命令

几分钟以上的（`daily run`、`maa fight`、`state box`）用 Bash 后台运行（`run_in_background`），结束时 ZCode 会叫醒你；前台命令超过 10 分钟不用管，自动转后台。
**应用重启（包括更新）会结束会话的后台任务**，在跑的 `daily run` 也算：会留下一次没收尾的日常，按 skill `maapilot-daily`「接着收尾」做（启动检查里有 `daily_open`，或下一次 `daily run` 被拒）。

## 其他

- 用户偏好写进 `private/memory/`（`AGENTS.md`「用户偏好」），**不要**写进 ZCode 的自动记忆：自动记忆只在 ZCode 里有，换 agent 就丢。
