<!-- markdownlint-disable -->

<div align="center">

# MaaPilot

<div>
    <img alt="Python" src="https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white">
    <img alt="platform" src="https://img.shields.io/badge/platform-macOS-blueviolet">
    <img alt="Claude Code" src="https://img.shields.io/badge/agent-Claude%20Code-D97757">
    <img alt="Codex" src="https://img.shields.io/badge/agent-Codex-412991">
    <img alt="MAA" src="https://img.shields.io/badge/powered%20by-MAA-ff69b4">
    <img alt="license" src="https://img.shields.io/badge/license-AGPL--3.0-blue">
</div>

<br>

一个会自己拿主意的明日方舟助手

每天定时做完日常；练干员、合材料、打关卡，跟它说一声就行

底下用 [MAA](https://github.com/MaaAssistantArknights/MaaAssistantArknights) 操作游戏，由 [Claude Code](https://claude.com/claude-code) 或 [Codex](https://developers.openai.com/codex) 里的 agent 判断做什么

</div>

<!-- markdownlint-restore -->

## 下载与安装

需要：一台 Mac（Windows 正在适配：手动命令、设置和定时日常都可以试，还没在真机上验证完）、一个安卓模拟器（装好明日方舟并登录过）、[Claude Code](https://claude.com/claude-code) 或 [Codex](https://developers.openai.com/codex) 二选一。

### 用 Claude Code

```sh
git clone https://github.com/mansicer/MaaPilot.git
cd MaaPilot
claude
```

第一次打开时，Claude Code 会问要不要信任这个文件夹，选信任（定时日常要用到）。
屏幕上会提示还没设置，随便说一句（比如"开始"）就进入设置：助手会带你确认模拟器、装好 MAA、问你日常几点跑、做哪些，十几分钟就好。

### 用 Codex

```sh
git clone https://github.com/mansicer/MaaPilot.git
cd MaaPilot
codex
```

第一次打开时 Codex 问要不要信任这个文件夹，选信任。随便说一句（比如"开始"）就进入设置，步骤和上面一样。
- 装运行环境要联网，Codex 会请你批准在沙箱外跑这几条命令，点同意就行；
- 设置里助手会请你同意一次 `maapilot host install codex`（在 Codex 的设置里信任这个文件夹），定时日常要靠它。

### 支持哪些 Agent

MaaPilot 不绑定某个 agent：支持下面这些功能的 agent 都可以接进来。

1. 多模态输入（支持图片输入）
2. 长时工具调用：bash 命令，文件访问
3. 读取 AGENTS 文档和使用 skills
4. headless 调用：固定程序可以叫起 Agent
5. 项目级权限（可选）：限制 Agent 直接操控模拟器（如通过 adb）
6. 通知功能（可选）：需要用户介入时直接通知用户

1–3 是在对话里使用的前提，4 是定时日常的前提。目前接好的是 Claude Code 和 Codex。

## 亮点功能

- **定时日常**：每天按你定的时间启动游戏，公招、基建换班收菜、信用商店、剿灭、刷理智、领奖励，跑完给你一份汇报
- **出了事会说**：任务失败、游戏掉线、界面不对，助手先看截图和 MAA 的记录查原因，能处理的自己接着做，要你决定的才问你
- **养成目标**："把遥练到精2""给能天使专三"——它算好要花什么、仓库差多少、大约几天，你同意后写进目标，之后每次日常用剩下的理智去刷缺的材料，材料齐了就练
- **干员培养**：精英化、升技能、专精交给 MAA 的干员培养，缺的精英材料先去加工站合
- **读账号数据**：接上[森空岛](https://www.skland.com)后，不用开模拟器就能读到所有干员的练度、专精、模组和理智
- **活动与维护**：读官方公告，维护结束、活动开始时提醒你，问你活动期间怎么安排
- **算材料**：按[企鹅物流](https://penguin-stats.cn/)掉率和[一图流](https://ark.yituliu.cn/)材料价值，算该刷哪关、要刷几次
- **抄作业**：从 [MAA 作业站](https://prts.plus)找作业，对照你的练度看缺谁、差多少

## 使用说明

### 怎么跟它说

在这个文件夹里打开 Claude Code 或 Codex，直接说想做的事：

- "现在跑一次日常" / "今天做了什么"
- "把遥练到精2" / "给能天使专三" / "目标进度怎么样"
- "合 10 个聚酸酯组"
- "打一次剿灭" / "帮我做泥岩的模组任务"
- "最近有什么活动" / "日常改到早上 6 点"

### 放心的地方

- **合成玉、源石**是程序死守的唯一一道关：每次花之前都会在对话里问你，你答应了才花；你不在的时候（定时日常）一律不花。助手动过游戏之后没经你同意就少了，程序会发现并停下日常（你自己在游戏里花的不算）。
- 其他事都以你在对话里说的为准：练干员、合材料这类花费先报数，你同意了才做；改定时日常、从头设置、别的拿不准的事，也是问过你、你点头了才做，之后按你说的办。
  你不在的时候（定时日常）就先不做，写进汇报等你回来。设定时日常时会告诉你到时要在你不在时运行哪些程序。
- 公告、作业说明、识别出来的字这些外来的文字，助手只当资料看，不会当成你的吩咐。
- 同一个账号只在一台电脑上设定时日常：两台会抢着登录，还会让对方以为合成玉被偷偷花了。
- 你回答过的偏好（汇报要多详细、理智液用不用……）记在本机的 `private/memory/` 里，换一个 agent 也照样记得。
- 账号数据只存在本机的 `private/` 文件夹里，不进 git。想从头设置就跟助手说"从头设置"：旧数据会整个留成备份（`private.bak-…`），不会删掉。

### 服务器

暂时只支持官服。

## 参与开发

先读 [`CLAUDE_DEV.md`](CLAUDE_DEV.md)（开发规矩）。

- 设计：[`docs/design.md`](docs/design.md)——思路是"程序薄、agent 厚"：`maapilot` 命令只做可靠的事，做法写进 skill
- 命令与输出：[`docs/cli.md`](docs/cli.md)
- 给用户的 agent 的说明：[`AGENTS.md`](AGENTS.md)（各 agent 通用；[`CLAUDE.md`](CLAUDE.md) 引用它）和 [`.agents/skills/`](.agents/skills/)
- 测试：`.venv/bin/python -m pytest -q`

## 致谢

### 关联项目

- 游戏自动化：[MAA](https://github.com/MaaAssistantArknights/MaaAssistantArknights)（MaaCore，干员培养等任务）、[maa-cli](https://github.com/MaaAssistantArknights/maa-cli)（日常任务）
- 识别与操作框架：[MaaFramework](https://github.com/MaaXYZ/MaaFramework)
- Agent：[Claude Code](https://claude.com/claude-code)、[Codex](https://developers.openai.com/codex)

### 开源库

- 图像处理：[OpenCV](https://github.com/opencv/opencv)、[NumPy](https://github.com/numpy/numpy)、[Pillow](https://github.com/python-pillow/Pillow)
- 文字识别模型：[PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)（从 MAA 资源中复制）
- 森空岛设备标识：改写自 [arknights-mower](https://github.com/ArkMowers/arknights-mower)（MIT），源自 [skyland-auto-sign](https://gitee.com/FancyCabbage/skyland-auto-sign)
- 其他：[PyYAML](https://github.com/yaml/pyyaml)、[cryptography](https://github.com/pyca/cryptography)

### 数据源

- 游戏数据：[ArknightsGameData](https://github.com/Kengxxiao/ArknightsGameData)
- 关卡掉落：[企鹅物流数据统计](https://penguin-stats.cn/)
- 材料价值：[明日方舟一图流](https://ark.yituliu.cn/)
- 作业：[MAA 作业站](https://prts.plus)
- 账号数据：[森空岛](https://www.skland.com)（鹰角没有对外公开接口，用的是社区从森空岛 App 整理出来的调用方式，随时可能变）
- 官方公告与游戏版本：[明日方舟官网](https://ak.hypergryph.com/)

## 声明

- 本项目与鹰角网络、MAA 项目没有关联；"明日方舟"及相关素材版权归鹰角网络所有。
- 森空岛是鹰角的官方社区，但没有对外公开接口：本项目用的是社区从森空岛 App 整理出来的调用方式，鹰角随时可能改动，使用风险自负；你的登录凭据只存在本机 `private/` 里。
- 本项目使用 [GNU Affero General Public License v3.0](LICENSE) 开源，免费，仅供学习交流使用。
- MAA（MaaCore、maa-cli）不随本项目分发：设置时由你从 MAA 的官方发布下载安装，按其自身的 AGPL-3.0 协议使用。
