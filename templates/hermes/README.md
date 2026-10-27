# Hermes 部署工具

本目录只适配 Hermes。游戏流程见 AGENTS.md 与项目 skills；宿主行为与限制的唯一说明见 [Hermes 接入](../../docs/harnesses/hermes.md)。

## 文件

- deploy.py：安装、交互 dispatch、只读 report-summary、无游戏 verify；旧 send-report 仅人工恢复。
- runtime-probe.py：script-only cron 使用的无游戏目录/通知探针，不调用模型。
- deployment.example.json：用户明确选择的基础 root、profile、模型、时区、时间和精确 target。
- maa-soul.md：身份与入口；daily-prompt.txt：定时任务入口，不复制完整游戏规则。
- compose.yaml：隔离开发验证示例，不是正式部署入口。

## 安装

用基础 HERMES_HOME 的属主账户，在原先可运行 MAA 的环境准备仓库和本机 settings JSON。不要把 named profile 目录当基础 root，也不要把凭据写入参数或仓库示例。时间须与 private/config.yaml 的 schedule 一致。

```sh
python templates/hermes/deploy.py install --settings /absolute/path/hermes-deployment.json
```

默认不复制凭据。确需复用用户指定的模型 key 时，追加 --credential-file 与 --credential-key；渠道 token 不复制。部署输出受管 deployment.json 的位置，任务默认暂停。按项目 skill 完成游戏设置，检查实际 skills 扫描状态。

## 无游戏验收与启用

Maa profile 的原生通知路径必须已配置；主 profile 有渠道并不充分。可以使用自有渠道或 Hermes 支持的精确共享路由，工具不替用户改渠道归属。send_profile 仅供旧人工恢复命令使用，不控制 cron 投递。

先验证真实 cron 工具环境（不发外部消息、不操作游戏）：

```sh
python templates/hermes/deploy.py verify --manifest /absolute/path/deployment.json
```

用户确认接收测试消息后，验证环境及成功/失败两条原生通知：

```sh
python templates/hermes/deploy.py verify --manifest /absolute/path/deployment.json --delivery
```

探针不调用模型或模拟器，完成后删除自己的测试 cron，保留诊断结果。失败探针故意以非零退出测试宿主失败通知；它不是实际日常失败。排队、未验证或没有真实运行结果都不算通过。环境不匹配时先重载 Maa profile 再验收，工具不重启主 Gateway。

用户已确认唯一日常宿主、游戏设置完成、三项验收通过后，重新执行安装以显式启用：

```sh
python templates/hermes/deploy.py install --settings /absolute/path/hermes-deployment.json --enable --confirm-exclusive
```

改变时间、时区、模型或目标要加 --replace-schedule；旧 local 通知升级后任务先暂停。配置、环境和目标变化会使旧验收失效。纯提示升级保留时间和启用状态。

定时最终答复由原生 delivery 发送；不在提示中调用发送命令。人工补发前必须先核对原生投递记录，避免双发。测试和版本边界见 [验证记录](../../docs/harnesses/validation/hermes.md)，真实聊天回传与完整游戏日常需另行验收。
