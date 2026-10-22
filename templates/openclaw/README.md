# OpenClaw 配置片段

把 MaaPilot 接进 [OpenClaw](https://docs.openclaw.ai) 用的片段。OpenClaw 2026.10 起使用按 agent id 索引的 `agents.entries`；旧的 `agents.list` 会由 `openclaw doctor --fix` 迁移，不要再按旧格式新增 agent。

仓库不替用户改 `~/.openclaw/openclaw.json`：把片段合并进已有配置，保留其它 agent 和渠道设置。完整接入步骤、限制和实测见 [`docs/harnesses.md`](../../docs/harnesses.md) 的「OpenClaw」一节。

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
| `${CHANNEL}` / `${DELIVERY_TO}` | 定时结果投递的渠道和会话；不需要外部通知时删掉整个 `delivery` 块 |

## 文件

- `agent-entry.json`：合并进 `openclaw.json` 的 `agents.entries.<id>`。`workspace` 必须是仓库根目录，否则 OpenClaw 读不到 `AGENTS.md` 和 `.agents/skills/`。
- `cron-daily.json`：每个日常时间点用一条 `schedule.kind = "cron"` 的重复任务。提示里已经明确不要建下一次一次性任务，避免 `maapilot-daily` 的接力规则和重复定时叠在一起。

一次性 `at` 任务曾出现到期未执行、任务残留的问题；在调度器行为确认前，MaaPilot 的 OpenClaw 接入使用重复定时。重复任务本身不会过期，但到点时 Gateway 必须在运行。

## 接入

1. 把 `agent-entry.json` 的 `agents.entries` 合并进配置，替换占位符。
2. 运行 `openclaw agents list` 或当前版本的等价命令，确认 agent 和 workspace 已载入。
3. 在 MaaPilot 仓库里打开这个 agent 的会话，完成 `maapilot-setup`。
4. 按 `config.yaml` 的 `schedule` 为每个时间点建一条 `cron-daily.json`。可以用当前版本的 `openclaw automations add` / Control UI 建，也可以直接调用 Gateway RPC；RPC 的 job 就是模板这个对象：

```sh
openclaw gateway call cron.add --params "$(cat templates/openclaw/cron-daily.json)"
openclaw gateway call cron.list --json
```

定时结果默认投递到 `delivery` 指定的会话；不需要外部通知就删掉 `delivery` 块。不要在仓库里放 `SOUL.md`、`USER.md`、`IDENTITY.md`、`TOOLS.md`、`HEARTBEAT.md` 这些 agent 自己的 bootstrap 文件；预置 workspace 可以设 `agents.defaults.skipBootstrap: true`。
