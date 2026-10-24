# OpenClaw 配置片段

把 MaaPilot 接进 [OpenClaw](https://docs.openclaw.ai) 用的片段。OpenClaw 2026.10 起使用按 agent id 索引的 `agents.entries`；旧的 `agents.list` 会由 `openclaw doctor --fix` 迁移，不要再按旧格式新增 agent。

仓库不替用户改 `~/.openclaw/openclaw.json`：把片段合并进已有配置，保留其它 agent 和渠道设置。完整接入步骤、限制和实测见 [`docs/harnesses/openclaw.md`](../../docs/harnesses/openclaw.md)。

## 占位符

| 占位符 | 换成 |
|---|---|
| `${AGENT_ID}` | 给 MaaPilot 起的 agent id，例如 `maa` |
| `${REPO}` | 本仓库的绝对路径，必须和 agent 的 `workspace` 一致 |
| `${AGENT_DIR}` | 这个 agent 的状态目录，例如 `~/.openclaw/agents/maa/agent`；不要和别的 agent 共用 |
| `${MODEL}` | `provider/model`，例如 `deepseek/deepseek-flash` |
| `${HH:MM}` | 日常时间，例如 `04:40` |
| `${HOUR}` / `${MINUTE}` | 同一个时间的 24 小时制和分钟，例如 `4` / `40` |
| `${TZ}` | IANA 时区，例如 `Asia/Shanghai` |
| `${CHANNEL}` / `${ACCOUNT_ID}` / `${DELIVERY_TO}` | 用户选定且通过主动投递测试的渠道、账号和会话；保留渠道提供的原始大小写，不需要外部通知时设置 `delivery.mode="none"` |

## 文件

- `agent-entry.json`：合并进 `openclaw.json` 的 `agents.entries.<id>`。`workspace` 必须是仓库根目录，否则 OpenClaw 读不到 `AGENTS.md` 和 `.agents/skills/`。
- `cron-daily.json`：每个日常时间点用一条 `schedule.kind = "cron"` 的重复任务。提示里已经明确不要建下一次一次性任务，避免 `maapilot-daily` 的接力规则和重复定时叠在一起。
- `main-agent-delivery.md`：仓库之外另有一个主 agent 负责转到 OpenClaw 时，把这份规则并入主 agent 的说明。它只描述渠道无关的转交、单次汇报和投递核对。

一次性 `at` 任务曾出现到期未执行、任务残留的问题；在调度器行为确认前，MaaPilot 的 OpenClaw 接入使用重复定时。重复任务本身不会过期，但到点时 Gateway 必须在运行。

## 接入

1. 用 `openclaw agents add ${AGENT_ID} --workspace "${REPO}" --agent-dir "${AGENT_DIR}" --model "${MODEL}"` 注册 agent，或把 `agent-entry.json` 的 `agents.entries` 合并进配置并替换占位符。
2. 运行 `openclaw agents list` 或当前版本的等价命令，确认 agent 和 workspace 已载入。
3. 在 MaaPilot 仓库里打开这个 agent 的会话，完成 `maapilot-setup`。
4. 按 `config.yaml` 的 `schedule` 为每个时间点建一条 `cron-daily.json`。可以用当前版本的 `openclaw automations add` / Control UI 建，也可以直接调用 Gateway RPC；RPC 的 job 就是模板这个对象。`agentTurn` 没写 `toolsAllow` 时，OpenClaw 会按当前调度器策略补默认的工具权限；要收紧权限，按 OpenClaw 自己的 exec/approvals 配置做：

```sh
openclaw gateway call cron.add --params "$(cat templates/openclaw/cron-daily.json)"
openclaw gateway call cron.list --json
```

定时结果默认投递到 `delivery` 指定的会话；不需要外部通知时显式设置 `delivery.mode="none"`，避免宿主采用默认投递策略。模板用 `bestEffort=false` 暴露投递失败，最终简报只由 `delivery` 发送一次。收件人保留原始大小写，不从 session key 推导；任务执行成功和消息送达分别检查。

选 `delivery` 前先做渠道能力预检：`channels status --probe` 只能说明连接正常，不能证明该渠道允许无人值守的主动发送。用一个不操作游戏的独立任务实测，确认用户能收到、任务记录显示 delivered，再填入 `${CHANNEL}` / `${ACCOUNT_ID}` / `${DELIVERY_TO}`。刚在用户发过消息后测成功，只能证明“当前回复窗口可用”；渠道文档提到入站上下文、回复窗口或 token 时，要按受限渠道处理。若只有同一入站对话内回复、或主动发送会因长时间无互动失效的渠道，就不要把它设成唯一通知路径；要么让用户明确接受定时结果只留在会话里（`delivery.mode="none"`），要么选择一个已通过测试的渠道。

模板的 cron 提示在运行期间只返回 `NO_REPLY`，`daily done` 成功后才给一条最终总结；不要改成每完成一个后台步骤就汇报。完成接入后，经用户授权分别验收“最终只有一条”和“阻塞提问回到原始会话”，详见 [`消息汇报与阻塞问题`](../../docs/harnesses/openclaw.md#消息汇报与阻塞问题)。

如果另有主 agent 转交任务，也要把该节的交互阻塞反馈、渠道发送状态检查和重复结果静默规则加入主 agent 的说明文件；只修改 MaaPilot 的提示词无法约束主 agent 的转发行为。

不要在仓库里放 `SOUL.md`、`USER.md`、`IDENTITY.md`、`TOOLS.md`、`HEARTBEAT.md` 这些 agent 自己的 bootstrap 文件；预置 workspace 可以设 `agents.defaults.skipBootstrap: true`。
