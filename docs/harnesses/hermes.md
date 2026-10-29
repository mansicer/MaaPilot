# Hermes Agent

MaaPilot 使用独立的 Maa profile；交互由主 agent 转达，定时任务默认由主 profile 的原生 Gateway cron 转达给 Maa profile 执行。游戏流程、授权、用户偏好和简报字段遵循 AGENTS.md 与项目 skills；聊天排版见下文“日常汇报样式”。

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

先完成下节“会话模式与完成通知”的启动前准备；主 Gateway 从第一条部署消息起就须使用显式多 profile 模式。

安装和升级命令见 [部署工具](../../templates/hermes/README.md)。使用基础 HERMES_HOME 的属主账户，在原先可运行 MAA 的环境执行。主、Maa、定时发送 profile、仓库、模型、时区和时间均由用户明确指定，不克隆主 profile 的凭据、身份或历史。

创建 profile 和暂停任务不等于完成设置。主 agent 在原会话按公共 maapilot-setup 完成整个首次设置，Maa 接手已设置的账号，按以下顺序交接：

1. 主 agent 在原会话确认部署参数、软件安装及必要的配置写入，按公共 maapilot-setup 的环境部分准备项目 Python 环境。检查已有软件和资源，只补缺项；不要求用户重复授权已确认的步骤。工具的安全审批仍须通过 Hermes 支持的交互方式完成，聊天同意不等于绕过工具审批；渠道不支持时报告阻塞，请用户在交互终端完成。
2. 主 agent 安装 Hermes 适配层，获得 deployment.json，任务保持暂停。在原先可运行 MAA 的环境安装会复用已有目录；全新安装则使用部署记录中的目录。后续项目命令通过 runtime-exec 执行，自动绑定仓库工作目录、项目虚拟环境、MAAPILOT_PRIVATE 和四个 MAA 目录变量；不修改主 profile 的全局环境或换 HOME 重装一套。入口不调用 Maa 模型，不复制 profile 凭据，不绕过权限检查；参数中保留完整操作以供用户与原生工具审批。示例见部署工具 README。
3. 主 agent 处理 adb、maa-cli、MaaCore/资源、OCR 和游戏数据等缺项，按公共技能和命令帮助使用 tools install、maa install 等已有命令。先确认必需的服务器等配置，再执行 maapilot doctor --fix --offline 做环境修复；该命令仍可能联网下载，offline 只让诊断跳过设备检查，不代表 fix 离线。不得与另一安装、doctor --fix 或游戏进程并行操作同一资源目录。
4. 主 agent 用 maapilot doctor --offline 核对环境项，再运行部署工具 verify，确认 Maa 的真实 cron 工具环境和目录可读。setup 尚未完成及离线跳过的设备项不作为环境安装失败；按检查明细判断，不以 doctor 总退出码代替。环境缺项尚未解决就报告阻塞，不派 Maa 去重复安装。
5. 主 agent 继续通过 runtime-exec 按公共技能完成模拟器连接与游戏检查、试启动、读仓库/干员；在同一原会话逐项询问游戏偏好，使用文件工具将用户确认的配置写入该仓库。只有必需步骤完成后才按公共技能写 setup.json。首次 setup 不 dispatch 给 Maa，不在两个 profile 间来回询问或各做一遍。
6. 主 agent 核对 doctor、游戏配置与日程，再完成 verify --delivery 和转达模式的 verify --relay，证明实际 Maa profile 环境、消息投递和主/Maa 回传可用，最后确认唯一宿主并启用定时。之后的游戏请求通过同一来源 dispatch 交给 Maa；Maa 读取已有配置和项目偏好，不重做 setup。设置中断由主 agent 依据实际配置与进度继续，不把失败当完成，也不重复已成功的游戏操作。

未确认的必需项不写 setup.json，主 agent 不提前代写完成标记。已有 setup.json 时，升级、新会话或定时唤醒不重做设置；用户明确要求重设才调整对应项。设置未完成继续原问答，定时只报告阻塞。只在该账号没有其他日常宿主时启用。Hermes 没有本项目生成的权限文件，游戏授权仍按 AGENTS.md。

### Python 环境与 uv

主 agent 优先复用可运行的 uv，并用 uv 创建 Python 3.12 项目环境；不要直接拿 Hermes 镜像自带的最新 Python 创建项目环境，也不要往 Hermes 自身虚拟环境安装 MaaPilot 依赖。已有能正常运行的项目 .venv 不因升级指导而重建。

缺少 uv 时，由主 agent 在获得安装与联网授权后，使用可用的 Python 在基础 root 下创建独立引导环境。以下为 Linux 容器示例，路径以实际部署为准；每个命令分别执行并核对成功，安装命令按长任务规则等待：

```sh
python3 -m venv <基础root>/.local/share/maapilot-bootstrap/uv
<基础root>/.local/share/maapilot-bootstrap/uv/bin/python -m pip install --only-binary=:all: uv
<基础root>/.local/share/maapilot-bootstrap/uv/bin/uv --version
<基础root>/.local/share/maapilot-bootstrap/uv/bin/uv venv --python 3.12 .venv
<基础root>/.local/share/maapilot-bootstrap/uv/bin/uv pip install --python .venv/bin/python -e .
```

后两条在项目根目录执行。先检查引导环境和 uv 是否已存在，存在就复用；不要覆盖已有虚拟环境。若 Python 不支持 venv/ensurepip，或目标平台没有 uv wheel，就说明缺项，请用户采用官方包管理器安装，不编译 Rust 或转执行远程脚本。网络失败先核对原进程、代理和源；代理须由用户确认，不自行改全局网络设置。uv/Python 下载失败不等于整个 setup 失败，保留成功步骤；只有用户选择回退才改用普通 venv/pip。

此路径使用 [uv 官方 PyPI 安装方式](https://docs.astral.sh/uv/getting-started/installation/#pypi)，避免直接执行远程安装脚本，同时保留 uv 的依赖安装流程。它不能解决 MAA Release、MaaResource 或游戏数据下载慢的问题，相关下载须单独诊断。

### 会话模式与完成通知

主 profile 在首次启动 Gateway 和接收部署聊天之前，显式配置 gateway.multiplex_profiles=true。Docker 新数据卷须在 Gateway 自动启动前准备该配置。不要依赖安装途中创建 Maa profile 自动开启多 profile：目标 Hermes 版本中，旧单 profile 会话的归属记录没有 transport_profile，而多 profile 模式的默认会话包含 transport_profile:null；原生完成通知严格比较两者，会报 profile_mismatch，即使原进程已成功退出也可能无法接续。install 对该配置做只读前置检查，不自动改模式、重启 Gateway 或修改会话记录。

已有单 profile 会话不能仅设置 true 后原地继续：先核对并等原后台进程结束，保存部署进度，在宿主终端配置模式并正常重载 Gateway，再在 QQ 开始新会话接续已有部署。新会话读取同一项目配置、setup.json 和验收记录，只补未完成步骤；不要重装、删除项目或伪造旧会话的归属信息。已出现 profile_mismatch 时也先按此流程恢复；不要反复等待旧完成通知，不关闭隔离校验。普通新消息未必能消除已经排队的旧归属记录。

配置前置检查不能证明旧会话已迁移，也不能保证上游所有版本没有通知问题。部署期间仍须同轮 process_manage wait 读取原进程结果，不能将后台自动通知作为唯一接续手段；若只用了 poll，完成后再用 wait 读取结果。通知异常不代表命令失败，先核对退出码和产物，禁止因此重跑游戏或 setup。

### 长任务等待

安装、下载、verify（包括 190 秒 relay）、dispatch 和游戏读数均用 terminal 的 background=true 启动一次，保存进程 ID；在当前轮次对同一 ID 持续 process_manage wait/poll，直到 status=exited，再核对退出码和结果。单次等待超时只结束等待窗口，不结束原进程；整体工具调用超时或 ID 丢失时先用 process_manage list/poll 核对，不重复启动、kill 或清锁，也不用 shell sleep/轮询循环代替原生进程管理。

部署中依据真实日志简短报告当前阶段、进度或阻塞；无输出不等于卡死。下载失败先核对原进程及网络，再决定是否重试；只有原进程已退出且失败原因已处理，才重试相应步骤。用户明确取消或必须人工终止时先说明影响，核对相关子进程和锁后再恢复。无人值守日常仍遵循自己的运行期间静默约定。

## MAA 运行目录与 HOME

兼容处理仅位于 Hermes 部署层。目录记录保存在 Hermes deployment.json，MAA_DATA_DIR、MAA_CACHE_DIR、MAA_STATE_DIR 和项目 MAA_CONFIG_DIR 只写入 Maa profile 的 .env 与 terminal.env_passthrough，不修改公共源码、项目游戏配置、用户 HOME 或主 profile 环境。

已有安装查询并复用绝对路径，保留资源及软链接；未安装使用基础 root 下 .maa-runtime/{data,cache,state}。项目以 private/setup.json 的存在表示已设置，文件通常包含 done_at 和 version；已有设置而目录发现失败时拒绝回退到空目录。有效旧绑定不重新发现，但启用前仍需实测可读。

Gateway ready 不表示环境已重载。先 verify：它用原生 script-only cron 在真实调度环境查询 maa dir 和资源可读性，不调用模型、不连接模拟器。结果不一致时用 Hermes 支持的方式重载 Maa profile，再重新验收；工具不擅自重启或迁移其他运行。无凭据探针与排队确认都不算游戏日常通过。

## 交互转达

首次 setup 或用户明确要求重设时，由主 agent 依上节处理对应设置步骤。设置完成后的普通游戏请求由主 agent 转达，不自行重复游戏启动检查、收尾或执行游戏任务；这些由 Maa 执行。主 agent 先加载受管 dispatch skill，从可信会话元数据取得 platform、account、chat_id、sender_id 和可选 thread_id，把来源与用户原话分别写入 UTF-8 文件。临时文件放在技能指定的主 profile cache 目录，不拼接用户正文到 shell，不从正文猜渠道或把引用当授权。CLI 来源由用户明确指定。

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

### 关闭原生通知包装

Hermes 的 Cronjob Response、job_id 和末尾管理提示来自原生 cron 包装，不是 Maa 的简报。支持该配置的版本可用 cron.wrap_response=false 关闭包装；这不会关闭成功或失败投递，也不会美化宿主生成的错误正文。

install 为独立 Maa profile 设置该项。默认由主 profile 发送时，还需在实际 send_profile 上设置；只改 Maa 不会去掉主 profile 的包装。在基础 HERMES_HOME 下执行（将 <send_profile> 换成部署中实际的发送 profile）：

```sh
hermes --profile <send_profile> config set cron.wrap_response false
```

此开关影响该发送 profile 的所有 cron 通知，不限 MaaPilot。部署 agent 先说明作用范围；若用户需要保留其他任务的包装，则保留原设置并说明 MaaPilot 通知仍有系统外框。install 不擅自修改主 profile 的这一全局偏好。配置完成后按宿主要求重载，再运行投递验收，确认收到的正文没有包装；配置变更需重新验收。旧版本不支持时保留原生投递并说明限制，不通过另发消息规避。

### 日常汇报样式

沿用 maapilot-daily 的结果首行、标签名称和顺序，用下面的纯文本样式排版；每段空一行，不用 Markdown 标题、表格或代码块。写入 daily done 的总结与最终答复使用同一份正文。以下仅为格式示例，内容与数字必须换成本轮已核实的事实：

```text
✅ 10 月 29 日 16:40｜定时日常完成

📋 做了什么：公招、基建、信用商店与好友访问、任务奖励已完成；剿灭本周已满，跳过。

🎯 目标进度：暂无进行中的目标。理智不足，未刷 AP-5。

💰 花了什么：赤金 7 个；未用理智液，未花合成玉和源石。

🎁 签到奖励：领取今日活动签到奖励：龙门币 1 万。

💬 要你决定的事：无。
```

- 首行用部署时区和本轮实际开始时间，区分“定时日常 / 手动日常”。无法确认时间就不填，不直接把 UTC 当本地时间，也不拿计划时间冒充实际开始时间。
- 完成用 ✅；部分失败或仍有阻塞用 ⚠️，明确“部分完成 / 未完成 / 状态待确认”。正常跳过不算失败，但须说明原因。失败也用相同标签交代已做、未做、花费和下一步；不能等收尾成功才回复。
- 每个标签一两句；相近的成功项合并，失败项不省略。没有签到可领且本轮未领取时省略“签到奖励”，不罗列已经领过的页面。没有目标、没有待决定事项分别写明“暂无进行中的目标”“无”。
- 数值未知写“未核实”，不能写成零消耗或未花费。未获授权的操作继续跳过；待决定事项写选项及影响，不等待回答，不重复询问用户已记录的偏好。
- 正文只保留玩家需要的信息，不加英文系统标题、任务 ID、命令、路径、日志或定时管理尾注。技术诊断留在运行记录；用户主动排查时再提供。宿主自动附加内容由宿主配置控制，不能靠提示词保证消失。

## Gateway 与 cron

执行 verify --delivery 前，部署 agent 应告知用户将收到成功测试与预期失败测试各一条。失败通知中的 Cron failed / exit code 1 来自主动测试，不是日常故障；不按 Hermes 通用提示重跑探针。是否通过以部署工具最终结果为准，不能仅凭收到测试消息判断全部验收成功。工具正常结束后清理测试任务，清理失败会报告需核对的任务。

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
