---
name: maapilot-setup
description: 第一次使用 MaaPilot 的设置向导 —— 确认模拟器、找 ADB 地址、doctor 逐项排查、装 maa-cli 与资源、试启动游戏、第一次读仓库和干员、问日常几点跑做哪些、用 harness 的会话内定时定好定时日常、清掉旧版的系统定时、写 setup.json。Use at session start when doctor --brief says next "setup" (private/setup.json missing), or when the user says 重新设置 / 换模拟器 / reinstall / setup / first run.
---

# maapilot-setup：第一次设置

目标：让日常能自己跑起来。**只问跑起来必须的事**（模拟器、日常几点跑、做哪些、没目标时刷哪关、旧版的定时任务怎么办）；
其余（理智液算不算能用、汇报要多详细……）以后第一次遇到时再问。整个过程大约 10–20 分钟，开头告诉用户。

一次只问一个问题。每一步做完用一两句话告诉用户到哪了。命令输出都是 JSON，按 `data`、`error.hint` 判断，别把输出念给用户。

## 0. 打招呼

说清你是谁、接下来做什么，例如："你好！我是你的明日方舟助手，能帮你每天自动做日常、练干员、合材料。先花十几分钟把环境设好：确认模拟器、装 MAA、问你几个日常的偏好。先问一句：电脑上已经装好安卓模拟器、装了明日方舟吗？"

用户说要从头设置（全部重来）：先 `maapilot reset --dry-run` 把会挪走什么给用户看，用户同意再 `maapilot reset`（
用户偏好记录也要清才加 `--memory`；日常在跑或模拟器被占着时它不做，等一会儿再来），然后删掉这个会话里还没到的定时日常（见「定时日常」），请用户重新开一次会话。

重新设置（`private/setup.json` 已经在）时：先 `maapilot doctor` 看哪些已经好了，只重做用户想改的和没通过的部分。

## 装运行环境

启动检查里是 `no_env`（项目里还没有 `.venv/bin/maapilot`）：`maapilot` 命令都用不了，先装好。命令都在仓库根目录跑。
1. 跟用户说一次：要在这个文件夹里建一个 Python 运行环境，默认用 uv 装（uv 会自己准备 Python 3.12，一两分钟）。问用户同不同意。
2. **同意**：
   - 没有 uv（`uv --version` 跑不了）：`curl -LsSf https://astral.sh/uv/install.sh | sh`（官方安装脚本，装在 `~/.local/bin`，之后用 `~/.local/bin/uv`）。
   - `uv venv --python 3.12 .venv`，再 `uv pip install --python .venv/bin/python -e .`。
3. **不同意**：请用户给一个 Python 3.12 或更新版本的路径（例如 `/usr/local/bin/python3.12`），先 `<路径> --version` 确认版本，
   再 `<路径> -m venv .venv`、`.venv/bin/pip install -e .`。用户没有合适的 Python，就告诉用户可以从 https://www.python.org/downloads/ 装。
4. 装好后 `maapilot doctor --offline`（Claude Code 的启动钩子已经把 `.venv/bin` 加进了 PATH，同一个会话就能用；提示找不到命令（例如在 Codex 里）就写 `.venv/bin/maapilot`，见 AGENTS.md「命令约定」）。能出结果就接着往下做。
   在 Codex 里装东西要联网，沙箱里没有网络：命令会请用户批准在沙箱外跑，告诉用户这是装运行环境、点同意。

**Windows**（上面是 macOS / Linux 的写法；Windows 上程序在 `.venv\Scripts\` 下，不是 `.venv/bin/`）：
- 没有 uv：征得同意后 `winget install astral-sh.uv`；没有 winget 就在 PowerShell 里 `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`。装完要重开终端（或 agent）才找得到 `uv`。
- `uv venv --python 3.12 .venv`，再 `uv pip install --python .venv -e .`；不用 uv 时是 `<python.exe 路径> -m venv .venv`、`.venv\Scripts\python.exe -m pip install -e .`。
- 之后命令写成 `.venv\Scripts\maapilot.exe <命令> …`（`maapilot` 提示找不到时；见 AGENTS.md「命令约定」）。
- 仓库最好用 `git clone -c core.symlinks=true …` 拿（`.claude/skills` 是指向 `.agents/skills` 的链接）；已经普通 clone 了，`maapilot doctor --fix` 会把这个链接补上。
- **项目文件夹、Python、maa、adb 最好装在普通目录**（例如 `C:\Users\<你>\` 下）：在 Codex 的 Windows 应用里装的东西可能落进它的隔离目录（`…\AppData\Local\Packages\…\LocalCache\…`），
  别的程序看不到。遇到时请用户在普通的 PowerShell 里重新装到普通目录。

## 1. 模拟器

**没装**：按 MAA 的文档引导，给链接 + 要点，等用户装好再继续：
- 支持哪些模拟器、各平台的说明：https://maa.plus/docs/zh-cn/manual/device/ （macOS 看其中的 macOS 页；链接打不开就从 https://maa.plus/docs/ 的「用户手册」进）
- 连接设置（ADB 地址怎么找）：https://maa.plus/docs/zh-cn/manual/connection.html
- 要点：分辨率 **16:9、不小于 1280×720**（最省事就是正好 1280×720）；在模拟器设置里**打开 ADB 调试 / ADB 连接**；装好明日方舟并**自己先登录一次账号**（MAA 不替用户输密码）。

**装好了**：找 ADB 地址。
1. 若没有 `private/config.yaml`：读 `templates/config.yaml`，原样写成 `private/config.yaml`（`emulator.address` 先空着）。
2. 请用户先把模拟器打开。跑 `maapilot doctor`，看 `emulator` 这一项的 `candidates`（它会列 `adb devices` 和常见端口的探测结果）。
   - 只有一个候选：填进 `emulator.address`，再跑 `doctor` 确认连上。
   - 有几个：**先自己试，再请用户确认**。依次把每个候选填进 `emulator.address`，跑 `maapilot doctor`，连得上的再 `maapilot screen shot` 看一眼：
     画面是明日方舟、`game` 项的 `package` / `installed` 有明日方舟的，就是它。认出来以后跟用户确认一句（"连上的是开着明日方舟的那个模拟器窗口，对吗？"），用户说对就定下。
     几个都像、或者都不像，才问用户是哪个模拟器（多开时问是哪个窗口）；常见端口：BlueStacks 5555 起、MuMu 12 / MuMu Pro 16384（旧版 7555）、雷电 5555 或 `emulator-5554`、夜神 62001、逍遥 21503。
   - 一个都没有：请用户在模拟器设置里找"ADB 端口 / ADB 地址"并告诉你；还连不上就看连接设置那页的排障。
3. **服务器**：`doctor` 的 `game` 项里，`package` 是前台应用的包名（游戏开着时），`installed` 是模拟器里装着的明日方舟客户端（游戏没开也有，已经带了对应的 `server`）。
   只装了一个就用它的 `server`；前台的包名是：`com.hypergryph.arknights` → `Official`，`com.hypergryph.arknights.bilibili` → `Bilibili`，
   `com.YoStarEN.arknights` / `JP` / `KR` → `YoStarEN` / `YoStarJP` / `YoStarKR`，`tw.txwy.and.arknights` → `txwy`（大小写以实际为准）。认得出就直接填 `server`，认不出才问用户玩的是官服、B 服还是国际服。

## 2. 环境逐项通过

反复 `maapilot doctor`，按每项的 `fix` 处理，直到 `ok_all: true`（`setup` 这项此时不通过是正常的，最后才做）。

| 项 | 常见处理 |
|---|---|
| `adb` | 没有 adb：先征得用户同意，`maapilot tools install adb`（从 Google 官方下载 platform-tools 装进项目的 `.tools/`，`emulator.adb` 保持写 `adb` 就会用它）。Windows：优先用模拟器自带的 adb（MuMu 12 在安装目录的 `shell\adb.exe`，雷电在安装目录的 `adb.exe`，问用户装在哪），绝对路径写进 `emulator.adb`（单引号，见模板）；没有再 `maapilot tools install adb`。maa-cli 会跟着用这个 adb，别让 PATH 上另一个版本的 adb 同时连 |
| `emulator` | 见上一步；模拟器没开、ADB 没打开、地址填错 |
| `screen` | 分辨率不对：请用户在模拟器设置里改成 1280×720（16:9），改完多半要重启模拟器 |
| `maa_cli` | 征得同意后 `maapilot tools install maa-cli`：从 GitHub 下载实测过的 maa-cli 版本装进项目的 `.tools/`，MaaCore 和资源还没有就顺手 `maa install`（几分钟，要联网）；`tools.maa` 保持写 `maa` 就会用它。用户已经自己装好了 maa-cli（Homebrew 等）也行：写了绝对路径就照它，`doctor` 只看版本在不在实测范围。太旧（`doctor` 不通过）也是 `maapilot tools install maa-cli`，`tools.maa` 写了别的路径时问用户要不要改成 `maa`。Windows：MaaCore 跑不起来（缺 DLL、退出码怪）多半是没装 VC++ 运行库：征得同意后 `winget install Microsoft.VCRedist.2015+.x64`（或 https://aka.ms/vs/17/release/vc_redist.x64.exe） |
| `maa_config` `ocr_model` `game_data` | `maapilot doctor --fix`（装任务模板、复制 OCR 模型、同步游戏数据，不用问用户） |
| `game` | 游戏没在前台：下一步的试启动会处理 |
| `legacy_schedule` | 还留着旧版的系统定时任务：见第 4 步「旧版的定时任务」，用户同意后 `maapilot daily unschedule` |
| `agent_config` | `config.yaml` 里还写着旧版的 `agent`（现在用不到了）：跟用户说一声，同意后删掉那几行 |

**在 Codex 里**还要信任这个文件夹：`maapilot host status` 里 codex 的 `trusted` 是 `false` 时，征得同意后 `maapilot host install codex`（在 Codex 设置里信任这个文件夹）。
必须做：不信任时 Codex 不加载项目的命令规则，`guard allow` 每次弹确认这道护栏就没了。

装软件前说清装什么、为什么；用户不同意就停在这里，告诉用户缺了它哪些事做不了。

Windows 路径在 YAML 里用单引号（`'C:\...\maa.exe'`），双引号里的 `\` 会被当成转义；`private/` 下的文件存成 UTF-8。

## 3. 试启动、第一次读账号

1. `maapilot maa startup`（启动游戏回首页，一两分钟）。成功后看 `data.home`：不是 `true` 就 `maapilot screen shot` 看停在哪，按 `maapilot-recovery`「回首页」回去；
   是 `true` 也截一张图，看一眼确实在首页（右上有"终端"）。
   失败：按 `maapilot-recovery` 看屏幕。常见：停在登录页（账号没登录过 → 请用户在模拟器里手动登录一次）、公告弹窗、正在更新资源（等它下完）。
2. `maapilot state depot`（读仓库，一两分钟），再 `maapilot state box`（读干员，几分钟；Claude Code 里放后台跑，Codex 里前台跑、等它结束）。
3. 告诉用户读到了什么，一两句："读到了：龙门币约 120 万、合成玉 3200、干员 86 名。"读不到就说哪样没读到，稍后日常会再读。

## 4. 问日常（一次一个问题）

1. **每天几次、几点**：默认每天两次 04:40 和 16:40。说明：游戏 04:00 换日，别放在 04:00 前后；分钟别选整点、半点（:00、:30，有的 harness 会提前一两分钟叫醒）。
   到点时电脑要开着、不能睡眠，模拟器也要开着，**这个会话也要一直开着**（Codex 桌面版要应用开着）：定时是记在这个会话里的，关掉就没了。
2. **做哪些日常项**，默认全做，逐项用玩家的话说：启动游戏、公开招募、基建换班收菜、信用商店与访问好友、剿灭（本周合成玉拿满就不打）、领任务奖励和邮件。用户说"都做"就不再细问。
3. **没有目标时刷哪关**：默认 1-7（产固源岩，理智划算）。有目标时会优先刷目标缺的材料。
4. **游戏更新后的用户协议**：游戏大版本更新后，有时会弹出《用户协议》《隐私政策》更新，要点"同意"才能进游戏。问："以后遇到这种更新，要我直接替你点同意，还是每次先问你？"
   说清：选"直接同意"，半夜的定时日常也能自己把游戏更新好接着跑；选"每次问"，遇到时日常会停在那里等你回来。写进 `game.accept_terms`（`true` / `false`）。
**旧版的定时任务**：以前的 MaaPilot 用系统的定时任务（macOS / Linux 的 crontab、Windows 的"任务计划程序"）跑定时日常，现在改成会话里的定时。
启动检查里有 `legacy_schedule`（或 `doctor` 报出旧的定时）时：
- 先 `maapilot daily unschedule --dry-run`，把要删的那几行 / 那个任务给用户看，说明留着它会和会话里的定时重复跑日常、抢模拟器；
- 问用户要不要删：**只有用户能决定**，不要自己删。用户同意后 `maapilot daily unschedule`；用户想自己处理，就告诉他在终端 `crontab -e` 里删掉那一行，或在"任务计划程序"的 `MaaPilot` 文件夹里删掉。
- 别的目录装的更早版本的定时（命令里有 job tick 或 bin/daily.sh）`daily unschedule` 不删：原样给用户看，请用户自己删。

## 4b. 要不要接森空岛（可选）

问一句："要不要接森空岛？接了以后读干员的技能、专精、模组更准、更快（不用在模拟器里一个个看），日常每天也会顺手更新。"说清楚：
- 要用你的**鹰角账号 token**，只存在这台电脑的项目文件夹里（`private/skland.json`），我（agent）看不到也不会碰；
- 这是社区整理的非官方接口，可能哪天就不能用了，到时候还是用 MAA 识别，不影响别的；
- 不想接就跳过，以后想接再说。

用户要接：请用户自己做（agent 不能代办，也不要让用户把 token 发在对话里）：
1. 在浏览器登录鹰角官网 https://user.hypergryph.com/login ；
2. 打开 https://web-api.hypergryph.com/account/info/hg ，页面上是一段 JSON，复制 `content` 后面引号里的那串；
3. 在终端里（项目文件夹下）运行 `maapilot skland token`，粘贴后回车（不会显示）。说"好了"就行。
之后 `maapilot skland status` 看 `configured` 是 `true`，再 `maapilot state skland` 读一次，告诉用户读到了几名干员。
`choose_role`（绑了好几个角色）：把 `data.roles` 给用户看，请用户在终端里跑 `maapilot skland token --uid <要用的>`。

## 5. 写配置、定时日常、收尾

1. 把回答写进 `private/config.yaml`（用编辑文件的工具或普通 shell 命令都行）：`schedule`、`daily.tasks`（按模板里的顺序，去掉不做的）、`daily.default_stage`、`game.accept_terms`、前面的 `server`、`emulator`、`tools`。改完 `maapilot doctor` 再确认一次。
2. 设定时前问一句：这个账号是不是已经在别的电脑上设了定时日常？是的话这里就不设（两台会抢着登录，还会让对方的货币对账以为钱被偷偷花了），跳到第 3 步；用户想换到这台，请他先在那台停掉。
   还留着旧版的定时任务的，先按上面「旧版的定时任务」处理。然后按下面「定时日常」在这个会话里定好。

### 定时日常

用你所在的 harness 自己的**会话内定时**，不用系统的定时任务。各 harness 的工具和实测情况见 `docs/harnesses.md`（Claude Code 的做法写在 `CLAUDE.md`）。
- **一次性定时接力**（harness 有一次性定时的都这样做）：会话里始终只留**一个**"下一次日常"的一次性定时，时间是 `schedule` 里离现在最近的下一个时间点。
  setup 时建第一个；以后每次定时日常醒来、跑日常之前先建下一个（见 `maapilot-daily`「定时日常」）。
  不用每天重复的定时：有的 harness 的重复定时几天后就过期，还会晚响。
- 提示写成单看这一句就知道要做什么的（到点时前面的对话可能已经被压缩掉了）："定时日常（04:40）：按 skill maapilot-daily 做；先定好下一次"（括号里写这一次的时间）。
- 建之前先列一遍 harness 里已有的定时：已经有下一次日常的就不再建（只留一个）。建完再列一遍，**以 harness 里读回来的为准**：时间对上了才告诉用户"下一次是 10 月 21 日（明天）04:40"；对不上就说没定成。
- 改时间：删掉还没到的那个，按新时间建下一个。不要定时了：删掉还没到的那个。
- harness 没有一次性定时、只有重复的：每个时间点建一个每天重复的定时，有过期的照 `docs/harnesses.md` 跟用户说清多久要续一次。
- harness 没有会话内定时（Codex 命令行）：定时日常做不了，跟用户说清：可以在对话里说"跑一次日常"；想要定时，换用 Claude Code 或 Codex 桌面版。setup 照样做完。
- 跟用户说清：到点时电脑要开着、不睡眠，模拟器要能连上（最好开着），**这个会话不要关**（Codex 桌面版要应用开着）。

3. 写 `private/setup.json`：`{"done_at": "<现在的 ISO 时间，带时区>", "version": 1}`。
4. 告诉用户以后怎么用，简短几条：
   - 每天几点会在这个会话里自动跑日常，跑完当场告诉你；会话别关，回来在这里问"今天做了什么"就行；
   - 可以直接说想做的事："把某某练到精2""合 10 个聚酸酯组""打一次剿灭""现在跑一次日常"；
   - 花合成玉、源石的事每次都会先问；别的第一次遇到时问，之后按用户的回答办。
5. 问一句要不要现在就跑一次日常（`maapilot-daily`），不跑也没关系。

## 问用户的情形

- 模拟器装没装、是哪个、ADB 地址找不到时；
- 装软件（Python、uv、adb、maa-cli、VC++ 运行库）前；
- 服务器认不出时；
- 日常几次几点、做哪些、没目标刷哪关；
- 游戏更新后的用户协议要不要直接同意；
- 旧版的定时任务要不要删；
- 这个账号是不是已经在别的电脑上定时跑着。

除此之外不要问（理智液、汇报详略、代选这类以后遇到再问）。

## 记住什么（写进用户偏好 private/memory/）

- 这台电脑的环境怪癖和解决办法（例如"这个模拟器重启后 ADB 端口会变""截图要等模拟器完全开好"），写明日期。
- 用户在 setup 里顺口说的偏好（例如"别在晚上 11 点后跑"），写明是用户哪天说的。
- 不记账号、昵称、UID、ADB 地址这类配置里已有的东西，也不记仓库数量。
