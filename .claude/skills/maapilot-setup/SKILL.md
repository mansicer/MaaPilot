---
name: maapilot-setup
description: 第一次使用 MaaPilot 的设置向导 —— 确认模拟器、找 ADB 地址、doctor 逐项排查、装 maa-cli 与资源、试启动游戏、第一次读仓库和干员、问日常几点跑做哪些、设定时任务、写 setup.json。Use at session start when doctor --brief says next "setup" (private/setup.json missing), or when the user says 重新设置 / 换模拟器 / reinstall / setup / first run.
---

# maapilot-setup：第一次设置

目标：让日常能自己跑起来。**只问跑起来必须的事**（模拟器、日常几点跑、做哪些、没目标时刷哪关、旧定时任务怎么办）；
其余（理智液算不算能用、汇报要多详细……）以后第一次遇到时再问。整个过程大约 10–20 分钟，开头告诉用户。

一次只问一个问题。每一步做完用一两句话告诉用户到哪了。命令输出都是 JSON，按 `data`、`error.hint` 判断，别把输出念给用户。

## 0. 打招呼

说清你是谁、接下来做什么，例如："你好！我是你的明日方舟助手，能帮你每天自动做日常、练干员、合材料。先花十几分钟把环境设好：确认模拟器、装 MAA、问你几个日常的偏好。先问一句：电脑上已经装好安卓模拟器、装了明日方舟吗？"

用户说要从头设置（全部重来）：先 `maapilot reset --dry-run` 把会挪走什么、删掉哪个定时任务给用户看，用户同意再 `maapilot reset`（弹确认；
自动记忆也要清才加 `--memory`），然后请用户重新开一次会话。

重新设置（`private/setup.json` 已经在）时：先 `maapilot doctor` 看哪些已经好了，只重做用户想改的和没通过的部分。

## 装运行环境

启动检查里是 `no_env`（项目里还没有 `.venv/bin/maapilot`）：`maapilot` 命令都用不了，先装好。命令都在仓库根目录跑。
1. 跟用户说一次：要在这个文件夹里建一个 Python 运行环境，默认用 uv 装（uv 会自己准备 Python 3.12，一两分钟）。问用户同不同意。
2. **同意**：
   - 没有 uv（`uv --version` 跑不了）：有 Homebrew 就 `brew install uv`，没有就 `curl -LsSf https://astral.sh/uv/install.sh | sh`（装在 `~/.local/bin`，之后用 `~/.local/bin/uv`）。
   - `uv venv --python 3.12 .venv`，再 `uv pip install --python .venv/bin/python -e .`。
3. **不同意**：请用户给一个 Python 3.12 或更新版本的路径（例如 `/usr/local/bin/python3.12`），先 `<路径> --version` 确认版本，
   再 `<路径> -m venv .venv`、`.venv/bin/pip install -e .`。用户没有合适的 Python，就告诉用户可以从 https://www.python.org/downloads/ 装。
4. 装好后 `maapilot doctor --offline`（启动钩子已经把 `.venv/bin` 加进了 PATH，同一个会话就能用；万一提示找不到命令，就用 `.venv/bin/maapilot` 跑这一次，并请用户之后重开一次会话）。能出结果就接着往下做。

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

反复 `maapilot doctor`，按每项的 `fix` 处理，直到 `ok_all: true`（`schedule`、`setup` 两项此时不通过是正常的，最后才做）。

| 项 | 常见处理 |
|---|---|
| `adb` | 没有 adb：先征得用户同意，`brew install --cask android-platform-tools`（没有 Homebrew 就请用户按 https://brew.sh 装） |
| `emulator` | 见上一步；模拟器没开、ADB 没打开、地址填错 |
| `screen` | 分辨率不对：请用户在模拟器设置里改成 1280×720（16:9），改完多半要重启模拟器 |
| `maa_cli` | 征得同意后 `brew install MaaAssistantArknights/tap/maa-cli`，再 `maa install`（下载 MaaCore 和资源，几分钟）。这两条会弹权限确认 |
| `maa_config` `ocr_model` `game_data` | `maapilot doctor --fix`（装任务模板、复制 OCR 模型、同步游戏数据，不用问用户） |
| `game` | 游戏没在前台：下一步的试启动会处理 |
| `claude` | `trusted: false`：请用户在这个文件夹里正常启动一次 claude 并接受信任提示（不然定时日常叫起的 agent 用不了放行的命令）；这项只是提示 |

装软件前说清装什么、为什么；用户不同意就停在这里，告诉用户缺了它哪些事做不了。

然后把 `tools.maa`、`tools.claude` 改成绝对路径（定时任务的 PATH 很短）：`doctor` 的 `maa_cli`、`claude` 两项的 `path` 就是，写进 `private/config.yaml`。`claude` 项没通过时问用户 claude 装在哪。

## 3. 试启动、第一次读账号

1. `maapilot maa startup`（启动游戏回首页，一两分钟）。成功后看 `data.home`：不是 `true` 就 `maapilot screen shot` 看停在哪，按 `maapilot-recovery`「回首页」回去；
   是 `true` 也截一张图，看一眼确实在首页（右上有"终端"）。
   失败：按 `maapilot-recovery` 看屏幕。常见：停在登录页（账号没登录过 → 请用户在模拟器里手动登录一次）、公告弹窗、正在更新资源（等它下完）。
2. `maapilot state depot`（读仓库，一两分钟），再 `maapilot state box`（读干员，几分钟，放后台跑）。
3. 告诉用户读到了什么，一两句："读到了：龙门币约 120 万、合成玉 3200、干员 86 名。"读不到就说哪样没读到，稍后日常会再读。

## 4. 问日常（一次一个问题）

1. **每天几次、几点**：默认每天两次 04:40 和 16:40。说明：游戏 04:00 换日，别放在 04:00 前后；到点时电脑要开着、不能睡眠，模拟器也要开着。
2. **做哪些日常项**，默认全做，逐项用玩家的话说：启动游戏、公开招募、基建换班收菜、信用商店与访问好友、剿灭（本周合成玉拿满就不打）、领任务奖励和邮件。用户说"都做"就不再细问。
3. **没有目标时刷哪关**：默认 1-7（产固源岩，理智划算）。有目标时会优先刷目标缺的材料。
4. **游戏更新后的用户协议**：游戏大版本更新后，有时会弹出《用户协议》《隐私政策》更新，要点"同意"才能进游戏。问："以后遇到这种更新，要我直接替你点同意，还是每次先问你？"
   说清：选"直接同意"，半夜的定时日常也能自己把游戏更新好接着跑；选"每次问"，遇到时日常会停在那里等你回来。写进 `game.accept_terms`（`true` / `false`）。

**旧版 MaaPilot 的定时任务**：`doctor` 的 `schedule` 项会报出旧版的定时行（命令里有 job tick 或 bin/daily.sh）。有的话：
- 把那一行**原样**给用户看，说明两边同时跑会抢模拟器、重复做日常；
- 问用户要不要停掉旧的：**只有用户能决定**，不要自己删。用户同意后在第 5 步用 `daily schedule --replace-old` 删掉；用户想自己处理，就告诉他在终端 `crontab -e` 里删掉或注释掉这一行。

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

## 5. 写配置、设定时、收尾

1. 把回答写进 `private/config.yaml`（用编辑文件的工具或普通 shell 命令都行；会弹确认框时告诉用户点同意就是保存设置）：`schedule`、`daily.tasks`（按模板里的顺序，去掉不做的）、`daily.default_stage`、`game.accept_terms`、前面的 `server`、`emulator`、`tools`。改完 `maapilot doctor` 再确认一次。
2. `maapilot daily schedule`（用户要停旧任务时加 `--replace-old`）。会弹权限确认；macOS 可能还会弹"终端想要管理电脑"之类的系统窗口，请用户点允许。
3. 写 `private/setup.json`：`{"done_at": "<现在的 ISO 时间，带时区>", "version": 1}`。
4. 告诉用户以后怎么用，简短几条：
   - 每天几点会自动跑日常，跑完有汇报，打开这个文件夹启动 claude 问"今天做了什么"就行；
   - 可以直接说想做的事："把某某练到精2""合 10 个聚酸酯组""打一次剿灭""现在跑一次日常"；
   - 花合成玉、源石的事每次都会先问；别的第一次遇到时问，之后按用户的回答办。
5. 问一句要不要现在就跑一次日常（`maapilot-daily`），不跑也没关系。
6. 开始盯着日常：后台挂一个 `maapilot daily watch`（见 CLAUDE.md「盯着日常」），以后一直挂着。

## 问用户的情形

- 模拟器装没装、是哪个、ADB 地址找不到时；
- 装软件（Python、Homebrew、adb、maa-cli）前；
- 服务器认不出时；
- 日常几次几点、做哪些、没目标刷哪关；
- 游戏更新后的用户协议要不要直接同意；
- 旧版定时任务要不要停。

除此之外不要问（理智液、汇报详略、代选这类以后遇到再问）。

## 记住什么（写进自动记忆）

- 这台电脑的环境怪癖和解决办法（例如"这个模拟器重启后 ADB 端口会变""截图要等模拟器完全开好"），写明日期。
- 用户在 setup 里顺口说的偏好（例如"别在晚上 11 点后跑"），写明是用户哪天说的。
- 不记账号、昵称、UID、ADB 地址这类配置里已有的东西，也不记仓库数量。
