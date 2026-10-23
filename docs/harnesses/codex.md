# Codex（桌面版、命令行）

在 Codex 里用 MaaPilot 的做法。通用的规矩在 `AGENTS.md` 和 skills 里，这里只写 Codex 自己的。**定时日常只有桌面版能做**，命令行没有会话内定时（10-20 确认），只能在对话里用。

| 项 | 做法 |
|---|---|
| 说明文件 | `AGENTS.md`（Codex 直接读） |
| skills | `.agents/skills/`（Codex 直接读） |
| 启动检查 | 没有启动钩子：`AGENTS.md` 让你开头自己跑一次 `maapilot doctor --brief` |
| 权限 | `.codex/rules/maapilot.rules`，由程序生成，不要手改。只在 Codex **信任这个文件夹**后才加载（见下「setup 时」）。规则只管命令、管不了文件：`private/skland.json` 拦不住，靠你自己不碰 |
| PATH | 没有钩子放 `.venv/bin`：`maapilot` 提示找不到时整个会话写 `.venv/bin/maapilot <命令>`（Windows 上是 `.venv\Scripts\maapilot.exe <命令>`），规则里都列了 |
| 定时 | 桌面版：会话内的定时任务，有一次性的，按 `docs/harnesses/README.md` 的一次性接力做；结果回到同一个会话；应用要开着。命令行：没有 |
| 长命令 | 后台命令结束时叫不醒你：前台跑、等它结束（桌面版能跑长任务，10-21 实测） |

## setup 时要多做的

- 装运行环境要联网，沙箱里没有网络：命令会请用户批准在沙箱外跑，告诉用户这是装运行环境、点同意。
- 信任这个文件夹：`maapilot host status` 里 codex 的 `trusted` 是 `false` 时，征得同意后 `maapilot host install codex`（在 Codex 的用户设置里信任这个文件夹）。
  必须做：不信任时 Codex 不加载项目的命令规则，`guard allow` 每次弹确认这道护栏就没了。
- Windows：项目文件夹、Python、maa、adb 最好装在普通目录（例如 `C:\Users\<你>\` 下）：在 Codex 的 Windows 应用里装的东西可能落进它的隔离目录
  （`…\AppData\Local\Packages\…\LocalCache\…`），别的程序看不到。遇到时请用户在普通的 PowerShell 里重新装到普通目录。
- 命令行：跟用户说清定时日常做不了，可以在对话里说"跑一次日常"；想要定时，换用 Codex 桌面版或 Claude Code。setup 照样做完。
- 桌面版：跟用户说清到点时 Codex 应用要开着、这个会话不要关。

## 实测（用户在 Windows 上）

- 桌面版：会话内定时任务可用（10-20）；长任务可以（10-21）；有一次性的定时任务，按一次性接力、按 v0.2.0 的流程真跑了一次完整的定时日常，正常（10-21）。
- 命令行：没有会话内定时（10-20）。
- 待实测：守住项目规则。
