# Hermes 接入验证记录

以下为历史记录，不是当前部署的能力承诺。旧的 local + send-report 路径已由原生投递替代。

## 2026-10-28：环境安装与游戏 setup 分工

- 主路由、受管 SOUL 和 dispatch 请求约定主 agent 在原会话处理环境安装与审批，环境检查及原生 verify 后才交给 Maa 做游戏 setup；Maa 缺依赖或遇审批要求时返回缺项与原进程状态，不在单次调用内反复安装。
- 明确 terminal(background=true) 启动一次、同轮按进程 ID 持续 wait/poll；工具超时先找回原进程，不重跑、kill、清锁或用 shell 等待循环。环境修复不与游戏操作并行，目录绑定仅作用于安装命令进程。
- Hermes 与文档测试 647 passed；同版镜像的无网络临时容器对生成的主路由 skill 原生扫描为 clean，零命中。本轮未重启用户容器、调用模型或执行环境安装与游戏 setup，真实模型遵循新分工的效果仍须部署验收。

## 2026-10-28：maa-cli 目录查询兼容与 relay 标记

- 修正 Hermes 目录发现和运行探针调用不存在的 `maa dir state`：查询 `maa dir log`，确认绝对路径及末级 debug 后取 parent 作为 state。不能取 parents[1]，也不能把 MAA_STATE_DIR 环境变量直接当作实际目录证据。
- 回归测试执行两处查询脚本，覆盖实际日志目录与环境变量不一致、相对日志路径和未知目录结构；Hermes 与文档测试 647 passed。
- 在当前部署容器中通过标准输入执行新版查询，使用部署记录中的目录绑定，真实 maa-cli 返回资源可读、state 与配置一致。未改运行中的部署文件、定时任务或用户配置，未操作游戏。无绑定的容器默认环境无法定位资源，不能替代 profile 环境验收。
- relay 验收为发送 profile 明确成功标记只输出原行，不附说明或格式；失败仍如实报告，未放宽精确校验。本轮未重新调用模型运行 190 秒验收。

## 2026-10-28：解除首次 setup 技能隔离

- 当前容器复现公共 maapilot-setup 被隔离：uv 的下载脚本直接传给 shell，命中 curl_pipe_shell 严重规则，判定 dangerous；仓库信任不会跳过内容扫描。
- 缺少 uv 时改用技能已有的 Python 3.12+ / venv 路径，保留已有 uv 的安装流程。没有关闭扫描、修改信任规则或绕过隔离。
- 修改同步到运行中的 Hermes 容器并备份原文件。原生扫描结果 safe；在 Maa profile 环境下新进程调用原生项目技能隔离入口，返回 quarantined=False。已有长驻进程可能仍缓存旧判定，需要重新加载；未重启 Gateway、执行游戏 setup 或操作模拟器。
- 文档一致性测试 555 passed。此前“首次 setup 技能隔离未解除”的记录仅描述当时状态；本次验证技能扫描，不代表完整首次设置已实测通过。

## 2026-10-28：补全转达验收与中断处理

- 新增 `verify --relay`：主 profile 的真实 agent cron 使用 run-daily 进入 Maa，Maa 运行只读目录检查和 190 秒等待探针。验收核对工具结果、子进程退出、Maa 答复及原生 cron 输出中长度分帧的主 agent 最终答复，不能用提示里的测试标识冒充成功；结果只保存在本地。转达模式启用必须同时具有该证据和原有环境、成功/失败通知证据。
- 安装、验收和启用使用同一 root 状态锁，读取最新 manifest 后才操作；分次验收保留同配置下其他有效结果。运行调用另有持久状态与子进程 PID，失联不视为已停止，未知状态拒绝再次启动，人工核对后才能解除。
- 主 agent 负责首次设置的原会话问答和部署，Maa 按公共技能完成游戏 setup；已完成设置不因升级或新会话重做，定时不进入 setup。错误类别可转达，原始 stderr 和结构化诊断仅保留在本地受限目录。
- 定向测试（Hermes、OpenClaw、说明一致性）650 passed；另新增真实无害子进程中断测试 1 passed，确认父调用中断时子进程仍存活、状态记录 unknown、再次调用被拒绝，最后只清理测试自己创建的子进程。另有真实子进程正常退出和 PID 记录检查。
- 本轮完整模型转达探针由模拟原生 CLI 验证成功/失败判定，未调用真实主/Maa 模型，未发外部消息或操作游戏。新的实际模型链路、190 秒宿主等待和目标平台送达须在部署环境执行相应验收；通过上述单测不等于这些现场验收已通过。

## 2026-10-28：恢复交互约定与主渠道兼容

- 恢复主 SOUL、dispatch skill 和 Maa SOUL 的完整双向约定：按可信来源接续，Maa 用最终答复返回问题和结果，主 agent 在原会话转达；长进程同轮等待到退出，失败也回复，不使用 message_agent 或普通 delegate_task 替代 profile 路由。
- 默认由 send_profile（默认主 profile）承载原生 cron，通过 run-daily 调用 Maa 的独立 headless 会话。成功与失败共用该发送 profile 的原生 delivery；不再要求 Maa 自有聊天渠道，也不恢复 cron 内显式 send-report 的冲突路径。发送 profile 自己的模型须可用。
- 定向回归：`tests/test_hermes_deploy.py` 与 `tests/test_docs_consistency.py` 合计 622 passed。覆盖旧任务暂停迁移、中断恢复、运行中拒绝迁移、旧任务重新启用检查、来源接续、主渠道归属、成功和失败结果、启动异常、并发调用拒绝、时区及配置变更。
- 使用上述镜像新建无网络、无用户配置挂载的隔离容器。真实原生 CLI 通过空白 profile 创建、config 查询/设置、主 profile 暂停 cron 创建、重复安装保持任务 ID，以及无凭据 Maa chat 返回失败。测试未启动 Gateway（ensure 返回未验证），未调用模型、未发外部消息、未操作游戏；测试容器随后删除。
- 迁移及投递结果判定已由模拟原生 CLI 的单元测试覆盖；本轮没有真实主模型到 Maa 模型的完整双轮交互、真实渠道送达或完整游戏日常实测。此前版本的模型交互记录不能代替这些新路径的验收。

## 实测与边界

依据 Hermes 官方 [profiles/skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)、[cron](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron) 文档和原生源码，参数以所部署版本为准。

开发验证环境是 Windows Docker Desktop 中的隔离测试容器，Hermes v0.22.5（`bf0203a7`），官方镜像摘要：
`nousresearch/hermes-agent@sha256:9c5561579f3ffdf333401797094ac3a743738d6c25d40f4331c7bf77b694238d`。

- 已验证：空白 Maa profile、指定 OpenCode Go / DeepSeek V4.1 Flash key 的选择性复制、主 Gateway 自动纳管 Maa，原生 ensure 返回 Maa profile 路径。重复安装的两条日常 ID 相同，均保持暂停和 local；主模型/渠道配置与主 cron 文件哈希不变，主 SOUL 只增加一个受管路由块。
- 已验证：安装的主路由 skill 在原生索引 enabled；真实主模型自动加载路由，把第一次问题交给 Maa profile，转达颜色选择问题；第二轮回到同一Maa profile 会话，正确回传选择及上一轮记号。未执行游戏或首次 setup。
- 之前已验证：真实 agent cron 读取项目说明和日常技能、后台启动 70 秒无害进程，并通过 process_manage 同轮等待退出；这不是一小时游戏日常验收。
- 已验证：独立 CLI 来源进入另一 Maa 会话，答复“无先前记号”，未沿用前一来源历史；最终交付脚本安装后又在新来源完成两轮主 agent 自动转达。两轮请求使用同一来源哈希，另一个来源哈希不同。请求文件改在主 profile cache 下生成，符合验证环境的文件工具安全目录要求。
- 已验证：一次性 agent cron 到点执行，保留上游固定 DELIVERY 前缀。工具记录证明显式执行了 send-report，mock CLI 收到指定发送 profile、精确目标、报告文件和 JSON 参数并产生 sent 回执；同一 cron 又调用原生 send，在未配置渠道的 Maa profile 本地目标解析失败、退出 1。没有外发消息、修改 guard 或关闭扫描。cron 返回成功并保存 local 报告。这验证该版本/模型能执行显式发送调用，不能据此声称真实平台送达。
- 验证容器的 cron terminal 曾找不到 PATH 中的 hermes；部署记录保存原生 CLI 的绝对路径，send-report 不依赖该 PATH。原生失败探针也用实际二进制绝对路径确认退出码。
- 回归：最终部署单测 36 项，与说明/权限检查合计 607 passed；生成的路由 skill 通过 quick_validate。隔离 Windows 副本全量回归曾得到 1190 passed、93 skipped、3 failed；失败为原有 Windows 旧定时识别、schtasks 和符号链接权限问题，未扩大修改。后续增加的平台启用检查使用定向单测验证。
- 外部聊天实际送达、外部渠道元数据、读图和完整游戏日常仍未验收。游戏首次 setup 技能隔离未解除。测试用合成的已收尾报告验证发送门槛，没有把它当作真实 daily done 或游戏日常通过。

测试只用临时仓库副本和独立容器，不挂载真实 `private/`、宿主虚拟环境或 Docker socket，不读取森空岛凭据、不操作真实模拟器。测试任务结束后删除，真实日常保持暂停。

## 当前变更的验收口径

- 单元/契约测试：使用假 CLI，验证部署边界、目录发现失败、agent 简报提取、通知配置和启用门槛；不能证明真实平台送达。
- 原生无游戏验收：用 verify 在实际 cron 环境检查目录、成功投递和执行失败投递。每次部署需有自己的结果，不沿用上述旧结果。
- 真实聊天双轮回传、读图、首次游戏设置和完整游戏日常：需在目标版本分别验收；脚本或字符串测试不计通过。
- 扫描隔离是版本和安装状态；部署时检查实际技能列表，不能将历史 skills-guard-v10 结果写成永久限制。

## 2026-10-27 本次修改验证

- 公共 maapilot 包、AGENTS.md 和通用 skills 无修改。最终定向测试 619 passed。
- 全量回归：1200 passed、93 skipped、4 failed。失败均在未改动的 Windows OS 兼容测试：旧定时识别、真实 schtasks 创建、符号链接权限与仓库链接类型；未为让测试变绿而修改公共代码。
- 原生验证使用隔离 HERMES_HOME 和临时 profile，Hermes v0.22.5（2026.9.24，bf0203a7，Python 3.14.9）。只调用 script-only cron，MAA 目录层使用假 CLI 和假资源，不是实际 MAA 或游戏验收。
- 原生目录探针实际执行并通过；确认测试 cron 数量归零。
- 无渠道凭据时，成功探针得到 delivery_failed，故意失败探针得到 error 且 delivery_error=true；两者均被部署工具拒绝计为投递通过，测试 cron 均已删除。未向真实平台发送消息。
- 发现并处理目标版本的两项行为：脚本须使用 scripts/ 下的相对文件名；暂停任务的手动 run 只返回提示而不执行，探针需在远期计划下恢复后再手动运行。
- 真实平台成功/失败消息送达、用户已读、真实模型终端与完整游戏日常仍未验收。实际部署仍须运行自己的 verify --delivery，不能沿用隔离测试结果。
