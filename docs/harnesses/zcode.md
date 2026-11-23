# ZCode

在 ZCode 里用 MaaPilot 的做法。通用的规矩在 `AGENTS.md` 和 skills 里，这里只写 ZCode 自己的。

| 项 | 做法 |
|---|---|
| 说明文件 | 直接读项目根的 `AGENTS.md`，不用别的引入文件 |
| skills | 直接读 `.agents/skills/`，不用建链接 |
| 启动检查 | 没有启动钩子：会话开头没有"MaaPilot 启动检查"那段时，按 `AGENTS.md` 自己跑一次 `maapilot doctor --brief` |
| 权限 | 没有项目级权限规则文件，弹不弹确认跟用户在 ZCode 里的权限设置走。用户开全自动（不弹确认）模式时 `guard allow` 也不会弹窗：合成玉、源石只有程序对账兜底（`AGENTS.md` 硬规则 1），`private/skland.json` 靠 agent 自觉不碰（硬规则 9） |
| PATH | 没有钩子代管：`maapilot` 提示找不到命令时，整个会话都写 `.venv/bin/maapilot <命令>` |
| 定时 | 定时自动化 `CronCreate` / `CronList` / `CronUpdate` / `CronDelete`，**每天重复的定时**（见下，不用一次性接力） |
| 长命令 | Bash 的后台运行，结束时叫醒你；前台超过 600 秒不死，自动转后台、结束时叫醒 |

## 定时日常

用 ZCode 的定时自动化（没加载就先用工具搜索找出来，有时要重新搜一遍才回来）。**用每天重复的定时，不用一次性接力**：

- ZCode 的定时是**工作区持久的自动化**：跨应用重启存活，到点自动触发，删掉才消失。
- 接力做不了：定时契约禁止"定时任务再建下一个"，而且定时触发的那一轮里 `CronCreate` / `CronDelete` / `CronUpdate` 会被摘掉（只剩 `CronPauseSelf`）。一次性（`recurring: false`）到点就自动结束，不适合定时日常。
- **一个会话同时只能绑一条自动化**：会话已被占着时再 `CronCreate` 会报错，把占着的那条 `CronDelete` 掉才能建。同一时间只在一个地方定（`AGENTS.md` 通用规矩）。
- 把 `config.yaml` `schedule` 的所有时间点写进**一条**多时点 cron，例如 04:40、16:40 就是 `40 4,16 * * *`。分钟别选 :00、:30。建完 `CronList` 核对各时间点都在；之后每次醒来确认还在、不再建。
- **提示要自包含**：定时提示送进当前会话（上下文还在），但应用重启后触发会落到新会话——单看提示就要能干活。除通用的日常提示外，把模拟器自举也写进去（setup 时查清这台机器的 AVD 名字和模拟器程序路径，填进提示；下面是写法示意，不是这台机器的设定）：

  > 定时日常（ZCode 每天重复定时）：先 `adb devices` 确认模拟器在线；不在线就 `rm -f ~/.android/avd/<AVD 名字>.avd/*.lock` 清残留锁，`nohup <模拟器程序> -avd <AVD 名字> -netdelay none -netspeed full > /tmp/avd-<AVD 名字>.log 2>&1 & disown` 拉起，等 `adb devices` 出现 `emulator-* device` 再继续。然后按 skill maapilot-daily 做日常（这是每天重复的定时，不用再建定时）。

- 模拟器检查**要用 `adb devices`，别只信 `doctor --brief`**：brief 不带逐项检查，模拟器死了它照样 ok。AVD 长挂会自己崩（图形后端崩溃、进程死掉，还留 `*.lock` 残留锁），自举步骤就是为这个准备的。

## 长命令

几分钟以上的（`daily run`、`maa fight`、`state box`）用 Bash 后台运行（`run_in_background`），结束时 ZCode 会叫醒你；前台命令超过 600 秒不用管，自动转后台。
**会话的后台任务（包括挂在里面启动的模拟器）跟着应用重启一起死**：模拟器要用 `nohup … & disown` 脱离会话启动，AVD 异常退出会留 `~/.android/avd/<AVD 名字>.avd/*.lock` 残留锁，启动前清掉。

## 其他

- 用户偏好写进 `private/memory/`（`AGENTS.md`「用户偏好」），**不要**写进 ZCode 的自动记忆：自动记忆只在 ZCode 里有，换 agent 就丢。
- 应用更新重启后：定时自动化原样保留、照常准点触发；重启后的那次触发落进新会话（所以提示要自包含）；会话里在跑的后台任务没了，模拟器按上面自举步骤重新拉起。
