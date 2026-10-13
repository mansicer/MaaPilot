# 开发说明

开发 MaaPilot 时先读这份。`CLAUDE.md` 和 `.claude/skills/` 是给用户的 agent 的，开发规则不要写进去。

## 项目
- 设计：`docs/design.md`；命令契约：`docs/cli.md`。思路是"程序薄、agent 厚"：工具只做可靠的事，做法写进 skill。
- 大小事情都让用户的 agent 知道：程序遇到情况（出错、维护、到期、跳过）不自己悄悄处理掉，要叫起或告诉 agent，说明发生了什么，由它判断、告诉用户。
- 代码：`maapilot/tools/`（工具命令）、`maapilot/flows/`（目标、日常、定时）、`maapilot/crew/`（设备与 MAA，旧项目搬来的）、`maapilot/knowledge/`（游戏数据）。
- 测试：`.venv/bin/python -m pytest -q`，改完要全过。
- 查用户的 agent 哪里出错、用错命令：`maapilot dev review [--since 3d] [--exclude <会话>]`（读命令记录和 Claude Code 会话记录，见 docs/cli.md「dev」），不用手动翻会话记录。

## 规矩
- 这台电脑的模拟器和账号是真实测试环境，也是用户在用的环境：代码、skill、模板不能写死它的设定（模拟器、地址、路径、账号进度）。
- `private/` 是用户的真实数据和配置，开发时只读；不改用户设好的日常时间、配置和目标。
- 碰模拟器前：避开定时日常（`private/config.yaml` 的 `schedule`）前后，`maapilot lock status` 确认没人占用；点击、花资源的操作先问用户。
- 要试 agent 的对话效果，在临时副本里跑 `claude -p`，不要在这个目录里跑（会留下会话记录和自动记忆）。
- 用户要求时才提交；提交信息用英文。

- 森空岛（`maapilot/crew/skland*.py`）用的是**非官方接口**（社区从森空岛 App / 网页版扒出来的，含数美设备指纹），随时可能变；
  坏了先对照社区的实现（docs/cli.md「skland」写了来源）。测试一律用假的 HTTP。用户的 token 在 `private/skland.json`，开发时也不要读、不要打印。

## 测试环境
- 本机的模拟器、服务器、日常时间都看 `private/config.yaml`，不写进仓库。
- 旧项目 `~/Developer/MAA` 只作参考，它的定时任务已停。
