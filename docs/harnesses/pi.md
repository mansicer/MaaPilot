# pi

在 [pi](https://pi.dev)（终端里的 coding agent）里用 MaaPilot 的做法。通用的规矩在 `AGENTS.md` 和 skills 里，这里只写 pi 自己的。
pi 本身没有定时，定时日常靠扩展 [pi-schedule-prompt](https://github.com/tintinweb/pi-schedule-prompt)（见「setup 时要多做的」）。

| 项 | 做法 |
|---|---|
| 说明文件 | `AGENTS.md`（pi 直接读；同一个目录里有 `AGENTS.md` 就不读 `CLAUDE.md`） |
| skills | `.agents/skills/`（pi 直接读），但**要用户先信任这个项目**：不信任时 skills 不加载，`AGENTS.md` 照样读。没加载时直接读 `.agents/skills/<名字>/SKILL.md` |
| 启动检查 | 没有启动钩子：`AGENTS.md` 让你开头自己跑一次 `maapilot doctor --brief` |
| 权限 | 没有：pi 跑命令、改文件都不问用户。`guard allow` 不会弹确认，`private/skland.json` 也拦不住，靠你守 `AGENTS.md`；程序自己的对账照样在 |
| PATH | 没有钩子放 `.venv/bin`：`maapilot` 提示找不到时整个会话写 `.venv/bin/maapilot <命令>`。Windows 上 pi 用 Git Bash 跑命令，写 `.venv/Scripts/maapilot.exe <命令>`（斜杠，不用反斜杠） |
| 定时 | 扩展 pi-schedule-prompt 的 `schedule_prompt` 工具，一次性接力（见下）；定时绑在建它的会话上，pi 要开着 |
| 长命令 | 后台命令结束时叫不醒你：前台跑、等它结束。pi 的 bash 默认不限时，**不要给 `daily run` 这类长命令设 `timeout`**；跑的时候用户说的话会排队，跑完再处理 |
| 读图 | pi 能读截图，但模型要能看图：setup 时先截一张图看看，看不了就请用户在 pi 里换一个能看图的模型 |

## setup 时要多做的

- **信任这个项目**：项目里有 `.agents/skills`，pi 第一次在这里启动时会问要不要信任，要选信任（以后不再问；之前选了不信任的，在 pi 里输入 `/trust` 改）。
  看 skills 有没有加载：pi 的系统提示里有没有 `maapilot-setup` 这些 skill。没有就请用户信任后重启 pi。
- **装定时扩展**（要定时日常才需要）：先看有没有 `schedule_prompt` 工具。没有就跟用户说清：定时要装一个第三方扩展 pi-schedule-prompt，会在 pi 里跑它的代码；
  用户同意后请用户在终端跑 `pi install npm:pi-schedule-prompt`（装到用户自己的 pi 设置里），然后重启 pi、用 `pi -c` 回到这个会话，再定定时日常。
  用户不想装：定时日常做不了，只能在对话里说"跑一次日常"，setup 照样做完。
- 跟用户说清：到点时 pi 要开着、开在这个项目文件夹里；关了以后要用 `pi -c`（或 `/resume`）回到**同一个会话**，定时才接着有效，开一个新会话就得重新定（见下）。
- Windows：要装 Git for Windows（pi 用它的 Git Bash 跑命令）。

## 定时日常

用 `schedule_prompt` 工具，**用一次性的定时接力**（`docs/harnesses/README.md`「定时日常怎么定」）：

- 建：`action: "add"`、`type: "once"`、`name` 写 `MaaPilot 日常 04:40`，`schedule` 写带时区的完整时间，例如 `2026-10-28T04:40:00+08:00`（先用 `date` 看现在的时间和时区）；
  `prompt` 写 `定时日常（04:40）：按 skill maapilot-daily 做；先定好下一次`（时间写这一次的）。
- 列：`action: "list"`，只列出这个会话的定时；删：`action: "remove"`，`jobId` 写要删的那个。
- setup 时建第一个；每次定时日常醒来、跑 `daily run` 之前先建下一个。建之前先列一遍，已经有下一次日常的就不再建；建完再列一遍，核对时间对上了再告诉用户。
- 改时间：删掉还没到的那个，建新的。
- 不要用 `type: "cron"`、`"interval"`，也不要设 `model`（那样会在另一个会话里跑，结果不回到这个会话）。这个扩展的 cron 是 6 段、带秒的，和别处不一样。
- 到点时 pi 没开着（或开的是别的会话），这一次就错过了，不补跑；接力也就断了，用户回来后在会话里重新定。

**定时绑在建它的会话上**，记在项目的 `.pi/schedule-prompts.json`（不入库）：

- 用户用 `pi -c` 回到原来的会话：定时还在，照常响。
- 用户开了新会话、要在这里跑定时日常：按 skill `maapilot-setup`「定时日常」在新会话里重新定。`list` 看不到旧会话的定时，
  先读一遍 `.pi/schedule-prompts.json`，把别的会话里还没到的 MaaPilot 日常用 `remove`（写它的 `jobId`）删掉，免得用户哪天回到旧会话时两边一起响。

## 其他

- 用户偏好写进 `private/memory/`（`AGENTS.md`「用户偏好」），pi 没有自己的记忆功能，不用另外处理。
- 会话太长时 pi 会自动压缩前面的对话：定时醒来记不清做法了，就按 `AGENTS.md` 再读一遍本文件。

## 实测

- 10-27（macOS，pi 1.2.0 + pi-schedule-prompt 0.4.1，在空目录里测扩展，没有跑 MaaPilot）：
  - 一次性定时准时响；到点时 agent 正在前台跑命令，提示排队，命令跑完、回完话再送进来，没有丢。
  - 接力：定时醒来的那一轮里建下一个一次性定时，下一个照样准时响。
  - 关掉 pi 后 `pi -c` 回到原会话，没到点的定时照样响；开新会话不响（定时还留在 `.pi/schedule-prompts.json` 里，绑着旧会话）。
- 同一轮比较过的另两个扩展：@pi-agents/loop（agent 刚忙完时到点，提示发送失败，一次性定时就丢了）不用；@mjakl/pi-cron 也能用，但没发到 npm、用的人少。
- 待实测：在 MaaPilot 项目里完整走一遍 setup（信任、skills、读截图）；前台跑完一次 `daily run`；真跑一次完整的定时日常；Windows（Git Bash）。
