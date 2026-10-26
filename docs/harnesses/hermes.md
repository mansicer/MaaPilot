# Hermes Agent

这里指 NousResearch 的 Hermes Agent。通用游戏规则仍在 `AGENTS.md` 和 `.agents/skills/`；本文件只规定 Hermes 的接入，不改变其他 harness 的行为。

| 项 | 做法 |
|---|---|
| 说明文件 | 在仓库根目录启动；cron 必须设置绝对路径 `workdir`，让新会话加载 `AGENTS.md` |
| skills | 支持项目技能的版本先运行 `hermes skills trust <仓库绝对路径>`；否则在独立 profile 的 `skills.external_dirs` 中登记仓库的 `.agents/skills` |
| 启动检查 | 没有 MaaPilot 专用启动钩子；按 `AGENTS.md` 自己运行 `maapilot doctor --brief` |
| 权限 | 本项目没有生成 Hermes 权限规则；信任 skills 不等于设置命令审批或保护凭据。仍严格遵守合成玉、源石逐次授权和不读森空岛凭据的规则 |
| PATH | 已安装到 PATH 时用 `maapilot <命令>`，否则用 `.venv/bin/maapilot <命令>`（Windows 用 `.venv\Scripts\maapilot.exe <命令>`） |
| 定时 | Hermes Gateway 的重复 cron，每个日常时间点一条；不使用一次性接力 |
| 长命令 | `terminal` 后台启动，保留本轮，用进程工具 `process_manage`（部分文档称 `process`）等待退出，再收尾 |
| 汇报 | cron 自己投递一次最终报告；没有经过验证的聊天目标时使用 `local`，明确告知用户只保存本地结果 |

## setup 时要多做的

1. 使用独立的 Hermes profile / `HERMES_HOME`，配置用户选择的模型服务。未经用户明确同意，不复制其他 agent 的凭据；不改它们的配置。
2. 将执行目录设为完整 MaaPilot 仓库，按 `maapilot-setup` 安装运行环境。容器里的 Linux 虚拟环境不能复用 Windows 的 `.venv`。
3. 从仓库目录运行 `hermes skills list`，确认全部 `maapilot-*` 技能可见；抽查 `maapilot-daily` 内容来自本仓库。项目技能需要信任，非交互 cron 不会替用户完成首次信任。若技能被安全扫描隔离，先查看报告并告诉用户；不要关扫描、改缓存或换加载路径绕过隔离，也不要宣称 setup 已可用。外部目录方案仅用于不支持项目发现的版本，见 [`templates/hermes/`](../../templates/hermes/)。不要复制一套技能后任其与仓库脱节。
4. 给 cron 平台启用终端、进程管理、文件、技能和读图所需的工具；用 `hermes tools` 检查 cron 的工具集，不能因为交互会话可用就认为定时也可用。
5. 确认时区和 `private/config.yaml` 中的日常时间。先查已有任务，只在一个宿主上启用真实日常，不能与原有 OpenClaw / 其他宿主重复跑。
6. 用不操作游戏的测试任务验证项目加载、长命令等待、本地输出；外部投递需用户指定渠道和接收目标后单独测试。把配置与测试结果汇总给用户，完成通用 setup 的确认后再写 `private/setup.json`。

## 定时日常

Hermes 的 cron 会话禁止递归创建 cron。因此本宿主明确选择重复定时：通用 setup / daily 中的“一次性接力”在这里不适用。定时醒来不尝试调用被禁用的 cron 管理工具，也不绕过限制用终端建立下一次任务；任务的创建、检查和修改在交互 setup 或用户要求改时间时完成。

- Gateway 必须一直运行；不要求原来的交互聊天一直开着。
- 调度时区来自 Hermes 的 `HERMES_TIMEZONE` 或 profile 配置 `timezone`，不要照抄 OpenClaw 的 `--tz` 参数。建完读回下一次执行时间，核对日期、时间和偏移。
- 通过当前版本的 cron 管理工具或 `hermes cron create` 建立任务；具体参数先核对 `hermes cron create --help`。CLI 示例和提示模板见 [`templates/hermes/`](../../templates/hermes/)。
- 每条任务必须绑定仓库绝对路径 `--workdir`；默认的无项目上下文会话不适合 MaaPilot。
- 日常用有 agent 的任务，不能用 `--no-agent` 脚本代替；失败接管、目标判断、领奖、对账与 `daily done` 都需要 agent 完成。
- 管理用 `hermes cron list`、`hermes cron status`、`hermes cron edit <id>`、`hermes cron pause <id>`、`hermes cron resume <id>`、`hermes cron remove <id>`。只修改 MaaPilot 自己的任务。
- 改时间前先暂停对应任务，核对修改后的下一次时间再恢复；Hermes 可能补跑暂停期间错过的时间点，恢复前确认不会与已有日常重复。

## 长命令与收尾

`daily run` 通常持续 20–60 分钟。用 `terminal(command="…", background=true)` 启动，在同一轮使用返回的进程句柄调用 `process_manage` 的 `wait` 或 `poll`，直到拿到退出码（部分文档示例仍称 `process`）。进程工具可能需要先用工具发现加载；具体名称、等待间隔和参数以当前工具 schema 为准，避免高频空轮询。

- cron 不能依赖交互会话的“完成后再唤醒”。后台命令还没退出时不要生成最终回复或结束本轮。
- 区分终端命令超时、agent 无活动超时与步数预算。为整轮日常和等待预留足够额度；`cron.script_timeout_seconds` 只控制前置脚本，不是 agent 日常的总超时，不能用它替代长任务配置。
- 退出后按 `maapilot-daily` 处理失败接管、目标、领奖和对账，然后执行 `maapilot daily done --summary "<本次结果>"`。
- 中途重启或超时，先用 `maapilot daily status`、`maapilot report show` 核对。已经完成但未收尾的只接着收尾，不因缺少通知重跑游戏。

## 投递与用户决定

- 定时运行中不发送进度消息；最终结果由 cron 的投递机制发送一次，不再另用发送消息工具转发。
- 明确设置 `local` 或用户指定的 `平台:会话目标`。从 CLI 建任务时不要依赖缺失的 origin，也不要猜接收人。
- `local` 表示保存在 Hermes 的 cron 输出中，不代表用户聊天里收到了报告。外部渠道需要主动投递实测，连接正常不等于投递成功。
- 运行成功与投递成功分开核对；失败时保留报告，只修复投递，不重跑日常。测试成功也不等于用户已读或批准。
- 定时遇到未授权选择，记入最终报告并继续其他可做的部分，不等待用户。交互任务遇到阻塞问题，在发起任务的原会话问；不要假设独立 cron 会话继承聊天上下文或用户授权。

## Docker 与验证

模板提供独立的数据卷和可指定版本的官方镜像。测试使用临时仓库副本，不挂载真实 `private/`、Windows 虚拟环境或其他 agent 数据；不挂载 Docker socket。生产部署的模拟器地址来自该部署的 `private/config.yaml`，容器需能连到宿主机的 ADB。

先测试说明与技能发现、定时到点执行、后台退出码和输出、本地报告；再用选定模型测试 agent 真的等到退出并继续处理。最后才验证外部渠道与真实日常。脚本 cron 成功不能替代 agent 测试，更不能当成游戏日常通过。

### 验证状态

- 接入依据：Hermes 官方 [cron](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron)、[skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)、[tools](https://hermes-agent.nousresearch.com/docs/user-guide/features/tools) 文档与当前源码。
- 2026-10-25，Windows Docker Desktop + 独立 Linux 容器：官方 `latest` 拉取后报告 Hermes v0.22.5（upstream `bf0203a7`）。镜像固定摘要为 `nousresearch/hermes-agent@sha256:9c5561579f3ffdf333401797094ac3a743738d6c25d40f4331c7bf77b694238d`。源码 main 与在线文档可能领先镜像，参数以容器里的 CLI 为准。
- 已通过：Gateway 启动与调度心跳；cron 的 workdir 和北京时间读回；无模型脚本定时实际执行、本地输出保存与任务删除；用户指定的 OpenCode Go / DeepSeek V4.1 Flash 连接测试。模型曾返回容量不足的 HTTP 429，随后重试成功。
- 技能发现：信任项目后识别 8 个技能，7 个进入可用列表。`maapilot-setup` 被 `skills-guard-v10` 隔离，扫描报告中最高严重级别项为在线安装脚本直接交给 shell 执行；另有安装、旧定时说明及 Windows 挂载权限相关提示。首次设置尚未通过，不能把“项目已信任”等同于“全部技能已加载”。本接入未关闭扫描或修改其他宿主的设置流程。
- 真实 agent cron 已通过：用上述模型读取项目说明、加载 `maapilot-daily`；后台启动 70 秒无害进程，通过 `process_manage` 同轮等待，核对退出码 0 和输出标记后才生成最终报告。日志明确记录 cron 不支持异步完成投递。这验证了等待与报告流程，未验证一小时日常或实际 `daily done`。
- 外部聊天投递、读图和完整游戏日常待实测。测试任务完成后删除，未安排真实日常，也未操作模拟器。
