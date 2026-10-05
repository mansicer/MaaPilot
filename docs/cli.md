# `maapilot` 命令契约

给实现命令的开发者和写 skill 的人：每个命令的参数、做什么、输出、碰不碰模拟器、要不要用户确认。设计依据见 [design.md](design.md)。

## 通用约定

- **调用形式**：agent 在仓库根目录用 `maapilot <命令> …`（不写 `./`、不用全路径、一次一条命令，权限规则按这个形式匹配）。
  `maapilot` 装在项目的虚拟环境 `.venv` 里（setup 时用 uv 建，见 skill `maapilot-setup`）；启动钩子 `bin/session-start` 把 `.venv/bin` 加进本次会话的 PATH，所以直接写 `maapilot`。定时任务用 `.venv/bin/maapilot` 的完整路径。
- **输出**：stdout 只有一个 JSON 信封，进度与日志写 stderr：

  ```json
  {"ok": true, "command": "state depot", "data": {…}, "error": null}
  {"ok": false, "command": "maa fight", "data": {…或 null}, "error": {"code": "maa_failed", "message": "…", "hint": "…"}}
  ```

  失败时 `data` 可以带有用的部分结果（例如对账差额、失败截图路径）。`hint` 是下一步该做的事。
- **退出码**：

  | 码 | 含义 | agent 该怎么做 |
  |---|---|---|
  | 0 | 成功 | 汇报 |
  | 1 | 执行了但失败 | 看 `error.code` 与 `data`；多半截图看看（`maapilot-recovery`） |
  | 2 | 请求不能执行（参数错、找不到目标、文件写错） | 改请求，原样重试没用 |
  | 3 | 要用户处理（货币对账不过、对账停机中） | 转述给用户，等用户说 |
  | 4 | 模拟器被占用 | 稍后再试；`lock status` 看谁占着 |
  | 5 | 环境问题（没配置、adb 连不上、maa-cli 没装） | `doctor` |

- **坐标**：所有坐标、区域都是 1280×720 参考坐标系（截图也按这个尺寸存），与设备真实分辨率无关（要求 16:9）。
- **锁**：操作模拟器的命令（点击、滑动、输入、MAA 任务、要进页面的读取）自动拿模拟器锁（`private/lock.json`），拿不到退出码 4。
  只读的 `screen shot` / `screen ocr` 不拿锁：用不开输入服务的只读连接，别人（MAA、日常）正在操作时也能截图看看。持锁的进程（`daily run`）起的子进程
  （无头 `claude -p`）通过环境变量 `MAAPILOT_LOCK_TOKEN` 继承锁，子进程里的命令不会被挡（见 `maapilot/crew/lock.py`）。
- **记录**：每个碰模拟器的命令结束时在 `private/log/<日期>.jsonl` 追加一行（见[文件](#private-文件)）。
- **物品与干员名**：参数里用游戏里的中文名；输出里物品同时给 `id` 与 `name`。
- **长命令**：`daily run` 要 20–60 分钟，`maa fight`、`state box` 等可能几分钟。Claude Code 的 Bash 超时最长 10 分钟：
  预计超过几分钟的用 Bash 的后台运行（`run_in_background`），结束时会收到通知。

## 命令总览

| 命令 | 碰模拟器 | 权限 | 负责 |
|---|---|---|---|
| `doctor [--brief] [--offline] [--fix]` | 除 `--brief`/`--offline` 外只读 | 放行 | A |
| `screen shot / ocr` | 只读，**不拿锁** | 放行 | A |
| `input tap / swipe / back / text` | 操作 | 放行 | A |
| `maa <任务>` | 操作 | 放行 | A |
| `maa why` | 否（只读 MAA 的日志） | 放行 | A |
| `state depot / box / sanity` | 只读（要进页面） | 放行 | A |
| `state show` | 否 | 放行 | A |
| `data …` | 否（`sync` 联网） | 放行 | A |
| `calc need / craft / stages` | 否 | 放行 | A |
| `lock status` / `lock release` | 否 | 放行 / **询问** | A |
| `daily watch`、`report seen` | 否（只读 / 记账） | 放行 | B |
| `reset [--dry-run] [--memory]` | 否 | **询问** | B |
| `guard snapshot / check / status` | 只读 | 放行 | A |
| `guard allow` | 否 | **询问** | A |
| `goals list / add / update / remove` | 否 | 放行 | B |
| `op upgrade / mastery`、`craft` | 操作 | 放行 | B |
| `daily run / status` | `run` 操作 | 放行 | B |
| `daily schedule / unschedule` | 否（改 crontab） | **询问** | B |
| `report show / latest / list / write` | 否 | 放行 | B |

"放行"的命令不弹确认；"询问"的每次都弹 Claude Code 的权限确认，无头运行（`claude -p`）里一律被拒。

---

## doctor —— 环境检查

`maapilot doctor [--brief] [--offline] [--fix]`、`maapilot doctor --hook`（只给启动钩子用）

逐项检查，每项给出结果和修复建议。不拿锁（只读）。

- 默认：全部检查，包括连模拟器截一张图。
- `--offline`：不碰模拟器、不联网的那几项。
- `--fix`：顺手做不需要问用户的本地修复：从 maa-cli 资源复制 OCR 模型、把 maa-cli 配置模板（`templates/maa-config/` 的
  `cli.toml`、`profiles/default.toml`）装进本项目的 maa-cli 配置目录 `private/maa-config/`（已有的不覆盖）、缺数据表时 `data sync`。
  装软件（Homebrew、maa-cli、adb）不在这里做：由 agent 问过用户后在终端里做。
- `--brief`：给启动钩子用，**不碰模拟器、不联网、1 秒内返回**，不做逐项检查，只给会话开头要知道的事（见下）。

检查项（`data.checks[].name`）；每项 `{name, ok, detail, fix}`，有的另带字段（`candidates`、`resolution`、`old_entries`…）。
标 `required: false` 的（`claude`、`game`、`schedule`、`setup`）只是提示，不影响 `ok_all`。有必需项没通过时退出码 5（`checks_failed`，`data` 照常给全）。

| name | 查什么 | 修复建议 |
|---|---|---|
| `config` | `private/config.yaml` 在、能解析、`emulator.address` 已填 | 做 setup |
| `adb` | adb 程序能跑（版本） | `brew install --cask android-platform-tools` |
| `emulator` | `adb connect` + 设备 online；地址没填或连不上时列出在线的候选（`adb devices` 与常见端口的 `adb connect` 探测） | 开模拟器、打开 ADB 调试、填地址 |
| `screen` | `adb exec-out screencap` 能截图；分辨率是 16:9 且不小于 1280×720 | 模拟器设置里改分辨率 |
| `input` | 模拟器里有 `input` 命令（能发点击）；不真的点 | 换 / 重装模拟器 |
| `maa_cli` | `maa version`：maa-cli 与 MaaCore 版本；`maa dir resource` 下有资源；`path` 是 maa 的绝对路径 | `brew install MaaAssistantArknights/tap/maa-cli`、`maa install` |
| `claude` | 找得到 `tools.claude`（定时日常叫无头 agent 用）；`path` 是绝对路径（setup 写进 `tools.claude`）；`trusted`：Claude Code 信任过这个文件夹没有（`~/.claude.json`，没信任时无头 `claude -p` 忽略项目的放行规则） | 在 `tools.claude` 写绝对路径；在这个文件夹里交互地启动一次 claude、接受信任提示 |
| `maa_config` | `private/maa-config/` 里有 `cli.toml`、`profiles/default.toml` | `doctor --fix` |
| `ocr_model` | `resource/model/ocr/` 下 `det.onnx`、`rec.onnx`、`keys.txt` | `doctor --fix`（从 maa-cli 资源的 `PaddleOCR` 复制） |
| `game_data` | `data/` 下数据表齐全；超过 30 天没同步算没通过 | `doctor --fix` 或 `data sync` |
| `game` | 游戏是否在前台（按 `server` 推出的包名看 `dumpsys window`）；`package` 是前台应用的包名；`installed` 是模拟器里装着的明日方舟客户端 `[{package, server}]`（`pm list packages`，游戏没开也能看；查不了为 `null`）。setup 据这两样推服务器；前台或装着的是别的服时提示改 `server`，一个都没装时提示去装 | `maa startup` |
| `schedule` | crontab 里有没有本项目的定时日常；有没有旧版 MaaPilot 的定时任务（`maapilot job tick`、`bin/daily.sh`，列在 `old_entries`） | `daily schedule` |
| `setup` | `private/setup.json` 在不在 | 做 setup |

`--offline` 跳过 `emulator`、`screen`、`input`、`game`（其余几项只跑本机命令）。

```json
{"ok_all": false,
 "setup_done": true,
 "offline": false,
 "checks": [{"name": "adb", "ok": true, "detail": "Android Debug Bridge 1.0.41", "fix": null, "path": "/opt/…/adb"},
            {"name": "emulator", "ok": false, "detail": "127.0.0.1:5555 连不上：…", "fix": "开模拟器…",
             "candidates": ["emulator-5554"]}],
 "fixed": [{"name": "ocr_model", "done": ["det.onnx", "rec.onnx", "keys.txt"], "detail": "…"}]}   // 只有 --fix 时
```

`--brief` 的 `data`（退出码总是 0）：

```json
{"setup_done": false,
 "goals_active": 2,
 "guard_stop": null,
 "last_daily": {"at": "2026-09-30T04:40:12+08:00", "ok": true, "report": "/…/private/reports/2026-09-30-0440.md"},
 "schedule": {"installed": true, "times": ["04:40", "16:40"]},
 "next": "setup",
 "unseen_problems": [{"run_id": "20261004-1640", "started_at": "…", "status": "stopped", "report": "…",
                      "lines": ["10 月 4 日 16:40 的定时日常因为货币对账停下了，有问题。", "…"]}],
 "lines": ["MaaPilot 还没设置：用户一开口就进入 maapilot-setup（…）"]}
```

`next`：`"setup"`（没有 `setup.json`，用户一开口 agent 就进入 `maapilot-setup`）、`"guard"`（对账停机中，先告诉用户）或 `null`。
`lines` 是给会话开头看的 3–5 行中文摘要。`last_daily` 取 `private/reports/` 里最新的 `.json`（读它的 `finished_at` / `at` / `started_at` 与 `ok`）；
`goals_active` 用 `maapilot.flows.goals.active_count()`。

## screen —— 截图与识别（只读，不拿锁）

不拿模拟器锁、不记进对账的 dirty：MAA 或日常正在操作时也能用。MaaFramework 连接时输入方式设为 `Null`（`Game(readonly=True)`），
设备上不起 maatouch / minitouch，也不发任何点击，所以不会和正在跑的任务抢输入；截图方式和平时一样（`RawWithGzip | Encode`，缩到 1280×720）。

`maapilot screen shot [--name <名>]`
截一张图存到 `private/shots/<YYYYmmdd-HHMMSS>[-<名>].png`（1280×720）。agent 用读图工具看它：`path` 是绝对路径。

```json
{"path": "/…/private/shots/20260930-214001-home.png", "width": 1280, "height": 720, "at": "…"}
```

`maapilot screen ocr [--roi X Y W H] [--find <正则>] [--name <名>]`
截图并识别文字（MaaFramework + PaddleOCR）。`--roi` 只识别一块（越界退出码 2 `bad_roi`）；`--find` 只返回匹配的。截图同样存下。

```json
{"path": "/…/private/shots/….png", "roi": [0, 0, 1280, 720],
 "hits": [{"text": "终端", "box": [1080, 150, 60, 30], "center": [1110, 165], "score": 0.99}]}
```

连不上模拟器或截不了图退出码 5（`screen_failed`）。

## input —— 操作

| 命令 | 做什么 |
|---|---|
| `input tap X Y` | 点一下 |
| `input swipe X1 Y1 X2 Y2 [--ms 毫秒]` | 滑动（默认 500 ms） |
| `input back` | 安卓返回键 |
| `input text "<文字>"` | 往当前输入框输入 |

`data`：`{"done": true}`。操作之后不自动截图：要看结果就 `screen shot`。坐标超出 1280×720 退出码 2（`bad_point`）；
设备上没做成退出码 1（`input_failed`）。
这里没有页面拦截；合成玉、源石的防线是[对账](#guard-货币对账)。

## maa —— 调 MAA

`maapilot maa <任务> [参数] [--params '<JSON>']`

用 maa-cli 跑一个 MAA 任务（连 `config.yaml` 的模拟器地址、客户端用 `server`）。`--params` 覆盖 MAA 的任务参数，
参数名按 `maapilot/knowledge/maa_protocol.py` 的白名单校验（写错退出码 2 `bad_params`）。
**源石永远是 0**：任何来源（`--params`、`daily.params`）写了 `stone` 都退出码 2（`forbidden_param`），Fight 固定写 `stone = 0`。
**理智液只听 `--medicine N`**：`medicine`、`medicine_expire_days`、`expiring_medicine`、`use_sanity_potion` 同样不能从 `--params` / `daily.params` 进来。

| 任务 | 参数 | MAA 任务 |
|---|---|---|
| `startup` | | StartUp（启动游戏、登录）；成功后再用 MaaFramework 回首页（游戏已经开着时 MAA 不管停在哪页），`data.home`：`true` 到了 / `false` 没回去 / `null` 连不上 |
| `recruit` | | Recruit（公招；默认 4 次、刷新、3★4★ 自动招、5★6★ 留给用户） |
| `infrast` | | Infrast（基建换班、收菜；无人机加速贸易站） |
| `mall` | | Mall（信用商店、访问好友；优先买招聘许可，不买家具零件） |
| `award` | | Award（任务奖励、邮件、免费单抽） |
| `fight <关卡>` | `[--times N] [--medicine N] [--expiring-medicine [天]]` | Fight（刷关；`--times` 默认打到理智用完；`--medicine` 默认 0；`--expiring-medicine` 只用 N 天内过期的，不写天数为 2） |
| `annihilation` | `[--stage <剿灭关>] [--times N] [--medicine N]` | Fight（剿灭；默认当期剿灭、打 1 次；`--stage` 也可写 龙门外环 / 龙门市区 / 切尔诺伯格） |
| `copilot <作业>` | `[--formation] [--loop-times N]` | `maa copilot`（作业：`maa://<id>` 或本地 JSON；`--formation` 自动编队；不用理智液） |
| `closedown` | | CloseDown（关游戏） |

`config.yaml` 的 `daily.params.<任务>` 会先合进默认参数，`--params` 最后合。理智液（`--medicine N`）：用户第一次同意后按记忆办。
认得出的资源本 / 芯片本今天不开时不跑 MAA，直接退出码 1（`not_open`，`data.open_weekdays`）。

maa-cli 用本项目自己的配置目录 `private/maa-config/`（经 `MAA_CONFIG_DIR` 传给 maa-cli，不动用户原有的 maa-cli 配置）；
任务文件每次生成为 `private/maa-config/tasks/maapilot-<任务>.toml`，maa-cli 的日志在 `private/log/maa/<YYYYmmdd-HHMMSS>-<任务>.log`。
跑的时候持锁并每分钟心跳（锁不会因为刷图时间长而过期）。maa-cli 自成进程组（超时时整组结束）；`maapilot maa` 自己被叫停
（SIGTERM / SIGINT / SIGHUP，例如日常超时杀掉它）时先结束 maa-cli 的进程组再退出（退出码 128+信号），正常或出错退出时 maa 还在也会结束掉。

```json
{"task": "fight", "stage": "1-7", "ok": true, "duration_s": 312,
 "tasks": [{"name": "fight", "status": "Completed"}],
 "times_done": 12, "sanity_used": 72, "sanity": {"current": 10, "max": 135}, "medicine_used": 0,
 "drops": [{"id": "30012", "name": "固源岩", "count": 14}],
 "offline": false,
 "log": "/…/private/log/maa/20260930-214500-fight.log"}
```

`fight` / `annihilation` 结束时用日志里最后一次的理智更新 `private/state/sanity.json`（`source: "maa-log"`）。
`annihilation` 另有 `orundum_gained`（这次掉的合成玉）、`week`（本游戏周累计 `{runs, orundum}`）和 `weekly_capped`（本周合成玉已经拿满：打了、认出了掉落却没有合成玉，或累计到 1800）。
每周记录在 `private/state/annihilation.json`（`{week, runs, orundum, capped, failures, blocked?}`，周一 04:00 清零）；`weekly_capped` 之后 `daily run` 跳过剿灭（`tasks` 里这一项 `skipped`）。
日常里剿灭连续 2 次没做成（接管也没救回来）记 `blocked`，这周的日常也跳过；打成一次 `failures` 清零。

失败退出码 1，`error.code`：`maa_failed`（`data.log`、`data.errors` 是日志里最后几行错误）、`offline`（日志里有 GameOffline：掉线 / 服务器维护，
重登或稍后重试，不用接管）、`not_open`（关卡今天不开）。maa-cli 找不到退出码 1（`maa_failed`，message 是启动失败的原因）。

**失败时取证**：maa 跑完而没成功时（`maa_failed`、`offline`），在做别的任何事之前先截一张图（同 `screen shot`，存 `private/shots/<时间>-fail-<任务>.png`），
再从 MAA 的日志目录（`maa dir log`；测试或特殊安装可以用环境变量 `MAAPILOT_MAA_LOG_DIR` 指定）里找这次任务开始以后 MAA 自己存的截图
（`interface/`、`other/` 等子目录下的 `YYYY.MM.DD-HH.MM.SS.mmm_raw.png`，本地时间，最多 6 张），**复制进 `private/shots/`**
（`<YYYYmmdd-HHMMSS>-maa-<任务>-<毫秒>.png`：MAA 的日志目录在项目外，无头 agent 读不了），放进 `data.evidence`：

```json
{"shot_now": "/…/private/shots/20261002-160918-fail-infrast.png",
 "maa_shots": [{"path": "/…/private/shots/20261002-160917-maa-infrast-997.png", "source": "/…/debug/interface/2026.10.02-16.09.17.997_raw.png",
                "kind": "interface", "at": "2026-10-02T16:09:17+08:00"}],
 "window": {"start": "…", "end": "…"}, "why": "maapilot maa why --task infrast", "missing": ["…"]}
```

取证尽力而为，自己失败不影响命令的结果：拿不到的写进 `missing`（连不上模拟器、找不到 MAA 的日志目录），都拿到了就没有 `missing`。
`shot_now` 也记进命令记录（`private/log/<日期>.jsonl` 那一行的 `shot_now`），`maa why` 会带出来。`not_open`、参数错这类没跑 maa 的失败不取证。

`maapilot maa why [--task <任务>] [--since <时间> [--until <时间>]] [--last]`（只读：不碰模拟器、不拿锁）

把一个 MAA 任务失败那段时间的事实都拿出来，交给 agent 自己判断：**只摆事实，不下结论**（没有"原因"字段，不按关键字归类；怎么读写在 skill `maapilot-recovery`）。
时间段默认取最近 7 天里最近一次失败的 `maapilot maa …`（命令记录的结束时间和时长；定时日常里跑的每一项也在里面），
`--task` 只看这一项，`--last` 不管成没成取最近一次，`--since`/`--until`（ISO 时间或今天的 `HH:MM`，`--until` 默认现在）自己指定。时间段前后各多读几秒。
从 maa-cli 的详细日志 `asst.log`（日志目录用 `maa dir log` 找；轮换下来的 `asst.bak.log` 也读）里按时间二分定位，只读这一段（最多 48 MB，超了 `log.truncated`），整理成：

```json
{"task": "infrast", "window": {"start": "…", "end": "…"},
 "record": {"at": "…", "command": "maa infrast", "ok": false, "error": "maa_failed", "duration_s": 496.0, "run_id": "…"},
 "shot_now": "…", "maa_cli_log": "/…/private/log/maa/20261002-160102-infrast.log", "maa_cli_log_tail": ["…"],
 "log": {"dir": "…/debug", "files": ["…/asst.log"], "bytes_read": 2677065, "truncated": false},
 "lines": 16279, "start": "16:00:59", "end": "16:09:18",
 "taskchains": [{"at": "16:01:10", "event": "TaskChainStart", "taskchain": "Infrast"}, …],
 "steps": [{"at": "16:06:53", "task": "LoadingText", "action": "DoNothing", "matched": "正在提交", "repeat": 39, "last": "16:07:24"}, …], "steps_total": 222,
 "tried": [{"at": "16:08:56", "cur_task": "InfrastBegin", "first": ["InfrastBegin"]}, …],
 "errors": [{"at": "16:09:17", "class": "ProcessTask", "first": ["InfrastBegin"], "pre_task": "Stop"}, …], "error_count": 24,
 "last_match": {"at": "16:07:24", "task": "LoadingText", "matched": "正在提交"},
 "ocr": [{"text": "正在提交", "first": "16:01:56", "last": "16:07:24", "count": 59}, {"text": "玩家/ | 187", "first": "16:08:30", "last": "16:08:30", "count": 1}, …], "ocr_total": 155,
 "waits": [{"task": "LoadingText", "matched": "正在提交", "first": "16:06:53", "last": "16:07:24", "count": 39, "seconds": 31}],
 "screencap_failures": [], "connection": [{"what": "Connected", "details": {…}, "first": "16:01:04", "last": "16:01:04", "count": 1}, …],
 "error_lines": [{"message": "skill has no recognition result", "first": "16:01:36", "last": "16:06:13", "count": 13}, …],
 "screenshots": [{"path": "/…/private/shots/20261002-160917-maa-infrast-997.png", "source": "…/interface/2026.10.02-16.09.17.997_raw.png", "kind": "interface", "at": "…"}]}
```

- `steps`：MAA 一步步做的事（`SubTaskStart`：任务、动作、认出的字或模板），连续相同的合成一条（`repeat`、`last`），留最后 40 条（`steps_total` 是总数）；
  `tried`：它在找哪一页（`cur_task` / `first`）的变化，最后 20 次；`errors`：最后 10 个 `SubTaskError`（`error_count` 是总数）；`last_match`：最后一次真的认出东西（不算 `JustReturn`）。
- `ocr`：这段时间 OCR 到的所有字（去掉空白处的噪声），同样的字合一条，带第一次、最后一次、次数，按第一次出现排；超过 80 种时留最后还出现的 80 种（`ocr_total` 是总数）。
- `waits`：MAA 自己的 `Loading*` 任务（等加载、等服务器回应）连续出现的一段和时长；`screencap_failures`：截图命令返回非 0 或报 ERR 的（连接时试 `nc` 截图方式的不算）；
  `connection`：MAA 的连接信息（每隔几秒一条的截图耗时、帧率不列）；`error_lines`：MAA 的 ERR 行，去重带次数，最后 15 种。
- `screenshots`：时间段内（结束后再宽 30 秒）MAA 存的截图，最多 12 张，同样复制进 `private/shots/`（`path` 是副本，`source` 是原位置；复制过的不再复制；复制不了的 `path` 为 `null`、带 `error`）。
- 找不到记录退出码 2（`not_found`）；找不到 MAA 的日志目录时照常返回，`log.dir` 为 `null`、`hint` 提示 `doctor`。

程序里 `maapilot.tools.maa_cmd.run(task, **opts)` 是不拿锁的版本（`daily run` 已经持锁时直接调；失败抛同样的 CliError）。

## state —— 读账号状态，存快照

| 命令 | 做什么 | 快照 |
|---|---|---|
| `state depot` | MAA 仓库识别（Depot），自动从首页进仓库 | `private/state/depot.json` |
| `state box` | MAA 干员识别（OperBox）：精英化、等级、稀有度、潜能 | `private/state/box.json` |
| `state op <干员>` | 进这名干员的干员页读精英化、等级、技能等级（MaaFramework，只读，约一分钟；不存快照）。专精、模组读不了（`unread` 列出没读到的） | — |
| `state sanity` | 回首页，OCR 右上角的理智（大号的当前值 + 下面的"理智/上限"；2026-09 真机核对过） | `private/state/sanity.json` |
| `state show [depot\|box\|sanity] [--item <名>…] [--op <名>…]` | 读快照，不碰模拟器；不写种类时给三样里有的 | — |

每次读完覆盖快照，`state depot` 并在 `private/log/` 的命令记录里记下和上一份的差（`depot_change`）。`--item` / `--op` 只筛选输出。
OperBox 读不到技能等级、专精、模组：技能等级用 `state op <干员>` 读；专精、模组以用户说的或在游戏里看到的为准，写在目标的"分解与进展"里。
`state op` 做不成退出码 1（`shortcut_failed`，带 `stage`、`shot`）。
识别失败退出码 1（`depot_failed` / `box_failed` / `sanity_failed`，后者 `data.shot` 是截图）。
程序里 `maapilot.tools.state.read_depot()` / `read_box()` 是不拿锁的版本。

```json
// state depot / state show depot
{"at": "2026-09-30T21:40:00+08:00", "age_min": 0,
 "items": [{"id": "4001", "name": "龙门币", "count": 1203456}, {"id": "4003", "name": "合成玉", "count": 3200}, …],
 "changed": [{"id": "30012", "name": "固源岩", "delta": 14}]}
// state box
{"at": "…", "operators": [{"name": "遥", "id": "char_…", "rarity": 6, "elite": 1, "level": 70, "potential": 1}]}
// state op 遥
{"name": "遥", "rarity": 6, "elite": 1, "level": 70, "skill_level": 7, "at": "…", "shot": "private/debug/…", "unread": ["专精", "模组"]}
// state sanity
{"at": "…", "current": 82, "max": 135}
```

`state show` 在快照不存在时退出码 2（`no_snapshot`，hint：先 `state depot`）。

## data —— 游戏数据（不碰模拟器）

| 命令 | 输出 |
|---|---|
| `data sync [--check]` | 从 ArknightsGameData 同步数据表、刷新企鹅物流掉率与一图流价值；`--check` 只看要不要更新。`{updated, to_update, missing, recipes_rebuilt, live, ages: {matrix_age_h, values_age_d}}`；联网失败退出码 1（`sync_failed`） |
| `data operator <干员>`（别名 `op`） | `{name, id, rarity, profession, levels: [{elite, max_level, to_max: {exp, lmd}}], elite: [{elite, lmd, items, needs_level}], skill_levels: [{to, items}], mastery: [{skill, ranks: [{rank, items, hours}]}], modules: [{type, name, levels: [{level, lmd, items}]}] \| null}` |
| `data item <物品>`（别名 `drops`） | `{id, name, rarity, type, craftable, recipe, stages: [{stage, ap, rate, sanity_per_item, open_days, open_today}], drops_source}`：掉落最好的几关（常驻主线 / 资源本 / 芯片本，按每件期望理智排） |
| `data recipe <物品>` | `{tree, shop}`：合成树（加工站 / 制造站），每一步的材料与龙门币、叶子的主掉落关；能在商店兑换的另给 `shop` |
| `data stage <关卡>` | `{code, stage_id, name, ap, navigable, kind, open_days, open_today, drops: [{id, name, rate, samples}], listed_drops}` |

名字认不出退出码 2（`unknown_name`，`data.suggestions` 给相近的名字）；关卡编号重名退出码 2（`ambiguous_stage`，写成 `活动名/编号`）。
数据表不全退出码 5（`no_game_data`，先 `data sync`）。没有企鹅物流缓存时掉率为空，只列数据表里的主掉落关。

## calc —— 纯计算（不碰模拟器，只给建议）

读 `private/state/` 的快照做对照；快照的时间放在输出里（`depot_at`），太旧的由 agent 决定要不要先 `state depot`。

`maapilot calc need "<对象>" [--from "<当前>"]`
一个养成目标要多少：`<对象>` 的写法同[目标清单的"对象"行](#对象的写法)。当前练度默认取 `state box`（精英化、等级），
技能等级 / 专精 / 模组快照里没有，要用 `--from` 给（如 `--from "技能 4级"`），不给就按最低算并在 `assumed` 里说明。

```json
{"object": "干员 遥 → 精2 1级", "from": {"elite": 1, "level": 70}, "assumed": [],
 "need": {"lmd": 590000, "exp": 360000,
          "items": [{"id": "3263", "name": "辅助双芯片", "count": 1}, {"id": "31014", "name": "聚合剂", "count": 2}]},
 "have": {"lmd": 1203456, "exp": 800000, "items": [{"id": "3263", "name": "辅助双芯片", "count": 0}]},
 "short": {"lmd": 0, "exp": 0, "items": [{"id": "3263", "name": "辅助双芯片", "count": 1}]},
 "craftable": [{"id": "31014", "name": "聚合剂", "count": 2, "fully": true, "uses": [{"id": "…", "name": "…", "count": 2}],
                "lmd": 800, "missing": []}],
 "farm": [{"id": "3272", "name": "辅助芯片组", "count": 2}],
 "depot_at": "…", "box_at": "…"}
```

- `have.exp` 是仓库里作战记录折成的经验。`craftable` 是缺的材料里能合成的：`fully` 为假时 `missing` 是合成也补不上的原料。
  `farm` 是合成之后还要去刷的（可以直接交给 `calc stages`）。
- 要专精但还没精2 时自动算上练到精2 1级；技能等级不知道时，专精按已到 7 级算、升技能按 1 级算，都写进 `assumed`。
- 快照不存在时按空仓库 / 精0 1级算，也写进 `assumed`。对象写错退出码 2（`bad_object`）。

`maapilot calc craft <物品> [--count N]`
用仓库现有材料再合成 N 个要做什么（仓库里已有的不算）：先合哪些中间材料、每步材料与龙门币、还缺什么基础材料、加工站心情消耗。
认得加工站精英材料、制造站双芯片、商店兑换（芯片助剂，花采购凭证，列在 `spend`）。不能合成的退出码 2（`not_craftable`）。
`{"item", "count", "steps": [{"item", "id", "count", "times", "station": "WORKSHOP|MANUFACTURE|SHOP", "costs": [...], "lmd": n, "mood"?: n}], "lmd": n, "short": [...], "spend": [...], "mood": n, "depot_at"}`

`maapilot calc stages <物品>[=数量] …`
缺的材料去哪刷最省理智：每种物品给几关（每件期望理智、掉率、今天开不开，只看常驻主线 / 资源本 / 芯片本），再给一个合并的建议
（每种物品挑每件期望理智最低的一关，次数 = 数量 ÷ 掉率向上取整；同一关服务几种物品时取最多的次数；不算副产物）。
`days` 按每天自然恢复 240 理智算。
`{"per_item": [...], "plan": [{"stage": "PR-C-2", "times": 8, "ap": 36, "sanity": 288, "open_days": "…", "open_today": true, "for": ["辅助芯片组"]}], "sanity_total": 540, "days": 2.3, "daily_sanity": 240, "note": "…", "no_stage"?: [...]}`

程序里 `maapilot.tools.calc.parse_object(text)` 解析"对象"一行（`goals` 可以用它校验），`need()` / `craft()` / `stages()` 是函数版本。

## goals —— 目标清单（不碰模拟器）

读写 `private/goals.md` 的固定字段；完成或放弃时整段挪进 `private/goals-done.md`。格式见[目标清单](#goalsmd-目标清单)。
"分解与进展"是 agent 的工作笔记：可以用 `goals update --note` 追加一行，也可以直接用文件工具编辑。

| 命令 | 做什么 |
|---|---|
| `goals list [--all]` | goals.md 里的目标（进行中、暂停）；`--all` 另给 `done`（goals-done.md 里的） |
| `goals add --title <标题> --object <对象> --said <原话> --approved <同意> [--premium 是\|否] [--note <文字>]` | 追加一个目标，状态"进行中"。`--approved` 也可以写成 `--agreed`。`--said` 带日期，如 `把遥练到精2（2026-09-30）`（没写日期就补今天）；`--approved` 如 `2026-09-30 21:40，已报：龙门币约 59 万…`（不以日期开头就在前面补现在的时间）；`--premium` 默认"否" |
| `goals update <标题> [--status 进行中\|暂停] [--object …] [--approved …] [--premium 是\|否] [--note <文字>] [--rename <新标题>]` | 改固定字段（缺的字段补在最后一个字段后面）；`--note` 在"分解与进展"末尾追加 `- MM-DD：<文字>`（还没有这一块就先加上标题行） |
| `goals remove <标题> --status 完成\|放弃 --reason <原因>` | 整段挪进 goals-done.md（放在最后），状态改成完成 / 放弃，加一行 `- 结束：<日期> <完成\|放弃>，<原因>` |

`<标题>` 精确匹配二级标题，也接受唯一的前缀；找不到或不唯一退出码 2（`unknown_goal` / `ambiguous_goal`）。
标题重复时 `add` / `update --rename` 退出码 2（`duplicate_goal`）；对象不是 `干员 / 材料 / 关卡 / 其他` 开头退出码 2（`invalid_object`）。

```json
// goals list
{"goals": [{"title": "遥 精2", "status": "进行中", "object": "干员 遥 → 精2 1级",
            "said": "把遥练到精2（2026-09-30）", "approved": "2026-09-30 21:40，已报：…", "premium": false,
            "notes": "分解与进展（agent 自由写）：\n- …"}],
 "active": 1,
 "problems": [{"title": "…", "file": "goals.md", "message": "缺少字段：同意"}]}
// goals add / update / remove
{"goal": {…同上一项…}, "active": 1}                 // update 另有 "changed": ["状态", "分解与进展"]
```

`premium` 读不出（不是 是 / 否）时为 `null`，并在 `problems` 里报。goals-done.md 里的目标多一个 `ended`（"结束"那一行）。

写目标的同意来自对话（用户看过 `calc` 报的数、说了同意），记在"同意"那一行；命令本身不再弹确认。
程序里 `maapilot.flows.goals.active_goals()` / `active_count()` 给 `daily run`、`doctor --brief` 用。

## op / craft —— 快捷操作（碰模拟器）

旧版的自动化脚本，能成就省事，失败了 agent 按 `maapilot-training` / `maapilot-base` 在界面上自己做。失败退出码 1，
`error.code` 是 `shortcut_failed`（另见下表），`data` 带 `stage`（做到哪一步）、`shot`（失败时的截图，`private/debug/…png`）、
`done`（已经做了的部分），脚本给得出的还有 `reached`（升级做到的练度）、`made` / `restored`（制造站）。
花了多少不在这里算：要知道就前后各 `state depot` 一次。连不上模拟器退出码 5（`device_failed`）。

| 命令 | 做什么 |
|---|---|
| `op upgrade <干员> [--elite E] [--level L] [--skill N]` | 升级 / 精英化 / 升技能（通用技能等级 1–7）到指定练度（`maapilot/crew/upgrade.py`）。只给一部分时，没给的按现在的练度：只给 `--level` 就在当前精英阶段内升；只给 `--elite` 就升到那一阶的 1 级 |
| `op mastery <干员> --skill K --rank M` | 在训练室开始技能 K 的下一级专精，不超过专 M（一次开一级；缺的 T3 以上材料先合成，要用 `state depot` 快照；`maapilot/crew/mastery.py`） |
| `craft <物品> [--count N]` | 精英材料走加工站（`maapilot/crew/craft.py`，只合这一样：中间材料先各自 `craft`，顺序看 `calc craft`）；双芯片走制造站（`manufacture.py`，造完改回原产物）；商店兑换的（芯片助剂，花采购凭证）走采购中心（`shop.py`，确认框上核对总价，对不上不买） |

```json
// op upgrade
{"operator": "遥", "target": {"elite": 2, "level": 1, "skill": null},
 "before": {"elite": 1, "level": 70}, "after": {"elite": 2, "level": 1, "skill": null}, "shot": "private/debug/…png"}
// op mastery
{"operator": "遥", "skill": 2, "status": "started", "rank": 1, "hours": 8, "short": null, "crafted": [...], "shot": null}
// craft
{"item": "聚酸酯块", "count": 3, "times": 3, "station": "WORKSHOP", "shot": "…"}
{"item": "辅助双芯片", "count": 2, "station": "MANUFACTURE", "made": 2, "original": "赤金", "restored": true}
{"item": "芯片助剂", "count": 1, "station": "SHOP", "currency": "采购凭证", "unit_price": 90, "balance_before": 727, "balance_after": 637}
```

| error.code | 退出码 | 什么情况 |
|---|---|---|
| `shortcut_failed` | 1 | 脚本出错、没到目标练度、认不出专精确认框 |
| `training_busy` | 1 | `op mastery`：训练室正在练别的 |
| `short_materials` | 1 | `op mastery`：材料不够、合成也补不上（`data.short`） |
| `not_restored` | 1 | `craft` 双芯片：造好了，但制造站没改回原产物（`data.original`、`shot`） |
| `not_bought` | 1 | `craft` 兑换：确认之前停下了，什么都没花（余额不够、找不到商品、确认框对不上） |
| `unknown_name` / `not_craftable` | 2 | 名字认不出；这样东西快捷操作做不了 |

`op mastery` 的 `status` 为 `reached`（要开的已经超过专 M，什么都没开）也算成功。

## guard —— 货币对账

只看两样：**源石**（`4002`）和**合成玉**（`4003`）。数量来自 MAA 仓库识别（和 `state depot` 同一次识别，顺手更新 depot 快照）：
按物品 id 给数，不依赖页面布局；但上万的数会取整（真机：合成玉 16566 认成 16000），所以接着回首页 OCR 顶栏（按位置取：源石最右、合成玉在它左边；龙门币读不出不影响，数字前后认错的杂字符先去掉），
和仓库识别对得上（两样都不小于它、差不到取整的一格）就用顶栏的精确值，有一样对不上或读不到就用仓库识别的数。
比较时，前后有一个数上千、减少不到 1000 的当作读数误差，不算花费（用户 2026-10-05 定：不考虑只花了几百合成玉的情况）。识别结果里没有这两项、但认出了别的物品时按 0 算（误认只会往"减少了"的方向错，宁可误停）；
一件都没认出退出码 1（`unreadable`），不做判断。

| 命令 | 做什么 |
|---|---|
| `guard snapshot` | 记下现在的源石、合成玉 → `private/guard/snapshot.json`。**上次对账之后跑过可能花东西的命令**（`private/guard/dirty.json` 在）又已有快照时，改做一次 `check`（`checked_instead: true`，不过就退出码 3），不重记起点：agent 不能靠重记快照抹掉自己的花费。停机中退出码 3（`guard_stop`） |
| `guard check` | 再读一次，与快照比。减少的部分扣掉快照之后记下的、还没用完的 `allow`（按时间先后，一笔可以分几次用）；还有没放行的减少 → 退出码 3（`currency_decreased`），写 `private/guard/stop.json`，快照不动。通过则更新快照、删掉 `stop.json`。还没有快照时把这次的数记为起点（`baseline: true`） |
| `guard allow <源石\|合成玉> <数量> --reason <用户同意了什么>` | 记下用户同意的一笔花费 → `private/guard/allowances.jsonl`。**权限设置里是"询问"**：每次弹确认 |
| `guard status` | 快照、未用的放行、是否停机中、上次对账后动过什么（`dirty`）（不碰模拟器） |

**dirty**：`input …`、`maa …`（`startup`、`closedown` 除外）、`op …`、`craft` 拿到模拟器锁时记进 `private/guard/dirty.json`；
`check` 通过（或重记快照）时清掉，读不到时留着。没人动过时 `snapshot` 才重记起点，所以用户自己在游戏里花的（两次日常之间、没有 agent 操作）不会被当成异常；
agent 动过之后的减少一定会在下一次 `snapshot` / `check` 被对出来。

合成玉、源石增加不算问题。停机中（`stop.json` 在）时 `daily run` 退出码 3 拒绝开始；用户说明情况、同意这笔减少后，
agent 用 `guard allow` 补记（弹确认），再 `guard check`：对上了就删掉 `stop.json`。

```json
// guard check 不通过（退出码 3）
{"before": {"originite": 12, "orundum": 3200}, "after": {"originite": 12, "orundum": 2600},
 "allowed": {"originite": 0, "orundum": 0}, "unexplained": {"orundum": -600}}
// guard check 通过
{"before": {…}, "after": {…}, "allowed": {"originite": 0, "orundum": 600}, "unexplained": {},
 "used": [{"item": "orundum", "amount": 600, "reason": "…"}], "resumed": true}
// guard allow
{"recorded": {"at": "…", "item": "orundum", "amount": 600, "reason": "…", "used": false, "used_amount": 0}, "hint"?: "…"}
// guard status
{"snapshot": {"at", "originite", "orundum"} | null, "allowances": [...还没用完的...], "stop": {...} | null,
 "dirty": {"since": "…", "commands": ["input tap", "maa fight"]} | null}
```

`guard allow` 的物品写 `源石`（或 `至纯源石`）/ `合成玉`，别的退出码 2。
程序里 `maapilot.tools.guard.snapshot()` / `check()` 是不拿锁的版本（`take_snapshot()` 无条件重记，只给 `check` 内部用），`stopped()` 看是否停机中（不碰模拟器）。

## lock —— 模拟器锁（内部）

`lock status`：`{"holder": {...} | null, "stale": {...} | null, "stale_reason": "…" | null, "inherited": bool}`（谁占着、从什么时候、在做哪步；残留的锁单列；
`inherited`：本进程环境里的 `MAAPILOT_LOCK_TOKEN` 对得上有效的锁，即定时日常把锁交给了这个 agent）。
碰模拟器的命令被占用时退出码 4（`busy`），`data.holder` 同样是谁占着（`cliutil.device()` 填）（外面有别的 `maa` 进程时是 `{"external_maa_pids": [...]}`）。
`lock release`：只删掉**已经残留**（持有进程不在了、心跳过期）的锁；有效的锁退出码 4。权限"询问"。

## daily —— 日常

`maapilot daily run [--only <项,…>] [--trigger manual|schedule] [--interactive] [--no-agent]`（碰模拟器，要 20–60 分钟，用 Bash 后台运行）

1. 对账停机中（`private/guard/stop.json` 在）→ 退出码 3（`guard_stop`）。另一个日常在跑 → 退出码 4（`daily_running`）；
   在无头 agent 里（环境变量 `MAAPILOT_DAILY_RUN`）→ 退出码 2（`nested_daily`）。拿模拟器锁；先 `startup`（配了的话：读仓库要在游戏里），
   再 `guard snapshot`（上次对账之后动过会花钱的操作时它改做对账，不过就停下，见「guard」）。
2. 按 `config.yaml` 的 `daily.tasks` 依次跑其余 MAA 日常项（每项是一条 `maapilot maa <项>`，继承锁；`award` 留到最后）。某项失败：
   - `--trigger schedule`：当场启动无头 `claude -p`（加载 `maapilot-recovery`，继承锁），等它结束（超时杀掉整个进程组），
     再 `guard check`，然后接着跑下一项；
   - `manual`（默认；`--interactive` 同义，agent 在场）：**第一项失败就停下**，不跑后面的，退出码 1（`handoff`），
     `data.handoff = {task, error, log, evidence, rest}`（`evidence` 是这一项的失败取证，见「maa」；`rest` 是这一项和后面还没跑的），`status: handed_off`；在场的 agent 处理好后 `daily run --only <rest>` 接着跑；
   - 掉线 / 维护（`offline`）也照上面交给 agent（重新登录、等维护由 agent 判断）。只有被占用（`busy`）不交。
3. 刷理智（`farm`）：有进行中的目标 → `schedule` 时启动无头 `claude -p`（加载 `maapilot-daily`）做目标部分，结束后 `guard check`；
   `manual` 时交回在场的 agent（`data.goals_pending: true`），`award` 也不跑（`data.award_pending: true`，领奖励要在刷理智之后）：
   agent 做完目标部分自己 `maa award`、`guard check`、`report write <run_id> --append …`。没有进行中的目标 → `maa fight <default_stage>`（失败同第 2 步）。
4. `award`；`guard check`；写汇报 `private/reports/<YYYY-MM-DD-HHMM>.md` 与 `.json`（开始时就写一份"还在跑"，每一步后更新）。

任何一次 `guard check` 退出码 3 → 立刻停下（后面的项不跑），退出码 3（`currency_decreased`），汇报里写明。
剿灭在一周里连续 2 次没做成（接管也没救回来，多半是还不能代理）→ 这周的日常不再打（`skipped`），汇报里告警；下周一 04:00 清零。
会话里跑停下交给 agent → 退出码 1（`handoff`）；定时运行里有没做成（agent 也没处理好）的项 → 退出码 1（`daily_incomplete`），`data` 照常给全。被占用、连不上 → 退出码 4 / 5，汇报的 `status` 是 `aborted`。

`--only` 只跑列出的项（补跑失败的几项；刷理智写 `farm`，不列就不刷）；`--no-agent` 不启动无头 agent（失败只记下）。

```json
{"run_id": "20260930-0440", "trigger": "schedule", "status": "done", "ok": true,
 "started_at": "…", "finished_at": "…",
 "tasks": [{"task": "recruit", "ok": true, "duration_s": 95},
           {"task": "infrast", "ok": false, "error": "…", "code": "maa_failed", "log": "…", "recovered": true,
            "agent": {"ok": true, "summary": "…\n结果：完成", "recovered": true, "timed_out": false,
                      "transcript": "private/log/agent-20260930-0440-1-infrast.jsonl", "cost_usd": 0.4, "duration_s": 310}},
           {"task": "farm", "ok": true, "result": {"stage": "1-7", "times_done": 12, "sanity_used": 72, "medicine_used": 0}},
           {"task": "goals", "ok": true, "agent": {…}}],
 "failed": [], "goals_active": 0, "goals_pending": false, "farm": {"stage": "1-7", "times_done": 12, …},
 "depot_change": [{"id": "4001", "name": "龙门币", "delta": 12000}],
 "guard": {"ok": true}, "alerts": [], "agents": 1, "report": "private/reports/2026-09-30-0440.md"}
```

`status`：`running` / `done` / `stopped`（对账停下）/ `aborted`（没跑完）。`guard.ok` 为 `null` 表示对账命令本身没做成（汇报里告警）。
`depot_change` 来自 guard snapshot / check 顺手更新的 `state/depot.json` 前后两份；读不到为 `null`。

无头 agent：`<tools.claude> -p "<提示>" --output-format stream-json --verbose --settings <仓库>/.claude/settings.json --permission-mode default --permission-prompts none`，
工作目录是仓库根目录（运行时由安装位置推出），显式带上项目的权限设置（文件夹没被信任时无头 claude 不读它；要确认的一律自动拒绝），
环境里带 `MAAPILOT_LOCK_TOKEN`（继承锁，agent 用 `lock status` 的 `inherited` 确认）和 `MAAPILOT_DAILY_RUN`。
agent 结束（或超时被杀）后日常换一次锁的 token：它留下还在跑的 maapilot 命令不能再拿锁；还有 maa 进程在跑就等 60 秒，不走就结束掉并告警。提示里写明是哪次运行、哪一项、错误与 MAA 日志、失败取证的截图路径（`shot_now`、`maa_shots`）和 `maapilot maa why --task <项>`、被登出就直接重新登录、只处理这一项、最后一行写 `结果：完成` 或 `结果：没做成`
（程序据此判断 `recovered`）。输出存 `private/log/agent-<run_id>-<序号>-<项>.jsonl`，最后的 `result` 作为总结进汇报。
做目标部分的 agent 最长 `daily.agent_timeout_min` 分钟（默认 90），接管失败项的最长 `daily.recovery_timeout_min` 分钟（默认 30）；
每个 MAA 日常项最长 `daily.task_timeout_min` 分钟（默认 90）。刷理智（`farm`）的时限按理智算：先读理智（`private/state/sanity.json` 1 小时内的直接用，
没到上限的按 6 分钟 1 点往上推；旧了就 `state sanity` 回首页读；还读不到就用旧快照推），能打几次 = ⌈理智 ÷ 关卡一次的理智⌉（关卡认不出按 6），
时限 = 次数 × `daily.farm_min_per_fight`（默认 3）+ `daily.farm_timeout_margin_min`（默认 15）分钟；理智超上限（如 358/205）照算。完全读不到理智才用 `task_timeout_min`。
算出的时限记在这一项的 `timeout`（`{min, basis, sanity, sanity_source, stage_cost, fights, …}`）。超时杀掉 `maapilot maa` 后，20 秒内 maa 进程还在就由日常结束掉（TERM，不走再 KILL，告警里写明）。提示里要求 agent 前台跑长命令（Bash 超时 10 分钟）、刷图分批。

`daily schedule [--times HH:MM[,HH:MM]] [--replace-old] [--dry-run]`（权限"询问"）：按 `--times` 或 `config.yaml` 的 `schedule` 写 crontab，每个时间点一行：

```
40 4 * * * PATH=<maa、claude、adb 所在目录>:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin PYTHONUTF8=1 <仓库>/maapilot daily run --trigger schedule >> <仓库>/private/log/cron.log 2>&1 # maapilot-v2 <仓库>
```

PATH 写在每行里（不加单独的 `PATH=` 行，免得影响别人的行）。只动以 `# maapilot-v2 <本仓库路径>` 结尾的行，别的行一个字节都不动，重复执行结果相同。
发现旧版 MaaPilot 的定时任务（`maapilot job tick`、`bin/daily.sh`）时列在 `data.old_entries`，带 `--replace-old` 才删掉它们。
`--dry-run` 只在 `data.crontab` 里给出改完的全文，不写。测试用环境变量 `MAAPILOT_CRONTAB` 换掉 crontab 程序。
`{"times": ["04:40", "16:40"], "installed": [...], "replaced": [...], "old_entries": [...], "removed_old": [...], "changed": true, "dry_run": false}`。
macOS 会弹窗问终端能否改 crontab，要用户点允许。

`daily unschedule [--dry-run]`（权限"询问"）：删掉本项目的定时行。`{"removed": [...], "old_entries": [...]}`
`daily status`：`{"running": {run_id, step, started_at, …} | null, "last": {run_id, at, ok, status, report} | null,
"schedule": {"installed": true, "times": [...], "old_entries": [...]}, "next_at": "…"}`。

`daily watch [--timeout-min N]`（只读：不拿锁、不碰模拟器）：等**下一次**跑完的日常（定时或手动；开始时正在跑的那次也算，等它跑完），然后输出它的结果。
会话里的 agent 一直在后台挂着一个（`CLAUDE.md`「盯着日常」），结束就告诉用户、`report seen`、再挂一个。每 20 秒看一次汇报（`private/reports/*.json` 的 `status`、`finished_at`）和运行标记；
只认命令开始之后跑完的（`finished_at` 晚于开始时间）。等着的那次进程没了而汇报还写着 `running` → 当成 `aborted`（`alerts` 里写明）。
默认最多等 110 分钟（Claude Code 的后台 Bash 最长 2 小时），到时退出码 1、`watch_timeout`（`data: {running, next_at}`），agent 再挂一个就行。

```json
{"run_id": "20261004-1640", "trigger": "schedule", "started_at": "…", "finished_at": "…", "status": "stopped", "ok": false,
 "failed": [{"task": "infrast", "label": "基建", "error": "…", "code": "maa_failed"}], "alerts": [],
 "guard_stop": {"reason": "日常之后发现合成玉 / 源石有没经同意的减少", "before": {…}, "after": {…}, "unexplained": {"orundum": -866}},
 "report": "private/reports/2026-10-04-1640.md", "seen": false,
 "lines": ["10 月 4 日 16:40 的定时日常因为货币对账停下了，有问题。", "对账：…（合成玉 -866）"]}
```

顺利（`status: done`、`ok` 不是 `false`、没有 `failed`）退出码 0；有问题（没做成的项、对账停下、没跑完、交给 agent）退出码 1、`daily_problem`，`data` 同上。
`handoff` 只在停下交给 agent 时有；`guard_stop` 只在 `stopped` 时有。`seen`：这次已经 `report seen` 过（告诉过用户）。
程序里 `maapilot.flows.daily.schedule_info()`、`maapilot.flows.report.latest()` 给 `doctor` 用。

## reset —— 从头设置

`maapilot reset [--dry-run] [--memory]`（权限"询问"；不碰模拟器）

用户说要从头设置时用。**什么都不删**：
1. 先查：日常在跑（退出码 4 `daily_running`）、模拟器锁被占着（有命令或接管的 agent 在操作，退出码 4 `busy`，`data.holder`）、
   在日常叫起来的 agent 里（环境里有 `MAAPILOT_DAILY_RUN` 或 `MAAPILOT_LOCK_TOKEN`，退出码 2 `not_allowed`）→ 不做。
2. crontab 里本项目的行（以 `# maapilot-v2 <本仓库路径>` 结尾，同 `daily unschedule`）删掉，别的行（包括别的目录的 MaaPilot）一个字节都不动。
3. `private/` 整个改名成同目录下的 `private.bak-<YYYYmmdd-HHMMSS>`（配置、`setup.json`、目标、快照、日志、汇报、截图、对账都在里面）；
   再把备份里的 `maa-config/`（maa-cli 的配置与任务模板）复制回新的 `private/maa-config/`。之后 `doctor --brief` 的 `next` 是 `setup`。
   恢复：把新建的 `private/` 挪开，把备份改回 `private`（`data.private.restore` 写着）。
4. `--memory`（默认不动）：Claude Code 给这个项目目录记的自动记忆 `<~/.claude 或 $CLAUDE_CONFIG_DIR>/projects/<仓库绝对路径里非字母数字都换成 ->/memory/`
   也改名成旁边的 `memory.bak-<时间>`。测试用 `MAAPILOT_CLAUDE_PROJECTS_DIR` 换掉 projects 目录。
5. 留着：`.venv`、`data/`、`resource/`、maa-cli 本身和它的资源。

`--dry-run` 只列出会挪什么、删哪几行，什么都不动。

```json
{"dry_run": false,
 "crontab": {"remove": ["40 4 * * * … # maapilot-v2 /…/MaaPilot"], "removed": ["…"], "other_lines_kept": true},
 "private": {"move": "/…/private", "to": "/…/private.bak-20261002-1700", "entries": ["config.yaml", "goals.md", "log", …],
             "restore": "mv … …", "keep_maa_config": "/…/private/maa-config"},
 "memory": {"move": null, "note": "没带 --memory：自动记忆不动"},
 "keep": ["/…/.venv", "/…/data", "/…/resource", "maa-cli（及它的资源）"], "next": "setup"}
```

重置之后让用户重新开一次会话（启动检查会进入设置）。

## report —— 汇报

| 命令 | 做什么 |
|---|---|
| `report show [--last \| <run_id>]` | 汇报全文（`markdown`）与摘要（`summary`，即 `daily run` 的 data），默认最近一次；agent 转述给用户 |
| `report latest` | 同 `show --last` |
| `report list [--limit N]` | 最近的汇报：`{"reports": [{run_id, at, trigger, ok, status, failed, report}]}` |
| `report write <run_id> --append <文字>` | 在这次运行的汇报后面追加一段"补充"（运行还没结束也可以，结束时保留） |
| `report seen [<run_id>]` | 记下这次运行的结果已经告诉过用户（默认最近一次）：写进 `private/reports/seen.json`（`{run_id: 时间}`）。`{"run_id", "seen_at"}`；没有这次运行退出码 2（`unknown_run`） |

"有问题、还没告诉过用户"的运行（最近 7 天、最多 5 个，新的在前；还在跑的不算）由 `maapilot.flows.report.unseen_problems()` 给出，
`doctor --brief` 的 `unseen_problems` 和 `lines` 里都会列出来，会话开头 agent 第一句就说。

---

## private/ 文件

`private/` 不入库，由程序和 setup 创建。`MAAPILOT_PRIVATE` 环境变量可以指到别处（测试用）。

```
private/
  config.yaml        配置（templates/config.yaml 复制来，setup 填好）
  setup.json         setup 完成的标记
  goals.md           进行中的目标
  goals-done.md      完成或放弃的目标
  state/             depot.json  box.json  sanity.json（最新快照）  annihilation.json（本周剿灭）
  log/               <YYYY-MM-DD>.jsonl（命令记录）  maa/（maa-cli 日志）  cron.log
                     daily-<run_id>.jsonl（一次日常的每一步）  agent-<run_id>-<序号>-<项>.jsonl（无头 agent 的输出）
  reports/           <YYYY-MM-DD-HHMM>.md / .json（每次日常的汇报）
  shots/             截图
  debug/             MaaFramework 调试输出、快捷操作失败时的截图
  guard/             snapshot.json  allowances.jsonl  stop.json
  maa-config/        本项目的 maa-cli 配置目录（MAA_CONFIG_DIR）：cli.toml  profiles/default.toml  tasks/maapilot-*.toml
  lock.json          模拟器锁
  daily-run.json     正在跑的日常（run_id、pid、当前步骤）；跑完删掉
```

### config.yaml

见 `templates/config.yaml` 的注释。键：

| 键 | 含义 | 默认 |
|---|---|---|
| `emulator.address` | adb 地址（setup 找出来填） | 无（必填） |
| `emulator.adb` | adb 程序 | `adb` |
| `server` | 客户端（MAA 的 client_type）：`Official` / `Bilibili` / `YoStarEN` / `YoStarJP` / `YoStarKR` / `txwy` | `Official` |
| `tools.maa` / `tools.claude` | 定时任务里用的 maa、claude 程序（setup 写绝对路径） | `maa` / `claude` |
| `schedule` | 每天定时日常的时间（本机时间，`"HH:MM"` 列表） | `["04:40", "16:40"]` |
| `daily.tasks` | 按顺序跑的 MAA 日常项：`startup` `recruit` `infrast` `mall` `annihilation` `award` | 全部 |
| `daily.default_stage` | 没有进行中的目标时刷哪关 | `"1-7"` |
| `daily.params.<任务>` | 覆盖该项的 MAA 参数 | `{}` |
| `daily.agent_timeout_min` | 做目标部分的无头 agent 最长跑多久（分钟） | `90` |
| `daily.recovery_timeout_min` | 接管失败项的无头 agent 最长跑多久（分钟） | `30` |
| `daily.task_timeout_min` | 一个 MAA 日常项最长跑多久（分钟） | `90` |

企鹅物流的服务器代码由 `server` 推出（Official/Bilibili → CN，YoStarEN → US，YoStarJP → JP，YoStarKR → KR，txwy → CN）。

### setup.json

setup 最后一步由 agent 用文件工具写：

```json
{"done_at": "2026-09-30T21:40:00+08:00", "version": 1}
```

不存在 = 没做过 setup（`doctor --brief` 的 `next: "setup"`）。重新 setup 时覆盖。

### goals.md —— 目标清单

半结构化 Markdown：每个目标一个二级标题，下面**固定 5 行字段**，程序只读这 5 行；再往下是 agent 自由写的"分解与进展"。
缺多少、刷几天这类会变的数不存，每次用 `calc` 现算。

```markdown
## 遥 精2
- 状态：进行中
- 对象：干员 遥 → 精2 1级
- 原话：把遥练到精2（2026-09-30）
- 同意：2026-09-30 21:40，已报：龙门币约 59 万、经验约 36 万、聚合剂 2、提纯源岩 9…
- 花合成玉/源石：否

分解与进展（agent 自由写）：
- 缺辅助芯片，靠 PR-C 刷；合成要换加工站干员
- 10-01：合成做了一半，差聚合剂 2
```

解析规则：

- 标题：`## ` 开头的一行，去掉首尾空白；文件里唯一。`## ` 之前的内容（文件说明）原样保留。
- 固定字段：标题下 `- <键>：<值>` 形式（全角冒号，也接受半角 `:`），键是 `状态`、`对象`、`原话`、`同意`、`花合成玉/源石` 五个，顺序不限；缺字段在 `goals list` 的 `problems` 里报，不报错退出。
- `状态`：`进行中` / `暂停`（goals-done.md 里是 `完成` / `放弃`）。只有"进行中"算"有进行中的目标"。
- `花合成玉/源石`：`是` / `否`。"是"的目标，每次执行到花合成玉、源石的那一步都要再问用户（`guard allow` 仍然弹确认）。
- 固定字段之后到下一个 `## ` 之前都是"分解与进展"，程序原样保留，只在 `--note` 时往末尾追加一行。
- 程序改文件时只改动涉及的那一段，其余字节不变；写入用临时文件 + 替换。

goals-done.md 同样格式，每段多一行 `- 结束：2026-10-05 完成，遥已精2` 放在固定字段之后，新挪来的放在文件最后。

#### 对象的写法

一行规整的写法，`calc need` 直接能用：

| 写法 | 例 |
|---|---|
| `干员 <名> → 精<E> <L>级` | `干员 遥 → 精2 1级` |
| `干员 <名> → <L>级`（当前精英阶段内升级） | `干员 香草 → 40级` |
| `干员 <名> → 技能 <N>级`（通用技能等级 1–7） | `干员 遥 → 技能 7级` |
| `干员 <名> → 技能<K> 专<M>` | `干员 能天使 → 技能3 专3` |
| `干员 <名> → 模组<类型> <N>级`（类型是模组代号的字母，如 X、Y、Δ） | `干员 泥岩 → 模组X 3级` |
| 同一干员多个目标用 `，` 连 | `干员 遥 → 精2 60级，技能 7级，技能3 专3` |
| `材料 <名> × <N>` | `材料 聚酸酯组 × 10` |
| `关卡 <编号>`（三星通关） | `关卡 12-17` |
| `其他 <文字>`（程序算不了的，如模组任务、活动） | `其他 泥岩模组X的模组任务` |

`calc need` 对 `关卡` 和 `其他` 退出码 2（`not_computable`），由 agent 自己分解。

### state/

`depot.json`：`{"at": iso, "items": {"<id>": count}}`；`box.json`：`{"at": iso, "operators": {"<名>": {"id", "rarity", "elite", "level", "potential"}}}`；
`sanity.json`：`{"at": iso, "current": n, "max": n}`。

### log/

`<YYYY-MM-DD>.jsonl`，每个碰模拟器的命令一行：`{"at", "command", "args", "ok", "exit", "duration_s", "error"?, "run_id"?, "depot_change"?, "shot_now"?}`（`shot_now`：maa 任务失败时当场截的图）。
`depot_change` 只在命令本身读过仓库时有（`state depot`）；`run_id` 取环境变量 `MAAPILOT_RUN_ID`（`daily run` 给子进程设）。
写记录用 `maapilot.tools.common.logged`（包住处理函数）或 `common.record()`。`maa/` 下是每次 maa-cli 调用的完整日志。

### reports/

`<YYYY-MM-DD-HHMM>.md`：给人看的汇报（做了什么、花了什么、失败与处理、目标进展、要用户决定的事）；
`.json`：`daily run` 的 `data`（加上无头 agent 追加的部分）。`seen.json`：`{run_id: 时间}`，哪几次的结果已经告诉过用户（`report seen`）。

### guard/

`snapshot.json`：`{"at", "originite", "orundum"}`；`allowances.jsonl`：`{"at", "item": "originite|orundum", "amount", "reason", "used": bool, "used_amount"}` 每行一笔；
`stop.json`：`{"at", "before", "after", "unexplained", "run_id"?}`，在就是停机中。

---

## Claude Code 权限设置

`.claude/settings.json`（入库，下面是它的全文；`tests/test_docs_consistency.py` 检查 skill 里写的每条命令都有放行或询问的规则）。无头运行（`claude -p`）用同一份设置，"询问"的操作自动被拒。

```json
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "permissions": {
    "allow": [
      "Bash(maapilot doctor:*)",
      "Bash(maapilot screen:*)",
      "Bash(maapilot input:*)",
      "Bash(maapilot maa:*)",
      "Bash(maapilot maa why:*)",
      "Bash(maapilot state:*)",
      "Bash(maapilot data:*)",
      "Bash(maapilot calc:*)",
      "Bash(maapilot goals:*)",
      "Bash(maapilot op:*)",
      "Bash(maapilot craft:*)",
      "Bash(maapilot report:*)",
      "Bash(maapilot report seen:*)",
      "Bash(maapilot daily run:*)",
      "Bash(maapilot daily status:*)",
      "Bash(maapilot daily watch:*)",
      "Bash(maapilot guard snapshot:*)",
      "Bash(maapilot guard check:*)",
      "Bash(maapilot guard status:*)",
      "Bash(maapilot lock status:*)",
      "Bash(sleep 300)",
      "Read(private/**)",
      "Edit(private/goals.md)",
      "Edit(private/goals-done.md)",
      "Edit(private/setup.json)",
      "Edit(private/shots/**)",
      "WebFetch(domain:prts.maa.plus)",
      "WebFetch(domain:prts.plus)",
      "WebFetch(domain:maa.plus)"
    ],
    "ask": [
      "Bash(maapilot guard allow:*)",
      "Bash(maapilot daily schedule:*)",
      "Bash(maapilot daily unschedule:*)",
      "Bash(maapilot lock release:*)",
      "Bash(maapilot reset:*)",
      "Edit(private/config.yaml)"
    ],
    "deny": [
      "Bash(maa run:*)",
      "Bash(maa fight:*)",
      "Bash(maa copilot:*)",
      "Bash(adb shell input:*)",
      "Edit(private/guard/**)",
      "Edit(private/lock.json)"
    ]
  },
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "\"$CLAUDE_PROJECT_DIR\"/bin/session-start",
            "timeout": 300
          }
        ]
      }
    ]
  }
}
```

- `maa why` 只读，`maapilot maa:*` 已经放行，单列一条写明它不碰模拟器。`sleep 300` 是会话里掉线时等用户回复的 5 分钟计时器（skill `maapilot-recovery`「掉线、被登出」，后台运行）。
- `reset` 在 **ask** 里：挪走用户的全部设置和数据，用户在确认框里点同意才做；无头运行里一律被拒（命令自己也拒绝）。
- `guard allow` 必须在 **ask** 里：这是代码里唯一的硬防线，用户在确认框里点同意才算数。
- 直接 `maa run` / `maa fight` 被拒：绕过了锁和对账，一律经 `maapilot maa`。setup 里装 maa-cli（`maa install`、`maa update`）不在拒绝之列，按默认弹确认。
- `private/guard/`、`lock.json` 只由程序写。
- `private/config.yaml` 的改动是 **ask**：里面有定时日常会执行的程序路径（`tools.*`、`emulator.adb`），无头 agent 不能改，会话里改要用户点同意。
- `guard snapshot` 可以放行：上次对账之后跑过可能花东西的命令时它改做对账（见「guard」），不能拿来抹掉花费。
- 启动钩子 `bin/session-start` 用 `$CLAUDE_PROJECT_DIR` 定位（不依赖钩子的工作目录）：先往 `$CLAUDE_ENV_FILE` 写一行把 `.venv/bin` 加进 PATH（环境还没建也写，setup 建好后同一会话就能用），再跑 `doctor --hook`：内容同 `--brief`，但输出成 Claude Code 钩子的格式 `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "MaaPilot 启动检查…<--brief 的信封>"}}`（直接输出的 JSON 会被当成钩子的控制字段，不进会话），退出码总是 0。
  `next` 是 `setup` / `guard` 时另带顶层的 `systemMessage`（直接显示给用户的一行）：钩子不会让 agent 先开口，要用户先说一句，这一行告诉用户"随便说一句就开始"。
  `.venv` 还没建时钩子自己按钩子的格式输出（`no_env`），agent 按 `maapilot-setup`「装运行环境」帮用户建。
- 启动钩子的输出进会话上下文；`CLAUDE.md` 让 agent 看到 `next: "setup"` 时，用户一开口就进入 `maapilot-setup`。
