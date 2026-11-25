# harness 的开发文档

给开发者（和开发用的 agent）的：怎么接一个新的 harness、各 harness 的验证记录。
**用户的 agent 不用读这个文件**：它要照着做的都在 `AGENTS.md`、skills 和 `docs/harnesses/<名字>.md` 里。

## 接一个新的 harness

### 要新增、修改的文件

**一定要改的**

- **新增 `docs/harnesses/<名字>.md`**：这个 harness 的全部做法都写在这里，照 `claude-code.md` 的样子：
  - 开头一张表：说明文件（它读哪个文件）、skills（怎么找到）、启动检查（有没有启动钩子，没有就靠 `AGENTS.md` 让 agent 自己跑）、权限（有没有项目规则、拦得住什么）、PATH（`maapilot` 怎么写）、定时（用什么工具、一次性还是重复）、长命令（能不能放后台等它叫醒）。
  - 「定时日常」：工具叫什么、怎么建、怎么列、怎么删，提示怎么写，有什么坑（过期、提前或推迟响、要应用开着）。
  - 「setup 时要多做的」（有才写）：例如信任文件夹、批准联网、注册 agent。
  - 不写实测记录、不标"待实测"：这个文件是给 agent 读的，只写做法；测出来 agent 要知道的结论写成做法或事实（不带日期、版本）。
    没测过、可能走不通的地方，写清走不通时怎么办。
- **改 `docs/harness-dev.md`**（本文件）：「验证记录」加一节，测过什么、哪天、哪个版本、结论，没测的列在"待实测"里。
- **改 `docs/harnesses/README.md`**：开头的对应表加一行。
- **改 `AGENTS.md`**：「你所在的 harness」的对应表加一行。别的地方不写这个 harness 的名字和做法。
- **改 `README.md`**（给用户看的）：
  - 「支持哪些 Agent」的表加一行（对话里用、定时日常支不支持）；
  - 开头的徽章、介绍和"需要"里的 agent 列表；
  - 加一节"用 XX"：怎么在项目文件夹里打开它、第一次要点什么、定时日常有什么前提。

**一般不改的**

- **`.agents/skills/*/SKILL.md`**：skills 只写通用规则。这个 harness 和通用规则对不上时，把通用规则补全（例如"没有一次性定时、或一次性的不可靠的，用每天重复的定时"），不写"某某例外"；它独有的做法写进 `<名字>.md`。
- **日常汇报的格式**：所有 harness 都一样，不为某个 harness 改。

**harness 不读我们的文件时**

- **新增说明文件**（例如 Claude Code 读的 `CLAUDE.md`）：harness 不读 `AGENTS.md`、只读别的文件名时才要。内容只有一行引入 `AGENTS.md`，不加专用内容。
- **新增 skills 链接**（例如 `.claude/skills`）：harness 不读 `.agents/skills/` 时才要，建一个指向 `.agents/skills` 的链接。Windows 上 git 可能没建成链接，在 `maapilot/tools/doctor.py` 里让 `doctor --fix` 补上，像 `skills_link` 那一项。

**harness 往项目里写东西、或要用户改它的配置时**

- **改 `.gitignore`**：harness 在项目文件夹里写本机状态的（像 Codex 的 `.codex/config.toml`），加进去，别提交到仓库。
- **新增 `templates/<名字>/`**：接入要用户改 harness 自己的配置时（例如注册 agent、建定时任务的配置片段），片段和说明放这里，由用户或 agent 照着做；项目不替用户改它的配置。

**harness 有项目级权限规则时**（例如 Claude Code 的 `.claude/settings.json`、Codex 的 `.codex/rules/`）

- **改 `maapilot/policy.py`**：加上生成这个 harness 规则文件的部分，按 `CLAUDE_DEV.md` 的做法重新生成；`tests/test_policy.py` 核对。

**规则要用户先在 harness 里做一步才生效时**（例如 Codex 要先信任这个文件夹）

- **改 `maapilot/hosts.py`**：加上它的名字和常见安装位置，让程序找得到它。
- **改 `maapilot/tools/host_cmd.py`**：让 `host install <名字>` 替用户做这一步（agent 先在对话里问过用户）。
- **跟着改**：`docs/cli.md` 的「host」一节，`tests/test_hosts.py`。

只靠读 `AGENTS.md`、skills 就能用的 harness，这两组都不用动。

定时不写进代码（设计哲学第 1 条）：定时由 harness 自己建，项目只在 `<名字>.md` 里写做法。

### 步骤

1. **核对要求**：对照 `docs/harnesses/README.md`「要 harness 提供什么」。没有定时的只能在对话里用。
2. 按上面「要新增、修改的文件」加文件、改文件。还没测的先列进本文件「验证记录」的"待实测"。
3. **实测**，记录写进本文件「验证记录」，agent 要照着做的结论写进 `<名字>.md`：
   - 定时按时醒来：有没有一次性的、可不可靠，重复的会不会过期；
   - `maapilot` 命令放行、`guard allow` 弹确认（拦不住的写明）；
   - 长命令怎么等结束；读图；
   - 真跑一次完整的定时日常。
4. 全部测试通过（`tests/test_docs_consistency.py` 也会检查 `<名字>.md` 里写的 `maapilot` 命令）。

## 验证记录

记录只对应所列的日期、版本和环境，不表示现在的版本照样通过。日期没写年份的是 2026 年。

### Claude Code

- 10-20（命令行）：会话内定时按时醒来；`maapilot` 命令直接放行、不弹确认；长命令放后台，结束时叫醒。
- 10-22：按 v0.2.0 的流程用一次性定时接力真跑了一次完整的定时日常，正常。
- 11-03 到 11-09（桌面版 Code 标签页，Remote Control 开的会话，v0.3.2）：会话内定时**不可靠**，这一周漏跑 9 次。漏跑时日常根本没启动，用户回来时 `CronList` 是空的。
  电脑插电不睡眠，桌面 App 主进程一直在；但会话自己的进程空闲时会被停掉，用户发消息时才重新拉起（同时出现启动检查）。
  推断：定时只在会话进程的内存里，到点时进程不在就错过（App 没公开说明这一点）。还没到点的定时，进程重启后还在。11-07 那次是电脑进了待机。
  可能的替代办法：桌面 App 自带的定时任务（存在硬盘上，到点新开一个会话跑），还没实测。
- 待实测：电脑睡眠时到点会怎样；桌面版的定时任务能不能跑定时日常；`CLAUDE.md` 改成只有 `@AGENTS.md` 后（v0.2.2），setup 和定时醒来时照样先读本文件、按一次性接力建定时。

### Codex

- 桌面版：会话内定时任务可用（10-20）；长任务可以（10-21）；有一次性的定时任务，按一次性接力、按 v0.2.0 的流程真跑了一次完整的定时日常，正常（10-21）。
- 命令行：没有会话内定时（10-20）。
- 待实测：守住项目规则。

### OpenClaw

- 10-21 至 10-22（OpenClaw 2026.10.5，Linux 容器 + Windows 宿主机 MuMu 12）：`maapilot doctor` 全绿；截图、OCR 和点击可用；重复 cron 到点唤醒并投递；长命令使用当时的 5400 秒超时，未超时。现模板改为 10800 秒，尚未做 3 小时上限实测。
- 10-23：独立任务禁用全部工具后模拟阻塞问题，约 5 秒完成并只投递一次；后台完成通知没有产生额外回复。
- 10-24：同一部署的 04:40 日常正常送达，12:40 日常执行成功但最终投递报 `prepare failed`；任务记录为 `ok (not delivered)`。同轮运行还生成了多段“正在跑”式 commentary。结论：执行成功、内部回复、平台连接都不能代替主动投递验收；定时日常必须在运行期间静默，并只保留一条最终结果路径。
- 已实测一次性 `at` 任务到期未执行，因此本接入使用重复 cron。
- 容器缺少桌面依赖或 `crontab` 时，截图测试和 v0.1 遗留的 `legacy_schedule` 检查可能报错，先排除环境问题再判断 MaaPilot。

**当前部署验收**

分别验证正常最终结果、启动/收尾失败结果、原始会话追问。检查原生执行与投递记录及实际渠道消息；提示词包含要求不等于实际行为正确。Gateway/模型完全无法启动时另核对宿主失败通知，不能靠 agent 提示词兜底。

### pi

- 10-27（pi 1.2.0 + pi-schedule-prompt 0.4.1，在空目录里测扩展，没有跑 MaaPilot）：
  - 一次性定时准时响；到点时 agent 正在前台跑命令，提示排队，命令跑完、回完话再送进来，没有丢。
  - 接力：定时醒来的那一轮里建下一个一次性定时，下一个照样准时响。
  - 关掉 pi 后 `pi -c` 回到原会话，没到点的定时照样响；开新会话不响（定时还留在 `.pi/schedule-prompts.json` 里，绑着旧会话）。
- 同一轮比较过的另两个扩展：@pi-agents/loop（agent 刚忙完时到点，提示发送失败，一次性定时就丢了）不用；@mjakl/pi-cron 也能用，但没发到 npm、用的人少。
- 10-28（pi 1.4.0 + pi-schedule-prompt，模型 DeepSeek flash，在 MaaPilot 项目里）：
  - setup 完整走通（信任、skills、读截图、问定时、装扩展、建一次性定时并核对）；前台跑完一次约 36 分钟的 `daily run`。
  - 16:40 的定时日常准时响：先建好下一次（次日 04:40）再 `daily run --trigger schedule`，跑完对账、`daily done`、在会话里汇报，中途没问问题。
  - 偏差（已补进上面和 skill `maapilot-daily`）：给 `daily run` 填了 `timeout: 7200`；刷理智交回后自己 `maa fight` 补刷，没用 `daily run --only farm`，汇报里刷理智一直算失败。
  - 次日 04:40 那一次（16:40 醒来时建的）也准时响了，接力在项目里连着跑通（用户 11-04 确认）。
- 待实测：setup 时由 agent 自己跑 `pi install npm:pi-schedule-prompt`（11-21 用户定：和装运行环境一样，问过用户后 agent 自己装；之前是请用户在终端里跑）。

### DSH

- 11-09（DSH 0.2.2-rc.1，Windows PowerShell 5.1，没装 pwsh 7）：在项目文件夹里跑一次 headless，会话记录里 `AGENTS.md` 全文注入（`CLAUDE.md` 也注入了），10 个 maapilot skills 都加载了，默认模型 `deepseek-flash`。
  `dsh web` 卡在"预览版说明"弹窗（点继续报保存不了确认状态，重启也一样）；命令行版不共用桌面版的 DeepSeek 账号登录。
- 待实测（桌面版）：
  - 桌面版的真实对话里 `/maapilot-setup` 能不能直接用；
  - PowerShell 里 `.venv\Scripts\maapilot.exe` 能直接跑、中文输出不乱码；
  - 装运行环境、`adb` 连模拟器时哪些命令要越界、会不会被沙箱挡住；
  - 读截图；`daily run` 放后台、结束时叫醒；
  - 每天重复的定时按时发回这个会话；DSH 退出再打开后的补发；
  - 真跑一次完整的定时日常。

### Antigravity

- 2026-11-16（Windows 11 + 安卓模拟器实机验证，当前仍为实验支持）：
  - 命令执行与看图：通过 `.venv\Scripts\maapilot.exe` 跑通 `doctor`（`ok_all: true`）、`screen shot` + `read_file` 看图识界面、`maa startup`、`state depot`、`state box`、`copilot find` / `show` / `strip`、`news list` / `told`。
  - 后台长命令与手动完整流程：`run_command`（`WaitMsBeforeAsync: 10000`）运行完整手动 `daily run`（含 `startup`、`recruit`、`infrast`、`mall`、`farm` 连战、`award`）及 `maa copilot`（自动编队并三星通关活动关）后，均自动唤醒会话完成 `guard check` 与 `daily done` 收尾。
- 2026-11-20（Windows 11 + 安卓模拟器实机验证）：
  - 工作区自动发现与启动检查：将仓库根目录设为 Active Workspace 开启新会话后，自动加载根目录 `AGENTS.md` 与 `.agents/skills/` 下全部 10 个 `maapilot-*` 技能；跑通 `doctor --brief`、完整 `doctor`（全 16 项检查 `ok_all: true`）与 `update check`。
  - 守护定时自动唤醒与完整定时日常：通过 `schedule`（`CronExpression`、`IsDaemon: true`、`ExactTiming: true`）注册定时任务并经 `manage_task`（`list`）核对后结束本轮进入空闲状态；到点准时自动唤醒会话，后台执行完整 `daily run --trigger schedule`（含 `startup`、`recruit`、`infrast`、`mall`、`annihilation`、`box`、`farm`、`award`）并在完成后自动唤醒，随后经 `input home` + `read_file` 看首页截图、`guard check` 对账与 `daily done` 收尾，最后用 `manage_task`（`kill` / `list`）删除测试定时并确认清零。
  - 重启限制确认：确认 Antigravity 服务端重启后会停止已有的后台定时任务，不会自动恢复或补发。
- 待实测：
  - 全新未初始化环境下的 `/maapilot-setup` 首句初始化流程；
  - 多日连续运行的稳定性，以及电脑睡眠唤醒后守护定时的表现；
  - macOS 平台的实机验证。

### ZCode

- 11-22 至 11-23（macOS Apple Silicon，ZCode 桌面版，AVD 模拟器，实验支持）：
  - 命令与看图：`.venv/bin/maapilot` 直跑（会话 PATH 没代管时整个会话写全路径）；`screen shot` 读图；读 `.agents/skills/` 里的 skill、根目录 `AGENTS.md` 注入。用户开全自动（不弹确认）权限模式：`guard allow` 不弹窗，程序侧对账兜底；`private/skland.json` 拦不住，靠 agent 自觉。
  - 长命令：后台 Bash 结束时叫醒；前台最长 600 秒（实测值，即文档里的 10 分钟），超时自动转后台、结束时叫醒，不会被杀掉。
  - 定时：一次性（`recurring: false`）准点触发（偏差约 4 秒）；每天重复的多时点 cron（`40 4,16 * * *`）实测 04:40 准点（+48 秒）。工具层禁止定时任务再建定时（接力做不了），定时触发的那一轮里 `CronCreate` / `CronDelete` / `CronUpdate` 被摘掉（只剩 `CronPauseSelf`），交互轮才回来 → 用每天重复的定时。一个会话同时只能绑一条自动化（占着时再建报错，删掉占用的才能建）。
  - 应用更新重启：定时自动化原样保留，重启后照常准点触发，落回原会话、上下文还在；会话里跑着的后台任务跟着应用重启一起结束。
  - 完整链路：从 0 装环境（venv、maa-cli、MaaCore、OCR、数据表、AVD、游戏登录、仓库/干员识别）→ 手动验收日常全项 + 目标（代理作战推活动关）+ 领奖励 + 对账 + `daily done` 收尾 → 升 0.4.0（补丁保留、`doctor` 全绿）→ 04:40 定时日常准点触发；开场时模拟器已经崩了（模拟器日志里 crashpad 记到一次，那个实例才启动 34 分钟），重启模拟器后 `daily run --only <rest>` 续跑成功并收尾。清锁、重启模拟器是本机的处理，必要性没验证过，不写进 `zcode.md`。
  - 模拟器连没连上用完整的 `maapilot doctor` 查（实测 `emulator` 项会连模拟器；`--brief` 不做逐项检查）。
- 待实测：电脑睡眠时到点会怎样；应用重启后触发会不会落到新会话、落到新会话时无人值守的全流程（本次 04:40 是同会话触发）。
