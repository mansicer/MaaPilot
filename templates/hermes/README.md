# Hermes 部署工具

由 Hermes agent 在自己的执行环境里运行。部署完成会建立空白 Maa profile、主 agent 转达入口和暂停的原生日常 cron；启用日常仍需模型、渠道和游戏 setup 等配置齐备。先读 [Hermes 专用说明](../../docs/harnesses/hermes.md)。

## 文件

- `deploy.py`：标准库一次性 CLI 适配工具，提供 install、dispatch、send-report，无调度器或后台服务。
- `deployment.example.json`：独立 root、profile、模型、时区、时间和报告目标的显式参数。`target: null` 表示渠道待配置，不能启用日常；真实目标须由用户指定，例如 `telegram:<chat_id>`，可带线程 ID。
- `maa-soul.md`、`daily-prompt.txt`：Maa 身份和 cron 指令，不改共用游戏技能。
- `compose.yaml`：仅用于开发验证的隔离容器示例，不是部署入口。

## 安装

在 Hermes 执行环境内准备完整仓库和基础 `HERMES_HOME`。从示例另存一份本机 JSON，修改实际路径和用户选择的配置。示例中的时刻只是示例，真实时间应与 `private/config.yaml` 的 schedule 一致。不要把 named profile 目录当 root。用 `HERMES_HOME` 的属主账户运行，确保该环境里的 Python 和 MaaPilot 运行环境可用。

```sh
python templates/hermes/deploy.py install --settings /absolute/path/hermes-deployment.json
```

默认不复制主配置或任何凭据。需要复用用户明确指定的模型 key 时，显式指定文件及键，可重复 `--credential-key`；只写入 Maa profile 的所选模型 key：

```sh
python templates/hermes/deploy.py install --settings /absolute/path/hermes-deployment.json --credential-file /absolute/path/model.env --credential-key OPENCODE_GO_API_KEY
```

不要把密钥放在命令参数、仓库示例或聊天中。渠道不复制：设置 `send_profile` 为主 profile 后，报告通过该 profile 现有渠道发送；该配置不创建另一个 bot 监听器。

输出会给出受管 `deployment.json` 的绝对位置，以及 Gateway、渠道和 setup 的待配置状态。生成文件在 `<root>/maapilot/<部署编号>/`；主 skill/SOUL 的编号也由部署身份确定，多部署不会互相替换。原生 profile 已存在但无本部署所有权时拒绝覆盖；中断后用相同命令恢复。

部署工具不会关闭扫描；当前 `maapilot-setup` 被安全扫描隔离，自动首次游戏设置尚受限。不要通过 external_dirs、修改扫描缓存或关闭扫描使它进入可用列表。

## 启用与升级

首次安装的日常默认暂停。用户完成游戏 setup、指定精确报告目标，并明确确认只在此宿主跑该账号日常后，使用：

```sh
python templates/hermes/deploy.py install --settings /absolute/path/hermes-deployment.json --enable --confirm-exclusive
```

此开关是用户确认的执行记录，不应由定时任务自动添加。工具核对项目 setup、schedule、Maa Gateway、任务绑定和发送 profile 的原生平台列表；条件不满足就拒绝启用。目标已填写也只代表待验证，不代表实际送达。游戏 setup 尚未完成或渠道未配置时仍可测试主 agent 的无游戏路由。

重复安装不会暂停已经启用的同配置日常。升级受管提示仅更新 prompt，不动计划时间/启用状态。时间、时区、模型或目标变更须 `--replace-schedule`；它暂停并更新受管任务，随后仍需上述 enable 条件。启用前用原生 schedule edit 重算未来时间，避免 resume 补跑历史时刻。只通过原生 CLI 修改任务，不手写 jobs.json。

## 交互与回执

部署后在主 agent 正常对话，Maa 请求由受管 skill 转达。可信渠道来源不可用时由主 agent 询问，CLI 用户可明确指定独立来源标识；后续回答保持同一来源。主 agent 必须等专长退出后再转达，外部渠道的具体元数据映射需另行验收。

日常任务只保存 local。Maa agent 在 daily done 后调用 `send-report`，通过指定发送 profile 的 `hermes send --to … --file … --json` 发送报告。outbox 按 run ID 保存回执：sent 不重发，unknown 人工核对且不自动重试；只有明确 failed 经人工核对后可加 `--retry-failed`。通知问题不重跑游戏。

## 无游戏验收

仓库单元测试使用假原生 CLI，覆盖幂等、中断恢复、配置边界、来源接续、回执和启用条件。真实验收还应检查主 agent 自动转达两轮、独立来源、Gateway 纳管、local cron 模型与工作目录，以及固定 DELIVERY 提示下显式发送是否实际执行。

无渠道时使用 mock 原生发送和原生无凭据失败路径，不声称真实送达。不执行 daily-prompt，不运行真实游戏，不复制/读取森空岛凭据。完成后删除自己的测试 cron，保留诊断数据；其他 Gateway 和任务不动。具体实测状态见 [Hermes 专用说明](../../docs/harnesses/hermes.md)。
