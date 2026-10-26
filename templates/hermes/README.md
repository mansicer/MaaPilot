# Hermes 接入模板

先读 [Hermes 接入说明](../../docs/harnesses/hermes.md)。这些模板不修改 OpenClaw、Claude Code、Codex，也不实现另一套调度器。

## 部署

- `compose.yaml`：独立 Hermes Gateway 和命名数据卷，不发布端口、不挂载宿主机 Docker socket；用独立 Compose 项目名，例如 `maapilot-hermes-test`。不设置固定 container_name，避免撞名。
- `config.example.yaml`：只合并所需字段到 Hermes profile。`external_dirs` 可用于没有项目技能发现的版本；支持项目发现时优先使用 `hermes skills trust <仓库绝对路径>`，然后从仓库目录核对技能列表。不要同时维护技能副本。
- `daily-prompt.txt`：真实日常的提示模板，只在用户完成 MaaPilot setup 并确认切换定时宿主后使用；部署测试不要直接创建真实日常任务。

设置 `HERMES_IMAGE`（官方 `nousresearch/hermes-agent` 的明确版本或 digest）、`HERMES_TIMEZONE`、`MAAPILOT_WORKSPACE` 后执行：

```sh
docker compose -p maapilot-hermes-test -f templates/hermes/compose.yaml up -d
docker compose -p maapilot-hermes-test -f templates/hermes/compose.yaml exec hermes hermes --version
```

使用官方镜像自带入口，不覆盖 `/init`。首次还需配置模型、技能和 cron 工具集；Gateway 容器启动不代表已经能跑 agent。MaaPilot 和 MAA 依赖另按 setup 安装进容器可访问的 Linux 环境。容器重建前应保留该环境或建立派生镜像。

## 创建日常（在 Hermes 的执行环境内）

先确认 profile 时区，列出已有任务；下面仅用 04:40 示范，实际时间来自 MaaPilot 配置。将完整 `daily-prompt.txt` 内容作为第二个位置参数，把仓库路径和用户指定的投递目标替换为实际值。

```sh
hermes cron list
hermes cron create "40 4 * * *" "<daily-prompt.txt 的完整内容>" --name "MaaPilot 日常 04:40" --workdir /absolute/path/to/MaaPilot --deliver local
hermes cron list
hermes cron status
```

`local` 只保存输出，不通知聊天。需要通知时将它替换为经过实测的 `平台:会话目标`。不要把 OpenClaw 的 JSON 字段或 `--tz`、`--session`、`--timeout-seconds` 参数套用到这里。CLI 参数以所部署版本的 `--help` 为准。

## 无游戏测试

在独立副本中测试，不挂载真实账号数据，不执行 daily-prompt：

1. 核对版本、技能发现、cron 创建/读回/暂停/删除与时区。
2. 在 Hermes scripts 目录放只输出测试标记的脚本，用 `--no-agent`、`--deliver local` 创建短期测试任务，确认调度器确实执行且留下输出；完成后删除任务。
3. 配置用户选定的模型后，创建普通 agent 测试：读取副本中的说明与技能，运行无害后台命令，等到退出后再输出标记。检查中途没有提前结束。
4. 测试指定渠道的单次投递和交互问题回到原会话；没有渠道配置时明确记录未验证。
5. 完整游戏日常需另按开发规矩确认模拟器空闲和操作授权，再验证收尾与报告。

停止测试用同一 Compose 项目的 `down`，保留数据卷作为诊断记录；只删除自己创建的测试 cron，不清理其他 profile 或容器。
