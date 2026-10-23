# 各 harness 怎么接 MaaPilot

MaaPilot 只做和游戏相关的部分（说明文档、skills、`maapilot` 工具）；定时、叫醒会话、通知用户由 agent 所在的 harness 提供（`docs/design.md` 第 1 条设计哲学）。
这份文档写每个 harness 的说明文件、skills、权限、定时怎么建、实测情况。没实测过的标"待实测"。

## 要 harness 提供什么

| 要求 | 用来做什么 |
|---|---|
| 读图 | 看截图判断界面 |
| 跑命令、读写文件，命令能跑很久（放后台等结束时叫醒，或前台够长） | 调 `maapilot`；`daily run` 要 20–60 分钟 |
| 读项目里的说明和 skills | `AGENTS.md`、`.agents/skills/` |
| **会话里能按时醒来**（Claude Code / Codex 用会话内定时；OpenClaw 用等价的 Gateway 定时） | 定时日常：到点跑日常、收尾并汇报 |
| （可选）项目级权限规则 | 护栏：`guard allow` 每次弹确认、不读森空岛凭据 |

Claude Code / Codex 的会话内定时结果回到原会话；OpenClaw 的独立定时任务用 harness 的投递能力汇报。前者要求会话一直开着，后者要求 Gateway 一直运行；会话关了、定时过期了或 Gateway 停了，由用户和 harness 处理。

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

## OpenClaw

OpenClaw 是一个常驻的 Gateway：agent 有独立的 workspace，定时用 Gateway 自己的 cron。MaaPilot 不需要为它生成项目级规则文件，也不用链接 skills。本节的配置格式按 OpenClaw 2026.10 及以后验证；旧配置里的 `agents.list` 可用 `openclaw doctor --fix` 迁移到 `agents.entries`。

| 项 | 做法 |
|---|---|
| 说明文件 | 直接用 `AGENTS.md`（OpenClaw 每次会话开头读 workspace 的 `AGENTS.md`，不用另写） |
| skills | **不用链接**：`<workspace>/.agents/skills` 是 OpenClaw 的原生 skills 目录（优先级仅次于 `<workspace>/skills`），8 个 maapilot skill 直接可见 |
| 接入 | 把本仓库注册成 `agents.entries.<id>` 下的 agent，`workspace` 指向仓库根目录；片段见 `templates/openclaw/agent-entry.json` |
| 权限 | 没有项目规则文件；用 `tools.exec.ask` / exec approvals 做护栏（可选，默认不拦） |
| 定时 | 每个日常时间点建一条重复任务（`schedule.kind="cron"` + `tz`），`payload.kind="agentTurn"`，`agentId` 指到本 agent；片段见 `templates/openclaw/cron-daily.json` |
| 长命令 | `exec` 前台/后台 + `process` 轮询；`daily run` 把 `timeoutSeconds` 给到 5400（和 `task_timeout_min` 对齐） |
| 汇报 | 可选用 `delivery.mode="announce"` 投递到指定渠道；模板里用占位符，不绑定具体平台 |

OpenClaw 也支持一次性 `at` 任务，但 10-22 实测出现过任务到期未执行、记录里没有 `lastRunAtMs`、任务仍残留的情况。在调度器行为确认前，MaaPilot 用重复 cron，避免日常依赖“上一轮创建下一轮”的链条。重复任务的提示必须明确“这是重复定时，不要再建下一次”，否则 `maapilot-daily` 会按一次性接力的规则多建一条。

OpenClaw 的 `sessionTarget: "isolated"` 会在独立任务会话里运行，不继承用户对话；适合无人值守的定时日常。无人值守任务跳过未获授权事项，在最终简报中列出，不等待交互。用户现场委托的任务需要拍板时，工作 agent 应及时把问题交给发起任务的主会话，由主会话向用户提问并核对渠道发送结果。

### OpenClaw 消息投递

本节仅适用于当前宿主为 OpenClaw 的会话，不改变 Claude Code、Codex 的汇报、后台通知或交互方式。

- **默认简报**：日常收尾后集中用几句话说明结果、目标进度、必要花费和待决定事项；没有目标或待决定事项就省略对应行，用户要求详细时再展开。用户主动问进度时正常回答。
- **一份结果只走一条投递路径**：独立日常使用 cron 的 `delivery` 发送最终简报，不再调用消息工具或请主会话转发同一结果。交互任务由发起它的主会话负责对用户汇报。主会话收到已投递的日常副本时保持静默。
- **无变化就静默**：旧截图、已消费的命令完成通知、无变化的轮询不产生面向用户的回复；也不要回复“没有新信息”。OpenClaw 要求静默标记时只返回 `NO_REPLY`。不要为减少消息而关闭所有后台完成通知，否则长任务可能无人接续。
- **需要决定就明确交接**：仅在已有用户授权的委托链内，工作 agent 向发起会话返回简短的阻塞原因、一个问题与选项，并暂停依赖该决定的操作。主会话核对消息发送结果后等待用户；跨会话工具返回成功仅代表内部转交，不代表渠道已投递，更不代表用户批准。
- **保留原始渠道地址**：账号、收件人、话题来自原始入站路由或渠道提供的 conversation reference；不能从转小写的 session key 重建收件人。微信等渠道的地址区分大小写。收件人和账号不明确时不要猜，也不要换一个收件人试发。
- **区分执行与投递**：模板设置 `delivery.bestEffort=false`，让投递失败可见。检查运行记录的 `lastDeliveryStatus` / `lastDeliveryError`（以安装版本为准），不能只看游戏执行完成或任务状态。明确失败可修正后有限重试；超时、状态不明或仅缺少本地出站记录，先按消息标识核对，不能盲目重发。平台确认发送也不代表用户已读。
- **部署时验收**：经用户授权，向实际渠道发送一条不操作游戏的测试消息；再用独立测试任务验证最终简报只投递一次。测试后删除测试任务。用户现场任务还要验证阻塞问题能从工作会话交回主会话并发出。不要拿真实日常或兑换任务做投递重试。

这些提示能减少 agent 的重复回复和静默等待，但不能修复 OpenClaw 的会话存储故障、渠道插件地址处理或平台拒收。出现 `SessionDeliveryGenerationUnavailableError` 等错误时应检查 Gateway 会话状态；不要手改会话数据库，也不要把提示词当作可靠送达保证。微信插件本地补丁在升级后可能被覆盖，需要重新验收。

微信等渠道还可能依赖近期入站消息提供的回复上下文。即使收件人正确，平台也可能拒收；遇到 `prepare failed` 时检查渠道状态，必要时请用户先发一条新消息再验证。不能仅凭该错误断定具体有效期，也不能承诺无人值守时始终主动送达。需要这种保障的部署应由用户选择并授权一个支持主动通知的备用渠道；不要自行向其他账号或渠道发消息。

`agentTurn` 任务的 `payload.toolsAllow` 省略时，OpenClaw 会按当前调度器策略补默认工具权限；MaaPilot 需要运行 `maapilot`、读写项目文件和看截图。需要收紧权限时用 OpenClaw 的 `tools.exec` / exec approvals 配置，模板不写死工具白名单。

容器里跑要注意：

- agent 的 workspace **必须**是仓库根目录，`AGENTS.md` 和 `.agents/skills` 都在那儿。
- 模拟器在宿主机、agent 在容器里时，adb 用 `host.docker.internal:<端口>`（MuMu 12 默认 16384，每多开一个实例 +32），模拟器里要打开 ADB 调试。
- 容器重建或宿主机重启后 adb 连接可能丢失。定时提示应在 `daily run` 前确认设备已连接；必要时重新 `adb connect`。
- `.openclaw/` 是 OpenClaw 往 workspace 里写状态的地方，已在 `.gitignore` 里。
- 不要往仓库里放 `SOUL.md` / `USER.md` / `IDENTITY.md` / `TOOLS.md` / `HEARTBEAT.md`：那是每个 agent 自己的 bootstrap 文件，缺了 OpenClaw 照常跑（只是提示里多一条"文件缺失"）。真不想让它生成，配 `agents.defaults.skipBootstrap`。

实测（10-21 至 10-22，OpenClaw 2026.10.5，Linux 容器 + Windows 宿主机 MuMu 12）：

- `maapilot doctor` 全绿（`ok_all: true`），截图 1600×900 约 0.2–0.6 秒一张，`input` 能发点击。
- adb / maa-cli 装在项目 `.tools/`（`maapilot tools install adb` / `maa-cli`），`doctor --fix` 补齐 OCR 模型、数据表、独立 MaaCore v6.20.1。
- 定时：重复 cron 到点唤醒，agent 启动任务后 52 秒完成并成功投递；`timeoutSeconds: 5400` 支撑长命令，实测未超时。
- 一次性 `at` 任务在 10-22 出现到期未执行；这版接入不依赖一次性接力。
- 容器里没有 `crontab` 时，`doctor` 的 `legacy_schedule` 项会报 false；这是 v0.1 遗留检查，不影响 OpenClaw 使用。截图 / OCR 测试在缺桌面依赖的容器里可能报错，验证时要先排除环境问题。
- OpenClaw 2026.10.5 要求 Node 24.16+ 或 26.1+；实际测试环境曾有 Node 24.14 能启动，但不要把它当成受支持版本，部署时按 OpenClaw 官方要求安装 Node。

## 从 v0.1 升级

v0.1 用系统的定时任务启动日常、无头叫起 agent、把结果推进会话；v0.2.0 起改成上面的 harness 定时。升级的用户：
1. 删掉旧的系统定时（crontab 里本项目的那一行、Windows "任务计划程序" `MaaPilot` 文件夹里本项目的任务）：启动检查和 `doctor` 会报 `legacy_schedule`，
   agent 先 `maapilot daily unschedule --dry-run` 给用户看，用户同意后 `maapilot daily unschedule`。
2. 按 skill `maapilot-setup`「定时日常」在 harness 里定好第一个定时。
3. 升级前就开着的会话：让 agent 重新读一遍 `AGENTS.md`，停掉它还在后台挂着的 `daily watch`（这个命令已经没有了）；最好直接重开一个会话。
4. `config.yaml` 里的 `agent`、`schedule_options` 用不到了，可以删（`doctor` 的 `agent_config` 会提示）。
5. 在 Windows 上靠 Codex 命令行无头跑定时日常的：改用 Codex 桌面版。

## 接一个新的 harness

1. **核对要求**：读图、跑长命令、读 `AGENTS.md` 和 skills、能按时唤醒 agent 的定时能力（对照最上面的表）。没有定时能力的只能在对话里用。
2. **说明文件**：harness 读 `AGENTS.md` 就不用另写；读别的文件名的（例如 `CODEBUDDY.md`），写一个引入 `AGENTS.md` 的，只加这个 harness 专用的几条（定时工具叫什么、长命令怎么等）。
3. **skills**：harness 不读 `.agents/skills/` 的，建一个指向它的链接（像 `.claude/skills`）。
4. **权限**（可选）：在 `maapilot/policy.py` 加生成这个 harness 规则文件的部分，`maapilot dev rules` 生成，`tests/test_policy.py` 核对；规则要信任文件夹才加载的，在 `host install` 里加上。
5. **实测**，结果写进本文件这个 harness 的一节：定时按时唤醒（一次性的有没有、重复的会不会过期、准不准）；`maapilot` 命令放行、`guard allow` 弹确认；
   长命令怎么等结束；读图；真跑一次完整的定时日常。
6. README「支持哪些 Agent」的表里加一行。

## 接一个新的 harness（补充：要不要写代码）

判断标准：**只有"找程序"和"权限规则格式"需要动代码，别的都是文档**。

- 找程序（`maapilot host status / install` 用）：在 `maapilot/hosts.py` 的 `HOSTS` 加名字 + 一组 `*_FALLBACKS`，
  再在 `maapilot/tools/host_cmd.py` 的 `status()` / `install()` 里给它一个分支（OpenClaw 无"信任目录"，
  `install` 只返回接下来要做什么，不改用户配置）。
- 权限规则格式（有项目级规则文件的才需要）：在 `maapilot/policy.py` 加生成逻辑，`maapilot dev rules` 生成，
  `tests/test_policy.py` 核对。OpenClaw 没有这一步。
- 定时**不要**写进代码：v0.2 起定时由 harness 在会话里建（`docs/design.md` 第 1 条设计哲学），
  `hosts.py` 只负责"程序在哪"。给新 harness 的定时模板放 `templates/<harness>/`，由 agent 按 skill 去建。
- skills：先确认 harness 读不读 `.agents/skills`。读就不用链接（OpenClaw 读），不读就补链接。
