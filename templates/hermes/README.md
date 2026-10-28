# Hermes 部署工具

本目录只适配 Hermes。游戏流程见 AGENTS.md 与项目 skills；宿主行为与限制的唯一说明见 [Hermes 接入](../../docs/harnesses/hermes.md)。

## 文件

- deploy.py：安装、交互 dispatch、定时 run-daily、只读 report-summary、daily-status / resolve-daily 运行状态核对、无游戏 verify；旧 send-report 仅人工恢复。
- runtime-probe.py：无游戏目录、等待及通知探针；脚本本身不调用模型，verify --relay 的父子 agent 会调用模型。
- deployment.example.json：用户明确选择的基础 root、profile、模型、时区、时间和精确 target。
- maa-soul.md：Maa 身份和交互返回约定；daily-prompt.txt：Maa 的定时游戏任务；relay-prompt.txt：发送 profile 的定时转达任务。
- compose.yaml：隔离开发验证示例，不是正式部署入口。

## 安装

用基础 HERMES_HOME 的属主账户，在原先可运行 MAA 的环境准备仓库和本机 settings JSON。不要把 named profile 目录当基础 root，也不要把凭据写入参数或仓库示例。时间须与 private/config.yaml 的 schedule 一致，发送 profile 的 timezone 须与部署时区一致。

```sh
python templates/hermes/deploy.py install --settings /absolute/path/hermes-deployment.json
```

默认不复制凭据。确需复用用户指定的模型 key 时，追加 --credential-file 与 --credential-key；渠道 token 不复制。部署输出受管 deployment.json 的位置，任务默认暂停。主 agent 可以边询问边部署：先确认 Hermes 参数，安装独立 profile 和路由；再通过同一来源 dispatch，让 Maa 按 maapilot-setup 逐项完成首次游戏设置。主 agent 在原会话提问并转达回答，不另做一套游戏设置。

已有 setup.json 时升级适配层或开启新会话不重复 setup；用户明确要求重设时才调整对应项。首次设置尚未完成就继续原问答。定时发现缺少设置只报告阻塞，不问问题或写 setup.json。检查实际 skills 扫描状态。

## 无游戏验收与启用

验收前确认 send_profile（默认 main_profile）的模型和渠道、Maa 的模型均可用。主 profile 转达与 Maa 直接承载两种模式的返回路径见 [结果与投递](../../docs/harnesses/hermes.md#结果与投递)。

先验证 Maa 的真实 cron 工具环境（不发外部消息、不操作游戏）：

```sh
python templates/hermes/deploy.py verify --manifest /absolute/path/deployment.json
```

用户确认接收测试消息后，验证 Maa 环境及发送 profile 的成功/失败两条原生通知：

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
