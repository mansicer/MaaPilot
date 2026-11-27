# 用《明日方舟》PC 客户端（不装模拟器）

MaaPilot 默认接安卓模拟器（adb）。它也支持直接操作**《明日方舟》PC 客户端**：
截图、识字、点击、读数据、跑 MAA 任务都走同一套 `maapilot` 命令，`daily run` 照常。

这条路的样子：`crew/win32.py` 拿窗口句柄，用 MaaFramework 的 `Win32Controller` 截图和点击；
MAA 任务不走 maa-cli，而是用 MaaCore 的 `AsstAttachWindow` 直接挂到游戏窗口上跑（见「MAA 任务是怎么跑的」）。

## 配置

`private/config.yaml`：

```yaml
emulator:
  kind: win32                                      # 默认 adb
  window: ""                                       # 留空 = 自动找 UnityWndClass / Arknights.exe
  address: ""                                      # win32 用不到
```

只接了 PC 客户端就不用填 `adb`。`maapilot doctor` 的 `config`、`emulator`、`screen`、`input` 几项会按 win32 检查。

## 必须先弄明白的三件事

### 1. 游戏是管理员权限跑的，MaaPilot 也得是

鹰角启动器默认用管理员权限拉起游戏，游戏进程因此是 High 完整性级别。普通权限的进程去点它会被 UIPI 挡掉
（`error=5`，表现是"点了没反应"）。所以：

- **在你自己的命令行里跑**：那个窗口必须是"以管理员身份运行"的终端。
- **让 agent 跑**：agent 自己的进程通常没有管理员权限，需要一条提权通道。仓库外的常见做法是让用户
  以管理员启动一次常驻 worker，agent 往目录里丢命令、读结果（实现见各人的 harness 说明）。

`maapilot doctor` 的 `input` 一项会直接告诉你当前进程能不能点。

**别只看 `IsUserAnAdmin()` / `TokenElevation`**：把 High 令牌的完整性级别降到 Low（沙箱就这么干）以后，
`TokenElevation` 还是 1，但点击会被 UIPI 全部拦掉——表现是"截图正常、点了没反应"，MAA 任务还会照样报
"完成"。MaaPilot 现在两个都看（`crew/win32.py` 的 `token_state()` 返回 `(是否提权, 完整性级别)`），
`doctor` 和 `daily run` 都会先卡这一道。

计划任务（`schtasks /create /rl highest`）实测**可能**起出 Low 的 worker：建完要核实一遍
`status.json` 里的 `il` 是不是 `High`；不是就改成手动"右键 → 以管理员身份运行"。

### 2. 截图只能用 ScreenDC，所以窗口必须真的看得见

MaaFramework 的四种 Win32 截图方式里，只有 ScreenDC 能拿到干净画面（GDI 会撕裂、PrintWindow 对
Unity 的窗口是纯黑）。而 ScreenDC 截的是**屏幕区域**——窗口被别的窗口压住，截到的就是压在上面的那个。

所以拿锁期间（`cliutil.device()`）会把挡在游戏上的其它顶层窗口**临时最小化**，命令结束后还原。
这也意味着：**日常跑的时候，游戏窗口要是最前面那个**，别让它被聊天窗口、浏览器盖住。

### 3. 点击只能用 Seize，而且合成输入会被系统吞掉

`PostMessage` 投给 Unity 窗口没反应（实测五种输入方式：Seize / SendMessage / PostMessage /
LegacyEvent / SendMessageWithCursorPos，只有 Seize 有效）。Seize 要求游戏窗口是当前前台窗口，
`crew/win32.prepare()` 会先把窗口还原、抢到前台并**核实**，抢不到就直接报错、不会瞎点。

还有一种情况要留意：**会话本身不接收合成输入**。远控断开、会话休眠时，桌面还在渲染（截图、OCR 都正常），
但 `SetCursorPos` / `mouse_event` 投出去的点击不会到达任何窗口——不是游戏的问题，也不是 MaaPilot 的错。
判断方法：

```sh
# 建一个自己的小窗口去接一次点击；收到 = 会话正常，收不到 = 环境问题
maapilot doctor        # 先看 input 是 OK 还是 FAIL
```

## MAA 任务是怎么跑的

adb 那条路是 `maa-cli`（`maa run`），PC 客户端没有这层——maa-cli 的 `--addr` 只认 adb 和 PlayCover。
所以 win32 下：

- `maapilot maa <任务>` 走 `crew/maacore.run_task()`：子进程加载 MaaCore，用
  **`AsstAttachWindow(handle, hwnd, screencap_method, mouse_method, keyboard_method)`** 挂到游戏窗口上，
  再 `AsstAppendTask` + `AsstStart`；进度、日志、超时、停止都是现成的。
- 结果形状和 maa-cli 那条路一致（`task / ok / duration_s / tasks / offline / log`），所以 `daily run`
  不用改。
- `maa copilot` 可在关卡详情页运行，支持用作业编队和指定助战。任务完成后看游戏结算，确认星数。
- `maa update` 和资源热更新依赖 maa-cli，PC 端不支持。

有一个坑值得记下来：MaaCore 是用**裸名字** `LoadLibrary("MaaWin32ControlUnit.dll")` 加载控制单元的，
默认搜索路径里没有 MaaCore 自己所在的目录，会报 `Failed to load DLL, error code: 126`。
`maacore_runner.py` 启动时会把那个目录加进 `PATH`。

## 读数据

| 命令 | PC 端怎么读 |
|---|---|
| `maapilot state sanity` | 首页顶栏 OCR（和模拟器一样） |
| `maapilot state depot` | 首页顶栏 OCR（PC 端没有 MAA Depot 任务）。龙门币不带 `+`，合成玉和源石带 `+`，带 `+` 里最右的是源石 |
| `maapilot state box` | MAA OperBox 任务；**需要游戏在干员列表页**，PC 端未验证 |
| `maapilot guard snapshot` / `check` | 用顶栏的精确值对账；`exact: true` |

顶栏读数的精度比 MAA Depot 好：Depot 会把 16566 取整成 16000，顶栏不会。

## 已知限制

- **`copilot`（作业）**：MaaCore 的 `Completed` 表示作业流程已结束。游戏判定失败时也可能返回这个状态。若停在战后剧情，跳过后再看结算。
  另外（2026-11-27 实测）：PC 端加 `--formation` 时，编队阶段会停在“干员选择”页然后报错退出；
  **可靠的用法是先在编队页把队伍（含助战位）摆好，停在关卡详情页，再跑 `maa copilot <作业>` 且不加 `--formation`**。
  助战也同理：`--support 3` 在 PC 端没能把助战位换上去（日志里 `BattleStartAll` 变成 `DoNothing`），
  要手动进编队页 → 点助战位 → 选好友那名干员 → `招募助战`。空作业（0 个动作）也会“跑完但什么都没做”，先 `copilot show` 看 `actions`。
- **战斗内拖拽部署**：Win32 控制器上 MaaFW 的 `swipe` 是 deprecated（直接返回 false），只能走 `touch_down/move/up`；
  它内部用 `ClientToScreen` 换算客户区坐标，**不是 `GetWindowRect`**（窗口有标题栏时两者差几十像素，落点会整体偏）。
- **`state box`（干员练度）**：走 MAA OperBox，需要在干员列表页。PC 端**已验证可用**（2026-11-27，
  提权进程、游戏停在干员列表页，读到 137 名干员）；进程不是 High 时会在连接窗口那一步报 `error=5`，
  这时先看 `doctor` 的 `input`。`daily` 里这一项失败会停下交给 agent（handoff），不会带着错数据继续。
- **`maa update` / 资源热更新**：PC 端跳过（preflight 里不跑）。
- **游戏版本检查**：靠 adb 查包名版本号，PC 端跳过。
- **多显示器 / 窗口被挡住**：见上面第 2 条，日常跑的时候别让别的窗口压在游戏上。

## 排错

| 现象 | 先看 |
|---|---|
| 点击没反应 | `maapilot doctor` 的 `input`；进程是不是管理员；游戏窗口在不在前台 |
| 截图是别的程序 | 有窗口压在游戏上（ScreenDC 只能截屏幕）；把窗口最小化再试 |
| 截图全黑 / 花屏 | 用了 GDI 或 PrintWindow；PC 端固定走 ScreenDC |
| `attach_window failed` / `error code: 126` | `MaaWin32ControlUnit.dll` 的依赖没找到；lib 目录要在 `PATH` 里 |
| `error=5`（拒绝访问） | 权限：游戏是管理员权限，MaaPilot 也得是 |
| MAA 任务"完成"了但什么都没做 | 同上——点击没投递进去；任务会报 Completed 因为 MAA 认为它按流程走完了 |
