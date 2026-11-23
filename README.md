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

LLM 接管的明日方舟全流程自动化 Agent，定时日常到特殊需求，都通过聊天框解决。

基于 [MAA](https://github.com/MaaAssistantArknights/MaaAssistantArknights) 项目进行大部分自动化，节省你的 Token。

已支持的 Harness：[Claude Code](#claude-code)（命令行、桌面版）、[Codex](#codex)（桌面版）、[OpenClaw](#openclaw)、[pi-agent](#pi-agent)、[DSH](#dsh)、[Antigravity](#antigravity)、[ZCode](#zcode)（实验支持）。（持续更新中...）

</div>

<!-- markdownlint-restore -->

https://github.com/user-attachments/assets/d309322a-e263-4ddf-a098-02634295295e

## 下载与安装

你需要：

- 一台 Mac 或 Windows 电脑
- 一个安卓模拟器（见 [MAA 文档里的模拟器支持](https://maa.plus/docs/zh-cn/manual/device/)）
- 配置好账户和模型的 Agent Harness：[Claude Code](#claude-code)、[Codex](#codex)、[OpenClaw](#openclaw)、[pi-agent](#pi-agent)、[DSH](#dsh)、[Antigravity](#antigravity) 或 [ZCode](#zcode)

安装步骤：

```sh
git clone https://github.com/mansicer/MaaPilot.git
cd MaaPilot
```

然后打开任意一个 harness（第一次打开时可能需要信任项目），输入 `/maapilot-setup`。

### 支持哪些 Agent

MaaPilot 不绑定具体 Harness，但使用到的模型和 Harness 应该满足下面的要求：
1. 能看图、执行命令和读写文件，支持 `AGENTS.md` 和 skills ；
2. Harness 支持设置定时任务来执行日常任务。

<a id="claude-code"></a>

#### Claude Code [[Website]](https://claude.com/claude-code) [[Doc]](docs/harnesses/claude-code.md)

命令行和桌面版都支持。定时日常记在会话里，到点在会话里醒来跑日常、当场告诉你结果，所以这个会话别关。

<a id="codex"></a>

#### Codex [[Website]](https://developers.openai.com/codex) [[Doc]](docs/harnesses/codex.md)

用 Codex 桌面版（Codex CLI 不支持定时任务！），定时日常靠它的会话内定时，应用要开着。设置时要批准联网安装运行环境，并同意信任这个文件夹。

<a id="openclaw"></a>

#### OpenClaw [[Website]](https://docs.openclaw.ai) [[Doc]](docs/harnesses/openclaw.md)

定时日常用 Gateway 的重复定时，Gateway 要一直开着。安装方式和其他几家不同。

<a id="pi-agent"></a>

#### pi-agent [[Website]](https://pi.dev) [[Doc]](docs/harnesses/pi.md)

需要选择支持多模态的模型。定时日常要装定时扩展 pi-schedule-prompt（设置时 Agent 会问过你再自己装好，装完要重启一次 pi）。定时记在会话里，pi 要开着；关了以后用 `pi -c` 回到同一个会话，或者自行安装支持远程操作的插件。

<a id="dsh"></a>

#### DSH 桌面版 [[Website]](https://www.deepseek.com/en/harness/) [[Doc]](docs/harnesses/dsh.md)

使用 DeepSeek Harness 桌面版（网页版暂不支持！），选具有多模态的模型（如 `deepseek-flash`）。定时日常用它的每天重复定时，DSH 要开着（桌面版关掉窗口会缩到托盘，照样跑）。

> [!IMPORTANT]
> 因为 DSH 的沙箱会给项目文件夹加上"低完整性"标签，Windows 上请在 DSH 里把 MaaPilot 的工作区设成"完全权限"，**在第一次用之前就设好**。"低完整性"标签会导致项目里的 MAA 写不了自己的缓存，**这台电脑上所有 harness 跑 MaaPilot 都会失败**（包括 Claude Code、Codex 的定时日常）。MaaPilot 会在运行时自己检查是否被加上了"低完整性"标签，如果被加上需要在 PowerShell 里手动运行去掉它的命令（`icacls "<项目文件夹>" /setintegritylevel "(OI)(CI)M"`）。

<a id="antigravity"></a>

#### Antigravity [[Website]](https://antigravity.google) [[Doc]](docs/harnesses/antigravity.md)

将项目文件夹打开并设为当前活动工作区（Active Workspace）即可使用 Antigravity，支持通过会话定时跑日常，需要保持应用和会话开启。

<a id="zcode"></a>

#### ZCode [[Doc]](docs/harnesses/zcode.md)

在 ZCode 里打开项目文件夹即可使用。定时日常用它的定时自动化（每天重复、随工作区保存，应用重启后照常触发、落回会话里），应用要开着；模拟器用 nohup 脱离会话启动，免得应用更新重启时被连带杀掉。目前为实验支持。

### 扩展和反馈

如果想要自行接入新的 harness，可以参考 [`docs/harness-dev.md`](docs/harness-dev.md)。

由于部分 Harness 没有经过充分测试，在某个 Agent 上用得不顺、或者遇到了问题，可以直接在对话里说一句"我想反馈问题"：Agent 会整理反馈记录，并在对话里给你看要上传的内容，你同意后才会发给开发者。

## 亮点功能

- **定时日常**：每天按时执行日常，启动游戏，公招、基建换班收菜、信用商店、剿灭、按你的目标刷理智、领奖励，并进行汇报。
- **Agent 接管**：MAA 工作流产生问题时，会自动交由 LLM agent 进行接管。
- **养成目标**：可以设置干员等级、精英状态、技能和材料等长期目标，Agent 会在每天运行日常任务的时候自动朝目标推进。
- **保持最新**：MAA 资源自动更新；每次日常都看 MaaPilot 和 MAA 有没有新版本，问过你再更新；新版本跑不起来会自动退回原来的版本
- **活动与维护**：读官方公告，维护结束、活动开始时提醒你，问你活动期间怎么安排
- **抄作业**：从 [MAA 作业站](https://prts.plus)找作业，对照你的练度看缺谁、差多少，自动选择最合适的作业。

## 使用说明

### 如何使用

在对应的 Harness 上直接描述你想做的事

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

支持国服的官服和 B 服；国际服、日服、韩服、繁中服暂时不支持。
B 服的游戏客户端有更新时要自己在模拟器里更新（官服的可以让 agent 代劳）。

## 参与开发

请先读（或者让 Agent 读）[`CLAUDE_DEV.md`](CLAUDE_DEV.md)（设计哲学和开发规矩）。

- 命令与输出：[`docs/cli.md`](docs/cli.md)
- Agent 的说明文档：[`AGENTS.md`](AGENTS.md)（各 agent 通用；[`CLAUDE.md`](CLAUDE.md) 引用）、[`.agents/skills/`](.agents/skills/) 和 [`docs/harnesses/`](docs/harnesses/)
- 测试：统一放在`tests`目录下

## 致谢

### 关联项目

- 游戏自动化：[MAA](https://github.com/MaaAssistantArknights/MaaAssistantArknights)（MaaCore，干员培养等任务）、[maa-cli](https://github.com/MaaAssistantArknights/maa-cli)（日常任务）
- 识别与操作框架：[MaaFramework](https://github.com/MaaXYZ/MaaFramework)
- Agent：[Claude Code](https://claude.com/claude-code)、[Codex](https://developers.openai.com/codex)、[OpenClaw](https://docs.openclaw.ai)、[pi](https://pi.dev)、[DSH](https://www.deepseek.com/en/harness/)、[Antigravity](https://antigravity.google)

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
- MAA 相关框架（MaaCore、maa-cli）不随本项目分发：从 MAA 的官方发布下载安装，按其自身的 AGPL-3.0 协议使用。
