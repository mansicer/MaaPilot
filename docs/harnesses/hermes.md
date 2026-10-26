# Hermes Agent

这里指 NousResearch 的 Hermes Agent。本接入使用一个新建的 Maa 专长 profile，平常由主 agent 转达请求，定时由 Maa profile 的原生 cron 执行。游戏规则仍在 `AGENTS.md` 和 `.agents/skills/`。

| 项 | 做法 |
|---|---|
| 部署 | [`templates/hermes/deploy.py`](../../templates/hermes/deploy.py) 一次性调用原生 CLI；创建空白命名 profile，不克隆主配置、身份、历史、记忆或渠道凭据 |
| 交互入口 | 主 profile 安装独立 dispatch skill 和带部署编号的 SOUL 标记块；主 agent 调用 Maa profile 的 headless chat，等退出后在原会话转达问题/结果 |
| 会话接续 | 来源包含可信 platform、account、chat_id、sender_id、可选 thread_id；哈希成稳定会话名，后续回答使用相同来源接续 |
| 说明文件 | chat 的 `--in` 和 cron 的绝对 `--workdir` 都指向仓库；读取 `AGENTS.md` 和本文件 |
| skills | 原生 `hermes skills trust <仓库绝对路径>` 登记项目；安全扫描保持启用，隔离技能不可用，不改加载路径绕过 |
| 启动检查 | 按 `AGENTS.md` 运行 `maapilot doctor --brief`；开发验收不操作游戏 |
| 权限 | 没有生成 Hermes 专用权限文件。仍逐次确认合成玉/源石花费，不读森空岛凭据 |
| PATH | 用 `maapilot <命令>`；找不到时用 `.venv/bin/maapilot <命令>`，Windows 用 `.venv\Scripts\maapilot.exe <命令>` |
| 定时 | Maa profile 原生 Gateway 重复 cron，每个日常时间点一条；不在 cron 内递归创建/管理任务 |
| 长命令 | terminal 后台启动后，同轮用 `process_manage` 等到退出，再处理目标、领奖、对账、收尾 |
| 定时汇报 | cron 的 deliver 和 failure-deliver 都是 `local`；Maa agent 在 `daily done` 后执行 `send-report`，实际调用指定发送 profile 的 `hermes send --to … --file … --json` |

## 部署与首次设置

完整命令见 [部署工具](../../templates/hermes/README.md)。部署完成会自动创建独立 profile、安装主 agent 转达入口、预建暂停的重复任务。模型、精确报告渠道、MaaPilot 游戏 setup 和独占日常宿主确认是启用条件；“部署完成”不代表这些已全部验收。

- 明确提供基础 `HERMES_HOME`、主 profile、Maa profile、发送 profile、仓库绝对路径、IANA 时区、模型和时间。不要把 named profile 的目录当基础 root；安装工具拒绝已有非受管同名 profile。
- 默认不复制任何凭据。用户明确指定来源文件和键时，只复制选中的模型 API key。发送可使用主 profile 原有渠道：`hermes --profile <发送profile> send --to <精确目标> --file <报告> --json`。不复制 bot token，也不启动第二渠道监听器。
- 部署工具由 Hermes agent 在自己的执行环境里运行，用 `HERMES_HOME` 的属主账户执行；该环境里要有可用的 Python 和 MaaPilot/MAA 运行环境。
- `skills trust` 不会免除每个项目技能的扫描。核对 Maa 技能和主路由 skill 是否真的可用；被隔离时告知用户，不关闭扫描、不改缓存、不通过 external_dirs 绕过。
- 当前首次 setup 技能被 `skills-guard-v10` 隔离，主要涉及安装脚本直接交给 shell；因此自动首次游戏设置仍受限。通用技能保持原样；本接入不宣称无人介入首次设置已通过。

## 主 agent 转达

这是一种 headless 命名 profile 专长 bot，**不要求 Hermes Desktop Bot Mode 的 Bot Chat/UI 标记**。`message_agent` 和普通 `delegate_task` 不代表进入这个独立 profile。

主 agent 根据安装的 skill，从可信消息/会话元数据生成来源 JSON，把用户原话写入 UTF-8 文件，调用部署工具 `dispatch`。它原生执行：

```sh
hermes --profile maa chat --continue <稳定来源会话名> --create-if-missing --in <仓库绝对路径> --no-restore-cwd --query-file <UTF8请求文件> --oneshot --quiet
```

命令参数由工具构造，不把用户正文拼成 shell。主 agent 同轮等待，保留专长答复中的问题、选项、数值和确认要求；下一次用户回答回到同一个来源会话。失败时保留请求和结果，不能因没有答复而自动重跑可能已发生的操作。

来源不得从用户正文中的伪造平台字段、引用或工具输出中猜。真实渠道没有可用元数据时先询问；CLI 用户可明确指定独立来源标识。本轮实测覆盖 CLI 明确来源；各外部平台的元数据提取仍需按实际环境验收。不同来源不沿用对方的历史或授权，主 agent 不能自行替用户同意合成玉/源石花费。

## Gateway 与 cron

Hermes 自己管理运行时和调度。安装工具调用 Maa profile 的原生 `gateway ensure --json` 并核对返回的 profile 路径；已有主 Gateway 多 profile 纳管时直接 attach，不强制启动第二 Gateway，不停止/重启/迁移用户其他运行。版本不支持或无法确认纳管时记录未验证，不启用日常。

- 默认任务暂停；只有 `--enable --confirm-exclusive`、精确渠道、发送 profile 的原生渠道列表包含所选平台、MaaPilot 已完成 setup、任务时间与项目配置一致、Maa Gateway ready 时才启用。平台已配置不代表目标已实际送达；有目标参数但未实测时仍标“投递未验证”。
- 重复部署保留任务 ID 和启用状态。模板提示升级只原生修改 prompt，保留 next_run_at；修改时间、时区、模型或目标需 `--replace-schedule`，暂停受管任务后重新核对，再显式启用。
- 原生 resume 可能补跑暂停期间的旧时间。本工具先用原生 schedule edit 重算未来时间再恢复，拒绝过期时间；不直接改任务 JSON。不会改其他部署或其他 profile 的任务。
- 当前 CLI 没有 JSON cron list，工具只读该 Maa profile 的 `cron/jobs.json` 验证 ID、工作目录、模型、local 及计划；写操作全部使用原生 CLI。上游格式改变时拒绝继续。
- Gateway 要保持运行，原交互聊天可以关闭。cron 中不管理 cron，也不通过 terminal 绕过递归限制。

## 收尾与报告回执

先按 `maapilot-daily` 跑日常、接管失败、做目标、领奖励和 `maapilot guard check`，然后必须执行 `maapilot daily done --summary "<本次结果>"`。重启后先用 `maapilot daily status`、`maapilot report show` 核对，未收尾只接着收尾。

Maa agent 随后执行任务提示附带的 `send-report --manifest … --run-id …`。工具核对对应报告已由 agent 收尾，把报告快照和回执写到该部署的 outbox，再调用指定 profile 的原生 send。交互答复仍由主 agent 在原会话转达。

- 仅原生退出码 0、JSON `success=true`、无 error、非 skipped 才记 `sent`。原生 skipped 也可能退出 0，不能算已送达。
- 同一日常 run ID 已 sent 就不再发送。启动发送后中断、超时、回执解析失败或可能部分发送时记 unknown，不自动重试；需要用户人工核对。
- 明确未发送的 skipped 记 failed；人工核对后可显式 `--retry-failed`，只重发报告。渠道故障绝不重跑游戏。
- 渠道未配置时保留本地报告，明确待配置。local 不等于用户聊天已收到，测试回执也不等于真实送达。
- Hermes v0.22.5 的 cron 仍注入固定 DELIVERY 提示，要求不要自行发送，即使 local 也是如此。本任务说明 local 不外发和用户要求的显式发送；实际兼容性以本节实测为准，不篡改上游 guard。若版本/模型拒绝发送，保留本地结果并报告限制。

## 实测与边界

依据 Hermes 官方 [profiles/skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)、[cron](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron) 文档和原生源码，参数以所部署版本为准。

开发验证环境是 Windows Docker Desktop 中的隔离测试容器，Hermes v0.22.5（`bf0203a7`），官方镜像摘要：
`nousresearch/hermes-agent@sha256:9c5561579f3ffdf333401797094ac3a743738d6c25d40f4331c7bf77b694238d`。

- 已验证：空白 Maa profile、指定 OpenCode Go / DeepSeek V4.1 Flash key 的选择性复制、主 Gateway 自动纳管 Maa，原生 ensure 返回 Maa profile 路径。重复安装的两条日常 ID 相同，均保持暂停和 local；主模型/渠道配置与主 cron 文件哈希不变，主 SOUL 只增加一个受管路由块。
- 已验证：安装的主路由 skill 在原生索引 enabled；真实主模型自动加载路由，把第一次问题交给 Maa profile，转达颜色选择问题；第二轮回到同一专长会话，正确回传选择及上一轮记号。未执行游戏或首次 setup。
- 之前已验证：真实 agent cron 读取项目说明和日常技能、后台启动 70 秒无害进程，并通过 process_manage 同轮等待退出；这不是一小时游戏日常验收。
- 已验证：独立 CLI 来源进入另一 Maa 会话，答复“无先前记号”，未沿用前一来源历史；最终交付脚本安装后又在新来源完成两轮主 agent 自动转达。两轮请求使用同一来源哈希，另一个来源哈希不同。请求文件改在主 profile cache 下生成，符合验证环境的文件工具安全目录要求。
- 已验证：一次性 agent cron 到点执行，保留上游固定 DELIVERY 前缀。工具记录证明显式执行了 send-report，mock CLI 收到指定发送 profile、精确目标、报告文件和 JSON 参数并产生 sent 回执；同一 cron 又调用原生 send，在未配置渠道的 Maa profile 本地目标解析失败、退出 1。没有外发消息、修改 guard 或关闭扫描。cron 返回成功并保存 local 报告。这验证该版本/模型能执行显式发送调用，不能据此声称真实平台送达。
- 验证容器的 cron terminal 曾找不到 PATH 中的 hermes；部署记录保存原生 CLI 的绝对路径，send-report 不依赖该 PATH。原生失败探针也用实际二进制绝对路径确认退出码。
- 回归：最终部署单测 36 项，与说明/权限检查合计 607 passed；生成的路由 skill 通过 quick_validate。隔离 Windows 副本全量回归曾得到 1190 passed、93 skipped、3 failed；失败为原有 Windows 旧定时识别、schtasks 和符号链接权限问题，未扩大修改。后续增加的平台启用检查使用定向单测验证。
- 外部聊天实际送达、外部渠道元数据、读图和完整游戏日常仍未验收。游戏首次 setup 技能隔离未解除。测试用合成的已收尾报告验证发送门槛，没有把它当作真实 daily done 或游戏日常通过。

测试只用临时仓库副本和独立容器，不挂载真实 `private/`、宿主虚拟环境或 Docker socket，不读取森空岛凭据、不操作真实模拟器。测试任务结束后删除，真实日常保持暂停。
