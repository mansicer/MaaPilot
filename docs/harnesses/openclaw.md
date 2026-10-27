# OpenClaw

在 OpenClaw 里用 MaaPilot 的做法。通用规矩在 `AGENTS.md` 和 skills 里，这里只写 OpenClaw 自己的。

OpenClaw 是一个常驻的 Gateway：agent 有独立 workspace，定时由 Gateway 的任务调度器执行。MaaPilot 不生成项目级权限文件，也不需要链接 skills。

| 项 | 做法 |
|---|---|
| 说明文件 | `AGENTS.md`（OpenClaw 读 workspace 根目录的 `AGENTS.md`） |
| skills | 直接读 `.agents/skills/`，不用链接 |
| 启动检查 | 没有项目启动钩子：`AGENTS.md` 让你在会话开头自己跑一次 `maapilot doctor --brief` |
| 权限 | 没有项目级规则文件；需要收紧时用 OpenClaw 的 `tools.exec` / exec approvals |
| PATH | 已在 PATH 时写 `maapilot <命令>`；否则用 `.venv/bin/maapilot <命令>`（Windows 为 `.venv\Scripts\maapilot.exe <命令>`） |
| 定时 | Gateway 的重复 cron；每个日常时间点一条（见下） |
| 长命令 | `exec` 放后台，再用 `process` 查看；定时任务的 `timeoutSeconds` 覆盖整个 cron turn（模板为 10800 秒，即 3 小时） |
| 汇报 | 先用独立任务验证渠道能否主动送达；定时结果只走一条 `delivery`，交互任务由发起它的原始会话负责回复 |

## setup 时要多做的

1. 把本仓库注册成 OpenClaw agent，`workspace` 指向仓库根目录；配置片段见 [`templates/openclaw/`](../../templates/openclaw/)。不要与别的 agent 共用 `agentDir`。
2. 让用户按 OpenClaw 当前版本检查配置：`openclaw config validate`，再用 `openclaw agents list` 确认 workspace。
3. 在这个 agent 的会话里按 skill `maapilot-setup` 完成 MaaPilot 设置。
4. 按 `private/config.yaml` 的 `schedule`，每个时间点建一条重复 cron；使用 [`cron-daily.json`](../../templates/openclaw/cron-daily.json) 的字段结构，把 `${...}` 换成实际值。
5. 先确认用户能收到哪个渠道的**主动投递**。不要因为渠道显示 connected 就认定它能主动发送；用下面的「投递渠道预检」实测。没有可用渠道时把 cron 的 `delivery.mode` 设成 `none`，并明确告诉用户定时任务仍会执行，但结果只保存在 OpenClaw 会话里。
6. 按下文「部署验收」检查最终结果、失败通知与原始会话追问，完成后删除测试任务。
7. OpenClaw 部署时不得静默采用默认值。`maapilot-setup` 里需要用户决定的项目必须逐项询问，仍未得到回答的不能当成已确认；结束时按下面的「setup 结束报告」把实际采用值和待决定事项告诉用户。

配置片段以 OpenClaw 2026.10 的实测格式为基线；其他版本须验证。旧配置里的 `agents.list` 先用 `openclaw doctor --fix` 迁移到 `agents.entries`。仓库不替用户修改 `~/.openclaw/openclaw.json`。

### setup 结束报告

OpenClaw 的 setup 结束时，在**发起 setup 的原始会话**里发一条结果报告，不要只在内部会话里写。它不是一次性确认清单：需要用户决定的事项仍按 `maapilot-setup` 一次只问一个问题；报告汇总已经得到的回答、明确沿用的旧值和仍未解决的事项，至少包含：

- 模拟器和服务器、游戏更新协议、日常时间、日常项目、没有目标时的默认关卡。
- 每条 cron 的时间、agent、投递渠道和目标，以及下一次执行时间。
- 主动投递测试和阻塞提问回传测试的实际结果；失败时写清失败在哪一层。
- setup 过程中遇到的错误、缺少的依赖、配置文件或文档缺失、Gateway 重启、agent 数据库异常等，标明“已修复 / 仍失败 / 暂时规避”。
- 用户没有答复、仍靠默认值运行的 setup 必需项，单独列在“待决定”；不能把默认值写成用户确认，也不能把旧部署的配置静默当成新部署的确认。

**重装或已有 `setup.json` 时**：列出准备沿用的旧值；其中 setup 必需项必须得到用户明确答复，不能因为用户说了“重新安装/重新配置”就默认沿用。

建议格式：

```text
OpenClaw / MaaPilot setup 结果
1. 模拟器与服务器：……
2. 游戏更新协议：……
3. 定时任务：04:40 / 12:40 / 20:40，投递到……；下一次……
4. 日常项目：……
5. 默认关卡：……
6. 投递测试与 setup 中遇到的问题：……
待决定：……（没有就写“无”）
如需修改，直接说编号和新值。
```

报告后按用户答复修正 `private/config.yaml` 和 OpenClaw cron。`maapilot-setup` 要求用户明确回答的项目，在得到答复前仍算未确认：不写 `setup.json`，不启动会消耗游戏资源的验证日常；已完成且可回滚的环境安装和投递测试配置可以保留。报告不是批量确认，不能因为用户没有反对就把默认值当成同意。

## 定时日常

OpenClaw 用 Gateway 的**重复 cron**，不用一次性接力。2026-10-22 的 OpenClaw 2026.10.5 实测里，一次性 `at` 任务出现过到期未执行、没有 `lastRunAtMs`、任务仍残留的情况；重复任务不依赖“上一轮创建下一轮”。

- 建任务用 `openclaw cron add`，参数与 [`cron-daily.json`](../../templates/openclaw/cron-daily.json) 对齐：

  ```sh
  openclaw cron add --cron "40 4 * * *" --tz Asia/Shanghai --agent <agent id> --session isolated \
    --name "MaaPilot 日常 04:40" --message "<自足的提示>" --timeout-seconds 10800 \
    --announce --channel <渠道> --account <账号> --to <会话>
  ```

  `--best-effort-deliver` 默认关着，保持不加，投递失败才会出现在执行记录里。
- `openclaw cron list --agent <agent id> --json` 列出现有任务，`openclaw cron edit <id>` 改时间，`openclaw cron rm <id>` 删。只删 MaaPilot 自己的任务。
- `schedule.kind` 用 `cron`，并写 IANA `tz`；每个日常时间点一条。
- `sessionTarget` 用 `isolated`，避免长任务混进用户对话历史。
- 提示单看一句就能执行，并明确写“这是重复定时，不要建下一次一次性定时”。
- 提示要求按 skill `maapilot-daily` 跑日常，结束时必须执行 `maapilot daily done`。
- 到点时先确认现有时间点，不要重复建；改时间时改对应任务，删任务时只删 MaaPilot 自己的。
- Gateway 必须一直运行；任务本身不过期，但 Gateway 停了就不会按时执行。

## 长时间任务

`daily run` 通常要 20–60 分钟。`exec` 超过当前等待时间后会返回一个后台进程；cron 的 `timeoutSeconds` 覆盖整个定时 turn（`daily run`、目标部分、`daily done` 和最终汇报），模板用 10800 秒（3 小时）。不要把它和单个 MAA 日常项的 `task_timeout_min` 对齐；那个默认 90 分钟只限制单个任务。给命令设置很短的超时会把进程杀掉并可能留下孤儿任务和锁。

**定时日常启动后台进程后，不要结束当前 agent turn 去等待“完成时自动唤醒”。** OpenClaw 在部分 isolated/cron 会话里会把完成事件的目标落成没有原始投递来源的 `agent:<id>:main`，随后报：

```text
Session event origin is missing; start fresh authorized work instead
```

这时 `daily run` 可能已经成功，但 agent 没有被唤醒执行 `daily done` 和最终总结，QQ、微信或其他渠道都收不到报告。修复方式是在**同一个 cron turn 内**用 `process` 反复查看后台进程，直到退出：

- `exec` 返回后台 `sessionId` 后，立即持续 `process` 的 `poll`；进程存活期间保持当前 turn，不用最终回复、不结束 turn、不发中间消息。
- 进程退出后，再读取 `maapilot daily status` 和报告，完成目标部分、`daily done`，最后只输出一条总结交给 cron `delivery`。
- 工具提示“Completion will wake this conversation automatically”时，MaaPilot 的定时日常仍按上面的轮询方式处理。只有确认当前宿主支持可靠的外部来源回传，才可以使用自动唤醒。
- 一次性 `at` 测试任务也按同样方式工作；不要用“结束本轮，等完成通知”来测试回传。

如果已经出现 `Session event origin is missing`：

1. 不要重跑 `daily run`，也不要反复重试那条完成事件。
2. 用 `maapilot daily status` 和 `report show` 核对运行状态；如果日常已是 `done/ok` 且 `open:true`，只补执行 `maapilot daily done`。
3. 从用户当前所在的原始渠道会话重新发起一次收尾；不要从没有来源的 `agent:<id>:main` 会话恢复。
4. 成功或失败都只发送一次结果报告；收尾失败也及时报告失败原因和未完成事项。若无法恢复原会话来投递，保留报告，等下一次用户消息或下一次日常接手；绝不为了补消息重跑游戏任务。

这是 OpenClaw 当前后台完成事件的来源归属问题。上游应让完成事件继承启动它的 cron run 的 `sessionId`、渠道和投递路由；在修复前，OpenClaw 适配用“同一轮 poll 到退出”避免走到这个回传路径。

## 消息汇报与阻塞问题

OpenClaw 把“会话运行”和“消息到达用户”分开处理。MaaPilot 不假定用户接的是哪个渠道，只依赖渠道声明的能力和部署时实测的结果。

### 投递渠道预检

setup 时先看 `openclaw channels status --probe`，再让用户选一个接收定时总结的渠道。可选渠道至少满足：

| 能力 | 能做什么 | 能否用于无人值守定时 |
|---|---|---|
| 同一入站对话内回复 | 用户发消息后正常回复 | 可以用于交互任务，不能证明定时主动发送可用 |
| 持久的主动发送 | 有新任务结果时不依赖用户刚发过消息 | 可以作为 `delivery` |
| 受回复窗口、上下文或最近互动限制 | 过一段时间可能拒绝发送 | 不能作为唯一无人值守通道，除非部署方式明确保证窗口有效 |
| 状态或能力未知 | 显示 connected 但没测过主动发送 | 先测试；未通过不得承诺定时送达 |

渠道的 connected、configured、restart 后仍在线，都只说明连接状态。主动发一条独立测试消息并拿到成功结果，才算通过预检。若渠道文档写明主动发送依赖最近的入站消息、回复窗口或按账号保存的上下文，即使刚测成功也要按“受限渠道”处理：刚发过消息时能通，不代表几小时后的定时日常能通。没有满足条件的渠道时，显式使用 `delivery.mode="none"`；不要把“最近联系过的人”或主会话的历史路由当成可靠收件人。

### 交互任务与阻塞提问

交互任务遇到需要用户决定的事时，暂停依赖该决定的操作，并把“卡在哪里、一个问题、有后果的选项”交回**发起任务时的原始会话**。

- 主 agent 委托任务时要把原始会话的精确 `sessionKey` 或 conversation reference 一起传下去。不要从转小写的 session key 重建收件人，也不要默认发到 MaaPilot agent 自己的 `main` 会话。
- 接手方可以用 `sessions_send` 发起方唤醒，但这只是内部传递。发起方必须在自己的原始渠道会话里回复用户；需要显式渠道工具时，使用原始 `channel`、`accountId`、`to`。
- 发起方结束一轮后，接手方只在自己的会话里写答案，用户看不到。做不到回传时，应让发起方保持等待，或把任务作为有完成回传的子任务；不要先把发起方结束掉。
- 一次阻塞只提一个问题。用户答复后用原话送回同一个任务；提问内容和执行结果都不要重复发到第二个人或第二个会话。

主 agent 不在 MaaPilot 仓库里时，把 [`main-agent-delivery.md`](../../templates/openclaw/main-agent-delivery.md) 的规则并入它的说明文件。只改 MaaPilot 的 prompt 管不到主 agent 的转发。

启动、工具调用、会话数据库、模型或收尾报错时，也要及时在原始会话回复失败原因、已完成和未完成事项、下一步；失败回复不以 `daily done` 成功为前提。接手方无法运行时由发起方报告，不能把内部错误或 `NO_REPLY` 当成已告知用户。

### 定时任务

定时任务运行在独立会话里，**在那里写的回答不会自动出现在用户的聊天界面**。结果走 cron 的 `delivery`，并且只走一次：

- 模板用 `bestEffort=false`，让投递失败进入执行记录。不要把中断进度、后台完成通知、相同状态的轮询、旧截图当成新的定时结果。
- `cron-daily.json` 只在正常等待期间静默。成功、部分失败、启动失败、超时、无法恢复、`daily done` 收尾失败都及时生成一条最终结果；失败通知不依赖收尾成功，不能用 `NO_REPLY` 隐藏异常。说明实际进度和收尾状态，不声称未完成的任务成功。不要用 `message`、`conversations_send`、`sessions_send` 或主会话代发同一份总结。
- `status: ok` 只代表 agent turn 跑完，不代表消息送达。每次核对 `completionStatus`、`delivered` / `deliveryStatus`；`ok (not delivered)` 是失败结果，不能向用户宣称已汇报。
- 投递失败时保留已生成的总结，修正通知路径后只重发通知。绝不为了补消息重跑日常、兑换或其它游戏操作。平台返回 sent 只说明平台接收，不代表用户已读或批准。
- 不使用自动猜测的备用渠道。用户明确指定了备用渠道和目标时，才为同一条结果建立一条去重后的备用通知；否则让失败保持可见，等用户回来处理。

提示词只有在 agent turn 能运行时才生效。会话数据库、模型或 Gateway 故障导致 turn 根本无法执行时，部署验收必须另测宿主错误通知能否通过已配置渠道送达；未通过应标明此限制，不能承诺提示词能覆盖宿主故障。

### 部署验收

经用户同意，用不操作游戏的独立任务做三项验收（结果和提问测试禁用所有工具，失败测试仅模拟错误）：

1. 最终结果测试：任务运行中故意等待一段时间并接收一次后台完成通知，确认用户最多收到一条最终结果，中间没有“正在跑”“还在等”这类消息。
2. 阻塞提问测试：让任务只返回一个模拟问题，确认它回到发起任务的原始会话，并且只出现一次；再回复一次，确认答复送回了同一任务。

3. 失败通知测试：分别模拟启动失败和收尾失败，确认没有成功的 `daily done` 也及时收到一条失败结果，且不重跑任务；另核对宿主无法启动 agent turn 时的错误通知路径。

同时查看 OpenClaw 的任务执行记录和渠道消息标识。发送成功但本地没有出站记录、超时导致状态不明、或只有一条内部交回记录时，都算未验收。测试任务完成后删除，不要拿真实日常或兑换任务做投递重试。

OpenClaw 或渠道升级后重跑这些验收。遇到会话存储、Gateway 或渠道错误时先检查版本、健康状态和实际运行的二进制；不要手改会话数据库。回复窗口受限的渠道在长时间空闲后可能要求用户先发一条消息刷新状态，这种渠道不能单独承担无人值守通知。

## 容器

- agent 的 workspace **必须**是仓库根目录，`AGENTS.md` 和 `.agents/skills` 都在那里。
- 模拟器在宿主机、agent 在容器里时，adb 用 `host.docker.internal:<端口>`（MuMu 12 默认 16384，每多开一个实例加 32），模拟器里要打开 ADB 调试。
- 容器重建或宿主机重启后，先确认 `daily run` 前设备仍能连接。
- `.openclaw/` 是 OpenClaw 往 workspace 写的状态，已在 `.gitignore`。
- 不要往仓库放 `SOUL.md`、`USER.md`、`IDENTITY.md`、`TOOLS.md`、`HEARTBEAT.md`；缺了不影响运行，要阻止生成就设 `agents.defaults.skipBootstrap`。

## 验证与版本

接入格式以实际目标版本的 validate、CLI 帮助和 Gateway 返回为准，不承诺所有后续版本兼容。升级后重跑上述无游戏验收；历史实测和已知问题见 [OpenClaw 验证记录](validation/openclaw.md)。测试数量、连接状态和提示词字符串都不能替代行为与实际送达验收。
