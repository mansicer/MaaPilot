# Antigravity

在 [Antigravity](https://antigravity.google)（Antigravity IDE / 2.0 桌面端 / CLI）里用 MaaPilot 的做法。通用的规矩在 `AGENTS.md` 和 skills 里，这里只写 Antigravity 自己的。

| 项 | 做法 |
|---|---|
| 说明文件 | `AGENTS.md`（打开或设为工作区根目录时自动加载） |
| skills | `.agents/skills/`（工作区内置发现，不需要额外链接） |
| 启动检查 | 没有项目启动钩子：`AGENTS.md` 让你在会话开头自己跑一次 `maapilot doctor --brief` |
| 权限 | 没有生成项目级规则文件：`guard allow` 不会由仓库规则自动弹确认，`private/skland.json` 也拦不住，靠你守 `AGENTS.md` 硬规则；程序自己的对账与锁照样在。需要收紧或放行命令时，可在 Antigravity 设置的 Command Allowlist / Tool Execution Policy 里配置 |
| PATH | 没有启动钩子把虚拟环境加入 PATH：命令提示找不到 `maapilot` 时，整个会话写 `.venv/bin/maapilot <命令>`（Windows 上是 `.venv\Scripts\maapilot.exe <命令>`） |
| 定时 | 会话内 `schedule` 工具（`CronExpression`、`IsDaemon: true`、`ExactTiming: true`），用 `manage_task`（`list` / `kill`）查看和删除（见下） |
| 长命令 | `run_command` 超过 `WaitMsBeforeAsync` 后自动转后台，结束时会主动叫醒当前会话；不要轮询或设计时器 |
| 读图 | `read_file` 支持直接读取 `private/shots/` 下的 PNG/JPG 截图 |

## setup 时要多做的

- **把仓库根目录设为当前工作区（Active Workspace）**：Antigravity 只会发现当前工作区下的 `AGENTS.md` 和 `.agents/skills/`。如果只把文件放进默认 `scratch` 目录而未打开为工作区，技能不会自动加载。
- **Windows 安装目录**：建议将项目放在普通用户目录或独立盘目录（例如 `C:\Users\<你>\MaaPilot`），在 `run_command` 中进入该目录后执行 `.venv\Scripts\maapilot.exe <命令>`。
- **保持运行条件**：定时日常依赖 `schedule` 守护任务，到点时电脑需开机不休眠、模拟器可连接、Antigravity 与该会话保持开启。

## 定时日常

Antigravity 的 `schedule` 工具提供两类参数：`DurationSeconds`（一次性计时）和 `CronExpression` + `IsDaemon`（守护重复 cron）。其中只有 `CronExpression` 支持 `IsDaemon: true`（在当前轮结束后继续在后台常驻触发），因此按 `docs/harnesses/README.md`「每天重复的定时」的做法配置：

- **建定时**：按 `private/config.yaml` 的 `schedule` 里的每个时间点，分别调用 `schedule` 工具：
  - `CronExpression`：标准 5 段本地时间 cron，例如 `40 4 * * *`、`40 16 * * *`；
  - `IsDaemon`：`true`（确保当前对话轮结束进入空闲后仍能按时触发）；
  - `ExactTiming`：`true`（避免定时任务随机抖动推迟）；
  - `Prompt`：写成自足的一句，例如：`定时日常（04:40）：按 skill maapilot-daily 做；这是每天重复的定时，不用再建`。
- **列定时**：建之前和建完后都用 `manage_task`（`Action: "list"`）检查已有的 `schedule` 任务，避免重复创建，并核对 cron 表达式是否与设置一致。
- **改时间 / 取消定时**：用 `manage_task`（`Action: "kill"`，`TaskId` 填列表里的任务 ID）删掉旧的，再按新时间建。
- **定时绑定在当前会话且依赖常驻进程**：`schedule` 守护任务与创建它的对话及后台服务绑定；切换到新对话并重建定时时，请先取消旧对话里的定时任务，避免同一账号在两个会话中重复触发。若电脑重启或 Antigravity 服务重启，正在运行中的后台定时会被停止（不会在开机后自动恢复或补发），需回到会话重新检查并按需补建。
- **到点醒来**：确认对应的日常任务仍在列表中即可，**不再额外建下一次一次性定时**。

## 长命令

- 预计运行几分钟以上的命令（`daily run`、`maa fight`、`state depot`、`state box`）直接用 `run_command` 执行（可将 `WaitMsBeforeAsync` 设为 `10000`）。命令转入后台后，**不要**在循环里调用 `manage_task` 轮询进度，也不要另起 `schedule` 计时器；直接输出简短提示并结束本轮工具调用，系统会在命令结束时自动唤醒会话继续处理。

## 其他

- 用户偏好写进 `private/memory/`（`AGENTS.md`「用户偏好」），不要只留在对话上下文或 Antigravity 个人记忆里，确保换用 Claude Code / Codex 时仍能共用。
- 在 Antigravity 对话中查看 `private/shots/` 截图后，需同时读取命令输出的 JSON 与图片内容核对结果。
