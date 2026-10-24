# OpenClaw

在 OpenClaw 里用 MaaPilot 的做法。通用规矩在 `AGENTS.md` 和 skills 里，这里只写 OpenClaw 自己的。

OpenClaw 是一个常驻的 Gateway：agent 有独立 workspace，定时由 Gateway 的任务调度器执行。MaaPilot 不生成项目级权限文件，也不需要链接 skills。

| 项 | 做法 |
|---|---|
| 说明文件 | `AGENTS.md`（OpenClaw 读 workspace 根目录的 `AGENTS.md`） |
| skills | 直接读 `.agents/skills/`，不用链接 |
| 启动检查 | 没有项目启动钩子：`AGENTS.md` 让你在会话开头自己跑一次 `maapilot doctor --brief` |
| 权限 | 没有项目级规则文件；需要收紧时用 OpenClaw 的 `tools.exec` / exec approvals |
| PATH | 已在 PATH 时写 `maapilot <命令>`；否则用 `.venv/bin/maapilot <命令>`（Windows 为 `.venv\Scripts\maapilot.exe <命令>`） |
| 定时 | Gateway 的重复 cron；每个日常时间点一条（见下） |
| 长命令 | `exec` 放后台，再用 `process` 查看；定时任务的 `timeoutSeconds` 设为 5400 |
| 汇报 | 定时任务可用 `delivery.mode="announce"` 直投；交互会话由发起任务的主会话负责回复 |

## setup 时要多做的

1. 把本仓库注册成 OpenClaw agent，`workspace` 指向仓库根目录；配置片段见 [`templates/openclaw/`](../../templates/openclaw/)。不要与别的 agent 共用 `agentDir`。
2. 让用户按 OpenClaw 当前版本检查配置：`openclaw config validate`，再用 `openclaw agents list` 确认 workspace。
3. 在这个 agent 的会话里按 skill `maapilot-setup` 完成 MaaPilot 设置。
4. 按 `private/config.yaml` 的 `schedule`，每个时间点建一条重复 cron；使用 [`cron-daily.json`](../../templates/openclaw/cron-daily.json) 的字段结构，把 `${...}` 换成实际值。
5. 经用户同意，用不操作游戏的消息和独立任务验收投递；测试完成后删除测试任务。

配置格式按 OpenClaw 2026.10 及以后验证。旧配置里的 `agents.list` 先用 `openclaw doctor --fix` 迁移到 `agents.entries`。仓库不替用户修改 `~/.openclaw/openclaw.json`。

## 定时日常

OpenClaw 用 Gateway 的**重复 cron**，不用一次性接力。2026-10-22 的 OpenClaw 2026.10.5 实测里，一次性 `at` 任务出现过到期未执行、没有 `lastRunAtMs`、任务仍残留的情况；重复任务不依赖“上一轮创建下一轮”。

- `schedule.kind` 用 `cron`，并写 IANA `tz`；每个日常时间点一条。
- `sessionTarget` 用 `isolated`，避免长任务混进用户对话历史。
- 提示单看一句就能执行，并明确写“这是重复定时，不要建下一次一次性定时”。
- 提示要求按 skill `maapilot-daily` 跑日常，结束时必须执行 `maapilot daily done`。
- 到点时先确认现有时间点，不要重复建；改时间时改对应任务，删任务时只删 MaaPilot 自己的。
- Gateway 必须一直运行；任务本身不过期，但 Gateway 停了就不会按时执行。

## 长时间任务

`daily run` 通常要 20–60 分钟。用 `exec` 放后台，`timeoutSeconds` 与配置里的 `task_timeout_min` 对齐（模板为 5400 秒），再用 `process` 查看结果。给命令设置很短的超时会把进程杀掉并可能留下孤儿任务和锁。

## 消息汇报与阻塞问题

定时任务运行在独立会话里，**在那里写的回答不会自动出现在用户的聊天界面**。结论、异常和需要用户拍板的问题必须通过真正到达用户的通道发出：

- 定时日常使用 cron 的 `delivery` 投递最终总结。模板用 `bestEffort=false`，让投递失败在执行记录里可见。一份结果只走一条外部投递路径：不要再调用消息工具或请主会话转发同一份总结。
- 长的总结仍按 skill `maapilot-daily` 的固定格式写，但只投递一次。旧截图、已经处理过的后台完成通知、没有变化的轮询不逐条回复，也不要回复“没有新信息”；需要静默时只返回 `NO_REPLY`。
- 用户现场委托的任务遇到需要决定的阻塞时，暂停依赖该决定的操作，把“卡在哪里、一个问题、选项”及时交给发起任务的主会话，由主会话回复用户并核对发送结果。`sessions_send` 成功只代表内部转交；普通会话回复也不等于外部渠道已送达。
- 无人值守日常仍按通用规则不等待交互：跳过未获授权事项，写进最终总结的待决定事项。
- 收件人、账号和话题使用原始入站路由或渠道提供的 conversation reference。地址区分大小写的渠道不能从转小写的 session key 重建收件人；不确定时问用户，不要猜。
- 游戏执行完成和消息送达分别检查。明确失败可以修正后重试；超时、状态不明或只缺本地出站记录时，先按消息标识核对，禁止盲目重发。投递失败只重试通知，绝不重跑日常、兑换或其它游戏操作。渠道确认 sent 也只代表平台接收，不代表用户已读或批准。

部署验收时，经用户授权先发一条不操作游戏的测试消息，再用一个禁用所有工具、只包含旧通知和模拟问题的独立任务验证最终只出现一条提问。测试任务完成后删除。不要拿真实日常或兑换任务做投递重试。

这些是 OpenClaw 的投递规则，不能保证底层平台始终可达。遇到 `SessionDeliveryGenerationUnavailableError` 等会话存储错误时检查 Gateway 状态；不要手改会话数据库。微信渠道还可能在回复上下文失效后拒绝发送 `prepare failed`，此时请用户先发一条新消息再验证；需要长期主动通知时，由用户选择并授权支持主动通知的备用渠道。

## 容器

- agent 的 workspace **必须**是仓库根目录，`AGENTS.md` 和 `.agents/skills` 都在那里。
- 模拟器在宿主机、agent 在容器里时，adb 用 `host.docker.internal:<端口>`（MuMu 12 默认 16384，每多开一个实例加 32），模拟器里要打开 ADB 调试。
- 容器重建或宿主机重启后，先确认 `daily run` 前设备仍能连接。
- `.openclaw/` 是 OpenClaw 往 workspace 写的状态，已在 `.gitignore`。
- 不要往仓库放 `SOUL.md`、`USER.md`、`IDENTITY.md`、`TOOLS.md`、`HEARTBEAT.md`；缺了不影响运行，要阻止生成就设 `agents.defaults.skipBootstrap`。

## 实测

- 10-21 至 10-22（OpenClaw 2026.10.5，Linux 容器 + Windows 宿主机 MuMu 12）：`maapilot doctor` 全绿；截图、OCR 和点击可用；重复 cron 到点唤醒并投递；长命令使用 5400 秒超时，未超时。
- 10-23：独立任务禁用全部工具后模拟阻塞问题，约 5 秒完成并只投递一次；后台完成通知没有产生额外回复。
- 已实测一次性 `at` 任务到期未执行，因此本接入使用重复 cron。
- 容器缺少桌面依赖或 `crontab` 时，截图测试和 v0.1 遗留的 `legacy_schedule` 检查可能报错，先排除环境问题再判断 MaaPilot。
