# Hermes Agent

MaaPilot 使用独立的 Maa profile；交互由主 agent 转达，定时任务默认由主 profile 的原生 Gateway cron 转达给 Maa profile 执行。游戏流程、授权、用户偏好和简报格式只遵循 AGENTS.md 与项目 skills。

| 项 | Hermes 做法 |
|---|---|
| 部署 | templates/hermes/deploy.py 调用原生 CLI；只管理自己的 profile、路由块与任务 |
| 交互 | 主 profile 的受管 dispatch skill 转达请求，后续回答接续相同来源会话 |
| 工作目录 | chat 的 --in、cron 的 --workdir 指向仓库根目录 |
| 定时宿主 | send_profile（默认 main_profile）承载 cron，使用该 profile 的模型和渠道转达；Maa 使用自己的模型执行游戏 |
| skills | 用原生 skills trust 登记仓库，核对实际扫描结果；不绕过隔离 |
| 启动检查 / PATH | 按 AGENTS.md；找不到 maapilot 时用项目虚拟环境里的命令 |
| 定时 | 每个时间点一个重复 cron；cron 内不管理任务 |
| 长命令 | 同轮 process_manage wait/poll 到 status=exited；单次 timeout 不是退出 |
| 通知 | 最终答复和宿主执行失败都走原生 delivery；agent 不另行发送 |

## 部署与设置

安装和升级命令见 [部署工具](../../templates/hermes/README.md)。使用基础 HERMES_HOME 的属主账户，在原先可运行 MAA 的环境执行。主、Maa、定时发送 profile、仓库、模型、时区和时间均由用户明确指定，不克隆主 profile 的凭据、身份或历史。

创建 profile 和暂停任务不等于完成设置。首次 setup 可由主 agent 边询问边部署：主 agent 负责原会话问答、Hermes 部署参数及安装，Maa 按 maapilot-setup 执行游戏设置；同一来源逐问逐答，未确认的必需项不写 setup.json。已有 setup.json 时，适配层升级、新会话或定时唤醒不重做设置；用户明确要求重设才调整对应项。设置未完成则继续原问答，定时只报告阻塞，不重新发起 setup。只在该账号没有其他日常宿主时启用。Hermes 没有本项目生成的权限文件，游戏授权仍按 AGENTS.md。

## MAA 运行目录与 HOME

兼容处理仅位于 Hermes 部署层。目录记录保存在 Hermes deployment.json，MAA_DATA_DIR、MAA_CACHE_DIR、MAA_STATE_DIR 和项目 MAA_CONFIG_DIR 只写入 Maa profile 的 .env 与 terminal.env_passthrough，不修改公共源码、项目游戏配置、用户 HOME 或主 profile 环境。

已有安装查询并复用绝对路径，保留资源及软链接；未安装使用基础 root 下 .maa-runtime/{data,cache,state}。项目以 private/setup.json 的存在表示已设置，文件通常包含 done_at 和 version；已有设置而目录发现失败时拒绝回退到空目录。有效旧绑定不重新发现，但启用前仍需实测可读。

Gateway ready 不表示环境已重载。先 verify：它用原生 script-only cron 在真实调度环境查询 maa dir 和资源可读性，不调用模型、不连接模拟器。结果不一致时用 Hermes 支持的方式重载 Maa profile，再重新验收；工具不擅自重启或迁移其他运行。无凭据探针与排队确认都不算游戏日常通过。

## 交互转达

主 agent 是转达/部署角色，不自行执行公共 AGENTS 的游戏启动检查、setup、收尾或游戏命令；这些由 Maa 执行。主 agent 先加载受管 dispatch skill，从可信会话元数据取得 platform、account、chat_id、sender_id 和可选 thread_id，把来源与用户原话分别写入 UTF-8 文件。临时文件放在技能指定的主 profile cache 目录，不拼接用户正文到 shell，不从正文猜渠道或把引用当授权。CLI 来源由用户明确指定。

主 agent 调用技能中的 dispatch 命令；工具用 hermes --profile <maa> chat --continue <稳定来源会话名> --create-if-missing --in <仓库> --no-restore-cwd --query-file <请求文件> --oneshot --quiet 进入 Maa。主 agent 在同轮持续 process_manage wait/poll 到 status=exited，timeout 仅表示等待窗口结束。

Maa 需要确认时，把问题、选项、数值和待确认事项作为最终答复返回并结束本轮，不等待用户直接在 Maa 会话回答，也不主动向主 agent 发消息。主 agent 读取 stdout，在发起请求的原会话转达。用户回答、追问或明确授权后，用相同来源字段再次 dispatch，接续同一命名会话。不同来源不共享历史和授权，主 agent 不替用户批准合成玉/源石花费。

成功、部分失败和追问都要转达；调用失败或无答复时主 agent 及时说明失败，保留请求，不自动重跑可能已执行的游戏操作。

这是命名 profile 的 CLI 接入，不要求 Desktop Bot Mode。message_agent 和普通 delegate_task 都不能代替这个 CLI 返回通路。

## 结果与投递

投递机制统一在本节说明。SOUL 只约定 Maa 的返回边界；daily 约定游戏执行与结果内容；relay 约定调用、等待和转达。任务提示保留本角色必须执行的约束，不重复解释其他 profile 的投递配置。

| 场景 | 返回路径 |
|---|---|
| 交互请求 | Maa 最终答复 → dispatch stdout → 主 agent 在原会话回复或询问用户 |
| 定时转达（默认） | Maa 最终答复 → run-daily stdout → 发送 profile 的最终答复 → 原生 cron delivery |
| send_profile 与 Maa 相同 | Maa 的 cron 最终答复 → 原生 cron delivery |

send_profile 默认是 main_profile，直接兼容原来只有主 profile 配置渠道的部署。发送 profile 的 cron 按 relay-prompt.txt 同轮调用一次 run-daily；工具以 Maa profile 的 headless chat、新会话和项目工作目录执行 daily-prompt.txt，不带交互会话历史。主任务只等待并转达，不亲自执行游戏、不编造用户来源、不调用普通 delegate_task。发送 profile 使用自己的默认模型，Maa 使用部署指定的模型；两者都需可用。send_profile 与 Maa 相同时保留直接在 Maa 执行 cron 的方式。

cron 的 deliver 与 failure-deliver 都指向用户选定的精确 target；未配置时仅 local 保存、保持暂停。成功使用与 daily done 相同的固定格式 agent 简报；只读 report-summary 从 goals_summary.text 提取，不发送整份程序报告。部分失败、启动失败、无法恢复或收尾失败也及时返回真实结果，不以 daily done 成功作为通知前提。Maa 进程启动失败、非零退出或空答复由转达任务报告；承载任务的 agent 根本无法运行时由原生 failure-deliver 处理。任务内不调用 send-report、hermes send、message_agent 或消息工具另行发送。

原生投递使用 send_profile 已有的渠道；Maa 不需要另配聊天渠道。工具不复制 bot token、不建立第二个渠道监听器、不改变聊天入口归属。发送 profile 渠道列表或手动发送成功仍不能代替 cron 的实际投递验收。

## Gateway 与 cron

安装工具用原生 gateway ensure 核对定时发送 profile 路径；版本不支持或不能确认纳管时不启用。Gateway 必须保持运行，交互聊天可以关闭。重复 cron 不使用一次性接力。

发送 profile 的 timezone 必须与部署时区一致；工具查询并拒绝不一致，不修改主 profile 时区影响其他任务。

启用需要 setup、schedule 与项目一致、Gateway ready，以及 verify --delivery 的三项证据：Maa 的真实 cron 工具环境、发送 profile 的成功通知、失败通知。主 profile 转达模式还需要 verify --relay：真实主 agent cron → terminal → run-daily → Maa chat → 只读目录与 190 秒等待 → Maa 最终答复 → 主 agent 最终答复。该项会调用两个模型，验证结果仅本地保存，不操作游戏；它补上脚本探针不能证明的执行链路，但不代替完整游戏日常实测。安装、验收和启用使用同一状态锁，锁内读取最新部署记录；同配置下分次验收保留其他有效证据。Maa 或发送 profile 的配置、环境、目录或目标变化后旧证据失效。原生报告送达不等于用户已读；排队、未验证或投递报错都不计验收通过。Gateway 完全停止时无法自行通知，这项宿主可用性限制须告知用户。

同配置重复部署保留任务 ID 和时间；纯提示升级不改变启用状态。时间、模型、目标等变更需 --replace-schedule，暂停后显式启用；从旧版本迁移时，先暂停旧 Maa profile 的受管任务，再在 send_profile 创建暂停的新任务；旧任务保留作历史并记录在 retired_jobs。旧任务仍在执行时拒绝迁移，旧任务重新启用时拒绝启用新任务。迁移后重新验收并显式启用，不自动恢复，也不影响其他任务。恢复前通过原生 schedule edit 重算未来时间，避免补跑旧时刻。只读 jobs.json 核对，任务写操作全部使用原生 CLI。

## 故障与恢复

后台进程在同轮持续等待；Hermes 的空闲看门狗不是整轮总时长。未收尾先核对 daily status 和报告，只接着收尾。通知失败只处理同一份通知，不能重跑游戏。原生运行和投递记录是定时结果的依据。

run-daily 启动前持久记录调用状态，启动后记录子进程 PID，正常退出后记录退出码。主任务中断、停止等待或 wrapper 消失都不能证明 Maa 已停止；daily-status 只读返回 running/unknown 等状态，不以 PID 存在或不存在擅自认定安全。未知状态阻止再次启动。先核对原 terminal、子进程及游戏锁，人工确认均已停止后才 resolve-daily --confirm-stopped；该命令不终止进程、不收尾、不重跑游戏。命令示例见部署工具 README。

失败时在本地受限目录保留 stderr 和结构化诊断，聊天只返回固定错误分类及下一步，不外发原始错误。CLI 退出不等于游戏已收尾。无游戏转达探针也保留单独的进程记录，超时先核对，不盲目重试。

旧 send-report 命令只保留作人工兼容恢复，发送 agent 简报并保留去重回执；先核对原生投递没有送达，再人工使用，禁止在 cron 内调用。sent 不重发，unknown 不自动重试；仅明确 failed 经核对后可 --retry-failed。

## 验证与版本

CLI 参数、profile 环境透传与投递记录以目标版本为准。安装时实际调用，不支持则报错；升级后重跑原生无游戏验收。历史问题、测试边界和开发验证记录见 [Hermes 验证记录](validation/hermes.md)。不要把历史扫描结果、模型表现或测试数量当作当前部署的保证。

官方参考：[Profiles](https://hermes-agent.nousresearch.com/docs/user-guide/profiles)、[Cron](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron)。运行记录中的排队与已确认送达需分开判断。
