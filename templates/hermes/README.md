# Hermes 部署工具

本目录只适配 Hermes。游戏流程见 AGENTS.md 与项目 skills；宿主行为与限制的唯一说明见 [Hermes 接入](../../docs/harnesses/hermes.md)。

## 文件

- deploy.py：安装、项目环境 runtime-exec、交互 dispatch、定时 run-daily、只读 report-summary、daily-status / resolve-daily 运行状态核对、无游戏 verify；旧 send-report 仅人工恢复。
- runtime-probe.py：无游戏目录、等待及通知探针；脚本本身不调用模型，verify --relay 的父子 agent 会调用模型。
- deployment.example.json：用户明确选择的基础 root、profile、模型、时区、时间和精确 target。
- maa-soul.md：Maa 身份和交互返回约定；daily-prompt.txt：Maa 的定时游戏任务；relay-prompt.txt：发送 profile 的定时转达任务。
- compose.yaml：隔离开发验证示例，不是正式部署入口。

## 安装

首次启动 Hermes Gateway、连接聊天渠道并开始部署聊天之前，在基础 HERMES_HOME 的属主终端运行（确保该终端使用的基础 HERMES_HOME 与 Gateway 一致）：

```sh
hermes --profile default config set gateway.multiplex_profiles true
```

先配置 `gateway.multiplex_profiles: true`，再启动 Gateway 并接收部署聊天；不能依赖安装途中创建 Maa profile 自动切换模式。install 会只读检查该配置，未显式开启就拒绝创建 profile，不替正在运行的会话切换模式。容器或系统服务若自动启动 Gateway，须在首次启动前准备实际使用的配置文件。

已有单 profile 会话的迁移与后台通知恢复见 [会话模式与完成通知](../../docs/harnesses/hermes.md#会话模式与完成通知)。仅修改配置文件不代表当前 Gateway 或旧会话已安全迁移。

用基础 HERMES_HOME 的属主账户，在原先可运行 MAA 的环境准备仓库和本机 settings JSON。不要把 named profile 目录当基础 root，也不要把凭据写入参数或仓库示例。时间须与 private/config.yaml 的 schedule 一致，发送 profile 的 timezone 须与部署时区一致。

复制 deployment.example.json 后，必须替换 root 和 workdir 两个占位路径：root 是现有 Hermes 的基础 HERMES_HOME，workdir 是该运行环境中 MaaPilot 仓库的绝对路径。不要把示例路径当默认目录，也不要在容器内填写只能由宿主机访问的路径。profile、模型、时区、日常时间和 target 也按实际部署填写；target 为 null 时任务保持暂停，不向外投递。

项目环境默认仍用 uv + Python 3.12。缺少 uv 时先按 [Python 环境与 uv](../../docs/harnesses/hermes.md#python-环境与-uv) 在独立引导环境安装，不默认退回 pip，不把项目依赖装入 Hermes 自身环境。

```sh
python templates/hermes/deploy.py install --settings /absolute/path/hermes-deployment.json
```

默认不复制凭据。确需复用用户指定的模型 key 时，追加 --credential-file 与 --credential-key；渠道 token 不复制。部署输出受管 deployment.json 的位置，任务默认暂停。主 agent 按公共 maapilot-setup 完成整个首次设置，使用 Maa 的项目环境执行安装、连接模拟器、试启动和读账号等步骤；设置完成并通过交接验收后，才把后续游戏请求交给 Maa。具体顺序见 [部署与设置](../../docs/harnesses/hermes.md#部署与设置)。

创建项目虚拟环境、安装适配层后，主 agent 通过以下入口执行项目命令：

```sh
python templates/hermes/deploy.py runtime-exec --manifest /absolute/path/deployment.json -- maapilot doctor --offline
```

将 `--` 后的完整命令替换为当前获准的 setup 步骤，如 `maapilot tools install maa-cli`、`maapilot maa startup` 或 `maapilot state depot`。入口使用部署记录中的目录绑定、项目虚拟环境和 private 目录，保留调用方身份，不切换 HOME、不加载 Maa 的凭据；只保证项目运行环境一致，真实 Maa profile 仍需 verify / --relay 验收。命令不经 shell 解释，退出码与输出直接返回；入口不自动安装、写 setup.json 或启用定时，也不替代工具审批。长任务用 terminal(background=true) 启动，持续等待同一进程。

已有 setup.json 时升级适配层或开启新会话不重复 setup；用户明确要求重设时才调整对应项。首次设置尚未完成就继续原问答。定时发现缺少设置只报告阻塞，不问问题或写 setup.json。检查实际 skills 扫描状态。

## 无游戏验收与启用

验收前确认 send_profile（默认 main_profile）的模型和渠道、Maa 的模型均可用。主 profile 转达与 Maa 直接承载两种模式的返回路径见 [结果与投递](../../docs/harnesses/hermes.md#结果与投递)。

验收前按 [关闭原生通知包装](../../docs/harnesses/hermes.md#关闭原生通知包装) 配置实际发送 profile，避免英文标题、任务 ID 和管理尾注；该设置影响此 profile 的全部 cron 通知。正文样式见同一文档的“日常汇报样式”。已有部署需重新 install 更新任务提示。

先验证 Maa 的真实 cron 工具环境（不发外部消息、不操作游戏）：

```sh
python templates/hermes/deploy.py verify --manifest /absolute/path/deployment.json
```

先告知用户：接下来会收到成功通知和预期失败通知各一条；失败通知中的 Cron failed / exit code 1 是主动验收，不是日常故障，没有操作游戏，无需按通知建议重跑。用户确认接收测试消息后，验证 Maa 环境及发送 profile 的成功/失败两条原生通知：

```sh
python templates/hermes/deploy.py verify --manifest /absolute/path/deployment.json --delivery
```

以上脚本探针不调用模型或模拟器，完成后删除自己的测试 cron，保留诊断结果。失败探针故意以非零退出测试宿主失败通知；它不是实际日常失败。排队、未验证或没有真实运行结果都不算通过。环境不匹配时先重载 Maa profile 再验收，工具不重启主 Gateway。

主 profile 转达模式还须运行完整链路验收（会调用真实主/Maa 模型，包含 190 秒无害等待；结果仅保存在本地，不发送外部消息）：

```sh
python templates/hermes/deploy.py verify --manifest /absolute/path/deployment.json --relay
```

它通过主 profile 的真实 agent cron 调用同一 run-daily 入口进入 Maa，由 Maa 调用只读目录/等待探针，然后核对 Maa 返回值和主 agent 的最终答复。仅启动了进程、只完成 script-only 探针或只在提示里出现测试标识都不算通过。验收不能替代真实游戏流程及外部渠道验收；模板禁止验收操作游戏。

各次 verify 会保留同配置下其他已通过的项目；安装、验收及启用共用状态写锁，不能并行操作同一 root。用户已确认唯一日常宿主、游戏设置完成、环境与成功/失败通知通过，且转达模式的 relay 验收也通过后，重新执行安装以显式启用：

```sh
python templates/hermes/deploy.py install --settings /absolute/path/hermes-deployment.json --enable --confirm-exclusive
```

改变时间、时区、模型或目标要加 --replace-schedule；旧版 Maa 定时迁移时先暂停旧任务，保留历史；新任务保持暂停，重新验收后显式启用。旧任务运行中不会迁移，不能把旧、新两套任务同时启用。任一参与 profile 的配置、环境和目标变化会使旧验收失效。纯提示升级保留时间和启用状态。

人工补发按 [故障与恢复](../../docs/harnesses/hermes.md#故障与恢复) 核对原生投递记录。测试和版本边界见 [验证记录](../../docs/harnesses/validation/hermes.md)，真实聊天回传与完整游戏日常需另行验收。


## 运行中断与诊断

主任务不再等待不代表 Maa 已停止。先用原 terminal 的 process_manage 检查，再读取适配层运行状态：

```sh
python templates/hermes/deploy.py daily-status --manifest /absolute/path/deployment.json
```

running_or_starting 表示调用仍持有锁；unknown 表示调用失联，子进程可能继续操作游戏。保留原进程 ID，不按 PID 盲目结束进程、不启动另一轮。只有人工核对原 terminal、Maa 子进程和游戏锁均已停止后，才解除未知状态：

```sh
python templates/hermes/deploy.py resolve-daily --manifest /absolute/path/deployment.json --confirm-stopped
```

这只记录人工核对，不停止进程、不收尾或重跑游戏。无游戏 relay 探针的进程记录在对应 relay-probes 目录，超时后同样先核对，不通过重跑验收来清理进程。

失败的请求目录保留权限受限的 stderr.txt 与 diagnostic.json；原始错误可能包含敏感信息，只供本机排查，不能复制给聊天渠道。用户回复只使用认证、限流、网络、环境、权限等固定错误类别。子进程退出只表示调用结束，不证明游戏日常已成功收尾。
