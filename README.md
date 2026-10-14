<!-- markdownlint-disable -->

<div align="center">

# MaaPilot

<div>
    <img alt="Python" src="https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white">
    <img alt="platform" src="https://img.shields.io/badge/platform-macOS-blueviolet">
    <img alt="Claude Code" src="https://img.shields.io/badge/agent-Claude%20Code-D97757">
    <img alt="MAA" src="https://img.shields.io/badge/powered%20by-MAA-ff69b4">
    <img alt="license" src="https://img.shields.io/badge/license-AGPL--3.0-blue">
</div>

<br>

一个会自己拿主意的明日方舟助手

每天定时做完日常；练干员、合材料、打关卡，跟它说一声就行

底下用 [MAA](https://github.com/MaaAssistantArknights/MaaAssistantArknights) 操作游戏，由 [Claude Code](https://claude.com/claude-code) 里的 agent 判断做什么

</div>

<!-- markdownlint-restore -->

## 下载与安装

需要：一台 Mac、一个安卓模拟器（装好明日方舟并登录过）、[Claude Code](https://claude.com/claude-code)（Codex 等其他 agent 的适配在做了）。

```sh
git clone https://github.com/mansicer/MaaPilot.git
cd MaaPilot
claude
```

第一次打开时，Claude Code 会问要不要信任这个文件夹，选信任（定时日常要用到）。
屏幕上会提示还没设置，随便说一句（比如"开始"）就进入设置：助手会带你确认模拟器、装好 MAA、问你日常几点跑、做哪些，十几分钟就好。

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

打开 Claude Code 后直接说想做的事：

- "现在跑一次日常" / "今天做了什么"
- "把遥练到精2" / "给能天使专三" / "目标进度怎么样"
- "合 10 个聚酸酯组"
- "打一次剿灭" / "帮我做泥岩的模组任务"
- "最近有什么活动" / "日常改到早上 6 点"

### 放心的地方

- **合成玉、源石**每次花之前都会在对话里问你，你答应了才花；你不在的时候（定时日常）一律不花。助手动过游戏之后没经你同意就少了，程序会发现并停下日常（你自己在游戏里花的不算）。
- 练干员、合材料这类花费，第一次会先报数，你同意了才做。
- 其他拿不准的事，第一次遇到时问你，之后按你说的办；你不在的时候（定时日常）就先不做，写进汇报等你回来。
- 改定时日常、从头设置也要你在对话里点头。设定时日常时会告诉你到时要在你不在时运行哪些程序；之后设置里这些程序被改了，定时日常就先停下，等你确认。
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
- Agent：[Claude Code](https://claude.com/claude-code)

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
