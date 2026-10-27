# Hermes Agent

MaaPilot 使用独立的 Maa profile；交互由主 agent 转达，定时任务由该 profile 的原生 Gateway cron 执行。游戏流程、授权、用户偏好和简报格式只遵循 AGENTS.md 与项目 skills。

| 项 | Hermes 做法 |
|---|---|
| 部署 | templates/hermes/deploy.py 调用原生 CLI；只管理自己的 profile、路由块与任务 |
| 交互 | 主 profile 的受管 dispatch skill 转达请求，后续回答接续相同来源会话 |
| 工作目录 | chat 的 --in、cron 的 --workdir 指向仓库根目录 |
| skills | 用原生 skills trust 登记仓库，核对实际扫描结果；不绕过隔离 |
| 启动检查 / PATH | 按 AGENTS.md；找不到 maapilot 时用项目虚拟环境里的命令 |
| 定时 | 每个时间点一个重复 cron；cron 内不管理任务 |
| 长命令 | 同轮 process_manage wait/poll 到 status=exited；单次 timeout 不是退出 |
| 通知 | 最终答复和宿主执行失败都走原生 delivery；agent 不另行发送 |

## 部署与设置

安装和升级命令见 [部署工具](../../templates/hermes/README.md)。使用基础 HERMES_HOME 的属主账户，在原先可运行 MAA 的环境执行。主、Maa、手动发送 profile、仓库、模型、时区和时间均由用户明确指定，不克隆主 profile 的凭据、身份或历史。

创建 profile 和暂停任务不等于完成设置。按 maapilot-setup 一次问一个问题；未确认的必需项不写 setup.json。只在该账号没有其他日常宿主时启用。Hermes 没有本项目生成的权限文件，游戏授权仍按 AGENTS.md。

## MAA 运行目录与 HOME

兼容处理仅位于 Hermes 部署层。目录记录保存在 Hermes deployment.json，MAA_DATA_DIR、MAA_CACHE_DIR、MAA_STATE_DIR 和项目 MAA_CONFIG_DIR 只写入 Maa profile 的 .env 与 terminal.env_passthrough，不修改公共源码、项目游戏配置、用户 HOME 或主 profile 环境。

已有安装查询并复用绝对路径，保留资源及软链接；未安装使用基础 root 下 .maa-runtime/{data,cache,state}。项目以 private/setup.json 的存在表示已设置，文件通常包含 done_at 和 version；已有设置而目录发现失败时拒绝回退到空目录。有效旧绑定不重新发现，但启用前仍需实测可读。

Gateway ready 不表示环境已重载。先 verify：它用原生 script-only cron 在真实调度环境查询 maa dir 和资源可读性，不调用模型、不连接模拟器。结果不一致时用 Hermes 支持的方式重载 Maa profile，再重新验收；工具不擅自重启或迁移其他运行。无凭据探针与排队确认都不算游戏日常通过。

## 交互转达

主 agent 加载受管 dispatch skill，从可信会话元数据取得来源，把用户原话作为文件数据传入；不从正文猜渠道或把引用当授权。CLI 来源由用户明确指定。主 agent 同轮等待结果，在原始会话保留问题、选项、数值和待确认事项；后续回答保持相同来源。失败时报告失败，保留请求，不自动重跑可能已执行的游戏操作。

这是命名 profile 的 CLI 接入，不要求 Desktop Bot Mode。普通 delegate_task 的新对话不等于进入 Maa profile。主 agent 不再转发定时结果，避免与宿主投递重复。

## Gateway、cron 与通知

安装工具用原生 gateway ensure 核对 Maa profile 路径；版本不支持或不能确认纳管时不启用。Gateway 必须保持运行，交互聊天可以关闭。重复 cron 不使用一次性接力。

cron 的 deliver 与 failure-deliver 都指向用户选定的精确 target；未配置时仅 local 保存、保持暂停。成功使用与 daily done 相同的固定格式 agent 简报；只读 report-summary 从 goals_summary.text 提取，不发送整份程序报告。部分失败、启动失败、无法恢复或收尾失败也返回真实结果，不以 daily done 成功作为通知前提。agent 根本无法运行时由原生 failure-deliver 处理。任务内不调用 send-report、hermes send 或消息工具。

原生投递使用 Maa profile 的自有渠道或已授权共享路由。send_profile 的渠道列表、连接正常或主 profile 发送成功都不能代替验收；工具不复制 bot token、不建立第二个渠道监听器，也不擅改主路由。缺少 Maa 原生通知路径时保持暂停，按 Hermes 文档配置后重试。

启用需要 setup、schedule 与项目一致、Gateway ready，以及 verify --delivery 的三项证据：真实 cron 环境、成功通知、失败通知。profile 配置、环境、目录或目标变化后旧证据失效。原生报告送达不等于用户已读；排队、未验证或投递报错都不计验收通过。Gateway 完全停止时无法自行通知，这项宿主可用性限制须告知用户。

同配置重复部署保留任务 ID 和时间；纯提示升级不改变启用状态。时间、模型、目标等变更需 --replace-schedule，暂停后显式启用；旧 local 投递升级为原生投递也会先暂停。恢复前通过原生 schedule edit 重算未来时间，避免补跑旧时刻。只读 jobs.json 核对，任务写操作全部使用原生 CLI。

## 故障与恢复

后台进程在同轮持续等待；Hermes 的空闲看门狗不是整轮总时长。未收尾先核对 daily status 和报告，只接着收尾。通知失败只处理同一份通知，不能重跑游戏。原生运行和投递记录是定时结果的依据。

旧 send-report 命令只保留作人工兼容恢复，发送 agent 简报并保留去重回执；先核对原生投递没有送达，再人工使用，禁止在 cron 内调用。sent 不重发，unknown 不自动重试；仅明确 failed 经核对后可 --retry-failed。

## 验证与版本

CLI 参数、profile 环境透传与投递记录以目标版本为准。安装时实际调用，不支持则报错；升级后重跑原生无游戏验收。历史问题、测试边界和开发验证记录见 [Hermes 验证记录](validation/hermes.md)。不要把历史扫描结果、模型表现或测试数量当作当前部署的保证。

官方参考：[Profiles](https://hermes-agent.nousresearch.com/docs/user-guide/profiles)、[Cron](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron)。运行记录中的排队与已确认送达需分开判断。
