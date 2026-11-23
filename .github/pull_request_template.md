<!--
感谢贡献！中文或英文都可以。不适用的小节直接删掉。
Thanks for contributing! Chinese or English is fine. Delete the sections that do not apply.
-->

## PR 改动说明 / Summary

**请人类简单描述PR的改动和必要性，不要让 agent 代写。 / A human should briefly describe what this PR changes and why it is needed. Please do not have an agent write it.**

<!--
用几句话写：这个 PR 改了什么、为什么需要它。简单说明就可以。
In a few sentences: what this PR changes and why it is needed. Keep it short.
-->

## 详细改动 / Details

<!--
具体改了哪些地方、怎么改的；这一节可以让 agent 整理。有对应的 issue 就写上（Fixes #123）。
What was changed and how, file by file or behaviour by behaviour; an agent may write this section. Link the issue if there is one (Fixes #123).
-->

**描述PR的详细改动，可以使用Agent整理内容。 / Describe the changes in detail. You may use an agent to put this together.**

## 类型 / Type

- [ ] 修复 / Bug fix
- [ ] 新功能或新命令 / New feature or command
- [ ] 接入新的 harness（照 `docs/harness-dev.md` 做）/ New harness (follow `docs/harness-dev.md`)
- [ ] 只改文档或 skill / Docs or skills only
- [ ] 其他 / Other

## 对 agent 行为的影响 / Effect on what the agent does

<!--
合并后，用户的 agent 会多做、少做或换一种方式做什么？例如：会自己点某个按钮、会删文件、会启动程序、
无人值守（定时日常）时会做以前要先问用户的事、会多花游戏里的东西。没有就写"没有"。
After this is merged, what will a user's agent start doing, stop doing, or do differently? For example: tap a new button,
delete files, start programs, do something unattended that used to need the user's answer, spend in-game resources.
Write "none" if nothing changes.
-->

## 怎么测的 / How it was tested

- 自动测试 / Automated tests:
- 真机实测 / On a real emulator:
  - 系统、模拟器 / OS, emulator:
  - Harness 和模型 / Harness and model:
  - MaaPilot、maa-cli、MaaCore 版本 / versions:
  - 跑了什么、结果 / What was run, result:

## 自查 / Checklist

- [ ] `pytest -q`、`ruff check`、`mypy` 都过 / all pass
- [ ] 固定写死的规则（点击坐标、导航、识图识字、读数解析）在真模拟器上跑通过，或在上面写明"还没上模拟器" /
      Hard-coded rules (tap coordinates, navigation, recognition, parsing) were run on a real emulator, or marked as not yet run above
- [ ] 截图、日志、测试样本里没有昵称、UID、账号、token、本机路径 /
      No nickname, UID, account, token or local path in screenshots, logs or fixtures
- [ ] 没有把某台电脑的设定（模拟器、地址、路径、账号进度）写进代码、skill 或模板 /
      No machine-specific settings in code, skills or templates
