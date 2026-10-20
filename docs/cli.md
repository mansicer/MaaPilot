# `maapilot` 命令契约

给实现命令的开发者和写 skill 的人：每个命令的参数、做什么、输出、碰不碰模拟器、要不要用户确认。设计依据见 [design.md](design.md)。

## 通用约定

- **调用形式**：agent 在仓库根目录用 `maapilot <命令> …`（不写 `./`、不用全路径、一次一条命令，权限规则按这个形式匹配）。
  `maapilot` 装在项目的虚拟环境 `.venv` 里（setup 时用 uv 建，见 skill `maapilot-setup`）；启动钩子 `bin/session-start` 把 `.venv/bin` 加进本次会话的 PATH，所以直接写 `maapilot`。
- **版本**：`maapilot --version` → `data: {version, commit, text}`（`version` 是 `maapilot.__version__`；`commit` 是当前提交的前 7 位，直接读 `.git`，
  不是 git 仓库时为 `null`；`text` 给人看，如 `"0.0.1（bff8a50）"`）。`doctor` 和 `doctor --brief` 的 `data.version` 同样是这个。
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

- **外部数据的格式变了**：森空岛、MAA 的活动列表 / 任务表、maa-cli 的输出、官方公告、官网安装包地址、企鹅物流、一图流、游戏数据表，
  程序用到的字段对不上时报 `error.code` 为 `upstream_format`（退出码 1，message 写明哪个来源、缺哪个字段，`data: {source, field, effect}`），
  不当成"没有数据"悄悄过去；有退路的（森空岛的理智 → 首页识别）照样退，同样记下来。都记进 `private/state/sources.json`，
  `doctor --brief` 的 `sources`、日常汇报的"要注意"会列出来（见 docs/design.md「外部数据源」）。这是要开发者改程序的事，原样重试没用。
- **坐标**：所有坐标、区域都是 1280×720 参考坐标系（截图也按这个尺寸存），与设备真实分辨率无关（要求 16:9）。
- **锁**：操作模拟器的命令（点击、滑动、输入、MAA 任务、要进页面的读取）自动拿模拟器锁（`private/lock.json`），拿不到退出码 4。
  只读的 `screen shot` / `screen ocr` 不拿锁：用不开输入服务的只读连接，别人（MAA、日常）正在操作时也能截图看看。持锁的进程（`daily run`）起的子进程
  （它调的 `maapilot maa …` 等）通过环境变量 `MAAPILOT_LOCK_TOKEN` 继承锁，子进程里的命令不会被挡（见 `maapilot/crew/lock.py`）。
- **记录**：每个碰模拟器的命令结束时在 `private/log/<日期>.jsonl` 追加一行；别的命令失败（包括参数写错、退出码 2）或有结果摘要的（`calc need`、`goals` 改动）也记一行（见[文件](#private-文件)）。
- **物品与干员名**：参数里用游戏里的中文名；输出里物品同时给 `id` 与 `name`。
- **长命令**：`daily run` 要 20–60 分钟，`maa fight`、`state box` 等可能几分钟。Claude Code 的 Bash 超时最长 10 分钟：
  预计超过几分钟的放后台跑，结束时会收到通知（`CLAUDE.md`）。Codex 的后台命令结束叫不醒模型：前台跑、等它结束。

## 命令总览

| 命令 | 碰模拟器 | 权限 | 负责 |
|---|---|---|---|
| `doctor [--brief] [--offline] [--fix]` | 除 `--brief`/`--offline` 外只读 | 放行 | A |
| `screen shot / ocr` | 只读，**不拿锁** | 放行 | A |
| `input tap / swipe / back / text` | 操作 | 放行 | A |
| `maa <任务>` | 操作 | 放行 | A |
| `maa why` | 否（只读 MAA 的日志） | 放行 | A |
| `copilot find / show / strip` | 否（联网查作业站） | 放行 | A |
| `state depot / box / sanity` | 只读（要进页面） | 放行 | A |
| `state show` | 否 | 放行 | A |
| `data …` | 否（`sync` 联网） | 放行 | A |
| `calc need / craft / stages / event` | 否 | 放行 | A |
| `lock status` / `lock release` | 否 | 放行 | A |
| `news list / show / told` | 否（`list` 联网） | 放行 | A |
| `game version` | 只读（adb 读版本，不拿锁；联网） | 放行 | A |
| `game update [--download-only] [--keep]` | 操作（只在安装时拿锁） | 放行 | A |
| `reset [--dry-run] [--memory]` | 否 | 放行 | B |
| `guard snapshot / check / status` | 只读 | 放行 | A |
| `guard allow` | 否 | **询问** | A |
| `goals list / add / update / remove / move / expire` | 否 | 放行 | B |
| `op upgrade / mastery / progress`、`craft` | 操作 | 放行 | B |
| `daily run / status` | `run` 操作 | 放行 | B |
| `daily done` | 否（agent 收尾这次日常） | 放行 | B |
| `daily unschedule` | 否（删 v0.1 留下的 crontab 行 / Windows 任务） | 放行 | B |
| `host status` / `host install <claude\|codex>` | 否（`install codex` 改 Codex 的用户设置） | 放行 | A |
| `report show / latest / list / write` | 否 | 放行 | B |

权限规则是 `maapilot` 整个放行、只列例外，表里没列的新命令也放行。
"放行"的命令不弹确认；"询问"的（`guard allow`，还有开发用的 `dev`）每次都弹宿主的确认（Claude Code 的 `ask`、Codex 的 `prompt`）。

**程序只死守真实货币**（docs/design.md「设计哲学」）：只有 `guard allow`（记下用户同意的一笔合成玉 / 源石花费）由程序把关——
有日常在跑、或者跑完还没收尾（`daily done` 之前；不分手动、定时）时，直接退出码 3 `needs_user`，什么也没做（`maapilot/tools/confirm.py`）：
定时日常多半是用户不在的时候跑的，这时记下的"同意"一定是假的；日常里本来就不该临时决定花真实货币，等收尾后用户在对话里同意再记。
别的会改东西的命令（`daily unschedule`、`reset`、`host install`、`lock release`、改 `config.yaml`）都放行：agent 先在对话里问清楚、用户同意后再跑（AGENTS.md 硬规则），程序不另弹确认。
`private/guard/` 下的记录文件 agent 改不改得了，靠宿主的权限 / 沙箱（Claude Code：拒绝编辑 `private/guard/**`；Codex 管不了，见「Codex 命令规则」）。

---

## doctor —— 环境检查

`maapilot doctor [--brief] [--offline] [--fix]`、`maapilot doctor --hook`（只给启动钩子用）

逐项检查，每项给出结果和修复建议。不拿锁（只读）。

- 默认：全部检查，包括连模拟器截一张图。
- `--offline`：不碰模拟器、不联网的那几项。
- `--fix`：顺手做不需要问用户的本地修复：从 maa-cli 资源复制 OCR 模型、把 maa-cli 配置模板（`templates/maa-config/` 的
  `cli.toml`、`profiles/default.toml`）装进本项目的 maa-cli 配置目录 `private/maa-config/`（已有的不覆盖）、缺数据表时 `data sync`、
  没有单独的 MaaCore（或版本太旧）时从 MAA 的 GitHub 发布下载（见下表 `maa_core`）。
  装软件（maa-cli、adb）不在这里做：由 agent 问过用户后用 `maapilot tools install`（见「tools」）。
- `--brief`：给启动钩子用，**不碰模拟器、不联网、1 秒内返回**，不做逐项检查，只给会话开头要知道的事（见下）。

检查项（`data.checks[].name`）；每项 `{name, ok, detail, fix}`，有的另带字段（`candidates`、`resolution`、`old_entries`…）。
标 `required: false` 的（`agent_config`、`game`、`legacy_schedule`、`skills_link`、`setup`、`maa_core`）只是提示，不影响 `ok_all`。有必需项没通过时退出码 5（`checks_failed`，`data` 照常给全）。

| name | 查什么 | 修复建议 |
|---|---|---|
| `config` | `private/config.yaml` 在、能解析、`emulator.address` 已填 | 做 setup |
| `adb` | adb 程序能跑（版本）；程序按 `config.adb_path`（`emulator.adb` 写了路径就照它，写 `adb` 时先用 `.tools/` 里装好的） | `maapilot tools install adb`（Windows 优先用模拟器自带的 adb.exe） |
| `emulator` | `adb connect` + 设备 online；地址没填或连不上时列出在线的候选（`adb devices` 与常见端口的 `adb connect` 探测） | 开模拟器、打开 ADB 调试、填地址 |
| `screen` | `adb exec-out screencap` 能截图；分辨率是 16:9 且不小于 1280×720 | 模拟器设置里改分辨率 |
| `input` | 模拟器里有 `input` 命令（能发点击）；不真的点 | 换 / 重装模拟器 |
| `maa_cli` | `maa version`：maa-cli 与 MaaCore 版本；`maa dir resource` 下有资源；`path` 是 maa 的绝对路径。`tested` 是实测过的 maa-cli 版本范围（`toolchain.MAA_CLI_TESTED`，发版前实测后更新）：低于下限不通过，高于上限照样通过、`warnings` 里提醒 | `maapilot tools install maa-cli`（太旧时也是它；`tools.maa` 写了别的路径时改成 `maa`） |
| `agent_config` | `config.yaml` 里还写着 v0.1 的设置（`agent`、`schedule_options`、`daily.agent_timeout_min`、`daily.recovery_timeout_min`：定时叫起无头 agent、系统定时用的）：现在不用了，写了也忽略；`legacy` 列出是哪几个。`required: false` | 用户同意后从 `config.yaml` 删掉（不删也不影响） |
| `maa_config` | `private/maa-config/` 里有 `cli.toml`、`profiles/default.toml` | `doctor --fix` |
| `maa_core` | 单独装的 MaaCore（`private/cache/maa-core/<版本>/`，`op progress` 用；maa-cli 自己的本体还跑不了干员培养）在、且不低于 v6.19.0。`required: false`：没有只影响 `op progress` | `doctor --fix`（下载 MAA 最新发布的 macOS / Linux 本体，约 370MB） |
| `ocr_model` | `resource/model/ocr/` 下 `det.onnx`、`rec.onnx`、`keys.txt` | `doctor --fix`（从 maa-cli 资源的 `PaddleOCR` 复制） |
| `game_data` | `data/` 下数据表齐全；超过 30 天没同步算没通过 | `doctor --fix` 或 `data sync` |
| `game` | 游戏是否在前台（按 `server` 推出的包名看 `dumpsys window`）；`package` 是前台应用的包名；`installed` 是模拟器里装着的明日方舟客户端 `[{package, server}]`（`pm list packages`，游戏没开也能看；查不了为 `null`）。setup 据这两样推服务器；前台或装着的是别的服时提示改 `server`，一个都没装时提示去装 | `maa startup` |
| `legacy_schedule` | 系统里（crontab / Windows 任务计划程序）还留着 v0.1 的定时日常没有（同 `daily unschedule` 删的那些；`times`、`entries`）：留着会到点自己跑 `daily run`，不通过。更旧的 MaaPilot 的定时（cron：`maapilot job tick`、`bin/daily.sh`；Windows：`\MaaPilot\` 下别的目录的任务）列在 `old_entries`，不算。读不了时不通过（`detail` 写原因）；这台电脑上没有系统定时（Windows 上找不到 schtasks.exe）算通过。`required: false` | 用户同意后 `daily unschedule`，再用 harness 的定时在会话里定好（`maapilot-setup`） |
| `skills_link` | `.claude/skills`（Claude Code 找 skill 的地方）是指向 `.agents/skills` 的目录；`state`：`file`（Windows 上 git 没建符号链接，检出成文本文件）/ `missing` / `broken` | `doctor --fix`（macOS / Linux 建符号链接，Windows 建目录联接） |
| `setup` | `private/setup.json` 在不在 | 做 setup |

`--offline` 跳过 `emulator`、`screen`、`input`、`game`（其余几项只跑本机命令）。

```json
{"ok_all": false,
 "setup_done": true,
 "offline": false,
 "version": {"version": "0.0.1", "commit": "bff8a50", "text": "0.0.1（bff8a50）"},
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
 "daily_open": {"run_id": "20260930-0440", "trigger": "schedule", "started_at": "…", "age_h": 1.5, "stale": false, "running": false},
 "legacy_schedule": {"times": ["04:40", "16:40"], "backend": "cron"},
 "next": "setup",
 "version": {"version": "0.0.1", "commit": "bff8a50", "text": "0.0.1（bff8a50）"},
 "goals": [{"title": "昨日海", "object": "活动 昨日海", "deadline": "2026-10-30T03:59+08:00", "days_left": 20.5}],
 "news": {"at": "…", "lines": ["进行中：昨日海（到 10月30日 03:59）"], "due": ["6774:2026-10-09T12:00:00+08:00"]},
 "sources": [{"source": "skland", "name": "森空岛", "at": "…", "message": "森空岛的数据格式变了（缺 status.ap）",
              "effect": "理智改用首页识别"}],
 "lines": ["MaaPilot 还没设置：用户一开口就进入 maapilot-setup（…）"]}
```

`next`：`"setup"`（没有 `setup.json`，用户一开口 agent 就进入 `maapilot-setup`）、`"guard"`（对账停机中，先告诉用户）或 `null`。
`lines` 是给会话开头看的几行中文摘要。`daily_open`：没收尾的日常（同 `daily status` 的 `open`，另有 `running`：`daily run` 还在跑；正在跑时带 `step`），没有为 `null`；
`lines` 里写一行：还在跑 / 还没收尾（做完目标部分、领奖励、对账后 `daily done`）/ 超过 6 小时没收尾（下次 `daily run` 自动收尾）。
`legacy_schedule`：系统里还留着 v0.1 的定时日常（同 `doctor` 的 `legacy_schedule` 项）时是 `{times, backend}`，`lines` 里提示用户同意后 `daily unschedule`、再用 harness 定好；没有（或看不了）为 `null`。
`last_daily` 取 `private/reports/` 里最新的运行汇报 `<YYYY-MM-DD-HHMM>.json`（读它的 `finished_at` / `at` / `started_at` 与 `ok`）；
`goals_active` 用 `maapilot.flows.goals.active_count()`。`goals`：进行中的目标（`title`、`object`、`deadline`、`days_left`，同 `goals list`），`lines` 里逐个列出，
让 agent 做别的事时想着别用掉它们要的材料；读不了为 `null`。`news`：缓存里的官方日程（不联网；只有国服——`server` 是 `Official` 或 `Bilibili`——有缓存时才有，否则 `null`）：
`lines` 同 `news list`，`due` 是还没告诉用户的时间点的 key（新公告、活动开始、维护结束）（`lines` 里也写一行，agent 按 `maapilot-events` 去说），列到 `news told` 为止。
`sources`：格式出了问题的外部数据源（`private/state/sources.json` 里最近一次出错比最近一次读成还新的；没问题的不列，全好是 `[]`），
`lines` 里每个一行（"外部数据：森空岛的数据格式变了（缺 status.ap），理智改用首页识别（要开发者修；…）"）。
`live_suspects`：可能读错了的现场练度（记下一天后森空岛还比它低，已不再采用；见「goals」的现场练度）：`[{name, at, source, parts}]`，`lines` 里每个一行。
v0.1 的 `schedule`、`unseen_problems`、`session` 去掉了（不再自己定时、不再记"告诉过用户没有"、不再记会话往里推消息）。
`memory`：用户偏好记录 `{dir: "private/memory", index: [MEMORY.md 里的 "- …" 行], imported}`（没设置过为 `null`）。偏好由 agent 读写（格式见 `AGENTS.md`「用户偏好」），
不依赖哪个 agent 的记忆功能。还没有 `private/memory/MEMORY.md`、也没搬过（没有记号文件 `private/memory/.imported-from-claude`）时，启动检查把 Claude Code 给这个项目记的自动记忆复制过来
（目录已经在也照样搬；同名文件不覆盖；原来的不删），写下记号（来源和时间；之后用户删掉 MEMORY.md 也不会再搬），`imported` 是复制的文件名。

## screen —— 截图与识别（只读，不拿锁）

不拿模拟器锁、不记进对账的 dirty：MAA 或日常正在操作时也能用。MaaFramework 连接时输入方式设为 `Null`（`Game(readonly=True)`），
设备上不起 maatouch / minitouch，也不发任何点击，所以不会和正在跑的任务抢输入；截图方式和平时一样（`RawWithGzip | Encode`，缩到 1280×720）。

`maapilot screen shot [--name <名>]`
截一张图存到 `private/shots/<YYYYmmdd-HHMMSS>[-<名>].png`（1280×720）。agent 用读图工具看它：`path` 是绝对路径。

```json
{"path": "/…/private/shots/20260930-214001-home.png", "width": 1280, "height": 720, "at": "…"}
```

`maapilot screen ocr [--roi X Y W H] [--find <正则>] [--name <名>] [--image <图片>]`
截图并识别文字（MaaFramework + PaddleOCR）。`--roi` 只识别一块（越界退出码 2 `bad_roi`）；`--find` 只返回匹配的。截图同样存下。
`--image` 不连模拟器、不截图，在一张已有的截图上跑同一套识别（排查识别错误、做回归样本用，见 design.md「识别回归测试」）；
`path` 就是这张图。图片不在、读不了或不是 16:9 退出码 2（`bad_image`）；OCR 资源加载不了退出码 5（`ocr_unavailable`）。

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
| `copilot <作业>` | `[--formation] [--formation-index 1-4] [--add-trust] [--ignore-requirements] [--support 0-3] [--support-name <干员>] [--raid normal\|raid\|both] [--loop-times N]` | `maa copilot`（作业：`prts://<id>`（旧写法 `maa://<id>`、纯数字也认；作业集 `prts://s<id>` 还不支持）或本地 JSON，如 `copilot strip` 存的。作业站的作业先取下来存成 `private/cache/copilot/<id>.json` 再把文件交给 maa-cli：0.7.6 起它的作业码改成 `prts://`、`maa://` 会报弃用，0.7.5 只认 `maa://`，给文件哪个版本都一样，`data.copilot.file` 是这个文件；`--formation` 自动编队，`--formation-index` 用第几个编队，`--add-trust` 空位按信赖从低到高补人；`--ignore-requirements` 不管作业写的练度要求照样开打；`--support` 助战：0 不用（默认）、1 只缺一人时借、2 缺一人时借否则借 `--support-name`、3 缺一人时借否则随便借；`--raid raid` 突袭：只给一份作业时 maa-cli 丢掉突袭设置、按屏幕上的模式打，所以开打前截一张图核对关卡详情页是突袭模式（只看、不点），核对结果写进 `data.copilot.raid_check: {mode, shot}`，不是突袭就不开打（`not_raid_mode`，见下）；`--raid both` 单份作业做不到，退出码 2（`bad_arguments`），分两次跑；不用理智液。用了哪些写进 `data.copilot`） |
| `closedown` | | CloseDown（关游戏） |

`config.yaml` 的 `daily.params.<任务>` 会先合进默认参数，`--params` 最后合。理智液（`--medicine N`）：用户第一次同意后按记忆办。
认得出的资源本 / 芯片本今天不开时不跑 MAA，直接退出码 1（`not_open`，`data.open_weekdays`）。
活动关按 MAA 的活动列表判断（见「data」的活动关）：MAA 导航不了的（列表里还没有这期活动、这期只支持别的几关、活动没开始或已结束）不跑 MAA，
退出码 1（`maa_unsupported_stage`，`data: {stage, stage_id, reason}`；hint：MAA 一般活动开始后一两天更新，或手动进关卡页用代理）。数据表里认不出的编号原样交给 MAA。

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

代理失败（代理指挥没打赢：游戏退回理智、没有掉落，MAA 日志记成掉落"理智 × N"）不算掉落：`drops` 里没有理智，`sanity_used` 扣掉退回的，
另有 `proxy_failed`（几场）和 `note`；`times_done` 仍算上这几场。`daily run` 刷理智的 `result` 也带 `proxy_failed`，汇报里写一句。

`fight` / `annihilation` 结束时用日志里最后一次的理智更新 `private/state/sanity.json`（`source: "maa-log"`）。
`annihilation` 另有 `orundum_gained`（这次掉的合成玉）、`week`（本游戏周累计 `{runs, orundum}`）和 `weekly_capped`（本周合成玉已经拿满：打了、认出了掉落却没有合成玉，或累计到 1800）。
每周记录在 `private/state/annihilation.json`（`{week, runs, orundum, capped, progress?}`，周一 04:00 清零）；`weekly_capped` 之后 `daily run` 跳过剿灭（`tasks` 里这一项 `skipped`）。
接了森空岛时，游戏里的本周剿灭合成玉（`{current, total}`，比自己累加的准）以它为准覆盖 `orundum`、拿满就 `capped`，记在 `progress: {current, total, source: "skland", at}`：
`state skland` 每次顺手记；`daily run` 判断要不要跳过剿灭前（自己的记录还没拿满时）也只为这个读一次森空岛（不碰模拟器、不改 `box.json`；读不到按自己的记录）。
剿灭没做成不另记：`daily run` 和别的项一样交回 agent，由它判断原因（不能代理、找不到入口、掉线），要用户决定的写进总结。

失败退出码 1，`error.code`：`maa_failed`（`data.log`、`data.errors` 是日志里最后几行错误）、`offline`（日志里有 GameOffline：掉线 / 服务器维护，
重登或稍后重试，不用接管）、`not_open`（关卡今天不开）、`maa_unsupported_stage`（MAA 导航不了这个活动关）、`not_raid_mode`（`copilot --raid raid` 开打前关卡详情页不是突袭模式：`data.raid_check.mode` 为 `normal` 是还在常规模式，`unknown` 是认不出模式按钮（不在详情页，如已进编队页，或这关没有突袭），`data.raid_check.shot` 是那张截图；没跑 maa、没花理智，切到突袭、停在详情页再跑）。maa-cli 找不到退出码 1（`maa_failed`，message 是启动失败的原因）。
maa 报成功（退出码 0）而输出里认不出程序要读的行时（`maa run` 的标准输出里没有 Summary 任务行；`fight` 的日志里既没有 `Current sanity` 也没有 `Mission started`），
命令照常返回，`data.upstream_format` 是一句说明，同时记进 `sources.json`（maa-cli 的输出格式可能变了，次数、理智、掉落记不到）。

**失败时取证**：maa 跑完而没成功时（`maa_failed`、`offline`），在做别的任何事之前先截一张图（同 `screen shot`，存 `private/shots/<时间>-fail-<任务>.png`），
再从 MAA 的日志目录（`maa dir log`；测试或特殊安装可以用环境变量 `MAAPILOT_MAA_LOG_DIR` 指定）里找这次任务开始以后 MAA 自己存的截图
（`interface/`、`other/` 等子目录下的 `YYYY.MM.DD-HH.MM.SS.mmm_raw.png`，本地时间，最多 6 张），**复制进 `private/shots/`**
（`<YYYYmmdd-HHMMSS>-maa-<任务>-<毫秒>.png`：MAA 的日志目录在项目外，agent 不一定读得了），放进 `data.evidence`：

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

## copilot —— MAA 作业站（不碰模拟器）

查 MAA 作业站（`prts.maa.plus`）的作业，对照自己的干员练度（`private/state/box.json`）：**只摆事实**（缺谁、差多少），不判断打不打得过。不拿锁。
森空岛来源的快照有技能等级、专精、模组；MAA 识别来源只有精英化、等级、潜能，读不出的项算"不知道"（`unknown`），不算差距。

| 命令 | 做什么 |
|---|---|
| `copilot find <关卡> [--limit N]` | 关卡 → stageId（同 `data stage` 的认法；认不出就原样当关键词，重名退出码 2 `ambiguous_stage`），按热度查最多 N 份（默认 20），逐份标注；排序：干员都有的在前，再按差距数少、热度高 |
| `copilot show <作业编号>` | 一份作业（编号或 `prts://编号`）：同上的标注，另给 `details`（作者说明）、`difficulty`、`minimum_required`（MAA 版本）、`actions`（动作数） |
| `copilot strip <作业编号>` | 去掉作业里所有干员的练度要求（`requirements`），存成 `private/cache/copilot/<编号>_noreq.json`（`maa copilot --ignore-requirements` 还被 MAA 拒绝时，把这个文件交给 `maa copilot`）。`{id, path, removed}` |

```json
// copilot find
{"stage": "YW-8", "stage_id": "act…_08", "keyword": "act…_08", "count": 20, "box_at": "…", "box_source": "skland",
 "plans": [{"id": 64408, "uri": "prts://64408", "title": "…", "stage": "…", "uploader": "…", "uploaded_at": "…",
            "views": 1200, "hot": 35, "like": 40, "dislike": 1, "rating": 9,
            "opers": [{"name": "能天使", "skill": 3, "required": {"elite": 2, "level": 60, "skill_level": 10},
                       "have": true, "current": {"elite": 2, "level": 90, "potential": 1, "skill_level": 7, "mastery": 2, "modules": [...]},
                       "gaps": ["要技能3 专3，现在专2"], "unknown": []}],
            "groups": [{"name": "…", "opers": [...], "have": true, "best": "…", "gaps": 0}],
            "missing_ops": ["…", "群攻（任选：…/…）"], "gap_count": 1, "all_owned": false}]}
```

- `required` 是作业的 `requirements`（MAA 作业协议：`skill_level` 1–7 是技能等级、8–10 是专精 1–3；`module` 1 χ、2 γ、3 α、4 Δ；`potentiality` 从 1 数）。
- `gaps`：精英化与等级、技能等级、专精、模组、潜能没到的项（中文）；没有这个干员是 `["没有这个干员"]`。森空岛把没解锁的模组也列出来：有 `locked` 照它，没有就按"没精2 → 没有、等级 ≥2 或装着 → 有、其余不知道"推。
- `groups` 是可选位（任选一名）：`best` 是有的里面差距最少的，`gaps` 是它的差距数。没有 `box.json` 时 `have`、`current`、`gap_count`、`all_owned` 都是 `null`。
- `rating`：评分人数不够时为 `null`。连不上作业站退出码 1（`network`）；没有这份作业退出码 2（`not_found`）；编号不是数字退出码 2（`bad_arguments`）；`strip` 读不出作业内容退出码 1（`bad_plan`）。

## skland —— 森空岛（非官方接口）

从森空岛直接读账号数据，比 MAA 识别快、准、全（技能、专精、模组都有），不碰模拟器。要用户的**鹰角账号 token**，只存在本机。

| 命令 | 谁跑 | 做什么 |
|---|---|---|
| `skland token [--uid <uid>]` | **用户自己在终端里跑**（agent 的权限里是拒绝） | 从标准输入读 token（终端里不回显；也接受整页 JSON 粘贴），先完整登录一次验证，再存进 `private/skland.json`（权限 600）。绑了几个明日方舟角色时按 `config.yaml` 的 `server` 挑（官服 / B 服），挑不出退出码 2 `choose_role`（`data.roles`），用 `--uid` 指定 |
| `skland status` | agent | `{configured, uid, role, saved_at, last: {at, ok, error?, code?}, token_expired}`；不显示 token |
| `state skland` | agent、日常 | 见「state」 |

token 怎么来：在浏览器登录鹰角官网（https://user.hypergryph.com/login），再打开 https://web-api.hypergryph.com/account/info/hg，
页面上的 JSON 里 `data.content` 就是（森空岛网页版登录后 https://web-api.skland.com/account/info/hg 也行）。token 过一段时间会失效，失效了重新取、重新跑 `skland token`。

流程（2026-10 按社区实现核对，`maapilot/crew/skland.py` 写了细节）：token → `POST as.hypergryph.com/user/oauth2/v2/grant`（appCode `4ca99fa6b56cc2ba`）拿授权码
→ `POST zonai.skland.com/web/v1/user/auth/generate_cred_by_code` 拿 cred 与签名密钥（请求头带数美设备号 `dId`，`maapilot/crew/skland_device.py`）
→ `GET zonai.skland.com/api/v1/game/player/binding`（明日方舟的绑定角色）→ `GET …/api/v1/game/player/info?uid=`。后两个要签名：
`sign = md5(hmac_sha256(签名密钥, 路径 + query + 时间戳 + {"platform","timestamp","dId","vName"} 的紧凑 JSON))`，时间戳按服务器的 `Date` 校准。

`state skland` 的 `data`：`{at, source: "skland", uid, operators, with_modules, mastered, sanity: {current, max}, annihilation}`（`--op` 另给这些干员的 `operators_detail` 和 `missing`）。
`annihilation`：`{current, total, max_kills: {<campaignId>: {name, kills}}}`（本周剿灭合成玉、每张剿灭图的最高击杀，400 = 能代理；接口格式变了为 `null`），顺手记进本周剿灭记录（见「maa」）。
`box.json` 里每名干员：`{id, rarity(1–6), elite, level, potential(1–6), skill_level, skills: [{index, id, name, mastery}], modules: [{id, name, level, equipped, locked}]}`
（`locked` 照接口原样，森空岛把没解锁的模组也列出来；潜能同 MAA 从 1 数，接口的 `potentialRank` 从 0 数、读进来加 1；模组名取游戏数据表，没有就用接口给的，再没有就是 id；技能名取 `skill_table`，没有就是 null）。失败不改 `box.json`。错误：没接 → 退出码 2 `not_configured`；
token 失效 → 退出码 3 `token_invalid`（请用户重新取 token、在终端跑 `skland token`）；接口、网络、设备号 → 退出码 1 `skland_api` / `skland_network` / `skland_device`。
接口格式变了：干员数据（`chars[]` 的 charId / evolvePhase / level / potentialRank / mainSkillLvl、`skills[]` 的 id / specializeLevel、`equip[]` 的 id / level、`charInfoMap`）
对不上 → 退出码 1 `upstream_format`，不改 `box.json`；理智（`status.ap.current / max`）、剿灭（`campaign.reward.current / total`）对不上 → 命令照常成功、
那一项为 `null`，记进 `sources.json`。`state sanity` 遇到理智格式变了改用首页识别，`skland_error` 写原因。
token 不出现在任何输出、日志、报错里；Claude Code 里 agent 读不了也改不了 `private/skland.json`（权限设置里拒绝）；Codex 的规则只管命令、拦不住读文件，靠 agent 守 AGENTS.md 硬规则不去读。

## news —— 官方日程（只有国服，不碰模拟器）

国服游戏内公告（官服、B 服共用一套维护和活动日程，`news.applies()`；别的服日程不一样，不看）（游戏里"公告"那一栏，不用登录：`ak-webview.hypergryph.com/api/game/bulletinList`、`bulletin/<cid>`）里的停机维护和活动（`maapilot/knowledge/news.py`）。
程序只做可靠的事：拉下来、去掉 HTML、按固定写法抽出时间段（`10月09日 12:00 - 10月30日 03:59`、`维护时间：2026年10月09日06:00 ~ 12:00`；没写年份取发布那年，止只写时刻算同一天），
分成 `活动`（标题有 活动即将开启 / 活动开启 / SideStory / 限时活动）、`维护`（标题有 维护 / 闪断 / 版本更新，或正文开头写了停机维护、闪断更新）、`其他`（只存标题）。
只看最近 30 天发的公告；抽不出的时间由 agent 读全文自己看。缓存 `private/state/news.json`：`{at, items, told: {<key>: 时间}}`，超过 6 小时算旧。
`told`：告诉过用户了（`news told`）。拉公告时先拉、再读盘上的 `told` 合进来存（别的进程这几秒里记下的不丢）。

| 命令 | 做什么 |
|---|---|
| `news list [--refresh] [--days N]` | 缓存旧了（或 `--refresh`）先重拉：拉不到而有缓存就用缓存（`refresh_error` 写原因），没缓存退出码 1（`network`）。`--days`：`upcoming` 看几天（默认 3）。`server` 不是官服或 B 服退出码 2（`unsupported_server`） |
| `news show <cid>` | 一条公告的全文（`其他` 类现拉）。缓存里没有退出码 2（`not_found`，先 `list --refresh`），拉不到退出码 1（`network`） |
| `news told <key>…` | 这些时间点已经告诉过用户（记进 `told`）：启动检查、`daily run` 的结果不再提。agent 在会话里说了就是说过了，日常在跑或没收尾时也能记。`{told, due}`；不认识的 key 退出码 2（`unknown_event`） |

```json
// news list
{"at": "2026-10-09T16:23:29+08:00", "refresh_error": null,
 "maintenance_now": {"cid": "5699", "title": "…", "start": "…", "end": "…", "forced_update": true},   // 不在维护时段为 null
 "upcoming": [{"key": "5699:维护开始", "at": "…", "what": "维护开始", "cid": "5699", "title": "…", "forced_update": true, "end": "…"}],
 "due": [{"key": "6774:2026-10-09T12:00:00+08:00", "at": "…", "what": "开始", "cid": "6774", "title": "…", "label": "关卡", "end": "…"}],
 "lines": ["10月9日 12:00 开始：昨日海", "进行中：昨日海（到 10月30日 03:59）"],
 "items": [{"cid": "6774", "title": "…", "kind": "活动", "published": "2026-10-08", "windows": [{"label": "…", "start": "…", "end": "…"}]},
           {"cid": "5699", "title": "…", "kind": "维护", "published": "…", "windows": [...], "maintenance": {"label", "start", "end"}, "forced_update": true}],
 "others": ["…其他公告的标题…"]}
```

- **时间点**（`upcoming` / `due` 的每一项）：维护公告给 `维护开始`、`维护结束`（key `<cid>:维护开始` / `<cid>:维护结束`；维护时段取标签带"维护"的那段，没有就取第一段）；
  活动公告每个开始时刻一个 `开始`（key `<cid>:<开始时刻>`；同一刻开始的几段算一个，`end` 取最长的）；
  活动、维护公告本身还有一个 `公告`（key `<cid>:公告`，`at` 是发布日，带 `kind`、`start`（最早开始）、`end`（最晚结束）、`forced_update`）。`forced_update`：正文写了强制更新 / 重新下载和安装。
- `upcoming`：接下来 `--days` 天内的时间点。`due`：还没 `told`、会话里的 agent 该按 `maapilot-events` 去跟用户说的：
  `公告`（新的活动 / 维护公告，还没开始；同一条公告别的时间点已经 `told` 过的不算），活动 `开始`（开始后 72 小时内、还没结束），`维护结束`（结束后 72 小时内）。
  公告缓存超过 6 小时 `daily run` 开头会重拉（`news list` 也会），所以新公告到下一次日常就提醒。
- `maintenance_now`：现在在不在某条维护公告的维护时段里。`lines`：正在维护、接下来几天的维护和活动开始、进行中的活动，给启动检查和转述用。
- `daily run` 开头（缓存旧了重拉、维护中不跑，结果里带 `news`；都只对国服）、`doctor --brief`（只读缓存，`due` 列到 `told` 为止）都用这份缓存。

## game —— 游戏客户端版本与更新（只有官服）

| 命令 | 做什么 |
|---|---|
| `game version` | 装着的版本（`adb shell dumpsys package <包名>`）和官网最新版（跟随 `https://ak.hypergryph.com/downloads/android_lastest` 的跳转，只请求 1 个字节，不下载安装包）。只读，**不拿锁** |
| `game update [--download-only] [--keep]` | 要更新时：下载安装包到 `private/cache/game/`（断点续传，先写 `.part`；已有完整的同名包直接用），**下载不拿锁**（十几分钟，不挡日常）；再拿模拟器锁 `am force-stop` 关游戏、`adb install -r` 覆盖安装（账号数据不丢）、重读版本核对。不启动游戏：第一次启动由 agent `maa startup` 做（资源下载、公告、用户协议弹窗）。`--download-only` 只下载、不拿锁；`--keep` 装完留着安装包（默认装完删掉缓存里的安装包和 `.part`） |

```json
// game version
{"installed": {"version": "2.7.71", "code": 123, "updated_at": "2026-09-20 10:00:00"},   // 没装为 null
 "latest": {"version": "2.7.81", "url": "https://…/arknights-hg-2781.apk", "size": 2147483648},
 "update_needed": true,          // 比不出为 null
 "accept_terms": null}           // config 的 game.accept_terms
// game update
{"updated": true, "from": "2.7.71", "to": "2.7.81", "apk_bytes": 2147483648, "duration_s": 812.3}
{"updated": false, "reason": "已经是最新版 2.7.81", "from": "2.7.81", "to": "2.7.81"}
{"updated": false, "reason": "只下载，没有安装", "from": "…", "to": "…", "apk": "private/cache/game/…apk", "apk_bytes": …, "duration_s": …}
```

版本号从安装包文件名来（`arknights-hg-2781.apk` → `2.7.81`），按数字逐段比。下载地址跳转后只认 https 的 `hypergryph.com`、`hycdn.cn`（及子域名）。

| error.code | 退出码 | 什么情况 |
|---|---|---|
| `unsupported_server` | 2 | `server` 不是官服（请用户自己更新） |
| `adb_failed` | 5 | adb 读不了版本、关不了游戏（`doctor`）；没配置、没填地址同样退出码 5 |
| `network` | 1 | 连不上官网、下载失败或中断、下载中安装包大小变了（官网刚发新版）；再跑一次会接着下 |
| `unexpected_download` | 1 | 下载地址跳到别的域名：不下载，告诉用户 |
| `upstream_format` | 1 | 跳到了官网域名，但文件名不是 `arknights-hg-<版本>.apk`：认不出最新版本（记进 `sources.json`），告诉用户、请用户自己更新 |
| `version_unknown` | 1 | 比不出要不要更新（`data` 同 `version`） |
| `disk_full` | 3 | 电脑磁盘不够（还要下的 + 1 GB 余量；`data.free_bytes`、`need_bytes`、`apk_bytes`）：请用户腾空间 |
| `download_incomplete` / `bad_apk` | 1 | 下得不全（再跑接着下）；下下来的不是 zip（已删） |
| `install_failed` | 1 | `adb install` 没成（`data.reason` 是 `INSTALL_FAILED_*`、`adb_output`、`apk`；`hint` 按原因给：模拟器存储不够、签名不一致、版本降级、CPU 架构）；安装包留着 |
| `verify_failed` | 1 | 装完读到的版本不对 |
| `busy` | 4 | 安装时模拟器被占用（下载好的包留着，稍后再跑） |

`daily run` 开头用同一个 `version_info()` 查版本（见「daily」）。

## state —— 读账号状态，存快照

| 命令 | 做什么 | 快照 |
|---|---|---|
| `state depot` | MAA 仓库识别（Depot），自动从首页进仓库 | `private/state/depot.json` |
| `state box` | MAA 干员识别（OperBox）：精英化、等级、稀有度、潜能 | `private/state/box.json` |
| `state op <干员>` | 进这名干员的干员页读精英化、等级、技能等级（MaaFramework，只读，约一分钟；不改 `box.json`，记成现场练度，见「goals」）。专精、模组读不了（`unread` 列出没读到的） | `live.json` |
| `state skland [--op <名>…]` | 从森空岛读（**不碰模拟器、不拿锁**，要先接森空岛，见「skland」）：每名干员的精英化、等级、稀有度、潜能，**加上**技能等级、各技能专精、模组（等级、装没装）；顺手更新理智 | `box.json`（`source: "skland"`）、`sanity.json` |
| `state sanity` | 接了森空岛就从森空岛读（准、几秒、**不碰模拟器、不拿锁**，`source: "skland"`）；没接或读不到（`skland_error`）才回首页 OCR 右上角的理智（大号的当前值 + 下面的"理智/上限"，`source: "ocr"`）。OCR 读数比上一份快照推出来的（6 分钟 1 点）少 20 以上时带 `suspect: true`、`estimate`（推算值）、`shot`（首页截图）和 `hint`：首页数字上压着图标，真机读错过（130 → 29、114 → 4）；用户自己打过关时读数低也正常，以截图为准 | `private/state/sanity.json` |
| `state show [depot\|box\|sanity] [--item <名>…] [--op <名>…]` | 读快照，不碰模拟器；不写种类时给三样里有的 | — |

每次读完覆盖快照，`state depot` 并在 `private/log/` 的命令记录里记下和上一份的差（`depot_change`）。`--item` / `--op` 只筛选输出。
OperBox 读不到技能等级、专精、模组：接了森空岛就用 `state skland`（全都有）；没接时技能等级用 `state op <干员>` 读；专精、模组以用户说的或在游戏里看到的为准，写在目标的"分解与进展"里。
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
{"at": "…", "current": 82, "max": 135, "source": "skland", "age_min": 0}
// state sanity（OCR、读数可疑）
{"at": "…", "current": 29, "max": 205, "source": "ocr", "suspect": true, "estimate": 130, "shot": "private/shots/…", "hint": "…"}
```

`state show` 在快照不存在时退出码 2（`no_snapshot`，hint：先 `state depot`）。

## data —— 游戏数据（不碰模拟器）

| 命令 | 输出 |
|---|---|
| `data sync [--check]` | 从 ArknightsGameData 同步数据表、刷新企鹅物流掉率与一图流价值；`--check` 只看要不要更新。`{updated, to_update, missing, recipes_rebuilt, live, ages: {matrix_age_h, values_age_d}}`；联网失败退出码 1（`sync_failed`）；下下来的表缺程序用的字段 → 不替换本地的表，退出码 1（`upstream_format`）。企鹅物流、一图流的格式问题写在 `live` 里、记进 `sources.json`，不算失败 |
| `data operator <干员>`（别名 `op`） | `{name, id, rarity, profession, levels: [{elite, max_level, to_max: {exp, lmd}}], elite: [{elite, lmd, items, needs_level}], skill_levels: [{to, items}], mastery: [{skill, ranks: [{rank, items, hours}]}], modules: [{type, name, levels: [{level, lmd, items}]}] \| null}` |
| `data item <物品>`（别名 `drops`） | `{id, name, rarity, type, craftable, recipe, stages: [{stage, ap, rate, sanity_per_item, open_days, open_today, event_end?}], drops_source}`：掉落最好的几关（常驻主线 / 资源本 / 芯片本，加上 MAA 现在能导航的活动关，按每件期望理智排；活动关带 `event_end`：活动什么时候结束） |
| `data recipe <物品>` | `{tree, shop}`：合成树（加工站 / 制造站），每一步的材料与龙门币、叶子的主掉落关；能在商店兑换的另给 `shop` |
| `data stage <关卡>` | `{code, stage_id, name, ap, navigable, kind, nav_kind, maa_value, open_days, open_today, drops: [{id, name, rate, samples}], listed_drops, not_navigable_reason?, maa_activity?}` |

`data stage` 的 `nav_kind`（`kind` 同值）：`main` / `resource` / `chip` / `annihilation` / `ss_current`（当期活动）/ `ss_reopen`（复刻）/ `not_navigable`；`maa_value` 是给 MAA 的关卡值（导航不了为 `null`）。

**活动关**：MAA 只能导航它活动列表里、当期开放的活动关。列表是 maa-cli 缓存的 `StageActivityV2.json`（macOS `~/Library/Caches/com.loong.maa/`，其他 `$XDG_CACHE_HOME/maa/`；
MAA 一般在活动开始后一两天的资源热更新里加上这期），能导航的是这期列出的关，加上 MAA 任务表（同目录 `resource/tasks/tasks.json`）里有 `<编号>@SideStoryStage` 导航任务的同前缀关。
测试用环境变量 `MAAPILOT_MAA_ACTIVITY_FILE`、`MAAPILOT_MAA_TASKS_FILE` 换掉这两个文件。没有列表文件时活动关一律不可导航（也没有原因）。
导航不了的活动关有 `not_navigable_reason`（MAA 还没加这期活动 / 只支持哪几关 / 活动没开始或已结束）；活动关另有
`maa_activity`：`{in_maa_list, name, start, end, listed, supported, reopen, open_now, min_maa}`（列表里没有这期时只有 `in_maa_list: false`）。

名字认不出退出码 2（`unknown_name`，`data.suggestions` 给相近的名字）；关卡编号重名退出码 2（`ambiguous_stage`，写成 `活动名/编号`）。
数据表不全退出码 5（`no_game_data`，先 `data sync`）。没有企鹅物流缓存时掉率为空，只列数据表里的主掉落关。

## calc —— 纯计算（不碰模拟器，只给建议）

读 `private/state/` 的快照做对照；快照的时间放在输出里（`depot_at`），太旧的由 agent 决定要不要先 `state depot`。

`maapilot calc need "<对象>" [--from "<当前>"] [--goal <标题>] [--ignore-goals]`
一个养成目标要多少：`<对象>` 的写法同[目标清单的"对象"行](#对象的写法)。当前练度取干员快照：精英化、等级都有；技能等级、专精、模组只有森空岛来源的快照（`state skland`）才有，
`--from` 给的优先（如 `--from "技能 4级"`）；都没有就按最低算并在 `assumed` 里说明。

默认还对照别的进行中目标（干员、材料目标；按优先级）还要用的材料：干员目标从快照里的当前练度算起，材料目标按写的数量；
`--goal <标题>` 说明算的就是这个目标（不算它自己占的；不给就排除对象写法相同的目标），`--ignore-goals` 不对照（`goals remove` 核对时就不对照）。

```json
{"object": "干员 遥 → 精2 1级", "from": {"elite": 1, "level": 70}, "assumed": [],
 "need": {"lmd": 590000, "exp": 360000,
          "items": [{"id": "3263", "name": "辅助双芯片", "count": 1}, {"id": "31014", "name": "聚合剂", "count": 2}]},
 "have": {"lmd": 1203456, "exp": 800000, "items": [{"id": "3263", "name": "辅助双芯片", "count": 0}]},
 "short": {"lmd": 0, "exp": 0, "items": [{"id": "3263", "name": "辅助双芯片", "count": 1}]},
 "craftable": [{"id": "31014", "name": "聚合剂", "count": 2, "fully": true, "uses": [{"id": "…", "name": "…", "count": 2}],
                "lmd": 800, "missing": []}],
 "farm": [{"id": "3272", "name": "辅助芯片组", "count": 2}],
 "depot_at": "…", "box_at": "…",
 "reserved": {"31014": {"name": "聚合剂", "count": 4, "goals": [{"title": "能天使 技能3专3", "priority": 1, "count": 4}]}},
 "conflicts": [{"id": "31014", "name": "聚合剂", "need": 2, "have": 5, "short_after_reserve": 1,
                "reserved_by_others": [{"goal": "能天使 技能3专3", "priority": 1, "count": 4}]}],
 "short_after_goals": {"lmd": 0, "exp": 0, "items": [{"id": "31014", "name": "聚合剂", "count": 1}, …]},
 "reserved_skipped": [{"title": "…", "why": "…"}]}                  // 有算不了的目标才有
```

- `have.exp` 是仓库里作战记录折成的经验。`craftable` 是缺的材料里能合成的：`fully` 为假时 `missing` 是合成也补不上的原料。
  `farm` 是合成之后还要去刷的（可以直接交给 `calc stages`）。
- 要专精但还没精2 时自动算上练到精2 1级；技能等级不知道时，专精按已到 7 级算、升技能按 1 级算，都写进 `assumed`。
- 快照不存在时按空仓库 / 精0 1级算，也写进 `assumed`。对象写错退出码 2（`bad_object`）。
- `short` 仍只按仓库算；`reserved` 是别的目标还要的（按物品，带是哪几个目标）；`short_after_goals` 是先给别的目标留够之后还缺的；
  `conflicts` 是这样之后不够、而且有别的目标占着的物品（谁占了多少）。要不要动别的目标的材料由 agent 按优先级问用户。`--ignore-goals` 时这几项都没有。

`maapilot calc craft <物品> [--count N]`
用仓库现有材料再合成 N 个要做什么（仓库里已有的不算）：先合哪些中间材料、每步材料与龙门币、还缺什么基础材料、加工站心情消耗。
认得加工站精英材料、制造站双芯片、商店兑换（芯片助剂，花采购凭证，列在 `spend`）。不能合成的退出码 2（`not_craftable`）。
`{"item", "count", "steps": [{"item", "id", "count", "times", "station": "WORKSHOP|MANUFACTURE|SHOP", "costs": [...], "lmd": n, "mood"?: n}], "lmd": n, "short": [...], "spend": [...], "mood": n, "depot_at"}`

`maapilot calc stages <物品>[=数量] …`
缺的材料去哪刷最省理智：每种物品给几关（每件期望理智、掉率、今天开不开；同 `data item`：常驻主线 / 资源本 / 芯片本和 MAA 能导航的活动关），再给一个合并的建议
（每种物品挑每件期望理智最低的一关，次数 = 数量 ÷ 掉率向上取整；同一关服务几种物品时取最多的次数；不算副产物）。
`days` 按每天自然恢复 240 理智算。
`{"per_item": [...], "plan": [{"stage": "PR-C-2", "times": 8, "ap": 36, "sanity": 288, "open_days": "…", "open_today": true, "for": ["辅助芯片组"]}], "sanity_total": 540, "days": 2.3, "daily_sanity": 240, "note": "…", "no_stage"?: [...]}`

`maapilot calc event`
正开着的活动（MAA 的活动列表，见「data」的活动关）每关：理智、主要掉落（只算材料，不算活动代币）和等级 `tier`（T1–T5）、一图流价值 `value`、
企鹅物流有掉率时的每点理智价值 `value_per_ap`、MAA 能不能直接刷 `maa_can_fight`。排序：MAA 能刷的在前 → 最高材料等级 `top_tier` 高的在前 → `value_per_ap` 高的在前。只给事实。
`{"activities": [{"name": "昨日海", "end": "…", "listed": [...], …, "stages": [{"code": "YW-6", "stage_id": "act52side_06", "ap": 21, "maa_can_fight": true, "top_tier": 3, "value_per_ap": null, "drops": [{"id": "30063", "name": "全新装置", "tier": 3, "value": 37.14}]}]}], "note": null}`

程序里 `maapilot.tools.calc.parse_object(text)` 解析"对象"一行（`goals` 可以用它校验），`need()` / `craft()` / `stages()` 是函数版本。

## goals —— 目标清单（不碰模拟器）

读写 `private/goals.md` 的固定字段；完成或放弃时整段挪进 `private/goals-done.md`。格式见[目标清单](#goalsmd-目标清单)。
**目标的先后就是优先级**：goals.md 里越靠前越高（进行中、暂停的按顺序从 1 数，`priority`），材料不够分时先给前面的；`add` 加在最后，用 `goals move` 调。
"分解与进展"是 agent 的工作笔记：可以用 `goals update --note` 追加一行，也可以直接用文件工具编辑。

| 命令 | 做什么 |
|---|---|
| `goals list [--all]` | goals.md 里的目标（进行中、暂停）；`--all` 另给 `done`（goals-done.md 里的） |
| `goals add --title <标题> --object <对象> --said <原话> --approved <同意> [--premium 是\|否] [--deadline <期限>] [--note <文字>]` | 追加一个目标，状态"进行中"。`--approved` 也可以写成 `--agreed`。`--said` 带日期，如 `把遥练到精2（2026-09-30）`（没写日期就补今天）；`--approved` 如 `2026-09-30 21:40，已报：龙门币约 59 万…`（不以日期开头就在前面补现在的时间）；`--premium` 默认"否"；`--deadline` 写期限（`活动` 目标必填） |
| `goals update <标题> [--status 进行中\|暂停] [--object …] [--approved …] [--premium 是\|否] [--deadline <期限>\|none] [--note <文字>] [--rename <新标题>]` | 改固定字段（缺的字段补在最后一个字段后面）；`--deadline none`（或空字符串）去掉期限；`--note` 在"分解与进展"末尾追加 `- MM-DD：<文字>`（还没有这一块就先加上标题行） |
| `goals remove <标题> --status 完成\|放弃 --reason <原因> [--confirmed-by-user]` | 整段挪进 goals-done.md（放在最后），状态改成完成 / 放弃，加一行 `- 结束：<日期> <完成\|放弃>，<原因>（核对结果）`。`--reason` 必填（没写退出码 2 `reason_required`）。完成前先按快照核对（见下） |
| `goals move <标题> (--top \| --before <标题> \| --after <标题>)` | 调整先后（= 优先级）：整段挪到最前 / 某个目标前面 / 后面，段落内容一字不改。三个参数要且只要一个，相对自己挪退出码 2。`{moved, order: [{title, status, priority}]}` |
| `goals expire` | 过了期限的目标（进行中、暂停都算）挪进 goals-done.md，状态"到期"，结束行写 `期限 … 已过，自动结束`。`{expired: [...], active}`；`daily run` 开头自动做 |

`<标题>` 精确匹配二级标题，也接受唯一的前缀；找不到或不唯一退出码 2（`unknown_goal` / `ambiguous_goal`）。
标题重复时 `add` / `update --rename` 退出码 2（`duplicate_goal`）；对象不是 `干员 / 材料 / 关卡 / 活动 / 其他` 开头退出码 2（`invalid_object`）。
`--deadline` 写成 `2026-10-30 03:59`（游戏时间 UTC+8，到这一刻到期）或 `2026-10-30`（算到那个游戏日结束，即次日 04:00）；写法不对、日期不存在、已经过了、
`活动` 目标没写期限都退出码 2（`bad_deadline`）。

**完成前核对**（`remove --status 完成`）：只读 `state/` 快照对照对象。干员：精英化与等级、技能等级、各技能专精、模组逐项比；材料：仓库够不够（同 `calc need` 的 `short`）；
关卡、活动、其他核对不了。有一项没到 → 退出码 3（`not_achieved`，`data.short` 是 `[{part, target, current}]`、`current`、`depot_at` / `box_at`），不挪：
只有用户能决定算不算完成，用户说算就加 `--confirmed-by-user` 再跑（结束行记"用户确认：快照里还没到 …"）。核对不了（没快照、快照里没有这一项）不拦，结束行记"没能核对：…"；
都到了记"已核对快照"；有几项是靠现场练度才到的（见下）记"已核对：快照加 <时间> <命令> 的现场读数"。
输出多 `verified`（`true` / `false` / `null`；放弃时 `null`）和 `check: {short, why, depot_at, box_at, live}`。

**现场练度**（`private/state/live.json`，`maapilot/tools/live_box.py`）：森空岛的练度会滞后（10-14 实测：专精练完 15 分钟后还是旧的，
几小时后才更新；社区有一天不更新的报告），而刚做完培养时最要用练度。所以我们的命令在游戏里读到 / 做成的练度记下来：
`op progress` 成功的项（专精只有 `AlreadySatisfied` 是现场读到的当前等级；开练的只说明至少到了前一级）、`op upgrade` 做完核对读到的、`state op`。
读干员快照的地方（`goals remove` 的核对、`calc need`、目标占用的材料、目标排序）都按项取快照和现场练度里高的（练度只会涨；精英化与等级连起来比），
用到了现场读数的干员带 `live: {at, source, parts}`。快照追上来的条目下次记录时清掉；记下一天后、更晚的快照还比它低的不再采用，
`doctor --brief` 的 `live_suspects` 列出来（可能读错了）。`state show box` 和 `box.json` 本身仍是快照原样。

```json
// goals list
{"goals": [{"title": "遥 精2", "status": "进行中", "object": "干员 遥 → 精2 1级",
            "said": "把遥练到精2（2026-09-30）", "approved": "2026-09-30 21:40，已报：…", "premium": false,
            "deadline": null, "expired": false, "priority": 1,
            "notes": "分解与进展（agent 自由写）：\n- …"}],
 "active": 1,
 "problems": [{"title": "…", "file": "goals.md", "message": "缺少字段：同意"}]}
// goals add / update / remove
{"goal": {…同上一项…}, "active": 1}                 // update 另有 "changed": ["状态", "分解与进展"]；remove 另有 "verified"、"check"
```

`premium` 读不出（不是 是 / 否）时为 `null`，并在 `problems` 里报。goals-done.md 里的目标多一个 `ended`（"结束"那一行）。
`deadline` 是到期时刻（ISO，到分钟；没写为 `null`），有期限时另有 `days_left`（还剩几天，一位小数）；`expired`：已经过了期限。
过了期限的目标不算进行中（`active` 不算它，`daily run` 不为它叫 agent），等 `goals expire` 挪走。期限写法不对、`活动` 目标没写期限在 `problems` 里报。

写目标的同意来自对话（用户看过 `calc` 报的数、说了同意），记在"同意"那一行；命令本身不再弹确认。
程序里 `maapilot.flows.goals.active_goals()`（按优先级，带 `priority`）/ `active_count()` 给 `daily run`、`doctor --brief`、`calc need` 用；`expire_due()` 是 `goals expire` 的函数版本，
`snapshot()` / `diff()` 给 `daily run` 比较前后的目标（见「daily」的 `goals_change`）。

## op / craft —— 快捷操作（碰模拟器）

旧版的自动化脚本，能成就省事，失败了 agent 按 `maapilot-training` / `maapilot-base` 在界面上自己做。失败退出码 1，
`error.code` 是 `shortcut_failed`（另见下表），`data` 带 `stage`（做到哪一步）、`shot`（失败时的截图，`private/debug/<名>_<时间>.png`，
带时间，同一个干员 / 物品再失败不会盖掉上一张；旁边同名的 `.json` 是最近几次识别读到的字和整张图的识别结果，查读错了什么用；只留最近 300 张）、
`done`（已经做了的部分），脚本给得出的还有 `reached`（升级做到的练度）、`made` / `restored`（制造站）。
花了多少不在这里算：要知道就前后各 `state depot` 一次。连不上模拟器退出码 5（`device_failed`）。

报失败前先核对结果：`op upgrade` 中途出错时回干员页重读一次练度，已经到目标就算成功（`data.recovered` 是当时的错误）；
`op mastery` 点了确定没看到"开始"时，确认框还在才再点一次，再回训练计划页看训练位是不是"训练中"；`craft` 加工完重读第一种原料的有/要，
正好少了这次要用的数为 `verified: true`（读不出为 `null`）。等页面时遇到认得的遮挡（专精完成提示、获得物资、公告、月签到等，
表在 `maapilot/crew/overlays.py`）先点关掉；不认得的不碰。界面上的中文标签按词认（OCR 读错一个字也认得，如"川练中""环烽预制体"），
"有/要"数字标签二值化后多读几遍、投票（`maapilot/crew/reading.py`）。加工站读不出份数、或弹了"材料不足"，马上停下报错，不再点"+"。

| 命令 | 做什么 |
|---|---|
| `op upgrade <干员> [--elite E] [--level L] [--skill N]` | 升级 / 精英化 / 升技能（通用技能等级 1–7）到指定练度（`maapilot/crew/upgrade.py`）。只给一部分时，没给的按现在的练度：只给 `--level` 就在当前精英阶段内升；只给 `--elite` 就升到那一阶的 1 级 |
| `op mastery <干员> --skill K --rank M` | 在训练室开始技能 K 的下一级专精，不超过专 M（一次开一级；缺的 T3 以上材料先合成，要用 `state depot` 快照；`maapilot/crew/mastery.py`） |
| `op progress <干员> [--elite E] [--skill-level N] [--mastery K:M …] [--allow-catalyst]` | 交给 MAA 本体的干员培养（MaaCore 的 OperProgress，见下）：精英化到精 E（1–2）、技能升到 N 级（2–7）、技能 K 专到 M（`--mastery` 可写多次）。不管等级、模组 |
| `craft <物品> [--count N]` | 精英材料走加工站（`maapilot/crew/craft.py`，只合这一样：中间材料先各自 `craft`，顺序看 `calc craft`）；双芯片走制造站（`manufacture.py`，造完改回原产物）；商店兑换的（芯片助剂，花采购凭证）走采购中心（`shop.py`，确认框上核对总价，对不上不买） |

`op progress` 不经 maa-cli（v0.7.5 还不认这个任务类型），在子进程里直接加载单独装的 MaaCore（`maapilot/crew/maacore.py`、
`maacore_runner.py`；资源先用本体自带的，再叠加 maa-cli 热更新的），回调全文记在 `data.log`（`private/log/maa/<时间>-op-progress.log`）。
拿锁后先回首页（MaaCore 只会从首页进干员列表；回不去退出码 1 `not_home`，什么都没做），做完再回首页（`data.home`）。
MaaCore 自己读练度：已经到了的项报 `AlreadySatisfied`，什么都不点；缺的精英材料会先去加工站合（花龙门币和下级材料）；
专精一次只开一级（要专 3、现在专 1 → 开专 2，`PrerequisiteTraining`，`training_started: true`），开了之后后面的专精项不再做。
5、6 星精二要双芯片：MaaCore 会在制造站造（造完改回原产物），**芯片助剂不够时会用采购凭证去采购中心买，关不掉**，
所以练度快照（`state skland` / `state box`）里还没到精二时要加 `--allow-catalyst`，否则退出码 2 `needs_consent`、不碰模拟器。
`steps` 按 MaaCore 的执行顺序对应参数里的各项；只回了一条失败、后面都没做时是"找干员"失败（列表里没找到、或页面认不出）——
除非 MaaCore 日志里有训练室选人没认出的那句、而失败的这一步是专精：那是已经进了训练室，算专精这一步失败（多步的计划也一样，例如先升技能再专精）。
第一条没做成的（前面的都做成了）是专精、报 `RecognitionFailed`（MaaCore 的训练室选人只认卡片上的名字，在基建设施里工作的干员卡片上显示的是设施名，它就找不到人）时，
命令自动改用 `op mastery` 的流程开同一级（`data.fallback`：`{command: "op mastery", reason, …op mastery 的 data}`；`steps` 里仍是 MaaCore 的原话）。
兜底开成了退出码 0、`training_started` 跟着它；兜底也没做成就报它的错，`error.message` 两边的原因都写上。精英化、技能等级不进训练室，不兜底。

```json
// op upgrade
{"operator": "遥", "target": {"elite": 2, "level": 1, "skill": null},
 "before": {"elite": 1, "level": 70}, "after": {"elite": 2, "level": 1, "skill": null}, "shot": "private/debug/…png"}
// （中途报错、重读发现已经到目标时多一个 "recovered": "<当时的错误>"）
// op mastery
{"operator": "遥", "skill": 2, "status": "started", "rank": 1, "hours": 8, "short": null, "crafted": [...], "shot": null}
// op progress
{"operator": "能天使", "plan": {"name": "能天使", "role": "SNIPER", "elite": 2, "skill_level": 7, "skill_mastery": [0, 0, 2]},
 "steps": [{"step": "精英化到精2", "result": "Success", "detail": "AlreadySatisfied", "text": "本来就到了", "elite": 2},
           {"step": "技能升到 7 级", "result": "Success", "detail": "AlreadySatisfied", "text": "本来就到了"},
           {"step": "技能3 专2", "result": "Success", "detail": "AlreadySatisfied", "text": "本来就到了", "skill_mastery": [0, 0, 2]}],
 "summary": {"success": 3, "failed": 0, "skipped": 0}, "training_started": false, "core": "v6.19.0",
 "log": "private/log/maa/…-op-progress.log", "duration_s": 20.5, "home": true}
// craft
{"item": "聚酸酯块", "count": 3, "times": 3, "station": "WORKSHOP", "shot": "…", "verified": true}
{"item": "辅助双芯片", "count": 2, "station": "MANUFACTURE", "made": 2, "original": "赤金", "restored": true}
{"item": "芯片助剂", "count": 1, "station": "SHOP", "currency": "采购凭证", "unit_price": 90, "balance_before": 727, "balance_after": 637}
```

| error.code | 退出码 | 什么情况 |
|---|---|---|
| `shortcut_failed` | 1 | 脚本出错、没到目标练度、认不出专精确认框；确认框里的材料数没认全（`status: costs unreadable`，`shot` 是确认框截图，不报缺材料） |
| `training_busy` | 1 | `op mastery` / `op progress`：训练室正在练别的 |
| `short_materials` | 1 | `op mastery`：材料不够、合成也补不上（`data.short`）；`op progress`：`ResourceInsufficient` / `ChipNotCraftable` / `FormulaLocked` |
| `operator_not_found` / `prerequisite_not_met` | 1 | `op progress`：干员列表里没找到；前置练度不够（专精要精二，技能 7 级要精一） |
| `not_home` / `maa_failed` | 1 | `op progress`：开始前回不到首页；MaaCore 连不上模拟器、超时（60 分钟）、没给结果 |
| `needs_consent` | 2 | `op progress`：5、6 星精二可能花采购凭证买芯片助剂，问过用户再加 `--allow-catalyst` |
| `needs_user` | 3 | `guard allow`：花合成玉 / 源石要用户在会话里同意，日常在跑或没收尾时一律拒绝。什么都没做，等 `daily done` 收尾后用户同意再记 |
| `maacore_missing` / `maacore_broken` | 5 | `op progress`：没装单独的 MaaCore 或低于 v6.19.0（`doctor --fix`）；本体或资源加载失败 |
| `not_restored` | 1 | `craft` 双芯片：造好了，但制造站没改回原产物（`data.original`、`shot`） |
| `not_bought` | 1 | `craft` 兑换：确认之前停下了，什么都没花（余额不够、找不到商品、确认框对不上） |
| `unknown_name` / `not_craftable` | 2 | 名字认不出；这样东西快捷操作做不了 |

`op mastery` 的 `status` 为 `reached`（要开的已经超过专 M，什么都没开）也算成功。

## guard —— 货币对账

只看两样：**源石**（`4002`）和**合成玉**（`4003`）。数量来自 MAA 仓库识别（和 `state depot` 同一次识别，顺手更新 depot 快照）：
按物品 id 给数，不依赖页面布局；但上万的数会取整（真机：合成玉 16566 认成 16000），所以接着回首页 OCR 顶栏（按位置取：源石最右、合成玉在它左边；龙门币读不出不影响，数字前后认错的杂字符先去掉），
和仓库识别对得上（两样都不小于它、差不到取整的一格）就用顶栏的精确值，有一样对不上或读不到就用仓库识别的数。
快照里记下 `exact`：两个数都是顶栏的精确值（顶栏和仓库识别对上了）。
比较时，前后有一个数上千、减少不到 1000 的当作读数误差，不算花费（用户 2026-10-05 定：不考虑只花了几百合成玉的情况）——只在前后不都是 `exact` 时；都是精确值就没有读数误差，少 1 也算。
这样放过的那一项，通过后快照里留着原来的数（起点不往下挪，`exact` 记成 false）：几次几百的小花费累计到 1000 照样会被对出来。识别结果里没有这两项、但认出了别的物品时按 0 算（误认只会往"减少了"的方向错，宁可误停）；
一件都没认出退出码 1（`unreadable`），不做判断。

| 命令 | 做什么 |
|---|---|
| `guard snapshot` | 记下现在的源石、合成玉 → `private/guard/snapshot.json`。**上次对账之后跑过可能花东西的命令**（`private/guard/dirty.json` 在）又已有快照时，改做一次 `check`（`checked_instead: true`，不过就退出码 3），不重记起点：agent 不能靠重记快照抹掉自己的花费。停机中退出码 3（`guard_stop`） |
| `guard check` | 再读一次，与快照比。减少的部分扣掉快照之后记下的、还没用完的 `allow`（按时间先后，一笔可以分几次用）；还有没放行的减少 → 退出码 3（`currency_decreased`），写 `private/guard/stop.json`，快照不动。通过则更新快照、删掉 `stop.json`。还没有快照时把这次的数记为起点（`baseline: true`） |
| `guard allow <源石\|合成玉> <数量> --reason <用户同意了什么>` | 记下用户同意的一笔花费 → `private/guard/allowances.jsonl`。每次都要用户先在对话里同意；权限是"询问"（唯一一条）；有日常在跑或没收尾时一律拒绝（退出码 3 `needs_user`，见「命令总览」） |
| `guard status` | 快照、未用的放行、是否停机中、上次对账后动过什么（`dirty`）（不碰模拟器） |

**dirty**：`input …`、`maa …`（`startup`、`closedown` 除外）、`op …`、`craft` 拿到模拟器锁时记进 `private/guard/dirty.json`；
`check` 通过（或重记快照）时清掉，读不到时留着。没人动过时 `snapshot` 才重记起点，所以用户自己在游戏里花的（两次日常之间、没有 agent 操作）不会被当成异常；
agent 动过之后的减少一定会在下一次 `snapshot` / `check` 被对出来。

合成玉、源石增加不算问题。停机中（`stop.json` 在）时 `daily run` 退出码 3 拒绝开始；用户说明情况、同意这笔减少后，
agent 用 `guard allow` 补记（宿主会再问一次），再 `guard check`：对上了就删掉 `stop.json`。

`daily run` 里对账没做成（`guard snapshot` / `check` 读不到仓库）时，汇报记 `guard_unreadable: true`（之后有一次 `check` 做成了就去掉）：算问题（`has_problem`），
`lines` 写"没能核对合成玉 / 源石（读不到仓库），这次的花费没有对账"，要告诉用户。

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
{"snapshot": {"at", "originite", "orundum", "exact"} | null, "allowances": [...还没用完的...], "stop": {...} | null,
 "dirty": {"since": "…", "commands": ["input tap", "maa fight"]} | null}
```

`guard allow` 的物品写 `源石`（或 `至纯源石`）/ `合成玉`，别的退出码 2。
程序里 `maapilot.tools.guard.snapshot()` / `check()` 是不拿锁的版本（`take_snapshot()` 无条件重记，只给 `check` 内部用），`stopped()` 看是否停机中（不碰模拟器）。

## lock —— 模拟器锁（内部）

`lock status`：`{"holder": {...} | null, "stale": {...} | null, "stale_reason": "…" | null, "inherited": bool}`（谁占着、从什么时候、在做哪步；残留的锁单列；
`inherited`：本进程环境里的 `MAAPILOT_LOCK_TOKEN` 对得上有效的锁，即这条命令是持锁的 `daily run` 起的子进程）。
碰模拟器的命令被占用时退出码 4（`busy`），`data.holder` 同样是谁占着（`cliutil.device()` 填）（外面有别的 `maa` 进程时是 `{"external_maa_pids": [...]}`）。
`lock release`：只删掉**已经残留**（持有进程不在了、心跳过期）的锁；有效的锁退出码 4。权限放行（只删残留的锁，没什么可拦的）。

## daily —— 日常

v0.2.0 起只有一种做法：**agent 在场**（用户开着的会话里）。定时日常也是 harness 的会话内定时到点在会话里叫醒 agent，
由它跑 `daily run --trigger schedule`；项目不再自己定时、不再无头叫起 agent、不往会话里推消息（docs/design.md「运行方式」）。
`daily run` 遇到要判断的事（某项失败、掉线、游戏要更新、有进行中的目标）就停下交回 agent；agent 处理完用 `--only` 接着跑，
做完目标部分、领奖励、对账后用 `daily done` 收尾。

`maapilot daily run [--only <项,…>] [--trigger manual|schedule]`（碰模拟器，要 20–60 分钟：Claude Code 里放后台跑，Codex 里前台跑）

`--trigger schedule` 只是标记"这次是定时叫醒的"（写进汇报的 `trigger`，汇报标题写"定时"，`lines` 写"定时日常"），做法和 `manual`（默认）完全一样。

1. 对账停机中（`private/guard/stop.json` 在）→ 退出码 3（`guard_stop`）。另一个日常在跑 → 退出码 4（`daily_running`）。
   有**没收尾的日常**（见下）：不带 `--only` → 退出码 2（`daily_open`，`data.open` 是那次的 `{run_id, trigger, started_at, age_h, stale}`，hint 说先 `daily done` 收尾或 `--only` 接着跑）；
   带 `--only` → 接着写那一次（同一个 `run_id`、同一份汇报）。没收尾的超过 6 小时算 agent 放弃了：先自动收尾（那次汇报的「要注意」加一条"agent 没有收尾"，`closed_by: "auto"`），再开新的。
   新开一次时记下"没收尾的日常"和目标的快照（结束时比较，见下面 `goals_change`），拿模拟器锁。
2. 开始前（尽力而为：出错记一条告警，照跑）：
   - 过了期限的目标挪走（同 `goals expire`，挪走的记在 `goals_expired`，`lines` 里提醒 agent 看看还有没有没做完的，例如活动商店没换完；接着跑时并进前几段的）；
   - 官方公告缓存旧了重拉（见「news」；拉不到用旧缓存）。**正在停机维护**（`maintenance_now`）→ MAA 项、对账都不跑，`status: maintenance`、`ok: true`、
     `data.maintenance`（`{cid, title, start, end, forced_update}`），退出码 0（agent 告诉用户、想想对目标的影响，再 `daily done` 收尾）；
   - 游戏版本（同 `game version`，结果在 `game_version: {installed, latest, update_needed}`；查不了照跑）。要更新 → 停下交给 agent：退出码 1（`handoff`），
     `handoff = {task: "game_update", error, rest, version}`，`rest` 是全部还没跑的项；agent 按 `maapilot-events` 更新好再 `daily run --only <rest>`。
3. 先 `startup`（配了的话：读仓库要在游戏里），再 `guard snapshot`（上次对账之后动过会花钱的操作时它改做对账，不过就停下，见「guard」）；
   读仓库没成（换日后首页常有弹窗）就再跑一次 `maa startup` 回首页、再读一次，还不成才在汇报里告警、对账以上一次的记录为准。
   这次日常的第一份仓库快照记在"没收尾的日常"里：接着跑的几段结束时都和它比（`depot_change` 是整次日常的）。
4. 按 `config.yaml` 的 `daily.tasks` 依次跑其余 MAA 日常项（每项是一条 `maapilot maa <项>`，继承锁；`award` 留到最后）。
   **第一项失败就停下**，不跑后面的，退出码 1（`handoff`），`data.handoff = {task, error, log, evidence, rest}`（`evidence` 是这一项的失败取证，见「maa」；
   `rest` 是这一项和后面还没跑的），`status: handed_off`；agent 按 `maapilot-recovery` 处理好后 `daily run --only <rest>` 接着跑。
   掉线 / 维护（`offline`）也照样交给 agent（重新登录、等维护由 agent 判断）。只有被占用（`busy`）不交。
5. 识别干员练度（`box`；接了森空岛先 `state skland`，没成记一条告警、不算失败，再用 `maapilot state box`（继承锁））：`daily.read_box`（默认 `true`）开着、`private/state/box.json` 的 `at` 不在当前游戏日（04:00 换日）里时才跑，
   读过了就记一行 `skipped`。最长 `daily.box_timeout_min` 分钟（默认 15）。失败同第 4 步（交给 agent）；失败不改 `box.json`。放在刷理智、目标部分之前，让它们看到今天的练度。
6. 刷理智（`farm`）：有进行中的目标 → 交回 agent（`data.goals_pending: true`），`award` 也不跑（`data.award_pending: true`，领奖励要在刷理智之后）：
   agent 按目标优先级做目标部分，自己 `maa award`、`guard check`，再 `daily done` 收尾。没有进行中的目标 → `maa fight <default_stage>`（失败同第 4 步）。
7. `award`；`guard check`；写汇报 `private/reports/<YYYY-MM-DD-HHMM>.md` 与 `.json`（开始时就写一份"还在跑"，每一步后更新）。

任何一次 `guard check` 退出码 3 → 立刻停下（后面的项不跑），退出码 3（`currency_decreased`），汇报里写明。
剿灭失败和别的项一样交回 agent（程序不计数、不整周跳过，10-20 用户定）。
本周合成玉拿满也跳过：自己的记录还没拿满时先问一次森空岛（接了的话，见「maa」），跳过的原因里写 `（current/total）`。
停下交给 agent → 退出码 1（`handoff`）；`--only` 补跑完还有没做成的项 → 退出码 1（`daily_incomplete`），`data` 照常给全。被占用、连不上 → 退出码 4 / 5，汇报的 `status` 是 `aborted`
（第一段就没拿到模拟器、什么都没做时不留"没收尾"，`open: false`）。

`--only` 只跑列出的项（补跑失败的几项；刷理智写 `farm`、识别干员练度写 `box`，不列就不跑）。接着没收尾的那次跑时：这次要跑的项从 `failed` 里拿掉（再失败会再记上），
之前没做成的那一行标 `retried: true`（汇报里写"（后面重跑了）"），新的结果另起一行；`segments` 是跑了几段；`trigger`、`started_at` 还是第一段的。

**没收尾的日常**（open daily，`private/daily-open.json`：`{run_id, trigger, started_at, depot_before, goals_before}`）：`daily run` 新开一次时记下，
`daily run` 进程结束后还在，直到 agent `daily done` 收尾（或者超过 6 小时、下次 `daily run` 开始时自动收尾）。这段时间里：
`daily run --only …` 接着写同一份汇报；不带 `--only` 的 `daily run` 拒绝；`guard allow` 一律拒绝（见「guard」）。汇报的 `open` 是 `true`，`data.next` 是给 agent 的下一步提示
（程序生成的一句话：按 `maapilot-recovery` 处理后 `--only <rest>` 接着跑 / 按 `maapilot-daily` 做目标部分、`maa award`、`guard check` / 告诉用户对账停下了……，最后 `daily done` 收尾；只是提示，做不做由 agent 判断）。
"正在跑"另有运行标记 `private/daily-run.json`（`run_id`、`pid`、`step`；进程结束就删）。

```json
{"run_id": "20260930-0440", "trigger": "schedule", "status": "done", "ok": true, "open": true,
 "started_at": "…", "finished_at": "…", "segments": 2,
 "tasks": [{"task": "recruit", "ok": true, "duration_s": 95},
           {"task": "infrast", "ok": false, "error": "…", "code": "maa_failed", "log": "…", "retried": true},
           {"task": "infrast", "ok": true, "duration_s": 310},
           {"task": "farm", "ok": true, "result": {"stage": "1-7", "times_done": 12, "sanity_used": 72, "medicine_used": 0}}],
 "failed": [], "goals_active": 0, "goals_pending": false, "award_pending": false, "farm": {"stage": "1-7", "times_done": 12, …},
 "depot_change": [{"id": "4001", "name": "龙门币", "delta": 12000}],
 "guard": {"ok": true}, "alerts": [], "report": "private/reports/2026-09-30-0440.md",
 "game_version": {"installed": {"version": "2.7.81", …}, "latest": {"version": "2.7.81", …}, "update_needed": false},
 "goals_expired": [{…同 goals list 的一项…}],                                    // 有到期挪走的才有
 "goals_change": {"removed": [{"title": "…", "fields": {…}, "done_entry": {"status": "完成", "ended": "…"}}],
                  "added": [{"title": "…", "fields": {…}}], "changed": [{"title": "…", "field": "deadline", "before": null, "after": "…"}]},
 "news": {"at": "…", "lines": ["…"], "due": ["6774:公告"]},                     // 同 doctor --brief 的 news；没有为 null
 "lines": ["9 月 30 日 04:40 的定时日常跑完了，都做成了。", "目标移除：遥 精2（完成，…）"],
 "next": "最后 maapilot daily done --summary \"<做了什么、目标进度、花了什么、要用户决定的事>\" 收尾，再告诉用户"}
```

`status`：`running` / `done` / `stopped`（对账停下）/ `aborted`（没跑完）/ `handed_off`（停下交给 agent）/ `maintenance`（赶上停机维护，没跑）。
（v0.1 的汇报里还会有 `update_failed`、`refused`，照样能读。）`guard.ok` 为 `null` 表示对账命令本身没做成（汇报里告警；`maintenance` 不对账，不算）；
对账因为读不到仓库没做成时另有 `guard_unreadable: true`（算问题，见「guard」）。
`depot_change` 来自 guard snapshot / check 顺手更新的 `state/depot.json`：这次日常第一份快照和这一段结束时的比；读不到为 `null`。
`goals_change`：每段结束时（不论怎么结束）把 goals.md 里进行中、暂停的目标和这次日常开头的快照比（`fields` 是固定 6 行的原文，`field` 用输出名 `status` / `object` / `deadline`…）；
没变就没有这个字段。`removed` 的 `done_entry` 是 goals-done.md 里同名那段的状态与结束行，`null` 表示没经过 `goals remove` / 到期就没了；改标题算一删一加。
汇报里写成「目标变动」一节，`lines` 里也有（目标变动不算问题，但要告诉用户）。
`news`：官方日程（开头拉过的缓存，同 `doctor --brief` 的 `news`）；`due` 不为空时 `lines` 里有一行，agent 按 `maapilot-events` 跟用户说、`news told`。
`lines`：每次都给的几句中文（怎么结束的、维护时段、目标变动、到期要看的、没做成的项、官方日程……），agent 照着转述。

每个 MAA 日常项最长 `daily.task_timeout_min` 分钟（默认 90）。刷理智（`farm`）的时限按理智算：先读理智（`private/state/sanity.json` 1 小时内的直接用，
没到上限的按 6 分钟 1 点往上推；旧了就 `state sanity`（森空岛，没接才回首页读）；还读不到就用旧快照推），能打几次 = ⌈理智 ÷ 关卡一次的理智⌉（关卡认不出按 6），
时限 = 次数 × `daily.farm_min_per_fight`（默认 3）+ `daily.farm_timeout_margin_min`（默认 15）分钟；理智超上限（如 358/205）照算。完全读不到理智才用 `task_timeout_min`。
算出的时限记在这一项的 `timeout`（`{min, basis, sanity, sanity_source, stage_cost, fights, …}`）。超时结束 `maapilot maa` 的整棵进程树（先 TERM 等 15 秒，再 KILL），
20 秒内 maa 进程还在就由日常结束掉（TERM，不走再 KILL，告警里写明）。子进程的环境里带 `MAAPILOT_LOCK_TOKEN`（继承锁）和 `MAAPILOT_RUN_ID`（命令记录里的 `run_id`）。

`daily done --summary <文字> | --summary-file <文件> [--result 完成|没做成]`（不碰模拟器）：**agent 收尾这次日常**。
`--summary` 和 `--summary-file`（UTF-8）只写一个；总结写给用户看（做了什么、目标进度、花了什么、要用户决定的事），进这次汇报的「目标部分」一节
（没有目标、只跑了固定日常也照样用它收尾）。`--result` 写成总结最后一行 `结果：…`（原来写了就换掉；汇报里不显示这一行）。
清掉"没收尾"的记录，重新生成汇报（`open: false`、`closed_at`、`closed_by: "agent"`、`goals_summary: {at, text, by, result}`）。超过 20000 字截掉中间（留开头和最后一行）。
没有没收尾的日常 → 退出码 2（`not_in_daily`）；`daily run` 还在跑 → 退出码 4（`daily_running`，跑完再收尾）。不看环境变量、不要求继承的锁。
`{run_id, closed, chars, truncated, report}`。

`daily status`：`{"running": {run_id, step, started_at, …} | null, "open": {run_id, trigger, started_at, age_h, stale, step?} | null, "last": {run_id, at, ok, status, trigger, open, report} | null}`。
`open` 是没收尾的日常（`stale`：超过 6 小时；正在跑时带 `step`），`last` 是最近一次汇报。

`daily unschedule [--dry-run]`：删掉 v0.1 留下的系统定时（用户在对话里同意后跑）。只删本项目的：
- **cron**（macOS / Linux）：crontab 里以 `# maapilot-v2 <本仓库路径>` 结尾的行（v0.1 的启动器写法和更早的写法——环境变量写在行里、`.venv/bin/maapilot … >> cron.log`——都认），
  别的行一个字节都不动；更旧的 MaaPilot 的行（`maapilot job tick`、`bin/daily.sh`）列在 `old_entries`，不删。测试用环境变量 `MAAPILOT_CRONTAB` 换掉 crontab 程序。
- **Windows 任务计划程序**（`schtasks.exe`）：任务 `\MaaPilot\daily-<仓库路径的 sha1 前 8 位>`，描述 `maapilot-daily <仓库路径>`（"本项目的"标记），`schtasks /Delete /TN <任务> /F`。
  `\MaaPilot\` 下别的任务（别的目录的 MaaPilot）列在 `old_entries`，不动。读的时候只认 `schtasks /Query /FO CSV /NH` 的任务名和 `/Query /TN <任务> /XML` 的内容
  （UTF-16 / UTF-8 / 系统代码页都认），不解析本地化的表格文字。测试用环境变量 `MAAPILOT_SCHTASKS` 换掉 schtasks 程序。
  找不到 schtasks.exe → 当作没有（`backend: "none"`，什么都不删）；schtasks 出错 → 退出码 5 `schtasks_failed`。
`{"backend": "cron" | "schtasks" | "fake", "removed": [...], "old_entries": [...], "dry_run": false}`（`--dry-run` 另有 `preview`：删完剩下的）。
`fake` 是测试用的（`MAAPILOT_SCHEDULER=fake:<json>`）。还留着没有，`doctor` 的 `legacy_schedule` 会说。

v0.1 的 `daily schedule`、`daily watch`、`daily run --interactive / --no-agent` 都去掉了：定时由 harness 在会话里定好（按 `config.yaml` 的 `schedule`），
结果就在会话里，不用再挂 `watch` 等。程序里 `maapilot.flows.daily.open_daily()`、`legacy_schedule()`、`maapilot.flows.report.latest()` 给 `doctor` 用。

## reset —— 从头设置

`maapilot reset [--dry-run] [--memory]`（不碰模拟器；用户在对话里同意后才跑，程序不另外确认）

用户说要从头设置时用。**什么都不删**：
1. 先查：日常在跑（退出码 4 `daily_running`）、模拟器锁被占着（有命令在操作；退出码 4 `busy`，`data.holder`）→ 不做。
   `private/` 里有文件正被别的程序打开、改不了名（Windows）→ 退出码 4 `in_use`，什么都没动。
2. 不动系统定时：还留着 v0.1 的定时日常（同 `daily unschedule` 删的那些）就在 `data.legacy_schedule`（`{times, entries, hint}`）里列出来，
   提示用户同意后 `daily unschedule`；没有为 `null`。
3. `private/` 整个改名成同目录下的 `private.bak-<YYYYmmdd-HHMMSS>`（配置、`setup.json`、目标、快照、日志、汇报、截图、对账都在里面）；
   再把备份里的 `maa-config/`（maa-cli 的配置与任务模板）复制回新的 `private/maa-config/`。之后 `doctor --brief` 的 `next` 是 `setup`。
   恢复：把新建的 `private/` 挪开，把备份改回 `private`（`data.private.restore` 写着）。
4. 用户偏好 `private/memory/` 默认也从备份复制回新的 `private/`（偏好留着）。
   `--memory`：偏好不留——不复制回来；Claude Code 给这个项目目录记的自动记忆（偏好以前的位置，
   `<~/.claude 或 $CLAUDE_CONFIG_DIR>/projects/<仓库绝对路径里非字母数字都换成 ->/memory/`）也改名成旁边的 `memory.bak-<时间>`，免得启动检查又搬回来。
   测试用 `MAAPILOT_CLAUDE_PROJECTS_DIR` 换掉 projects 目录。
5. 留着：`.venv`、`data/`、`resource/`、maa-cli 本身和它的资源。

`--dry-run` 只列出会挪什么，什么都不动。

```json
{"dry_run": false,
 "legacy_schedule": {"times": ["04:40"], "entries": ["40 4 * * * … # maapilot-v2 /…/MaaPilot"], "hint": "…daily unschedule"},
 "private": {"move": "/…/private", "to": "/…/private.bak-20261002-1700", "entries": ["config.yaml", "goals.md", "log", …],
             "restore": "mv … …", "keep_maa_config": "/…/private/maa-config"},
 "memory": {"move": null, "note": "没带 --memory：用户偏好（private/memory/）留着"},
 "keep": ["/…/.venv", "/…/data", "/…/resource", "maa-cli（及它的资源）"], "next": "setup"}
```

重置之后让用户重新开一次会话（启动检查会进入设置）。

## report —— 汇报

| 命令 | 做什么 |
|---|---|
| `report show [--last \| <run_id>]` | 汇报全文（`markdown`）与摘要（`summary`，即 `daily run` 的 data），默认最近一次；agent 转述给用户 |
| `report latest` | 同 `show --last` |
| `report list [--limit N]` | 最近的汇报：`{"reports": [{run_id, at, trigger, ok, status, failed, report}]}` |
| `report write <run_id> --append <文字>` | 在这次运行的汇报后面追加一段「补充」（运行还没结束、收尾之后都可以，结束时保留）。目标部分的总结用 `daily done` 写，不用它 |

汇报的 Markdown：标题 `# 日常汇报：<时间>（定时 | 手动）`（按 `trigger`）；开头一句（按 `status`；还没收尾时另写一句"还没收尾"）、
「做了什么」（每项一行；重跑过的旧行写"（后面重跑了）"；有目标时一行"见下面「目标部分」"或"留给 agent 接着做"；`award_pending` 一行）、
「目标部分」（`daily done` 交的总结全文）、「仓库变化」（维护没跑时不写）、「目标变动」（有 `goals_change` 或 `goals_expired` 时）、
「要注意」（告警）、「补充」（`report write`；和目标部分的总结几乎一样的不再重复写）。总结最后的「结果：…」一行不写进汇报。
v0.1 的 `report seen`（记"告诉过用户没有"）去掉了：日常就在用户的会话里跑，跑完 agent 当场说。

## tools —— 项目里的 maa-cli 和 adb（不碰模拟器）

maa-cli 本体和 adb 由 MaaPilot 自己从官方发布下载，装在项目的 `.tools/`（不进 git）：`.tools/maa-cli/maa`、`.tools/platform-tools/adb`（Windows 带 `.exe`），
各带一个 `VERSION` 文件。不用 Homebrew / winget，三个系统一样。MaaCore 和资源仍由 maa-cli 下载到它自己的数据目录。

找程序（`config.tool("maa")`、`config.adb_path()`，doctor、日常都用它）：`config.yaml` 的 `tools.maa` / `emulator.adb` 写的是路径 → 照它；
写的是程序名（模板默认的 `maa`、`adb`）或没写 → `.tools/` 里装了就用装好的，没装再在 PATH 上找。所以自己用 Homebrew 等装好、写了绝对路径的照样能用。

`maapilot tools status`（只读）

```json
{"dir": ".tools", "maa_cli_tested": ["0.7.5", "0.7.7"],
 "maa-cli": {"installed": "/…/.tools/maa-cli/maa", "version": "0.7.7", "using": "/opt/homebrew/bin/maa", "configured": "/opt/homebrew/bin/maa",
             "note": "config.yaml 的 tools.maa 写的是 /opt/homebrew/bin/maa，用的是它；要改用装好的，把 tools.maa 改成 maa"},
 "adb": {"installed": null, "version": null, "using": "adb", "configured": "adb"}}
```

`maapilot tools install maa-cli|adb [--version <maa-cli 版本>]`
下载装好（拿模拟器锁：日常、MAA 在跑时不换，被占用退出码 4）。maa-cli 默认装 `maa_cli_tested` 的上限（`toolchain.MAA_CLI_TESTED`，实测过的最新版），
`--version` 装别的版本；包从 GitHub 发布页按系统和 CPU 选（`maa_cli-v<版本>-<aarch64|x86_64>-<apple-darwin|pc-windows-msvc|unknown-linux-gnu>`）。
装好后 MaaCore 还没装就顺手 `maa install`（几分钟）。adb 从 Google 下 `platform-tools-latest-<darwin|windows|linux>.zip`，整个 `platform-tools/` 留着。
旧的整个换掉（先挪开、换好再删）。走系统的代理环境变量（`HTTPS_PROXY`）。

```json
{"name": "maa-cli", "version": "0.7.7", "path": "/…/.tools/maa-cli/maa", "url": "https://github.com/…", "replaced": "0.7.5",
 "core": "6.18.0", "using": "/…/.tools/maa-cli/maa"}
```

失败：`network`（下载不了，退出码 1）、`install_failed`（包里没有程序、旧的换不掉）、`maa_install_failed`（maa-cli 装好了但 `maa install` 没成）、`unsupported_platform`（5）。
要联网、会换掉日常执行的程序：agent 先问过用户。`config.yaml` 写了别的路径时 `note` 提示怎么改用装好的（改不改问用户）。

## host —— agent 宿主（不碰模拟器）

用户可以在 Claude Code 或 Codex 里用 MaaPilot（各 harness 怎么建会话内定时见 docs/harnesses.md）。程序的找法在 `maapilot/hosts.py`：
写了 `tools.<宿主>` 就只认它（写错了报出来，不悄悄换成别的）；没写时 PATH → 常见安装位置（`CODEX_FALLBACKS` / `CLAUDE_FALLBACKS`，可以写 `%APPDATA%` 这样的环境变量），
Windows 上找到 npm 的 `.cmd` 包装时优先用原生的 `.exe`。

`host status`（只读）：`{"hosts": {"claude": {path, trusted}, "codex": {path, trusted}}}`：`trusted` 是这个文件夹被信任过没有——Claude Code 看 `~/.claude.json`，
Codex 看 `$CODEX_HOME/config.toml` 的 `[projects."<路径>"] trust_level`（上级目录信任也算；没信任时 Codex 不加载 `.codex/rules/`）。v0.1 的 `agent`、`session(s)` 去掉了。

`host install <claude|codex> [--dry-run]`（权限放行：改的是用户的 Codex 设置，agent 先在对话里问过用户）：
- `codex`：Codex 的用户设置（`$CODEX_HOME/config.toml`，默认 `~/.codex/config.toml`）里还没信任这个文件夹就在末尾追加
  `[projects."<仓库>"] trust_level = "trusted"`（别的不动；已有这一段但不是 trusted → `codex_untrusted`，请用户自己改），
  再用 `codex execpolicy check` 核对 `.codex/rules/maapilot.rules`（`rules.ok`）。`next` 是还要做的事（`--dry-run` 时再不带它跑一次；规则核对没过时告诉开发者）。
  `{"host", "path", "config", "append", "changed", "trusted", "rules": {ok, results}, "next": [...]}`。
- `claude`：Claude Code 的信任只能在交互里接受，只报告 `trusted` 和做法。

| 错误码 | 退出码 | 什么时候 |
|---|---|---|
| `no_codex` | 5 | 找不到 codex |
| `codex_config_unreadable` | 5 | Codex 的 config.toml 读不了（TOML 写错） |
| `codex_untrusted` | 2 | 用户在 Codex 里明确没信任这个文件夹 |

## dev —— 开发用（不给用户的 agent）

`maapilot dev review [--since 3d|12h|<日期>|<时间>] [--session <编号>]… [--exclude <编号>]… [--include-dev] [--transcripts <目录>] [--limit N]`：
代替手动翻会话记录，看用户的 agent（和 v0.1 定时日常叫起的无头 agent）哪里出错、哪里用错了命令。只读，不碰模拟器，自己不写命令记录。默认看最近 3 天。

- 命令记录 `private/log/<日期>.jsonl`（`log`）：`failures` 按命令 + 错误码归类（次数、最后一次、原话、哪几次日常）；`misuse` 参数写错（退出码 2，带 `argv`）；
  `repeated` 同一条命令（参数也一样）失败两次以上；`shortcut_then_manual` 快捷操作（`op upgrade / mastery`、`craft`、`state op`）失败后 30 分钟内接着手动 `input …` 的。
- 会话记录（`transcripts`）：目录按仓库路径推出（Claude Code 的规则：路径里非字母数字的字符换成 `-`，放在 `~/.claude/projects/` 下；在 git worktree 里跑时用主仓库的路径），`--transcripts` 可以改。
  每个会话：`label`（v0.1 定时日常叫起的无头 agent 写成"日常 <运行编号> <项>"，对照 `private/log/agent-*.jsonl`）、开头的提示、`denied` 被拒的工具调用（权限没放行、用户拒绝）、
  `maapilot_failed` 退出码不为 0 的 maapilot 命令（命令、退出码、错误码、原话）、`tool_errors` 其他工具出错、`bash_nonstandard` 不是"一条 `maapilot …`"形式的 Bash
  （管道、`&&`、`;`、`cat`、`grep`……；引号里的 `|` 不算）、`result` 最后的"结果：…"。
  不看：`--exclude` 的、当前会话（环境变量 `CLAUDE_CODE_SESSION_ID`）、开发会话（改过 `maapilot/`、`tests/`、`docs/`、skill、`CLAUDE*.md`，跑过 pytest、git commit，
  或 agent 名以 dev 开头；`--include-dev` 也看）。`--session` 点名的会话照看不误。
- `data.lines` 是给人看的几行要点。

```json
{"since": "2026-10-10T16:00+08:00", "lines": ["命令记录 656 行，失败 14", "  失败 craft / shortcut_failed ×3：…", "会话 9 个（排除 7）", "  6aa16058 日常 20261011-1640 goals …：被拒 1、maapilot 失败 6、非标准 Bash 14/64；结果：完成"],
 "log": {"rows": 656, "failed": 14, "failures": [{"command", "error", "exit", "count", "last_at", "message", "runs"}], "misuse": […], "repeated": […], "shortcut_then_manual": […]},
 "transcripts": {"dir": "…", "sessions": [{"session", "label", "start", "end", "prompt", "result", "bash", "denied"?, "maapilot_failed"?, "tool_errors"?, "bash_nonstandard"?}], "excluded": [{"session", "why"}]}}
```

`maapilot dev rules [--check] [--root <目录>]`：按 `maapilot/policy.py` 生成两个宿主的权限规则文件和本文件里抄的设置（见「Claude Code 权限设置」）。
`data`：`{"files": [{"path", "changed"}], "written"}`；`--check` 只核对、不写，有不一样的退出码 1 `rules_out_of_date`（`data.files` 照样带上）。

`dev` 下的命令都不加权限规则（用户的 agent 用会弹确认），skill 和 `AGENTS.md` / `CLAUDE.md` 里不提，`maapilot --help` 里不列（`tests/test_docs_consistency.py` 的 `DEV_ONLY`）。

---

## private/ 文件

`private/` 不入库，由程序和 setup 创建。`MAAPILOT_PRIVATE` 环境变量可以指到别处（测试用）。

```
private/
  config.yaml        配置（templates/config.yaml 复制来，setup 填好）
  setup.json         setup 完成的标记
  goals.md           进行中的目标
  goals-done.md      完成或放弃的目标
  state/             depot.json  box.json  sanity.json（最新快照）  live.json（现场练度）  annihilation.json（本周剿灭）  news.json（官方公告缓存）
                     sources.json（外部数据源的格式健康：{来源: {last_ok, last_error, recent}}）
  cache/game/        game update 下载的安装包（.part 是没下完的；装完删掉）
  cache/copilot/     copilot strip 存的去掉练度要求的作业
  log/               <YYYY-MM-DD>.jsonl（命令记录）  maa/（maa-cli 日志）  daily-<run_id>.jsonl（一次日常的每一步）
                     （v0.1 留下的 schedule.log / cron.log、agent-*.jsonl 不再写）
  reports/           <YYYY-MM-DD-HHMM>.md / .json（每次日常的汇报）
  shots/             截图
  debug/             MaaFramework 调试输出、快捷操作失败时的截图
  guard/             snapshot.json  allowances.jsonl  stop.json  dirty.json
  memory/            用户偏好（MEMORY.md 是目录；.imported-from-claude 是从 Claude Code 自动记忆搬过一次的记号）
  maa-config/        本项目的 maa-cli 配置目录（MAA_CONFIG_DIR）：cli.toml  profiles/default.toml  tasks/maapilot-*.toml
  lock.json          模拟器锁
  daily-run.json     正在跑的日常（run_id、pid、当前步骤）；daily run 进程结束就删
  daily-open.json    没收尾的日常（run_id、trigger、started_at、第一份仓库和目标快照）；daily done 收尾时删
```

### config.yaml

见 `templates/config.yaml` 的注释。键：

| 键 | 含义 | 默认 |
|---|---|---|
| `emulator.address` | adb 地址（setup 找出来填） | 无（必填） |
| `emulator.adb` | adb 程序 | `adb` |
| `server` | 客户端（MAA 的 client_type）：`Official` / `Bilibili` / `YoStarEN` / `YoStarJP` / `YoStarKR` / `txwy` | `Official` |
| `agent` | v0.1 的设置（定时日常叫起哪个无头 agent），v0.2.0 起不用：写了就忽略，`doctor` 的 `agent_config` 提示可以删 | 无 |
| `tools.maa` / `tools.claude` / `tools.codex` | maa、claude、codex 程序（`maa` 写程序名时先用 `.tools/` 里装好的；claude / codex 给 `host status / install`、`doctor` 找程序用） | `maa` / `claude` / `codex` |
| `schedule` | 每天定时日常的时间（本机时间，`"HH:MM"` 列表）：程序不按它定时，agent 按它在 harness 里建会话内定时（`maapilot-setup`、docs/harnesses.md） | `["04:40", "16:40"]` |
| `schedule_options` | v0.1 的设置（Windows 任务计划程序的补跑、唤醒），不用了：写了就忽略（`agent_config` 提示可以删） | 无 |
| `daily.tasks` | 按顺序跑的 MAA 日常项：`startup` `recruit` `infrast` `mall` `annihilation` `award` | 全部 |
| `daily.default_stage` | 没有进行中的目标时刷哪关 | `"1-7"` |
| `daily.params.<任务>` | 覆盖该项的 MAA 参数 | `{}` |
| `daily.agent_timeout_min` / `daily.recovery_timeout_min` | v0.1 给无头 agent 的时限，不用了：写了就忽略 | 无 |
| `daily.task_timeout_min` | 一个 MAA 日常项最长跑多久（分钟） | `90` |
| `game.accept_terms` | 游戏更新后弹出《用户协议》《隐私政策》更新时 agent 能不能直接点同意：`null` 还没问过（每次问用户）、`true` 直接点、`false` 每次问；别的值当 `null`（`game version` 的 `accept_terms`） | `null` |

企鹅物流的服务器代码由 `server` 推出（Official/Bilibili → CN，YoStarEN → US，YoStarJP → JP，YoStarKR → KR，txwy → CN）。

### setup.json

setup 最后一步由 agent 用文件工具写：

```json
{"done_at": "2026-09-30T21:40:00+08:00", "version": 1}
```

不存在 = 没做过 setup（`doctor --brief` 的 `next: "setup"`）。重新 setup 时覆盖。

### goals.md —— 目标清单

半结构化 Markdown：每个目标一个二级标题，下面**固定 5 行字段**（限时的另有第 6 行"期限"），程序只读这几行；再往下是 agent 自由写的"分解与进展"。
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
- `期限`（可选，`活动` 目标必填）：`2026-10-30 03:59`（游戏时间 UTC+8，到这一刻到期）或 `2026-10-30`（算到次日 04:00）；后面可以跟括号说明，如 `2026-10-30 03:59（昨日海结束）`。
  过了期限就"到期"：不算进行中，`goals expire`（`daily run` 开头自动做）把它挪进 goals-done.md。
- `状态`：`进行中` / `暂停`（goals-done.md 里是 `完成` / `放弃` / `到期`）。只有"进行中"且没过期限的算"有进行中的目标"。
- `花合成玉/源石`：`是` / `否`。"是"的目标，每次执行到花合成玉、源石的那一步都要再问用户（`guard allow` 仍然弹确认）。
- 固定字段之后到下一个 `## ` 之前都是"分解与进展"，程序原样保留，只在 `--note` 时往末尾追加一行。
- 程序改文件时只改动涉及的那一段，其余字节不变；写入用临时文件 + 替换。

goals-done.md 同样格式，每段多一行 `- 结束：2026-10-05 完成，遥已精2（已核对快照）` 放在固定字段之后，新挪来的放在文件最后。到期的是 `- 结束：2026-10-30 到期，期限 2026-10-30 03:59 已过，自动结束`。

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
| `活动 <名>`（限时活动：刷活动商店、活动关三星……，要写期限） | `活动 昨日海` |
| `其他 <文字>`（程序算不了的，如模组任务） | `其他 泥岩模组X的模组任务` |

`calc need` 对 `关卡`、`活动` 和 `其他` 退出码 2（`not_computable`），由 agent 自己分解。

### state/

`depot.json`：`{"at": iso, "items": {"<id>": count}}`；`box.json`：`{"at": iso, "operators": {"<名>": {"id", "rarity", "elite", "level", "potential"}}}`；
`sanity.json`：`{"at": iso, "current": n, "max": n}`；
`live.json`：`{"operators": {"<名>": {"elite", "level", "skill_level", "mastery": {"<技能>": 专几}, "at", "source"}}}`（只有读到的项，都是下界）。

### log/

`<YYYY-MM-DD>.jsonl`，每个碰模拟器的命令一行：`{"at", "command", "args", "ok", "exit", "duration_s", "result"?, "error"?, "message"?, "stage"?, "argv"?, "run_id"?, "depot_change"?, "shot_now"?}`
（失败时 `error` 是 `error.code`、`message` 是原话，快捷操作另有 `stage`：做到哪一步；`shot_now`：maa 任务失败时当场截的图）。
`depot_change` 只在命令本身读过仓库时有（`state depot`）；`run_id` 取环境变量 `MAAPILOT_RUN_ID`（`daily run` 给子进程设）。
`result` 是关键结果的摘要（`maapilot/tools/log_summary.py`，一行要小，不放整个 data）：`state sanity` 的 current / max / source / suspect、
`state depot` 的物品种数和龙门币、合成玉、源石，`guard snapshot / check` 的前后数和没放行的减少，`maa <任务>` 的关卡、次数、理智、代理失败、掉落种数，
`op` / `craft` 的前后或状态，`goals add / update / remove / move / expire` 改了哪个目标，`calc need` 缺什么。
不碰模拟器的命令没包 `logged` 的，由 `cli` 补记：失败的都记（参数写错时另带 `argv`：去掉 token 等的命令行），成功的只记有摘要的。
森空岛 token、uid、昵称一律不记：`skland …` 的命令行只留子命令名、出错原话不记，`--uid` 之类的值换成 `<略>`，像 token 的长串也换掉。
写记录用 `maapilot.tools.common.logged`（包住处理函数）或 `common.record()`。`maa/` 下是每次 maa-cli 调用的完整日志。

### reports/

`<YYYY-MM-DD-HHMM>.md`：给人看的汇报（做了什么、花了什么、失败与处理、目标进展、要用户决定的事）；
`.json`：`daily run` 的 `data`（加上 `report write` 追加的 `appendix`、`daily done` 写的 `goals_summary`、收尾的 `open` / `closed_at` / `closed_by`）。
v0.1 留下的 `seen.json` 不再读写。

### guard/

`snapshot.json`：`{"at", "originite", "orundum", "exact"}`（`exact`：两个数都是首页顶栏的精确值）；`allowances.jsonl`：`{"at", "item": "originite|orundum", "amount", "reason", "used": bool, "used_amount"}` 每行一笔；
`stop.json`：`{"at", "before", "after", "unexplained", "run_id"?}`，在就是停机中。

---

## Codex 命令规则

`.codex/rules/maapilot.rules`（入库，Starlark 的 `prefix_rule`）和下面 Claude Code 的权限出自同一份规则 `maapilot/policy.py`，由 `maapilot dev rules` 生成（见「Claude Code 权限设置」；不要手改）：
放行 → `allow`（在沙箱外跑），询问 → `prompt`，拒绝 → `forbidden`：`prompt` 只有 `guard allow`，没有 `forbidden`（10-20 用户定：权限只留两条，另一条是森空岛凭据文件，只有 Claude Code 拦得住）。
规则按命令的第一个词认程序：maapilot 的几种写法（`maapilot`、`.venv/bin/maapilot`、`maapilot.exe`、`.venv\Scripts\maapilot.exe`、`.venv/Scripts/maapilot.exe`）
放行 / 询问都一样。macOS 上 Codex 还会把绝对路径的程序按程序名认（`execpolicy check --resolve-host-executables` 实测），
Windows 上 Codex 经 PowerShell 跑命令，规则看到的是不是这样的写法还没核实。没列的命令在沙箱里跑（只能写仓库、没有网络，连不上模拟器）。
只有 Codex 信任这个文件夹时才加载（`host install codex`；`host status` 的 `hosts.codex.trusted`）。

**在 Codex 里防得住什么**：防得住 agent 的无心之失（用错命令、绕过 maapilot 直接点、记错同意），防不住一个被注入了指令、存心绕过的 agent：
- 规则只管命令，管不了文件：沙箱里写得了 `private/guard/`、`private/lock.json`，读得了 `private/skland.json`（Claude Code 是拒绝编辑 / 读取）；只靠 AGENTS.md 硬规则；
- 放行的 maapilot 命令在沙箱外跑，而仓库里的代码（`maapilot/`）沙箱里也写得了：改过的代码会以沙箱外的权限运行。
真实货币的对账照样在每次日常前后跑，花掉的会被发现（但不是拦在花之前）。

`daily run` 把锁交给它起的子进程用的环境变量 `MAAPILOT_LOCK_TOKEN` 名字里带 `TOKEN`：子进程是 `daily run` 自己起的，不经过 Codex 的环境变量策略，不受影响。

## Claude Code 权限设置

`.claude/settings.json`（入库，下面是它的全文；`tests/test_docs_consistency.py` 检查 skill 里写的每条命令都有放行或询问的规则）。

**权限规则只写一份**：`maapilot/policy.py`（每个 maapilot 子命令前缀的放行 / 询问 / 拒绝、别的程序 maa / adb / sleep、只有 Claude Code 有的文件规则 Read / Edit / WebFetch）。
`maapilot dev rules`（开发用）按它生成三处：`.claude/settings.json` 的 `permissions`（别的部分如 `hooks` 原样保留）、整份 `.codex/rules/maapilot.rules`、
本节下面抄的 settings.json 全文；`--check` 只核对，不一样就退出码 1 `rules_out_of_date`（`tests/test_policy.py` 也核对，并检查两边的结论一致）。这三处都不要手改。
两边的差别：Claude Code 的放行 / 询问只列本名 `maapilot`、拒绝的列全部写法，Codex 每条都列全部写法；`sleep 300` 在 Claude Code 里精确匹配、在 Codex 里是前缀；文件规则只有 Claude Code 有。

```json
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "permissions": {
    "allow": [
      "Bash(maapilot:*)",
      "Bash(sleep 300)",
      "Read(private/**)",
      "Edit(private/config.yaml)",
      "Edit(private/goals.md)",
      "Edit(private/goals-done.md)",
      "Edit(private/setup.json)",
      "Edit(private/shots/**)",
      "WebFetch(domain:prts.maa.plus)",
      "WebFetch(domain:prts.plus)",
      "WebFetch(domain:maa.plus)"
    ],
    "ask": [
      "Bash(maapilot guard allow:*)"
    ],
    "deny": [
      "Read(private/skland.json)",
      "Edit(private/skland.json)"
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

- `maa why` 只读，在 `maapilot maa:*` 的放行里。`news …` 只联网、写自己的缓存；`game update` 要下载、覆盖安装游戏，
  但只装官网的包（地址只认官方域名），所以都放行。`sleep 300` 是会话里掉线时等用户回复的 5 分钟计时器（skill `maapilot-recovery`「掉线、被登出」，后台运行）。
- 只有 `guard allow`（记下花合成玉 / 源石的同意）是 **ask**：真实货币是程序唯一死守的一道（见「命令总览」），再弹一次宿主的确认（远程也能点）；
  有日常在跑或没收尾时命令自己也拒绝，换了宿主也一样。
- `daily unschedule`、`reset`、`host install`、`lock release`、改 `private/config.yaml` 都放行：agent 在对话里问过、用户同意了就做（AGENTS.md 硬规则），
  程序不另设关卡。`reset` 日常在跑、锁被占着时自己不做；`lock release` 只删残留的锁。
- `maapilot` 整个放行（`Bash(maapilot:*)`），只列本名（启动钩子把 `.venv` 的程序目录放进了 PATH），别的写法按默认弹确认。
  直接跑 `maa` / `adb`、改 `private/guard/` 和锁都没有规则：靠 AGENTS.md 硬规则、锁和对账（10-20 用户定，开发后期再收）。
- 森空岛凭据：`private/skland.json` agent 读不了、改不了（拒绝；Codex 拦不住读文件，见「Codex 命令规则」）。
  `skland token` 让用户自己在终端里跑是 AGENTS.md 的约定，不是权限规则。
- `guard snapshot` 可以放行：上次对账之后跑过可能花东西的命令时它改做对账（见「guard」），不能拿来抹掉花费。
- 启动钩子 `bin/session-start` 用 `$CLAUDE_PROJECT_DIR` 定位（不依赖钩子的工作目录）：先往 `$CLAUDE_ENV_FILE` 写一行把 `.venv/bin` 加进 PATH（环境还没建也写，setup 建好后同一会话就能用），再跑 `doctor --hook`：内容同 `--brief`，但输出成 Claude Code 钩子的格式 `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "MaaPilot 启动检查…<--brief 的信封>"}}`（直接输出的 JSON 会被当成钩子的控制字段，不进会话），退出码总是 0。
  `next` 是 `setup` / `guard` 时另带顶层的 `systemMessage`（直接显示给用户的一行）：钩子不会让 agent 先开口，要用户先说一句，这一行告诉用户"随便说一句就开始"。
  `.venv` 还没建时钩子自己按钩子的格式输出（`no_env`），agent 按 `maapilot-setup`「装运行环境」帮用户建。
- 启动钩子的输出进会话上下文；`AGENTS.md` 让 agent 看到 `next: "setup"` 时，用户一开口就进入 `maapilot-setup`。
