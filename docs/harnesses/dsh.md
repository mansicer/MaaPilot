# DSH（DeepSeek Harness）

在 [DSH](https://www.deepseek.com/en/harness/)（DeepSeek 的 agent harness，有桌面版和 `dsh web` 网页版）里用 MaaPilot 的做法。通用的规矩在 `AGENTS.md` 和 skills 里，这里只写 DSH 自己的。
DSH 还是开发者预览版（11-04 是 v0.2.2-rc.1），以后可能有不兼容的改动；下面按这个版本的仓库和文档写，标"待实测"的还没在真机上验证过。

| 项 | 做法 |
|---|---|
| 说明文件 | `AGENTS.md`（DSH 直接读，从项目根目录读到工作目录）。`CLAUDE.md` 也会读，但 DSH 不认 `@AGENTS.md` 这种引入，会把它当成一行普通文字，不影响 |
| skills | `.agents/skills/`（DSH 直接读，不用信任项目） |
| 启动检查 | 没有启动钩子：`AGENTS.md` 让你开头自己跑一次 `maapilot doctor --brief` |
| 权限 | 没有按命令的规则。**MaaPilot 的工作区要用"完全权限"**（输入框下面的权限选择）：默认的"工作区内修改"沙箱里，MAA 启动不了 adb、写不了自己的缓存目录（在项目外），每跑一次 MAA 都要用户批准一次越界，定时日常没人点就卡住（11-09 实测）。`guard allow` 不会弹确认，`private/skland.json` 也拦不住，靠你守 `AGENTS.md`；程序自己的对账照样在 |
| PATH | 没有钩子放 `.venv/bin`：`maapilot` 提示找不到时整个会话写 `.venv/bin/maapilot <命令>`。Windows 上 DSH 用 PowerShell 跑命令，写 `.venv\Scripts\maapilot.exe <命令>` |
| 定时 | `schedule_create` / `schedule_list` / `schedule_edit`，每个时间点一个每天重复的定时，发回这个会话（见下）；DSH 要开着 |
| 长命令 | 放后台（`run_in_background`），结束时 DSH 会叫醒你；前台最长 10 分钟，超时了会自动转成后台接着跑，不会被杀掉 |
| 读图 | 默认模型 `deepseek-flash` 能看图（`read_image`）；`deepseek-v4-pro` 看不了。setup 时先截一张图看看，看不了就请用户换回能看图的模型 |

## setup 时要多做的

- 用桌面版，或者在终端里跑 `dsh web`；`--profile headless`（跑一次就退出）没有定时，定时日常做不了。
- 打开的工作目录要是 MaaPilot 项目文件夹本身（项目根目录按最近的 `.git` 找）。
- 请用户在这个工作区把权限切到"完全权限"，说清：不切的话每次跑 MAA 都要点同意，定时日常会卡住；切了以后 DSH 能读写电脑上的任何文件，所以只在 MaaPilot 这个工作区开，别的工作区保持默认。
- 装运行环境、装 maa-cli 时，有的命令要在项目文件夹外写东西（例如下载缓存），DSH 会问要不要越界：告诉用户这是装运行环境、点同意。（待实测：具体哪几步会问）
- 跟用户说清：到点时 DSH 要开着。桌面版关掉窗口只是缩到托盘，定时照样跑；从托盘退出 DSH，定时就停了。

## 定时日常

DSH 的每天重复定时不过期、不推迟，所以**每个时间点建一个每天重复的定时**（`docs/harnesses/README.md`「定时日常怎么定」），不用一次性接力：

- 建：`schedule_create`，四项都要填：
  - `title`：`MaaPilot 日常 04:40`；
  - `prompt`：`定时日常（04:40）：按 skill maapilot-daily 做；这是每天重复的定时，不用再建`（时间写这一个的）；
  - `rrule`：两行，`DTSTART;TZID=<时区>:<日期>T044000`、`RRULE:FREQ=DAILY`，例如 `DTSTART;TZID=Asia/Shanghai:20261110T044000` 换行 `RRULE:FREQ=DAILY`。时区写用户电脑的 IANA 时区（先看一下现在的时间和时区），日期写下一次的那天；
  - `target`：`this_session`（发回这个会话，结果就在这里）。不要用 `new`、`new_once`：那样每次在别的会话里跑。
- 列：`schedule_list`（列出所有会话的定时）。建之前先列一遍，同一个时间点已经有了就不再建；建完再列一遍，核对时间对上了再告诉用户。
- 改时间、删掉：`schedule_edit`，`action` 写删除，`id` 写要删的那个；再按新时间建。
- 定时日常醒来时只确认各时间点的定时都在，不再建。
- DSH 的规矩是只在用户明确要求时建、改定时：setup 时问过用户几点跑、用户同意了再建。
- 到点时你正在做别的事，提示会排队，做完再送进来。
- DSH 关着的时候不会叫醒你；重新打开后，每个定时只补发最近错过的一次。这时候日常可能在别的时间点跑：照常跑完，汇报里说明是补跑的。
- 偶尔可能重复送达同一次提示：醒来时先 `maapilot daily status`，这一次的日常已经在跑或刚跑完的，就不要再跑。

## 其他

- 用户偏好写进 `private/memory/`（`AGENTS.md`「用户偏好」），DSH 的记忆功能不用。

## 实测

- 待实测（Windows 11 桌面版）：
  - `AGENTS.md` 和 skills 是否加载，`/maapilot-setup` 能不能直接用；
  - PowerShell 里 `.venv\Scripts\maapilot.exe` 能直接跑、中文输出不乱码；
  - 装运行环境、`adb` 连模拟器时哪些命令要越界、会不会被沙箱挡住；
  - 读截图；`daily run` 放后台、结束时叫醒；
  - 每天重复的定时按时发回这个会话；DSH 退出再打开后的补发；
  - 真跑一次完整的定时日常。
