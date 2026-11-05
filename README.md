<!-- markdownlint-disable -->

<div align="center">

# MaaPilot

<div>
    <img alt="Python" src="https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white">
    <img alt="platform" src="https://img.shields.io/badge/platform-macOS%20%7C%20Windows-blueviolet">
    <img alt="MAA" src="https://img.shields.io/badge/powered%20by-MAA-ff69b4">
    <img alt="license" src="https://img.shields.io/badge/license-AGPL--3.0-blue">
</div>

<br>

LLM接管的明日方舟全流程自动化Agent，定时日常到特殊需求，都通过聊天框解决。

基于 [MAA](https://github.com/MaaAssistantArknights/MaaAssistantArknights) 项目进行大部分自动化，节省你的Token。

已支持的Harness：[Claude Code](#claude-code)（命令行、桌面版）、[Codex](#codex)（桌面版）、[OpenClaw](#openclaw)、[pi-agent](#pi-agent)。（持续更新中...）

</div>

<!-- markdownlint-restore -->

https://github.com/user-attachments/assets/d309322a-e263-4ddf-a098-02634295295e

## 下载与安装

你需要：

- 一台 Mac 或 Windows 电脑
- 一个安卓模拟器（见 [MAA 文档里的模拟器支持](https://maa.plus/docs/zh-cn/manual/device/)）
- 一个已配好模型的 harness：[Claude Code](#claude-code)、[Codex](#codex)、[OpenClaw](#openclaw) 或 [pi-agent](#pi-agent)

安装步骤：

```sh
git clone https://github.com/mansicer/MaaPilot.git
cd MaaPilot
```

然后打开任意一个 harness（第一次打开时可能需要信任项目），输入 `/maapilot-setup`。

### 支持哪些 Agent

MaaPilot 不绑定具体 Harness，但使用到的模型和Harness应该满足下面的要求：
1. 能看图、执行命令和读写文件，支持 `AGENTS.md` 和 skills ；
2. Harness支持设置定时任务来执行日常任务。

#### [Claude Code](https://claude.com/claude-code)

命令行和桌面版都支持。定时日常记在会话里，到点在会话里醒来跑日常、当场告诉你结果，所以这个会话别关。

#### [Codex](https://developers.openai.com/codex)

用 Codex 桌面版，定时日常靠它的会话内定时，应用要开着。设置时要批准联网安装运行环境，并同意信任这个文件夹。

#### [OpenClaw](https://docs.openclaw.ai)

定时日常用 Gateway 的重复定时，Gateway 要一直开着。安装方式和其他几家不同，见 [`docs/harnesses/openclaw.md`](docs/harnesses/openclaw.md)。

#### [pi-agent](https://pi.dev)

选择支持多模态的模型。定时日常要装定时扩展 pi-schedule-prompt，设置时助手会带你装。定时记在会话里，pi 要开着；关了以后用 `pi -c` 回到同一个会话，或者自行安装支持远程操作的插件。

接新的 harness 看 [`docs/harnesses/README.md`](docs/harnesses/README.md)。

## 亮点功能

- **定时日常**：每天按时执行日常，启动游戏，公招、基建换班收菜、信用商店、剿灭、按你的目标刷理智、领奖励，并进行汇报。
- **Agent接管**：MAA工作流产生问题时，会自动交由LLM agent进行接管。
- **养成目标**：可以设置干员等级、精英状态、技能和材料等长期目标，Agent 会在每天运行日常任务的时候自动朝目标推进。
- **活动与维护**：读官方公告，维护结束、活动开始时提醒你，问你活动期间怎么安排
- **抄作业**：从 [MAA 作业站](https://prts.plus)找作业，对照你的练度看缺谁、差多少，自动选择最合适的作业。

## 使用说明

### 如何使用

在对应的Harness上直接描述你想做的事

- "现在跑一次日常"
- "把遥练到精2"
- "合 10 个聚酸酯组"
- "把活动关卡全部打完并领取奖励"

### 安全防护

- **合成玉、源石**是程序死守的唯一一道关：每次花之前都会在对话里问你，你答应了才花。如果 Agent 在两次运行前后发现合成玉、源石数量发生异变，则会要求你的同意才继续去操作
- 练干员、合材料这类花费先汇报预算开销，用户同意了才做。
- 你回答过的偏好记在本机的 `private/memory/` 里，换一个 agent 也照样记得。
- 账号数据只存在本机的 `private/` 文件夹里，不受到 git 追踪。

### 服务器

暂时只支持官服。

## 参与开发

请先读（或者让Agent读） [`CLAUDE_DEV.md`](CLAUDE_DEV.md)（设计哲学和开发规矩）。

- 命令与输出：[`docs/cli.md`](docs/cli.md)
- Agent 的说明文档：[`AGENTS.md`](AGENTS.md)（各 agent 通用；[`CLAUDE.md`](CLAUDE.md) 引用）、[`.agents/skills/`](.agents/skills/) 和 [`docs/harnesses/`](docs/harnesses/)
- 测试：统一放在`tests`目录下

## 致谢

### 关联项目

- 游戏自动化：[MAA](https://github.com/MaaAssistantArknights/MaaAssistantArknights)（MaaCore，干员培养等任务）、[maa-cli](https://github.com/MaaAssistantArknights/maa-cli)（日常任务）
- 识别与操作框架：[MaaFramework](https://github.com/MaaXYZ/MaaFramework)
- Agent：[Claude Code](https://claude.com/claude-code)、[Codex](https://developers.openai.com/codex)、[OpenClaw](https://docs.openclaw.ai)、[pi](https://pi.dev)

### 开源库

- 图像处理：[OpenCV](https://github.com/opencv/opencv)、[NumPy](https://github.com/numpy/numpy)、[Pillow](https://github.com/python-pillow/Pillow)
- 文字识别模型：[PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)
- 森空岛设备标识：改写自 [arknights-mower](https://github.com/ArkMowers/arknights-mower)（MIT），源自 [skyland-auto-sign](https://gitee.com/FancyCabbage/skyland-auto-sign)
- 其他：[PyYAML](https://github.com/yaml/pyyaml)、[cryptography](https://github.com/pyca/cryptography)

### 数据源

- 游戏数据：[ArknightsGameData](https://github.com/Kengxxiao/ArknightsGameData)
- 关卡掉落：[企鹅物流数据统计](https://penguin-stats.cn/)
- 材料价值：[明日方舟一图流](https://ark.yituliu.cn/)
- 作业：[MAA 作业站](https://prts.plus)
- 账号数据：[森空岛](https://www.skland.com)
- 官方公告与游戏版本：[明日方舟官网](https://ak.hypergryph.com/)

## 声明

- 本项目与鹰角网络、MAA 项目没有关联；"明日方舟"及相关素材版权归鹰角网络所有。
- 森空岛是鹰角的官方社区，本项目仅使用社区从森空岛整理出来的调用方式，使用风险自负，用户登录凭据只存在本机 `private/` 里。
- 本项目使用 [GNU Affero General Public License v3.0](LICENSE) 开源，免费，仅供学习交流使用。
- MAA相关框架（MaaCore、maa-cli）不随本项目分发：从 MAA 的官方发布下载安装，按其自身的 AGPL-3.0 协议使用。
